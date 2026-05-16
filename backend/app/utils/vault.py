from __future__ import annotations

import re
from pathlib import Path

from slugify import slugify as _slugify

from app.config import get_settings

_SAFE_SEG = re.compile(r"[^A-Za-z0-9._-]")


def safe_seg(part: str) -> str:
    return _SAFE_SEG.sub("-", part).strip("-") or "unknown"


def slug(name: str) -> str:
    return _slugify(name) or "unknown"


def patient_root(patient_id: str, *, create_subdirs: bool = False) -> Path:
    root = get_settings().vault_dir / "patients" / safe_seg(patient_id)
    if create_subdirs:
        for sub in ("visits", "problems", "medications", "labs", "sources"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def wiki(kind: str, name: str) -> str:
    return f"[[{kind}/{slug(name)}|{name}]]"
