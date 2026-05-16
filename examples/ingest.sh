#!/usr/bin/env bash
# Examples for using the Clinical Note Graph API.
# Requires: jq, curl. The backend must be running (default :8000).

set -euo pipefail
BASE="${BASE:-http://localhost:8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Ingest admission note (text)"
curl -sS -X POST "$BASE/api/emr/ingest" \
  -H 'Content-Type: application/json' \
  -d @- <<JSON | jq .
{
  "patient": { "patientId": "HN123456", "name": "Somchai Sample", "gender": "male", "birthDate": "1965-04-12" },
  "encounter": { "type": "admission", "dateTime": "2026-05-15T10:00:00+07:00", "department": "Internal Medicine", "provider": "Dr. Demo" },
  "format": "text",
  "content": $(jq -Rsa . < "$ROOT/sample-data/emr-1-admission.txt"),
  "source": { "system": "SampleHIS", "documentId": "doc-001", "version": "1" }
}
JSON

echo ""
echo "→ Ingest progress note (text)"
curl -sS -X POST "$BASE/api/emr/ingest" \
  -H 'Content-Type: application/json' \
  -d @- <<JSON | jq .
{
  "patient": { "patientId": "HN123456" },
  "encounter": { "type": "progress_note", "dateTime": "2026-05-16T08:30:00+07:00", "department": "Internal Medicine", "provider": "Dr. Demo" },
  "format": "text",
  "content": $(jq -Rsa . < "$ROOT/sample-data/emr-2-progress.txt"),
  "source": { "system": "SampleHIS", "documentId": "doc-002", "version": "1" }
}
JSON

echo ""
echo "→ Ingest FHIR bundle"
curl -sS -X POST "$BASE/api/emr/ingest" \
  -H 'Content-Type: application/json' \
  -d @- <<JSON | jq .
{
  "patient": { "patientId": "HN789" },
  "encounter": { "type": "admission", "dateTime": "2026-05-10T08:00:00+07:00" },
  "format": "fhir",
  "content": $(cat "$ROOT/sample-data/emr-4-fhir.json"),
  "source": { "system": "EHR-X", "documentId": "fhir-001", "version": "1" }
}
JSON

echo ""
echo "→ Summary"
curl -sS -X POST "$BASE/api/patient/HN123456/summary" \
  -H 'Content-Type: application/json' \
  -d '{"type":"detailed","includeEvidence":true}' | jq .

echo ""
echo "→ Coding suggestion"
curl -sS -X POST "$BASE/api/patient/HN123456/coding/suggest" \
  -H 'Content-Type: application/json' \
  -d '{"standards":["ICD10","SNOMEDCT"]}' | jq .

echo ""
echo "→ Export FHIR bundle"
curl -sS -X POST "$BASE/api/export" \
  -H 'Content-Type: application/json' \
  -d '{"patientId":"HN123456","exportType":"fhir_bundle"}' | jq .
