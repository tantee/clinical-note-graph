# From Clinical Narrative to Coded Knowledge Graph: A Schema-Constrained Large Language Model Pipeline for Automated Medical Coding

**Authors:** [INSERT: author list, affiliations, ORCID]
**Corresponding author:** [INSERT: name, email, address]

---

## Abstract

*[To be written last, after Results are finalized. Structured format: Background, Objective, Methods, Results, Conclusions; target 250–300 words.]*

**Keywords:** clinical natural language processing; large language models; medical coding; ICD-10; SNOMED CT; knowledge graph; electronic health records; human-in-the-loop

---

## 1. Introduction

Clinical documentation is the connective tissue of modern healthcare, yet most of it remains locked in unstructured free text. Discharge summaries, progress notes, and consult letters encode the reasoning, findings, and decisions of care in prose that is easy for clinicians to write but difficult for machines to use. Converting this narrative into structured, standardized data—diagnoses mapped to the International Classification of Diseases (ICD), clinical concepts to SNOMED Clinical Terms (SNOMED CT), laboratory results to Logical Observation Identifiers Names and Codes (LOINC), and medications to RxNorm—underpins reimbursement, quality measurement, cohort discovery, and clinical decision support. Today that conversion is performed largely by hand. Professional medical coders translate charts into billing codes through a labor-intensive process that is expensive, slow, and subject to inter-coder variability, while the documentation burden itself is a recognized driver of clinician burnout.

Automating the path from narrative to code has been a long-standing goal of clinical natural language processing (NLP). Early systems relied on rule-based pattern matching and dictionary lookups against controlled terminologies; later work applied supervised machine learning and, more recently, deep neural classifiers trained on labeled corpora. These approaches improved coverage but remained brittle: rules are costly to maintain across specialties and institutions, and supervised models require large annotated datasets and degrade on the long tail of rare codes. None of them produces, on its own, the *contextualized, provenance-linked* output that a human coder can audit and trust.

Large language models (LLMs) change the economics of this problem. A single instruction-following model can extract clinical entities, normalize them to standard terminologies, and explain its reasoning—without task-specific training. But naive application of LLMs to medical coding introduces two failure modes that are unacceptable in a clinical setting. The first is **hallucination**: a model may emit a plausible but fabricated code, or attribute a finding to text that does not support it. The second is **abstention**: faced with ambiguity, a model may decline to commit to any code, pushing the entire burden back onto the human and negating the time savings the system was meant to deliver. A deployable system must suppress the former while refusing to indulge the latter.

This paper presents **Clinical Note Graph**, an end-to-end system that converts EHR documents into a coded, longitudinal clinical knowledge graph using schema-constrained LLM extraction with explicit provenance and human-in-the-loop confirmation. The system ingests documents in plain text, JSON, or HL7 Fast Healthcare Interoperability Resources (FHIR) format; extracts clinical facts under a strict, validated output schema; assigns multi-terminology codes through a dedicated coding stage; deduplicates and merges facts across encounters into a patient-level longitudinal record; and materializes the result as both a relational fact store and a property graph. Every extracted fact retains the verbatim evidence text that justifies it, and no fact is treated as final until a human reviewer confirms it.

The contributions of this work are fourfold:

1. **A schema-constrained, end-to-end pipeline** that links ingestion, extraction, coding, and graph construction, in which structured-output validation acts as a hard gate against malformed or unfaithful LLM output (fail-closed).
2. **A non-abstaining but calibrated coding policy.** The coding stage is explicitly instructed to *always return at least one defensible code* per active problem—choosing the broadest defensible code at low, honestly-reported confidence rather than deferring—and enforces this with a deterministic retry, while surfacing tie-breaks and uncertainty as machine-readable warnings.
3. **A dual relational-plus-graph longitudinal data model with full provenance**, in which patient-level deduplication collapses repeated concepts while encounter-level detail and evidence links are preserved, and LLM-declared clinical relationships (e.g., *treats*, *monitors*, *complication-of*) become typed edges.
4. **A multi-dimensional evaluation** on a public benchmark dataset, assessing coding accuracy against gold-standard codes, fact-extraction quality, human review effort and time savings, and operational cost and latency.

