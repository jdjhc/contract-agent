# Ingest Node Evaluation Report

**Run date:** 2026-05-22 &nbsp;|&nbsp; **Concurrency:** 3 &nbsp;|&nbsp; **Wall time:** 705.7s

---

## How Clause Splitting Works

Clause splitting uses **LLM as the primary path**: the LLM identifies verbatim clause openings as anchors, then the original text is sliced at those boundaries. If the LLM is not configured, fails, or returns fewer than 3 clauses, the pipeline falls back to **regex heuristics** (pattern-based heading detection + paragraph splitting).

PDF text extraction uses `pdfplumber` for pages with a text layer. Pages with no usable text (scanned/image-only) are rendered to PNG and transcribed via **GPT-4o vision** (Azure OpenAI).

> **Azure services used:** Azure OpenAI — GPT-4o for clause splitting; GPT-4o vision for scanned-page OCR fallback.

---

## Results

| Metric | Value |
|--------|-------|
| Files | 28 |
| Success | 28 / 28 |
| Clauses per file (min / avg / max) | 14 / 58.5 / 213 |
| Text chars per file (min / avg / max) | 5,943 / 33,346 / 214,952 |

---

## Clause Count by Contract Type

> Contract types were assigned by group members with AI assistance, verifying each file against its content. This classification is consistent with the corrected ground-truth labels used in the Classify evaluation.

| Contract Type | Files | Avg Clauses | Min | Max |
|---------------|-------|-------------|-----|-----|
| Confidential Disclosure Agreement | 6 | 39 | 14 | 78 |
| Collaboration Agreement | 5 | 63 | 38 | 125 |
| Material Transfer Agreement | 3 | 39 | 38 | 39 |
| Provision of Services Agreement | 3 | 104 | 42 | 213 |
| Research Subcontract | 2 | 114 | 42 | 187 |
| Student Research Agreement | 2 | 40 | 39 | 40 |
| Data Transfer Agreement | 2 | 56 | 41 | 71 |
| Public Research Contract | 2 | 41 | 41 | 41 |
| Master Services Agreement | 1 | 79 | 79 | 79 |
| Consultancy Services Agreement | 1 | 41 | 41 | 41 |
| Commercial Research Contract | 1 | 42 | 42 | 42 |

---

## Cost and Latency

| Metric | Avg | Max (file) |
|--------|-----|------------|
| Wall time / file | 60s | 510s (Master Services Agreement Example 1) |

The slowest file (MSA Example 1) involved merging two PDFs — the elevated latency is expected.

---

## Conclusion

All 28 files processed successfully. Clause counts scale predictably with document length and complexity. All 28 clause splits were reviewed by group members with AI assistance — no specialist legal knowledge was available, so verification reflects structural correctness (boundaries, numbering, completeness) rather than domain-level judgement.
