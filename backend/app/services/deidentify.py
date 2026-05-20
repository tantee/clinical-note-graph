"""HIPAA Safe Harbor de-identification at the outbound boundary.

Each AI call constructs a fresh `Deidentifier` (so pseudonyms are stable
inside one request but never leak across requests), pipes the structured
inputs through `pseudonymize_patient` / `pseudonymize_facts`, then the
free-text inputs through `redact_text(..., lang=...)`. The redacted payload
is what gets POSTed to the AI provider; the on-disk data model stays
unredacted.

Levels:

* `off` — no-op (used with BAA-bound providers like Anthropic Enterprise)
* `regex_only` — fast deterministic regex sweep (the recognisers in
  `deidentify_recognizers.BUILTIN_PATTERNS`)
* `safe_harbor` — regex + Presidio NER (English) + PyThaiNLP NER (Thai) for
  free-text. Falls back to regex_only if the NER libraries aren't importable
  so a slim install can still demo end-to-end without the +500 MB image hit.

The redactor reports per-category counts via `counts_snapshot()`. We persist
those into `ai_outputs.redaction_counts` so an auditor can ask "what did the
redactor catch on the prompt that left the host on 2026-05-19 at 14:03?".
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from typing import Any, Iterable

from app.services.deidentify_recognizers import (
    BUILTIN_PATTERNS,
    Recognizer,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy NER pipelines — loaded once at first use, reused across calls.
# ---------------------------------------------------------------------------

_PRESIDIO_LOCK = threading.Lock()
_PRESIDIO_ANALYZER: Any = None
_PRESIDIO_FAILED: bool = False

_PYTHAINLP_LOCK = threading.Lock()
_PYTHAINLP_NER: Any = None
_PYTHAINLP_FAILED: bool = False


def _get_presidio_analyzer():
    """Return a singleton Presidio AnalyzerEngine with our custom recognizers,
    or None if Presidio / spaCy aren't installed."""
    global _PRESIDIO_ANALYZER, _PRESIDIO_FAILED
    if _PRESIDIO_FAILED:
        return None
    if _PRESIDIO_ANALYZER is not None:
        return _PRESIDIO_ANALYZER
    with _PRESIDIO_LOCK:
        if _PRESIDIO_ANALYZER is not None:
            return _PRESIDIO_ANALYZER
        if _PRESIDIO_FAILED:
            return None
        try:
            from presidio_analyzer import AnalyzerEngine
            from app.services.deidentify_recognizers import build_presidio_recognizers

            analyzer = AnalyzerEngine()
            for rec in build_presidio_recognizers():
                analyzer.registry.add_recognizer(rec)
            _PRESIDIO_ANALYZER = analyzer
            return analyzer
        except Exception as exc:
            log.warning("Presidio analyzer unavailable; falling back to regex-only: %s", exc)
            _PRESIDIO_FAILED = True
            return None


def _get_pythainlp_ner():
    """Return a PyThaiNLP NER tagger, or None if PyThaiNLP isn't installed."""
    global _PYTHAINLP_NER, _PYTHAINLP_FAILED
    if _PYTHAINLP_FAILED:
        return None
    if _PYTHAINLP_NER is not None:
        return _PYTHAINLP_NER
    with _PYTHAINLP_LOCK:
        if _PYTHAINLP_NER is not None:
            return _PYTHAINLP_NER
        if _PYTHAINLP_FAILED:
            return None
        try:
            from pythainlp.tag.named_entity import NER  # type: ignore

            _PYTHAINLP_NER = NER("thainer")
            return _PYTHAINLP_NER
        except Exception as exc:
            log.warning("PyThaiNLP NER unavailable; Thai narrative will use regex only: %s", exc)
            _PYTHAINLP_FAILED = True
            return None


def warm_up() -> None:
    """Optionally pre-load NER pipelines at app startup so first-request
    latency doesn't pay the model-load cost. Safe to call from a FastAPI
    startup hook; failures are swallowed (already logged inside the loaders)."""
    _get_presidio_analyzer()
    _get_pythainlp_ner()


# ---------------------------------------------------------------------------
# Pseudonym map
# ---------------------------------------------------------------------------