The remainder of this paper is organized as follows. Section 2 describes the system architecture, the ingestion and extraction stages, the medical coding pipeline, the longitudinal data model and knowledge graph, the human-in-the-loop review model, the implementation, and the evaluation design. Section 3 reports results across the four evaluation dimensions. Section 4 discusses principal findings, relation to prior work, limitations, and future directions. Section 5 concludes.

---

## 2. Methods

### 2.1 System overview

Clinical Note Graph is a service-oriented system that transforms individual EHR documents into a continuously updated, patient-level clinical knowledge base. Processing follows a five-stage pipeline (Figure 1):

1. **Pre-extraction persistence.** The incoming document, its patient, and its encounter are written to a relational store within a single transaction, preserving the original content verbatim before any model is invoked.
2. **LLM extraction.** The normalized document text is sent to a large language model, which returns a structured set of candidate clinical facts together with the verbatim evidence supporting each one.
3. **Schema validation and fact persistence.** The model output is validated against a strict output schema; only conforming output is converted into typed fact rows and persisted, while non-conforming output is rejected and logged.
4. **Graph and narrative materialization.** Validated facts are upserted, in parallel, into a property graph and into a longitudinal human-readable note vault.
5. **Embedding.** Selected facts and notes are embedded into a vector index to support semantic retrieval.

Medical coding is invoked as a dedicated stage over the accumulated patient facts (Section 2.4), either inline during ingestion or on demand.

The system deliberately maintains **two complementary stores**. A relational database holds the canonical, append-only record of every document, fact, and model invocation, and serves as the authoritative source of provenance, audit, and cost accounting. A property graph holds the same facts arranged for traversal and visualization—linking problems to the medications that treat them, the observations that monitor them, and the encounters in which they appeared. This dual-store design separates the concern of *trustworthy record-keeping* from that of *relational reasoning and presentation*: the graph can be rebuilt deterministically from the relational facts at any time without re-invoking the LLM, which makes recovery cheap and keeps the costly extraction step idempotent with respect to downstream representations.

A consistent principle runs through every stage: **the LLM produces suggestions, not ground truth.** Each stage records what the model was asked, what it returned, whether the output validated, and what it cost; each extracted fact carries an explicit review status; and no fact is considered confirmed until a human accepts it (Section 2.7).

> **Figure 1.** End-to-end architecture of Clinical Note Graph. Documents enter through a format-agnostic ingestion endpoint, are normalized to text, and pass through schema-constrained extraction, multi-terminology coding, deduplication, and dual materialization into a relational fact store and a property graph, with a vector index for retrieval. *[INSERT: architecture diagram.]*

### 2.2 Data ingestion and normalization

Documents enter the system through a single ingestion endpoint that accepts three input formats, reflecting the heterogeneity of real EHR environments: free-text clinical narrative, structured JSON, and HL7 FHIR bundles. Each request carries the document content together with patient identifiers and demographics, encounter metadata (type, datetime, department, provider), and a source descriptor identifying the originating system, document identifier, and version.

Regardless of format, the system reduces every document to a single plain-text representation before extraction. JSON payloads are serialized to indented text, and FHIR bundles are traversed and rendered to a textual summary that flattens Patient, Encounter, Condition, MedicationStatement/MedicationRequest, Observation, Procedure, AllergyIntolerance, and DiagnosticReport resources into readable clinical prose. This **normalize-to-text** design keeps the downstream extraction stage format-independent: the same prompt and schema apply whether the source was a typed FHIR resource or a dictated note, and adding a new input format requires only a new adapter, not changes to the model interface.

Ingestion is **idempotent**. The combination of patient identifier, source document identifier, and version forms a uniqueness key, so re-submitting the same document version does not create duplicate records—an essential property when documents are streamed or replayed from upstream systems. Processing is **asynchronous by default**: a submission is enqueued and a job identifier is returned immediately, with per-stage progress tracked so that partial results (for example, a successfully built graph but a pending embedding step) are observable. A synchronous mode is available for interactive use and testing.

Because clinical documents contain protected health information (PHI), de-identification is applied at the outbound boundary—before any text leaves the trust boundary for an external model—under a configurable policy ranging from disabled, through regular-expression redaction, to a Safe Harbor profile. The number and types of redactions performed on each call are recorded alongside the model invocation, so the PHI exposure of every external request is auditable after the fact. The privacy implications of this boundary are discussed further in Section 4.

