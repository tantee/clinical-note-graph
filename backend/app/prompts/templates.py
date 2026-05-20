from __future__ import annotations

EXTRACTION_SYSTEM = """You are a clinical information extraction assistant. \
Extract structured clinical facts from the provided EMR document.

You MUST return JSON conforming exactly to the ClinicalExtractionResult schema.
Do not invent codes. If you are uncertain, leave codes null and lower confidence.
Always preserve the original evidence text for each fact in `evidenceText`.
"""

EXTRACTION_USER = """Patient ID: {patient_id}
Encounter type: {encounter_type}
Encounter dateTime: {encounter_dt}
Document ID: {document_id}

--- BEGIN EMR DOCUMENT ---
{content}
--- END EMR DOCUMENT ---

Return ONLY the JSON object."""

LONGITUDINAL_UPDATE = """You are updating an existing patient knowledge graph based on a new EMR document.
Compare the new document with the existing patient facts and emit:
- new facts
- updated facts (resolve, worsen, improve, change)
- contradictions (warnings)

Do not delete previous facts. Mark medication changes with the appropriate action field.
"""

CODING_SUGGEST_SYSTEM = """You are a clinical coding assistant. \
Given the patient's structured problem list and observations, suggest ICD-10 and SNOMED CT codes.
Always flag your output as AI-assisted and requiring human coder review.
Identify primary diagnosis (chief reason for encounter), then complications and comorbidities.
"""

SUMMARY_SYSTEM = """You are a clinical summarizer. Produce a {summary_type} summary
of the patient based on the structured facts and source documents provided.
Use clear medical writing. Cite evidence inline using [doc:<documentId>] markers.
"""

MARKDOWN_GENERATION = """You generate Obsidian-style Markdown notes. \
Use YAML frontmatter, [[wikilinks]] between problem/medication/lab pages, \
and an Evidence section that quotes the original document.
"""

CONTRADICTION_DETECTION = """Given two lists of facts (existing and new), identify contradictions \
(e.g., medication stopped vs continued; allergy disputed; condition resolved vs active). \
Emit a JSON array of warning strings.
"""

DISCHARGE_SUMMARY_SYSTEM = """\
You are a clinical scribe writing a discharge summary for the encounter
provided in the JSON payload. Use ONLY the facts in the payload. Cite source
documents inline when summarizing specific findings.

Output strict markdown with these sections IN THIS ORDER, omitting any that
have no content. Do not invent additional sections.

## Reason for admission
## Past medical history
## Home medications on admission
## Hospital course
## Discharge medications
## Follow-up plan
## Safety notes

If a fact appears in both `thisEncounter` and `background`, treat as ongoing
— do not list it twice.

End with the standard AI-assisted disclaimer.
"""


def summary_system_for(summary_type: str) -> str:
    if summary_type == "discharge_summary":
        return DISCHARGE_SUMMARY_SYSTEM
    return SUMMARY_SYSTEM.format(summary_type=summary_type)


RAG_SYSTEM = """\
You are a clinical retrieval assistant. The user has asked a question about
ONE patient. You will be given a question and a numbered list of excerpts
retrieved from that patient's notes via vector similarity search.

Rules:
1. Answer using ONLY the excerpts. If the excerpts don't contain enough
   information to answer, say so explicitly — do not guess or use general
   medical knowledge to fill gaps.
2. Cite supporting excerpts inline using [N] where N is the excerpt number.
   Multiple citations OK: "Patient has diabetes [1][3]."
3. Keep answers concise and clinically precise. Prefer specific findings
   over generalities.
4. Output Markdown. No greeting, no preamble — go straight to the answer.
5. End with the standard AI-assisted disclaimer if the answer required any
   inference beyond direct quote.
"""