class _PseudonymMap:
    """Deterministic-per-request mapping of original identifier -> pseudonym.

    The same patient name appearing five times in one request becomes the
    same `PATIENT-A1` token every time, so the LLM can reason about the
    referent. Between requests the map is reset, so the LLM never sees
    cross-request linkage.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._by_key: dict[tuple[str, str], str] = {}

    def assign(self, kind: str, value: str) -> str:
        norm = (value or "").strip()
        if not norm:
            return ""
        key = (kind, norm.lower())
        if key in self._by_key:
            return self._by_key[key]
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        # PATIENT-A1, PATIENT-A2, ..., PATIENT-A26, PATIENT-A27, ...
        # Single letter cycles A..Z then double-letter; index lets us scale.
        token = f"{kind}-A{n}"
        self._by_key[key] = token
        return token

    def known(self) -> list[tuple[tuple[str, str], str]]:
        return list(self._by_key.items())


# ---------------------------------------------------------------------------
# Deidentifier
# ---------------------------------------------------------------------------


# Fields whose values are dates that should be rounded to a year. The
# `date_time` variant covers the rama-* style snake_case used in extraction
# evidence objects.
DATE_FIELDS: set[str] = {
    "dateTime", "date_time", "datetime",
    "received_at", "receivedAt",
    "created_at", "createdAt",
    "updated_at", "updatedAt",
    "encounterDt", "encounter_dt", "encounter_datetime",
    "onset", "onsetDate", "onset_date",
    "performed", "performedDateTime", "performed_datetime",
    "effective", "effectiveDateTime", "effective_datetime",
    "issued",
}

BIRTH_FIELDS: set[str] = {"birth_date", "birthDate", "dob", "DOB"}

PATIENT_NAME_FIELDS: set[str] = {"name", "patient_name", "patientName", "fullName"}
PROVIDER_NAME_FIELDS: set[str] = {
    "provider", "providerName", "physician", "physicianName",
    "attending", "attendingName", "author", "authorName",
    "ordered_by", "orderedBy",
}
PATIENT_ID_FIELDS: set[str] = {"patient_id", "patientId", "hn", "HN", "mrn", "MRN"}


def _to_year(value: Any) -> Any:
    """Round any recognisable date / datetime down to its year. Returns
    `value` unchanged when nothing date-like is detected."""
    if value is None:
        return value
    if isinstance(value, datetime):
        return str(value.year)
    if isinstance(value, date):
        return str(value.year)
    if isinstance(value, (int, float)):
        # Already a year (or a unix timestamp — but we don't try to disambiguate;
        # callers shouldn't be passing ms-since-epoch into a `dateTime` field).
        v = int(value)
        if 1900 <= v <= 2100:
            return str(v)
        return value
    s = str(value)
    # ISO-ish: 2026-05-19, 2026-05-19T14:03:22, 2026-05-19 14:03
    m = re.match(r"\s*(\d{4})\b", s)
    if m:
        return m.group(1)
    # US/EU slash: 05/19/2026 or 19/05/2026 — both end in a 4-digit year
    m = re.search(r"\b(\d{4})\b\s*$", s)
    if m:
        return m.group(1)
    # 19/05/26 — 2-digit; punt to 20xx.
    m = re.search(r"\b\d{1,2}/\d{1,2}/(\d{2})\b", s)
    if m:
        return f"20{m.group(1)}"
    return value


def _age_bucket_for(birth_year: int, today_year: int) -> str:
    age = today_year - birth_year
    if age < 30:
        return "<30"
    if age < 45:
        return "30-44"
    if age < 65:
        return "45-64"
    return "65+"


class Deidentifier:
    """Per-request HIPAA Safe Harbor redactor.

    Construct one per outbound AI call so pseudonyms stay stable within the
    call but never cross between calls. Pass the `level` from
    `Settings.DEIDENTIFY_LEVEL`.
    """

    def __init__(self, level: str = "safe_harbor", ner_threshold: float = 0.5) -> None:
        self.level = (level or "safe_harbor").strip().lower()
        if self.level not in {"off", "regex_only", "safe_harbor"}:
            self.level = "safe_harbor"
        self.ner_threshold = float(ner_threshold)
        self._pseudo = _PseudonymMap()
        self._counts: dict[str, int] = {}
        self._today_year = date.today().year

    # ----- public API -----

    def disabled(self) -> bool:
        return self.level == "off"

    def counts_snapshot(self) -> dict[str, int]:
        return dict(self._counts)

    def pseudonym_for(self, value: str, kind: str = "PATIENT") -> str:
        """Get-or-create a pseudonym for `value` under `kind`."""
        if not value:
            return ""
        token = self._pseudo.assign(kind, value)
        if token:
            self._bump(f"{kind}_PSEUDONYM")
        return token

    def redact_text(self, text: str, *, lang: str = "en") -> str:
        """Redact PHI in free text. `lang` selects the NER pipeline ("en" or "th"
        or "auto"); regex recognisers always run regardless of lang."""
        if self.disabled() or not text:
            return text

        # First: replace anything we've already pseudonymised in this request
        # so the LLM sees a consistent token across structured + free-text.
        text = self._apply_known_pseudonyms(text)

        # Regex sweep — always on, both for `regex_only` and as the fast first
        # pass of `safe_harbor`.
        text = self._apply_regex(text)

        if self.level == "safe_harbor":
            language = (lang or "en").lower()
            if language == "auto":
                language = "th" if _looks_thai(text) else "en"
            if language == "en":
                text = self._apply_presidio(text)
            elif language == "th":
                text = self._apply_pythainlp(text)

        return text

    def pseudonymize_patient(self, patient: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return a copy of `patient` with name / HN / DOB / dates redacted."""
        if not patient or self.disabled():
            return patient
        out: dict[str, Any] = {}
        for k, v in patient.items():
            if k in PATIENT_NAME_FIELDS and isinstance(v, str) and v.strip():
                out[k] = self.pseudonym_for(v, "PATIENT")
            elif k in PATIENT_ID_FIELDS and isinstance(v, str) and v.strip():
                out[k] = self.pseudonym_for(v, "HN")
            elif k in PROVIDER_NAME_FIELDS and isinstance(v, str) and v.strip():
                out[k] = self.pseudonym_for(v, "PROVIDER")
            elif k in BIRTH_FIELDS and v is not None:
                year = _to_year(v)
                try:
                    bucket = _age_bucket_for(int(str(year)[:4]), self._today_year)
                    out[k] = f"{year} ({bucket})"
                    self._bump("BIRTH_DATE")
                except (ValueError, TypeError):
                    out[k] = year
                    self._bump("BIRTH_DATE")
            elif k in DATE_FIELDS and v is not None:
                rounded = _to_year(v)
                if rounded != v:
                    self._bump("DATE_TIME")
                out[k] = rounded
            elif isinstance(v, dict):
                out[k] = self.pseudonymize_patient(v)
            elif isinstance(v, list):
                out[k] = [self.pseudonymize_patient(x) if isinstance(x, dict) else x for x in v]
            else:
                out[k] = v
        return out

    def pseudonymize_facts(self, facts: dict[str, Any] | None) -> dict[str, Any] | None:
        """Apply the redactor to a `patient_facts` payload (the dict shipped to
        summarize / suggest_coding). Walks every dict/list, recursively, so
        evidence text and embedded patient blocks both get redacted."""
        if facts is None or self.disabled():
            return facts
        return self._walk(facts, lang="auto")

    # ----- internals -----

    def _walk(self, obj: Any, *, lang: str) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if k in PATIENT_NAME_FIELDS and isinstance(v, str) and v.strip():
                    out[k] = self.pseudonym_for(v, "PATIENT")
                elif k in PATIENT_ID_FIELDS and isinstance(v, str) and v.strip():
                    out[k] = self.pseudonym_for(v, "HN")
                elif k in PROVIDER_NAME_FIELDS and isinstance(v, str) and v.strip():
                    out[k] = self.pseudonym_for(v, "PROVIDER")
                elif k in BIRTH_FIELDS and v is not None:
                    year = _to_year(v)
                    try:
                        bucket = _age_bucket_for(int(str(year)[:4]), self._today_year)
                        out[k] = f"{year} ({bucket})"
                        self._bump("BIRTH_DATE")
                    except (ValueError, TypeError):
                        out[k] = year
                        self._bump("BIRTH_DATE")
                elif k in DATE_FIELDS and v is not None:
                    rounded = _to_year(v)
                    if rounded != v:
                        self._bump("DATE_TIME")
                    out[k] = rounded
                else:
                    out[k] = self._walk(v, lang=lang)
            return out
        if isinstance(obj, list):
            return [self._walk(x, lang=lang) for x in obj]
        if isinstance(obj, str):
            return self.redact_text(obj, lang=lang)
        return obj

    def _bump(self, category: str, n: int = 1) -> None:
        self._counts[category] = self._counts.get(category, 0) + n

    def _apply_known_pseudonyms(self, text: str) -> str:
        """Substitute strings we've already pseudonymised, longest first
        (so 'Dr Somchai Sample' beats a fragment 'Somchai'). Counts go to a
        synthetic `PSEUDONYM_REPLACED` bucket so we can tell rewrite from
        recogniser catches in audit."""
        if not self._pseudo.known():
            return text
        items = sorted(
            self._pseudo.known(),
            key=lambda kv: len(kv[0][1]),
            reverse=True,
        )
        for (kind, original_lc), token in items:
            # We stored the lowercased original; find any case in the text.
            pattern = re.compile(re.escape(original_lc), re.IGNORECASE)
            new_text, n = pattern.subn(token, text)
            if n:
                self._bump("PSEUDONYM_REPLACED", n)
                text = new_text
        return text

    def _apply_regex(self, text: str) -> str:
        # Apply each recognizer; for date matches we replace with the year only,
        # for everything else we replace with a category tag.
        for rec in BUILTIN_PATTERNS:
            text = self._sub_recognizer(text, rec)
        return text

    def _sub_recognizer(self, text: str, rec: Recognizer) -> str:
        def _replace(m: re.Match[str]) -> str:
            span = m.group(0)
            if rec.validator and not rec.validator(span):
                return span
            self._bump(rec.category)
            if rec.category == "DATE_TIME":
                year = _to_year(span)
                return str(year) if year else "[REDACTED-DATE]"
            return f"<{rec.category}>"

        return rec.pattern.sub(_replace, text)

    def _apply_presidio(self, text: str) -> str:
        analyzer = _get_presidio_analyzer()
        if analyzer is None:
            return text
        try:
            results = analyzer.analyze(text=text, language="en", score_threshold=self.ner_threshold)
        except Exception as exc:
            log.warning("Presidio analyze failed; leaving text as-is: %s", exc)
            return text
        if not results:
            return text
        # Replace right-to-left so spans don't shift under us.
        results = sorted(results, key=lambda r: r.start, reverse=True)
        for r in results:
            entity = getattr(r, "entity_type", None) or "PHI"
            span = text[r.start : r.end]
            # Use stable pseudonyms for PERSON / LOCATION so the LLM can reason
            # across multiple mentions in the same prompt.
            if entity == "PERSON" and span.strip():
                replacement = self.pseudonym_for(span, "PROVIDER")
            else:
                replacement = f"<{entity}>"
            text = text[: r.start] + replacement + text[r.end :]
            self._bump(entity)
        return text

    def _apply_pythainlp(self, text: str) -> str:
        ner = _get_pythainlp_ner()
        if ner is None:
            return text
        try:
            tags = ner.tag(text, pos=False)
        except Exception as exc:
            log.warning("PyThaiNLP tag failed; leaving Thai text as-is: %s", exc)
            return text
        # PyThaiNLP returns tokens with BIO-style tags; aggregate consecutive
        # tokens with the same entity into spans, then sub them out.
        out_parts: list[str] = []
        i = 0
        sensitive = {"PERSON", "LOCATION", "PHONE", "EMAIL", "DATE", "ORGANIZATION"}
        while i < len(tags):
            tok, tag = tags[i]
            if tag and tag != "O":
                # tag looks like 'B-PERSON' or 'I-PERSON'
                kind = tag.split("-", 1)[-1].upper()
                buf = [tok]
                i += 1
                while i < len(tags) and tags[i][1] and tags[i][1].endswith(kind):
                    buf.append(tags[i][0])
                    i += 1
                span = "".join(buf)
                if kind in sensitive:
                    self._bump(kind)
                    out_parts.append(f"<{kind}>")
                else:
                    out_parts.append(span)
            else:
                out_parts.append(tok)
                i += 1
        return "".join(out_parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_THAI_BLOCK = re.compile(r"[฀-๿]")


def _looks_thai(s: str) -> bool:
    """True if a text contains any character in the Thai Unicode block."""
    return bool(_THAI_BLOCK.search(s or ""))


def for_settings(settings: Any) -> Deidentifier:
    """Construct a fresh `Deidentifier` for the current effective Settings."""
    level = getattr(settings, "DEIDENTIFY_LEVEL", "safe_harbor")
    thr = getattr(settings, "DEIDENTIFY_NER_THRESHOLD", 0.5)
    return Deidentifier(level=level, ner_threshold=thr)