### 2.3 Schema-constrained fact extraction

The extraction stage converts normalized document text into a structured set of candidate clinical facts. A large language model receives a system instruction describing the extraction task and a user message containing the patient identifier, encounter type and datetime, document identifier, and the document text delimited by explicit markers (see Appendix A.1 for the verbatim prompts). The model is asked to return a single JSON object enumerating the document's problems, medications, observations, procedures, allergies, and care plans, together with a top-level list of inter-fact relationships.

Three design choices make this stage reliable enough to build on.

**Strict output schema as a fail-closed gate.** The model's output is validated against a strict schema (`ClinicalExtractionResult`) that forbids unspecified fields and constrains enumerated values—condition severity to *mild | moderate | severe | critical | unknown*, condition status to *active | resolved | in_remission | recurrent | suspected | ruled_out | inactive*, and so on. Validation is a hard gate: if the model returns malformed JSON, an unexpected field, or an out-of-range value, the output is rejected in its entirety, the failure is recorded as an audit event, and no downstream write occurs. The system therefore never persists partially-understood model output. This fail-closed posture trades recall (a rejected document yields no facts until re-processed) for a guarantee that every persisted fact is structurally well-formed and machine-interpretable.

**Evidence grounding.** Every fact must carry an `evidenceText` field containing the verbatim span of source text that justifies it. This requirement serves two purposes. Operationally, it gives a human reviewer the exact passage to check, turning verification from a re-reading of the whole note into a glance at a quoted phrase. Methodologically, it provides a direct, per-fact handle on faithfulness: a fact whose evidence text does not appear in, or support, the source document is a detectable hallucination (Section 2.9).

**Conservative defaults against fabrication.** The prompt instructs the model not to invent codes—leaving a code null and lowering confidence when uncertain—and not to infer severity or status from clinical priors or from the treatment plan alone. The intent is to keep the extraction faithful to what the document states rather than to what is clinically plausible, deferring inference to the explicitly-reasoned coding stage that follows.

In addition to per-fact attributes, the model emits **inter-fact relationships** linking facts it has already extracted: a medication *treats* a condition, an observation *monitors* or is *diagnostic-of* a condition, one condition is a *complication-of* or is *caused-by* another, sibling labs are *panel-members* of a panel, and so on. Each relationship references the source and target facts by type and value so that the graph layer (Section 2.6) can pair them unambiguously, and each carries its own evidence text and confidence. The prompt constrains relationship endpoints to facts the model actually returned, preventing edges to entities that were never extracted.

### 2.4 The medical coding pipeline

Medical coding is the stage where extracted clinical facts become billing- and analytics-ready standardized codes, and it is where the failure modes identified in Section 1—hallucination and abstention—must be managed most carefully. Coding runs as a dedicated stage over the patient's accumulated, deduplicated facts (Section 2.5) rather than over a single document, so that the assigned codes reflect the patient's consolidated problem list rather than one encounter in isolation.

**Multi-terminology output.** The coding stage produces a structured suggestion comprising a single primary diagnosis, lists of secondary diagnoses, complications (diagnoses caused by the primary problem), and comorbidities (independent chronic conditions), together with a flat list of coding candidates. Each candidate names its terminology system, the code, a human-readable display, the condition it codes for, and a confidence value. Codes are drawn from a defined priority order—ICD-10 first (billing-critical), then SNOMED CT (clinical specificity), then LOINC where a laboratory observation drives the code—mirroring how the outputs are consumed downstream. Each diagnosis carries a free-text rationale, and the response includes an evidence list mapping codes back to the facts that support them and a warnings list for caveats requiring human attention.

**A non-abstaining but calibrated policy.** The defining feature of this stage is an explicit policy that the model must *always* return codes and must *never* defer entirely (Appendix A.2). Clinically ambiguous patients—multifactorial acute kidney injury, conflicting evidence, an unclear primary—are precisely the cases where a naive model abstains, and precisely the cases where abstention is most costly, because it returns the hardest charts to the human with no starting point. The policy forbids three specific evasions: returning an empty candidate list, returning a null primary diagnosis when the patient has any problems, and using the warnings field as a substitute for codes rather than as a caveat alongside them. Crucially, the policy does not ask the model to be *overconfident*; it asks the model to *commit and calibrate*. When uncertain, the model is instructed to pick the broadest defensible code at a correspondingly low confidence (for example, coding unspecified acute kidney failure at confidence 0.4 while flagging the multifactorial uncertainty in a warning) and to record any tie-break it made. Uncertainty is thus relocated from a binary decision to abstain into a continuous, machine-readable confidence signal plus an actionable warning—information a human coder can triage, rather than a blank the coder must fill from scratch.

