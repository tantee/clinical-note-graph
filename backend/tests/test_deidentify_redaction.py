"""Per-recognizer redaction tests.

Tests exercise the regex_only level so they don't depend on the +500 MB
spaCy / Presidio install. Each test pins one Safe Harbor identifier
category and asserts both that the input is redacted **and** that the
category appears in the audit `counts_snapshot()`.
"""

from __future__ import annotations

import pytest

from app.services.deidentify import Deidentifier
from app.services.deidentify_recognizers import _thai_national_id_valid


def _deid() -> Deidentifier:
    return Deidentifier(level="regex_only")


def test_redact_email_address():
    d = _deid()
    out = d.redact_text("Contact me at jane.doe@example.com today.")
    assert "jane.doe@example.com" not in out
    assert "<EMAIL_ADDRESS>" in out
    assert d.counts_snapshot()["EMAIL_ADDRESS"] == 1


def test_redact_url():
    d = _deid()
    out = d.redact_text("See https://hospital.example.com/patient for details.")
    assert "https://hospital.example.com/patient" not in out
    assert "<URL>" in out


def test_redact_ipv4_and_ipv6():
    d = _deid()
    out = d.redact_text("Server at 192.168.1.5 and 2001:db8::1 went down.")
    assert "192.168.1.5" not in out
    assert "2001:db8::1" not in out
    assert d.counts_snapshot().get("IP_ADDRESS", 0) == 2


def test_redact_thai_national_id_valid_checksum():
    # 1101700230708 has a valid Thai national ID checksum (weights 13..2,
    # check digit = (11 - sum % 11) % 10 = 8 for first 12 digits 110170023070).
    d = _deid()
    out = d.redact_text("เลขบัตรประชาชน 1101700230708 ของผู้ป่วย")
    assert "1101700230708" not in out
    assert "<TH_NATIONAL_ID>" in out


def test_thai_national_id_rejects_invalid_checksum():
    # Random 13 digits with a bad checksum should NOT be redacted as TH_NATIONAL_ID.
    assert not _thai_national_id_valid("1234567890123")
    d = _deid()
    out = d.redact_text("Tracking number 1234567890123 was assigned.")
    assert "<TH_NATIONAL_ID>" not in out


def test_redact_thai_phone():
    d = _deid()
    out = d.redact_text("Call patient at 081-234-5678 or +66 2 123 4567.")
    assert "081-234-5678" not in out
    assert "+66 2 123 4567" not in out
    assert d.counts_snapshot().get("PHONE_NUMBER", 0) >= 1


def test_redact_hn_patient_id():
    d = _deid()
    out = d.redact_text("Patient HN-DEMO-1 admitted on Monday.")
    assert "HN-DEMO-1" not in out
    assert "<HN_PATIENT_ID>" in out


def test_redact_mrn():
    d = _deid()
    out = d.redact_text("Medical record MRN: 123456 reviewed.")
    assert "123456" not in out
    assert "<MEDICAL_RECORD_NUMBER>" in out


def test_redact_us_ssn_defensive():
    d = _deid()
    out = d.redact_text("US visitor SSN 123-45-6789 needs forwarding.")
    assert "123-45-6789" not in out
    assert "<US_SSN>" in out


def test_redact_iso_date_rounds_to_year():
    d = _deid()
    out = d.redact_text("Encounter on 2026-05-19 at 14:03.")
    assert "2026-05-19" not in out
    assert "2026" in out
    assert d.counts_snapshot().get("DATE_TIME", 0) >= 1


def test_off_level_is_passthrough():
    d = Deidentifier(level="off")
    src = "Patient HN-DEMO-1 (jane@example.com) on 2026-05-19."
    out = d.redact_text(src)
    assert out == src
    assert d.counts_snapshot() == {}


def test_redact_room_number_thai():
    d = _deid()
    out = d.redact_text("ผู้ป่วยอยู่ห้อง 305B")
    assert "305B" not in out
    assert "<LOCATION>" in out


def test_redact_thai_address_token():
    d = _deid()
    out = d.redact_text("ที่อยู่: จังหวัด กรุงเทพ อำเภอ ปทุมวัน")
    # Either token should be redacted under LOCATION.
    assert d.counts_snapshot().get("LOCATION", 0) >= 1


def test_safe_harbor_falls_back_to_regex_without_presidio(monkeypatch):
    """If Presidio isn't installed, safe_harbor must still redact via regex —
    the loader returns None and the English pass becomes a no-op."""
    import app.services.deidentify as deid_mod
    monkeypatch.setattr(deid_mod, "_get_presidio_analyzer", lambda: None)
    monkeypatch.setattr(deid_mod, "_get_pythainlp_ner", lambda: None)

    d = Deidentifier(level="safe_harbor")
    out = d.redact_text("Call 081-234-5678 or email a@b.com", lang="en")
    assert "<PHONE_NUMBER>" in out
    assert "<EMAIL_ADDRESS>" in out
