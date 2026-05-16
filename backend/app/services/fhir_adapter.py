from __future__ import annotations

from typing import Any


def fhir_bundle_to_text(bundle: dict[str, Any]) -> str:
    """Render a FHIR bundle into plain text suitable for AI extraction.

    Supports the common resources we expect in EMR exports:
    Patient, Encounter, Condition, MedicationStatement/Request, Observation,
    Procedure, AllergyIntolerance, DocumentReference, DiagnosticReport.
    """
    if not isinstance(bundle, dict):
        return str(bundle)

    lines: list[str] = []
    entries = bundle.get("entry") or []
    for entry in entries:
        res = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(res, dict):
            continue
        rtype = res.get("resourceType")
        if rtype == "Patient":
            name = res.get("name", [{}])[0]
            given = " ".join(name.get("given", []) or [])
            family = name.get("family", "")
            lines.append(f"Patient: {given} {family}, gender={res.get('gender')}, birthDate={res.get('birthDate')}")
        elif rtype == "Encounter":
            t = (res.get("class") or {}).get("display") or res.get("status")
            period = res.get("period", {})
            lines.append(f"Encounter ({t}): {period.get('start')}–{period.get('end','')}")
        elif rtype == "Condition":
            code = (res.get("code") or {})
            text = code.get("text") or ((code.get("coding") or [{}])[0].get("display"))
            clinical = (res.get("clinicalStatus") or {}).get("text") or ""
            lines.append(f"Condition: {text} {('('+clinical+')') if clinical else ''}".strip())
        elif rtype in ("MedicationStatement", "MedicationRequest"):
            med = res.get("medicationCodeableConcept", {}) or {}
            text = med.get("text") or ((med.get("coding") or [{}])[0].get("display")) or res.get("medicationReference", {}).get("display")
            status = res.get("status", "")
            dosage = ""
            if res.get("dosage"):
                dosage = res["dosage"][0].get("text", "") if isinstance(res["dosage"], list) else ""
            lines.append(f"Medication: {text} {status} {dosage}".strip())
        elif rtype == "Observation":
            code = (res.get("code") or {})
            name = code.get("text") or ((code.get("coding") or [{}])[0].get("display"))
            value = res.get("valueQuantity", {})
            v = f"{value.get('value','')} {value.get('unit','')}".strip() or res.get("valueString", "")
            lines.append(f"Observation: {name} = {v}")
        elif rtype == "Procedure":
            code = (res.get("code") or {})
            text = code.get("text") or ((code.get("coding") or [{}])[0].get("display"))
            lines.append(f"Procedure: {text}")
        elif rtype == "AllergyIntolerance":
            code = (res.get("code") or {})
            text = code.get("text") or ((code.get("coding") or [{}])[0].get("display"))
            lines.append(f"Allergy: {text}")
        elif rtype == "DocumentReference":
            content = (res.get("content") or [{}])[0].get("attachment", {})
            data = content.get("data") or ""
            if data:
                # Plain-text attachment
                lines.append(data)
        elif rtype == "DiagnosticReport":
            text = res.get("conclusion") or ""
            if text:
                lines.append(f"DiagnosticReport: {text}")
    return "\n".join(lines)


def fhir_extract_patient(bundle: dict[str, Any]) -> dict[str, Any]:
    for entry in (bundle.get("entry") or []):
        res = entry.get("resource", {}) if isinstance(entry, dict) else {}
        if res.get("resourceType") == "Patient":
            name = (res.get("name") or [{}])[0]
            return {
                "patientId": res.get("id"),
                "name": (" ".join(name.get("given", []) or []) + " " + name.get("family", "")).strip() or None,
                "gender": res.get("gender"),
                "birthDate": res.get("birthDate"),
            }
    return {}