**Deterministic enforcement via retry.** Because a policy stated in a prompt is not self-enforcing, the system verifies the model's output and retries when the policy is violated. If the patient has any problems on record yet the response contains neither a primary diagnosis nor any coding candidate, the stage re-invokes the model exactly once, appending an addendum that names the violation and restates the requirement to commit to a primary and emit at least one candidate per active problem at low confidence (Appendix A.2). The retry is conditioned on the patient actually having problems, so a genuinely empty input still legitimately yields empty output. This single deterministic retry converts a soft instruction into an enforced contract while bounding the additional cost.

**Robust parsing.** Models occasionally collapse list-valued fields into prose or emit lists of strings where objects are expected. The stage coerces these shapes—wrapping a stray warning string into a list, normalizing evidence entries to objects—so that one formatting deviation does not discard an otherwise valid coding suggestion. Candidates and diagnoses that fail validation individually are dropped rather than failing the whole response, prioritizing the delivery of usable partial output to the reviewer.

### 2.5 Deduplication and longitudinal merge

A patient accrues many documents over time, and the same problem, medication, or laboratory test recurs across them. To build a coherent patient-level record rather than a pile of per-document facts, the system deduplicates facts when assembling the consolidated view that feeds coding, summarization, and the graph.

Deduplication uses a normalized key: a fact's normalized code when present (lower-cased and trimmed), and otherwise its lower-cased value. Facts sharing a key collapse into a single patient-level concept. This means two encounters that both record "Type 2 diabetes," or that record the same condition under the same ICD-10 code, contribute one node to the consolidated problem list while their individual encounter-scoped occurrences—each with its own date, evidence, and review status—are preserved underneath. The design separates the *identity* of a clinical concept from its *longitudinal instances*: the consolidated list answers "what conditions does this patient have," while the underlying instances answer "when, and on what evidence, was each recorded."

The merge is **append-only**. New documents add facts and may update status (a condition marked resolved, a medication changed) but never delete prior facts. When a new document contradicts the existing record—a medication previously continued is now stopped, an allergy is disputed, a condition recorded as both resolved and active—the contradiction is surfaced as a warning rather than silently overwritten, preserving the full history and flagging the conflict for human adjudication.

### 2.6 Knowledge graph construction

The consolidated facts are materialized into a property graph that makes clinical relationships traversable and visualizable. The graph uses typed nodes for patients, encounters, documents, conditions, medications, observations, procedures, allergies, care plans, and coding candidates, each carrying the attributes extracted for it (for a condition: severity, status, onset and resolution dates, normalized code and coding system, confidence, review status, and evidence text).

Edges come from three sources. **Structural edges** record the document hierarchy and provenance: a patient *has* encounters, an encounter *has* a document and mentions the conditions, prescribes the medications, records the observations, and so on. **Clinical relationship edges** are the inter-fact links declared by the model during extraction (Section 2.3)—*treats*, *addresses*, *monitors*, *diagnostic-of*, *causes*, *due-to*, *complication-of*, *related-to*, *panel-member-of*, *precedes*, *follows*. **Provenance edges** connect each document to the facts extracted from it, annotated with the supporting evidence and confidence, so that any node in the graph can be traced back to the source text and model invocation that produced it. In addition, the graph layer derives co-occurrence edges between conditions from cross-encounter co-occurrence statistics, complementing the explicitly-stated comorbidity links.

Graph upserts are issued in batched operations keyed on stable identifiers, so re-processing a document or rebuilding the graph is idempotent: nodes and edges are merged, not duplicated. Because the graph is derived entirely from the relational facts, it can be rebuilt from scratch at any time without re-invoking the LLM—used both for recovery and for schema migrations. For interactive visualization, a read-time guard rejects graph requests whose pre-deduplication node count exceeds a fixed bound (500 nodes), prompting the user to narrow scope (to an encounter or a set of encounters) rather than attempting to render an unusably dense canvas; this is a presentation safeguard and does not limit the size of the stored graph.

