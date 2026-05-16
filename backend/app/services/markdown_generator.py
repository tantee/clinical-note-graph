from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from app.config import get_settings
from app.schemas.extraction import ClinicalExtractionResult
from app.utils.datetime import iso, utcnow
from app.utils.vault import patient_root, safe_seg, slug, wiki


def _frontmatter(meta: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _section(text: str, header: str) -> str | None:
    """Content under `header` until the next `## ` (header excluded)."""
    if not text:
        return None
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.strip() == header:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            out.append(line)
    return "\n".join(out).strip() if in_section else None


def _append_unique(new_lines: Iterable[str], existing: str) -> str:
    have = {line.strip() for line in existing.splitlines() if line.strip()}
    out = existing.splitlines()
    for line in new_lines:
        stripped = line.strip()
        if stripped and stripped not in have:
            out.append(line)
            have.add(stripped)
    return "\n".join(out).strip() + "\n"


def _visit_slug(encounter: dict[str, Any]) -> str:
    dt = encounter.get("dateTime")
    if isinstance(dt, datetime):
        date_str = dt.strftime("%Y-%m-%d")
    elif isinstance(dt, str):
        date_str = dt[:10]
    else:
        date_str = "unknown-date"
    return f"{date_str}-{safe_seg(str(encounter.get('type', 'visit')))}"


def _write_entity_note(
    root: Path,
    *,
    kind: str,
    slug_value: str,
    frontmatter_meta: dict[str, Any],
    title: str,
    patient_id: str,
    sections: list[tuple[str, list[str]]],
) -> str:
    path = root / kind / f"{slug_value}.md"
    existing = _read(path)
    body = [_frontmatter(frontmatter_meta), f"# {title}", f"Patient: [[index|{patient_id}]]", ""]
    for header, new_lines in sections:
        merged = _append_unique(new_lines, _section(existing, header) or "")
        body.extend([header, merged.strip(), ""])
    content = "\n".join(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content


def generate_markdown(
    *,
    patient: dict[str, Any],
    encounter: dict[str, Any],
    document: dict[str, Any],
    raw_content: str,
    extraction: ClinicalExtractionResult,
) -> dict[str, str]:
    pid = patient["patientId"]
    root = patient_root(pid, create_subdirs=True)
    now = iso(utcnow())
    vault = get_settings().vault_dir
    written: dict[str, str] = {}

    def remember(path: Path, content: str) -> None:
        written[str(path.relative_to(vault))] = content

    visit_slug = _visit_slug(encounter)
    visit_link_line = f"- {encounter.get('dateTime')} — [[visits/{visit_slug}|{encounter.get('type')}]]"

    # --- index.md (longitudinal merge) ---
    index_path = root / "index.md"
    existing_idx = _read(index_path)
    problem_links = sorted({wiki("problems", p.value) for p in extraction.problems})
    med_links = sorted({wiki("medications", m.name) for m in extraction.medications})

    timeline = _append_unique([visit_link_line], _section(existing_idx, "## Timeline") or "")
    problems_md = _append_unique(problem_links, _section(existing_idx, "## Active problems") or "")
    meds_md = _append_unique(med_links, _section(existing_idx, "## Medications") or "")

    idx_meta = {
        "type": "patient",
        "patientId": pid,
        "name": patient.get("name"),
        "gender": patient.get("gender"),
        "birthDate": str(patient.get("birthDate")) if patient.get("birthDate") else None,
        "updatedAt": now,
    }
    idx_body = [
        _frontmatter(idx_meta),
        f"# Patient {pid}",
        "",
        "> AI-assisted notes. Requires clinical review before any clinical action.",
        "",
        "## Active problems", problems_md.strip() or "_(none yet)_", "",
        "## Medications", meds_md.strip() or "_(none yet)_", "",
        "## Timeline", timeline.strip() or "_(no visits)_", "",
    ]
    idx_content = "\n".join(idx_body)
    index_path.write_text(idx_content, encoding="utf-8")
    remember(index_path, idx_content)

    # --- visits/{date}-{type}.md ---
    visit_path = root / "visits" / f"{visit_slug}.md"
    visit_meta = {
        "type": "visit",
        "patientId": pid,
        "encounterId": encounter.get("encounterId"),
        "encounterType": encounter.get("type"),
        "dateTime": encounter.get("dateTime"),
        "department": encounter.get("department"),
        "provider": encounter.get("provider"),
        "documentId": document.get("documentId"),
        "updatedAt": now,
    }
    visit_body = [
        _frontmatter(visit_meta),
        f"# {encounter.get('type', 'visit').title()} — {encounter.get('dateTime')}",
        "",
        f"Source document: [[sources/{document.get('documentId')}]]",
        f"Patient: [[index|{pid}]]",
        "",
        "## Summary (AI-generated)",
        extraction.summary or "_(no summary)_",
        "",
        "## Problems",
        *([f"- {wiki('problems', p.value)}{(' `' + p.normalizedCode + '`') if p.normalizedCode else ''}  _(confidence {p.confidence:.2f}, {p.reviewStatus})_" for p in extraction.problems] or ["_(none)_"]),
        "",
        "## Medications",
        *([f"- {wiki('medications', m.name)} — {m.action}{(' (' + m.indication + ')') if m.indication else ''}" for m in extraction.medications] or ["_(none)_"]),
        "",
        "## Observations",
        *([f"- {wiki('labs', o.name)}: {o.value} {o.unit or ''}".rstrip() for o in extraction.observations] or ["_(none)_"]),
        "",
        "## Plan",
        *([f"- {p.description}" for p in extraction.plans] or ["_(none)_"]),
        "",
        "## Evidence excerpt",
        "```",
        raw_content[:1500] + ("\n…" if len(raw_content) > 1500 else ""),
        "```",
        "",
    ]
    visit_content = "\n".join(visit_body)
    visit_path.write_text(visit_content, encoding="utf-8")
    remember(visit_path, visit_content)

    # --- sources/{documentId}.md ---
    src_path = root / "sources" / f"{safe_seg(document.get('documentId', ''))}.md"
    src_meta = {
        "type": "source", "patientId": pid,
        "documentId": document.get("documentId"),
        "sourceSystem": document.get("sourceSystem"),
        "version": document.get("version"),
        "format": document.get("format"),
        "receivedAt": now,
    }
    src_content = "\n".join([
        _frontmatter(src_meta),
        f"# Source document {document.get('documentId')}",
        f"Encounter: [[visits/{visit_slug}]]",
        "",
        "```",
        raw_content,
        "```",
    ])
    src_path.write_text(src_content, encoding="utf-8")
    remember(src_path, src_content)

    # --- problems/{slug}.md ---
    for p in extraction.problems:
        meta = {
            "type": "problem", "patientId": pid, "name": p.value,
            "icd10": p.normalizedCode if p.codingSystem == "ICD10" else None,
            "codingSystem": p.codingSystem, "updatedAt": now,
        }
        evidence_line = (
            f"- {encounter.get('dateTime')} [[visits/{visit_slug}]]: "
            f"\"{(p.evidenceText or '').strip()}\""
            f"  _(confidence {p.confidence:.2f}, {p.reviewStatus})_"
        )
        content = _write_entity_note(
            root, kind="problems", slug_value=slug(p.value), frontmatter_meta=meta,
            title=p.value, patient_id=pid,
            sections=[("## Timeline", [visit_link_line]), ("## Evidence", [evidence_line])],
        )
        remember(root / "problems" / f"{slug(p.value)}.md", content)

    # --- medications/{slug}.md ---
    for med in extraction.medications:
        meta = {
            "type": "medication", "patientId": pid, "name": med.name,
            "rxNorm": med.rxNorm, "lastAction": med.action, "indication": med.indication,
            "updatedAt": now,
        }
        history_line = (
            f"- {encounter.get('dateTime')} — {med.action} [[visits/{visit_slug}]]"
            f"{(' (' + med.indication + ')') if med.indication else ''}"
        )
        content = _write_entity_note(
            root, kind="medications", slug_value=slug(med.name), frontmatter_meta=meta,
            title=med.name, patient_id=pid,
            sections=[("## History", [history_line])],
        )
        remember(root / "medications" / f"{slug(med.name)}.md", content)

    # --- labs/{slug}.md ---
    for obs in extraction.observations:
        meta = {"type": "lab", "patientId": pid, "name": obs.name, "loinc": obs.loinc, "updatedAt": now}
        value_line = (
            f"- {obs.dateTime or encounter.get('dateTime')}: {obs.value} {obs.unit or ''} "
            f"[[visits/{visit_slug}]]"
        ).strip()
        content = _write_entity_note(
            root, kind="labs", slug_value=slug(obs.name), frontmatter_meta=meta,
            title=obs.name, patient_id=pid,
            sections=[("## Values", [value_line])],
        )
        remember(root / "labs" / f"{slug(obs.name)}.md", content)

    return written


_WIKI_RE = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]+)?\]\]")


def collect_backlinks(patient_id: str, target_rel: str) -> list[str]:
    """Find every patient file referencing `target_rel` (relative to vault root)."""
    root = patient_root(patient_id)
    if not root.exists():
        return []
    needle = target_rel.replace(".md", "").split("/", 2)[-1]
    target_simple = target_rel.replace(".md", "")
    hits: list[str] = []
    for p in root.rglob("*.md"):
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(p.relative_to(get_settings().vault_dir))
        if rel == target_rel:
            continue
        for match in _WIKI_RE.finditer(content):
            target = match.group(1).strip()
            if target == target_simple or target == needle:
                hits.append(rel)
                break
    return hits


def list_patient_files(patient_id: str) -> list[dict[str, Any]]:
    root = patient_root(patient_id)
    if not root.exists():
        return []
    vault = get_settings().vault_dir
    files: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(vault)
        files.append({"path": str(rel), "name": p.name, "kind": rel.parts[2] if len(rel.parts) > 2 else "index"})
    return files


def read_note(rel_path: str) -> str | None:
    vault = get_settings().vault_dir.resolve()
    full = (get_settings().vault_dir / rel_path).resolve()
    try:
        full.relative_to(vault)
    except ValueError:
        return None
    try:
        return full.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
