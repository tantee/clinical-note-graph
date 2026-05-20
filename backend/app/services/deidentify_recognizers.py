"""Regex recognizers used by the HIPAA Safe Harbor de-identifier.

Two consumers:

1. `Deidentifier.redact_text` in `deidentify.py` runs every entry in
   `BUILTIN_PATTERNS` as a plain regex sweep — that's the `regex_only` level
   and the fast path inside the `safe_harbor` pipeline. Each pattern carries
   a HIPAA Safe Harbor identifier category so audit counts stay meaningful.

2. When Presidio is available, the same recognisers are registered with the
   analyzer (`build_presidio_recognizers`) so the NER pipeline can pick them
   up alongside spaCy's `PERSON` / `LOCATION` detections. Presidio expects
   `PatternRecognizer` instances; we adapt our pattern tuples to that shape
   on demand.

Thai-specific patterns live alongside the generic ones — we ship Thai +
English mixed data, and Thai phone/ID/address shapes don't survive an
English-only recogniser set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Recognizer:
    """One regex-based recogniser.

    `category` lines up with the Presidio entity names we want in audit
    counts (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, …). `validator` is an
    optional second-stage filter that rejects false positives — e.g. our
    Thai national ID check verifies the 13-digit checksum so 13 consecutive
    digits in a date range don't trip the recogniser.
    """

    name: str
    category: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    score: float = 0.85


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _thai_national_id_valid(s: str) -> bool:
    """Thai national ID checksum (13 digits).

    The 13th digit is `(11 - (sum of digits[i] * (13 - i) for i in 0..11)) % 10`.
    We keep only digits — the input may include hyphens — and bail on length.
    """
    digits = re.sub(r"\D", "", s)
    if len(digits) != 13:
        return False
    total = sum(int(digits[i]) * (13 - i) for i in range(12))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


# ---------------------------------------------------------------------------
# Patterns — order matters: more specific patterns first so they win when two
# recognisers overlap on the same span. Presidio handles overlap resolution
# itself; for our regex_only path we apply replacements left-to-right with a
# longest-match-wins sweep inside `Deidentifier`.
# ---------------------------------------------------------------------------

BUILTIN_PATTERNS: list[Recognizer] = [
    # ---- email ----
    Recognizer(
        name="email",
        category="EMAIL_ADDRESS",
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    # ---- URLs ----
    Recognizer(
        name="url",
        category="URL",
        pattern=re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE),
    ),
    # ---- IPv4 ----
    Recognizer(
        name="ipv4",
        category="IP_ADDRESS",
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
        ),
    ),
    # ---- IPv6 (loose — covers full and `::`-compressed forms) ----
    Recognizer(
        name="ipv6",
        category="IP_ADDRESS",
        pattern=re.compile(
            r"(?:[0-9A-Fa-f]{1,4}::?)+[0-9A-Fa-f]{1,4}"
        ),
    ),
    # ---- Thai national ID: 13 digits, optionally with hyphens ----
    Recognizer(
        name="thai_national_id",
        category="TH_NATIONAL_ID",
        pattern=re.compile(r"\b\d(?:[-\s]?\d){12}\b"),
        validator=_thai_national_id_valid,
        score=0.95,
    ),
    # ---- US SSN-shape (defensive even though we're Thai) ----
    Recognizer(
        name="us_ssn",
        category="US_SSN",
        pattern=re.compile(r"\b(?!000|666)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    ),
    # ---- Thai phone: 08x-xxx-xxxx / 0xx xxx xxxx / +66 ... ----
    Recognizer(
        name="thai_phone",
        category="PHONE_NUMBER",
        pattern=re.compile(
            r"(?:(?:\+?66[\s\-]?)|\b0)(?:\d[\s\-]?){8,9}\d"
        ),
        score=0.7,
    ),
    # ---- International phone (E.164-ish, broad) ----
    Recognizer(
        name="intl_phone",
        category="PHONE_NUMBER",
        pattern=re.compile(
            r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}\b"
        ),
        score=0.7,
    ),
    # ---- Our HN-XXXX patient identifier ----
    Recognizer(
        name="hn_id",
        category="HN_PATIENT_ID",
        pattern=re.compile(r"\bHN-[A-Z0-9\-]{2,}\b"),
        score=0.95,
    ),
    # ---- MRN-like patterns (MRN: 12345 / MRN# 12345) ----
    Recognizer(
        name="mrn",
        category="MEDICAL_RECORD_NUMBER",
        pattern=re.compile(r"\bMRN[#:\s]?\s*\d{4,}\b", re.IGNORECASE),
    ),
    # ---- Room number ----
    Recognizer(
        name="room_number",
        category="LOCATION",
        pattern=re.compile(r"\b(?:Room|Rm|ห้อง)\s*[A-Z0-9\-]{1,8}\b", re.IGNORECASE),
        score=0.6,
    ),
    # ---- Account / certificate / license numbers (generic, low confidence) ----
    Recognizer(
        name="acct_license",
        category="ACCOUNT_NUMBER",
        pattern=re.compile(
            r"\b(?:Acct|Account|License|Lic|Cert|Certificate)[#:\s]?\s*[A-Z0-9\-]{5,}\b",
            re.IGNORECASE,
        ),
        score=0.55,
    ),
    # ---- Health plan beneficiary ----
    Recognizer(
        name="health_plan",
        category="HEALTH_PLAN_BENEFICIARY",
        pattern=re.compile(
            r"\b(?:Member ID|Policy|Plan|Beneficiary)[#:\s]?\s*[A-Z0-9\-]{4,}\b",
            re.IGNORECASE,
        ),
        score=0.55,
    ),
    # ---- Vehicle (VIN/plate) ----
    Recognizer(
        name="vin",
        category="VEHICLE_ID",
        pattern=re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"),
        score=0.5,
    ),
    # ---- Device serial ----
    Recognizer(
        name="device_serial",
        category="DEVICE_ID",
        pattern=re.compile(
            r"\b(?:Serial|SN|S/N)[#:\s]?\s*[A-Z0-9\-]{6,}\b",
            re.IGNORECASE,
        ),
        score=0.55,
    ),
    # ---- Biometric mentions (free-text references, not actual data) ----
    Recognizer(
        name="biometric_mention",
        category="BIOMETRIC",
        pattern=re.compile(
            r"\b(?:fingerprint|retina scan|iris scan|voice print|gait)\b",
            re.IGNORECASE,
        ),
        score=0.4,
    ),
    # ---- Dates: ISO-ish, US, EU, written ----
    Recognizer(
        name="date_iso",
        category="DATE_TIME",
        pattern=re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
        score=0.9,
    ),
    Recognizer(
        name="date_slash",
        category="DATE_TIME",
        pattern=re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
        score=0.7,
    ),
    # ---- Thai address tokens (best-effort) ----
    Recognizer(
        name="thai_admin",
        category="LOCATION",
        pattern=re.compile(
            r"(?:จังหวัด|อำเภอ|ตำบล|เขต|แขวง)\s*\S+",
        ),
        score=0.5,
    ),
]


def by_name(name: str) -> Recognizer:
    for r in BUILTIN_PATTERNS:
        if r.name == name:
            return r
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Presidio adapter
# ---------------------------------------------------------------------------


def build_presidio_recognizers() -> list[Any]:
    """Return Presidio `PatternRecognizer` objects mirroring `BUILTIN_PATTERNS`.

    Returns an empty list if Presidio isn't installed — callers should treat
    that as the cue to fall back to the regex-only sweep.
    """
    try:
        from presidio_analyzer import Pattern, PatternRecognizer
    except Exception:
        return []

    out: list[Any] = []
    for r in BUILTIN_PATTERNS:
        pat = Pattern(name=r.name, regex=r.pattern.pattern, score=r.score)
        out.append(
            PatternRecognizer(
                supported_entity=r.category,
                patterns=[pat],
            )
        )
    return out