### 2.7 Human-in-the-loop review

The system treats LLM output as a suggestion that a clinician or coder confirms, not as an authoritative record. Every fact carries a review status that begins as *ai-suggested* and transitions, under human action, to *human-confirmed* or *rejected*. Downstream consumers filter on this status—by default hiding rejected facts—so that confirmation progressively hardens the record without ever discarding the model's original suggestion or the human's decision.

This review model is reinforced by the system's pervasive provenance. Because every fact retains its evidence text, its confidence, and a link to the document and model invocation that produced it, a reviewer can adjudicate each suggestion against its source in one step, and every confirmation or rejection is itself recorded in an append-only audit log. The combination—calibrated confidence from the model, verbatim evidence for verification, and an immutable audit trail—is what makes the pipeline's output defensible for clinical and billing use, and it is the basis for the human-effort and time-savings evaluation in Section 2.9.

### 2.8 Implementation

The backend is a Python service built on FastAPI with Pydantic for schema definition and validation; the strict extraction and coding schemas described above are Pydantic models with unspecified fields forbidden. Canonical state—patients, encounters, documents, facts, model invocations, jobs, and the audit log—is stored in PostgreSQL, with the pgvector extension holding embeddings for semantic retrieval. The property graph is stored in Neo4j and accessed via parameterized Cypher. Asynchronous ingestion is handled by an in-process job queue with priority and lock-based claiming.

LLM access is mediated by a provider abstraction targeting any OpenAI-compatible chat-completions and embeddings endpoint, which allows the same pipeline to run against hosted APIs or self-hosted open-weight models, and against a deterministic mock provider for testing. Per-task model selection lets extraction, coding, and summarization use different models. Every model invocation is logged with its prompt template, model identifier, token counts, latency, computed cost, validity, and any redaction counts, providing the raw data for the cost and latency evaluation. De-identification at the outbound boundary uses a configurable policy with regular-expression and Safe Harbor profiles. [CITE: framework/library versions as needed.]

### 2.9 Evaluation design

We evaluate the system on a publicly available benchmark corpus of clinical notes with reference codes [INSERT: dataset name, version, citation, and license]. Using a public benchmark makes the evaluation reproducible and avoids the need for institutional data access; the cohort, note types, and code distribution of the benchmark are summarized in [INSERT: Table 1]. Because the corpus is already de-identified and publicly released, no additional ethics approval was required; the de-identification stage was [INSERT: enabled/disabled] for these experiments. [INSERT: model(s) and versions evaluated; decoding settings; date of access.]

We assess the system along four dimensions.

**2.9.1 Coding accuracy.** We compare system-assigned codes against the benchmark's gold-standard codes. We report precision, recall, and F1 at the code level, micro- and macro-averaged, with a breakdown by terminology (ICD-10, SNOMED CT, LOINC) and, where the benchmark supports it, top-*k* accuracy for the primary diagnosis. To probe the calibration of the non-abstaining policy, we additionally report accuracy stratified by the model's reported confidence, and the abstention rate (the fraction of problems for which no code was produced), which the policy is designed to drive to zero. [INSERT: matching/equivalence criterion—exact code, category-level, or terminology-mapped.]

**2.9.2 Extraction quality.** We evaluate the fidelity of fact extraction against [INSERT: reference annotations or a manually abstracted subset]. We report entity-level precision and recall for each fact type (conditions, medications, observations, procedures, allergies), a hallucination rate (facts whose evidence text is not supported by the source document), and an evidence-grounding rate (facts whose `evidenceText` is an exact or near-exact span of the source).

**2.9.3 Human review effort and time savings.** We measure the effort required to bring the system's output to a confirmed state. We report per-chart confirmation, edit, and rejection rates over the review workflow, and—in a [INSERT: reader study / timing substudy with N reviewers]—the time to code a chart with the system's suggestions versus unaided coding, with [INSERT: statistical test]. [INSERT: inter-rater agreement and study protocol.]

**2.9.4 Cost and latency.** Using the per-invocation logs, we report mean and tail input/output token counts and monetary cost per document and per patient, end-to-end ingestion latency, and a comparison across [INSERT: the candidate models/providers evaluated], including the marginal cost of the coding retry.

---

## 3. Results

*[All quantitative values below are placeholders to be populated from the evaluation runs.]*

### 3.1 Dataset and cohort

[INSERT: Table 1 — benchmark composition: number of notes/patients, note types, code-system coverage, code-frequency distribution.]

### 3.2 Coding accuracy

[INSERT: Table 2 — precision/recall/F1 by terminology (ICD-10, SNOMED CT, LOINC), micro/macro; primary-diagnosis top-*k* accuracy.]
[INSERT: Figure 2 — accuracy vs. reported confidence (calibration curve); abstention rate.]

Principal observations: [INSERT: headline F1; effect of the non-abstaining policy on abstention rate; where the model is well- vs. poorly-calibrated.]

### 3.3 Extraction quality

[INSERT: Table 3 — entity-level precision/recall by fact type; hallucination rate; evidence-grounding rate.]

### 3.4 Human review effort and time savings

[INSERT: Table 4 — confirmation/edit/rejection rates; time-per-chart with vs. without the system; statistical comparison.]

### 3.5 Cost and latency

[INSERT: Table 5 — tokens, cost per document/patient, end-to-end latency, per-model comparison, marginal cost of the coding retry.]

---

## 4. Discussion

### 4.1 Principal findings

[INSERT: one-paragraph synthesis of the four result arms.] The central design claim of this work is that the two failure modes blocking LLM use in medical coding—hallucination and abstention—can be addressed not by a better model alone but by the *scaffolding around* the model: a strict, fail-closed output schema that prevents malformed or unfaithful output from being persisted; mandatory per-fact evidence that makes faithfulness checkable; and an explicit, deterministically-enforced policy that converts abstention into calibrated, committed suggestions. The evaluation is structured to test exactly this claim, isolating the schema gate's effect on output validity, the evidence requirement's effect on detectable hallucination, and the non-abstaining policy's effect on the abstention rate and on downstream human effort.

### 4.2 Relation to prior work

[CITE: rule-based and terminology-lookup coding systems; supervised and deep-learning automated coding; LLM-based clinical information extraction and coding; clinical knowledge-graph construction.] Compared with rule-based and supervised approaches, the present system requires no task-specific training and adapts to new terminologies and note formats through prompt and adapter changes rather than re-annotation. Compared with prior LLM coding work, its contributions are the fail-closed schema gate, the evidence-grounded provenance model, the explicitly non-abstaining-but-calibrated coding policy with deterministic enforcement, and the integration of coding into a longitudinal knowledge graph rather than a per-document classification task.

### 4.3 Limitations

Several limitations bound the interpretation of these results. The evaluation uses a public benchmark, which may not reflect the documentation styles, code distributions, or data quality of a specific institution's live EHR; generalization to deployment is unverified. LLMs are subject to version drift, and reported accuracy is tied to the specific model versions and decoding settings used. The non-abstaining policy reduces abstention by construction, but a committed low-confidence code still requires human review and could introduce automation bias if reviewers over-trust suggestions; the human-factors evaluation only partially addresses this. The fail-closed schema gate improves output validity at the cost of recall on documents the model fails to format correctly. Finally, [INSERT: any benchmark-specific limitations—code-set coverage, single-language corpus, absence of certain note types]. De-identification and the external-model trust boundary mitigate but do not eliminate privacy risk in real deployments.

### 4.4 Future work

[INSERT/EXPAND as desired.] Promising directions include closing the loop from human confirmations back into the system through active learning or retrieval-augmented few-shot exemplars; integrating an authoritative terminology server to validate and expand codes; extending evaluation to multi-institutional and prospective settings; and studying reviewer behavior to detect and counter automation bias.

---

## 5. Conclusion

We presented Clinical Note Graph, an end-to-end system that converts heterogeneous EHR documents into a coded, longitudinal clinical knowledge graph using schema-constrained LLM extraction with explicit provenance and human-in-the-loop confirmation. The system's medical coding stage addresses the abstention failure mode directly, through a policy that requires committed but honestly-calibrated codes and enforces it deterministically, while a fail-closed output schema and mandatory per-fact evidence guard against malformed and unfaithful output. [INSERT: one-sentence statement of the headline empirical result.] By treating model output as an auditable suggestion rather than ground truth, the approach offers a path to reducing manual coding burden without sacrificing the traceability that clinical and billing use demands.

---

## Declarations

**Data and code availability.** [INSERT: repository/DOI for code; benchmark access details.]
**Ethics.** The evaluation uses a publicly released, de-identified benchmark corpus; [INSERT: IRB/exemption statement as applicable].
**Funding.** [INSERT.]
**Competing interests.** [INSERT.]
**Author contributions.** [INSERT.]

---

## References

*[INSERT: full reference list. `[CITE: …]` markers in the text indicate where citations are required: clinician documentation burden/burnout; rule-based and terminology-lookup coding; supervised/deep-learning automated coding (e.g., ICD coding from discharge summaries); LLM clinical information extraction and coding; clinical knowledge-graph construction; the benchmark dataset; ICD-10, SNOMED CT, LOINC, RxNorm, and FHIR specifications.]*

---

## Appendix A. Prompt templates (verbatim)

The following are the exact system and user prompts used in the pipeline, reproduced for transparency and reproducibility.

### A.1 Extraction

**System prompt:**

```
You are a clinical information extraction assistant. Extract structured clinical facts from the provided EMR document.

You MUST return JSON conforming exactly to the ClinicalExtractionResult schema.
Do not invent codes. If you are uncertain, leave codes null and lower confidence.
Always preserve the original evidence text for each fact in `evidenceText`.

# Temporality + severity on conditions

For every condition / problem, populate when the source EMR supports it:
- `severity`: one of `mild | moderate | severe | critical | unknown`. Leave
  null when the document doesn't state it; do not infer severity from the
  treatment plan alone.
- `status`: one of `active | resolved | in_remission | recurrent |
  suspected | ruled_out | inactive`. Default to `active` for newly-stated
  problems; mark `resolved` only when the document explicitly says so;
  `suspected` for differential diagnoses; `ruled_out` for excluded ones.
- `onsetDate` / `resolvedDate`: only when the document gives a specific
  date. Don't approximate; leave null when uncertain.

# Inter-fact relationships

Populate the top-level `relationships` array with explicit links between
facts you already emitted. Each entry has:
  { sourceType, sourceValue, targetType, targetValue, relation,
    evidenceText, confidence }

`sourceType` / `targetType` are one of `condition | medication |
observation | procedure | allergy | plan`. `sourceValue` / `targetValue`
must match the `value` (or `name` for medications / observations) you
already used for that fact — keep it exact so the graph layer can pair
them.

Allowed `relation` values and when to use each:

- `treats` — Medication → Condition. The EMR text indicates this med is
  for that diagnosis. Example: "metformin for diabetes" → Medication
  Metformin -[treats]-> Condition Type 2 diabetes.
- `addresses` — Plan → Condition. The plan item targets that condition.
- `monitors` — Observation → Condition. The lab/vital is being followed
  because of that condition. Example: HbA1c monitors diabetes; BP
  monitors hypertension.
- `diagnostic_of` — Observation → Condition. The observation result is
  what established the diagnosis (e.g. abnormal TSH diagnostic of
  hypothyroidism).
- `causes` / `due_to` — Condition → Condition. Use `causes` when the
  source condition produced the target (forward direction); use `due_to`
  when the source IS the consequence (reverse). Don't emit both for the
  same pair.
- `complication_of` — Condition → Condition. The source is a complication
  of the target. E.g. "Diabetic nephropathy complication_of Type 2
  diabetes".
- `co_occurs` — Condition ↔ Condition. Comorbid but no clear causal
  direction (e.g. diabetes + hypertension when not specified). Use only
  when the EMR explicitly links them; the graph layer also derives
  CO_OCCURS edges from cross-encounter co-occurrence statistics, so don't
  flood this for single-visit observations.
- `related_to` — generic fallback when none of the above fit but the EMR
  shows the link.
- `panel_member_of` — Observation → Observation. Sibling lab in the same
  panel (lipid panel: LDL, HDL, triglycerides → all panel_member_of
  "Lipid panel" or pairwise).
- `precedes` / `follows` — chronological sequence (rare; use only when
  ordering matters and the EMR makes it explicit).

# What NOT to do

- Don't emit `treats` / `monitors` etc. for facts that aren't actually in
  your `problems` / `medications` / `observations` lists — every endpoint
  of a relationship must be a fact you also returned.
- Don't restate trivial parent/child links — the patient → condition
  attachment is already implicit; only add inter-fact edges.
- Don't fabricate severity or status from clinical priors. If the
  document doesn't say, leave it null.
```

**User prompt template:**

```
Patient ID: {patient_id}
Encounter type: {encounter_type}
Encounter dateTime: {encounter_dt}
Document ID: {document_id}

--- BEGIN EMR DOCUMENT ---
{content}
--- END EMR DOCUMENT ---

Return ONLY the JSON object.
```

### A.2 Medical coding

**System prompt:**

```
You are a clinical coding assistant. Given the patient's structured
problem list and observations, suggest ICD-10 and SNOMED CT codes.

# YOU MUST ALWAYS RETURN CODES — NEVER DEFER ENTIRELY

This is the most important rule. Some patients are clinically ambiguous
(multifactorial AKI, conflicting evidence, unclear primary). When you
feel uncertain, you must STILL produce candidate codes — just lower the
`confidence` field and explain the uncertainty in `warnings`.

What you must NEVER do:
- Return empty `codingCandidates`. The clinician needs SOMETHING to
  audit; a blank list with prose warnings forces them to redo the
  work from scratch. Always emit your best-guess codes, even at low
  confidence (0.3-0.5).
- Return `primaryDiagnosis: null`. Always pick a primary from the
  patient's `problems` list — the one most likely to be the chief
  reason for encounter based on the source EMR. If you can't decide
  between two, pick one and add a warning explaining the tie-break.
- Use the `warnings` field as an excuse to skip codes. Warnings are
  *caveats alongside codes*, not a substitute for codes.

# How to choose codes when uncertain

- **Pick the broadest defensible code at low confidence** rather than
  no code. For example: AKI without a clear cause → `N17.9 Acute
  kidney failure, unspecified` at confidence 0.4, with a warning
  noting "multifactorial — sepsis vs rhabdomyolysis vs uremic".
- For each problem in the patient's list, emit AT LEAST one
  `codingCandidate` (ICD-10 preferred; add SNOMED CT when known).
  Confidence reflects how sure you are the code matches that problem.
- Coding system order: ICD-10 (billing-critical), then SNOMED CT
  (clinical), then LOINC (labs) when an observation drives the code.
- Pseudonyms in the input (PATIENT-A1, HN-A1, PROVIDER-A1) are normal
  — they're the de-identifier's output. Treat them as opaque tokens
  and code based on the clinical content around them.

# Schema reminder

Return a JSON object with this shape. ALL list fields are required;
emit `[]` only when there's genuinely nothing of that type, not as a
way to avoid the work.

  primaryDiagnosis: {condition, icd10, snomed, rationale, confidence}
    - MUST be non-null when the patient has any problems.
  secondaryDiagnoses: [{...}]
  complications: [{...}]      - other diagnoses caused by the primary
  comorbidities: [{...}]      - independent chronic conditions
  codingCandidates: [{system, code, display, forCondition, confidence}]
    - MUST contain at least one candidate per active problem.
  evidence: [{condition, evidence}]  - which fact supports each code
  warnings: [string, …]       - caveats / human-review flags
  disclaimer: string          - the standard "AI-assisted, review required" line

# Tone

Be concise. The reviewer's time is expensive — every warning should be
actionable (e.g. "primary is hard to assign because both diagnoses
were documented at admission; consider X first if discharge focused
on it"). Don't repeat the same caveat across multiple entries.
```

**Retry addendum** (appended to the system prompt on the single deterministic retry when the model returns no primary diagnosis and no coding candidates for a patient who has problems):

```
Your previous response had no codingCandidates AND no primaryDiagnosis.
That's not acceptable per the schema rules above — empty output forces
the human coder to redo the work from scratch. Please retry. Pick the
single most likely primary diagnosis from the patient's problems (any
tie-break is fine, just commit and note the reason in `warnings`), and
emit at least one ICD-10 candidate per active problem at lower confidence
(0.3-0.5 is fine when uncertain). Any caveats belong in `warnings`, not
as a reason to omit codes.
```
