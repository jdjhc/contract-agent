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
# Classify Node Evaluation Report

**Run date:** 2026-05-22 &nbsp;|&nbsp; **Concurrency:** 4 &nbsp;|&nbsp; **Wall time:** 31.3s

> **Azure services used:** Azure OpenAI — GPT-4o for contract type classification.

---

## Results

| Metric | Value |
|--------|-------|
| Files | 28 |
| Success | 28 / 28 |
| Classification accuracy | **26 / 28 (93%)** |
| Average confidence | 0.948 |

> **Note on accuracy:** This figure was not produced automatically. Group members manually reviewed all 28 predictions with AI assistance, verifying each result against the actual contract content. Two label errors in the original ground truth were also identified and corrected during this process.

---

## Contract Types Covered (13 defined, 11 touched in test set)

DAA and CTRA appear in neither labels nor predictions.

| Contract Type | Samples | In predictions |
|---------------|---------|----------------|
| Confidential Disclosure Agreement | 6 | ✓ |
| Collaboration Agreement | 5 | ✓ |
| Material Transfer Agreement | 3 | ✓ |
| Research Subcontract | 2 | ✓ |
| Student Research Agreement | 2 | ✓ |
| Provision of Services Agreement | 3 | ✓ |
| Consultancy Services Agreement | 1 | ✓ |
| Data Transfer Agreement | 2 | ✓ |
| Master Services Agreement | 1 | ✓ |
| Public Research Contract | 2 | ✓ |
| Commercial Research Contract | 1 | ✓ |
| **Not covered:** Data Access Agreement, Clinical Trial Research Agreement | — | — |

---

## Prediction Errors (2)

| File | Expected | Predicted | Reason |
|------|----------|-----------|--------|
| Master Services Agreement Example 1 (1).pdf | Master Services Agreement | Provision of Services Agreement | UoA acts as Service Provider; structure resembles a PSA and the model failed to detect the Work Orders framework |
| NDA student work experience example 1.pdf | Confidential Disclosure Agreement | Student Research Agreement | Title contains "Student Work Experience"; model over-indexed on the student context and missed that the core legal instrument is an NDA |

---

## Conclusion

The classify node performs well overall, with high confidence scores (avg 0.95) and accurate predictions across most contract types. The two remaining errors occur in structurally ambiguous edge cases: service contracts where UoA is the provider (MSA vs PSA) and NDAs with a student work experience framing. Both could be addressed by adding stronger disambiguation rules to the classifier prompt.
# Compare Node Evaluation Report

**Run date:** 2026-05-22 &nbsp;|&nbsp; **Concurrency:** 3 &nbsp;|&nbsp; **Wall time:** 0.86s

---

## Overview

The compare node performs deterministic, rule-based clause matching — **no LLM, no Azure services involved.** It takes each clause from the ingest checkpoint and the contract type from the classify checkpoint, then checks every clause against the relevant UoA Preferred Contracting Positions. The result is a set of flags at four severity levels.

**28 / 28 files processed successfully.**

---

## How Flagging Works

**Step 1 — Topic detection (regex keyword scoring)**
Each clause is provided by the LLM-based ingest node (which split the contract into structured clauses). The compare node then matches each clause against the keyword lists of every UoA Position; the Position with the highest score is assigned as the clause's topic. If no Position matches → the clause is immediately flagged **Blue**.

**Step 2 — IP special case**
Any clause matched to `POS-IP` is automatically flagged **Amber** with a fixed rationale: IP positions are owned by Auckland UniServices, not covered by UoA Contracting Positions, and must be referred to UniServices for review regardless of wording.

**Step 3 — Level assignment (priority: Red > Amber > Green > fallback Amber)**
Each Position has three pre-written regex cue sets:
- `RED_CUES` — patterns indicating direct conflict (e.g. "unlimited liability", "merchantab", "disrepute") → **Red**
- `AMBER_CUES` — patterns in the acceptable-but-not-preferred range → **Amber**
- `GREEN_CUES` — patterns confirming UoA preferred wording (e.g. "governed by the laws of New Zealand", "mutual confidentiality") → **Green**
- If none of the three sets match → **Amber fallback** ("topic identified but alignment unclear — requires contract manager review")

The high amber count (517) is a direct consequence of the fallback: any clause whose topic is recognised but whose wording does not trigger a specific cue lands in amber by default. This conservative design ensures no risk is silently dropped — nuanced calls are deferred to the augment node.

---

## Flag Summary

| Level | Count | Meaning |
|-------|-------|---------|
| 🔴 Red | 13 | Direct conflict with a UoA position — requires escalation |
| 🟡 Amber | 517 | Touches a sensitive topic; exact alignment unclear — needs manual review |
| 🟢 Green | 13 | Confirmed alignment with UoA preferred position |
| 🔵 Blue | 1,096 | Informational — topic not covered by any UoA position |
| **Total** | **1,639** | |

---

## Flag Examples

### 🔴 Red — Direct conflict

> **File:** Collaboration Agreement Example 3.pdf &nbsp;|&nbsp; **Clause:** 7.2 &nbsp;|&nbsp; **Ref:** POS-12 (Publication)
>
> *"…submitted for approval and approved by [Redacted] at least thirty (30) days prior to the intended publication."*
>
> **Why flagged:** UoA's position requires that the Principal Investigator retains the right to publish, with a patent stand-down of no more than 60 days. Requiring the counterparty's approval before publication directly conflicts with this position.

---

### 🟡 Amber — Needs manual review

> **File:** CDA example 1.pdf &nbsp;|&nbsp; **Clause:** 2 &nbsp;|&nbsp; **Ref:** POS-11 (Confidentiality)
>
> *"Confidentiality Agreement"*
>
> **Why flagged:** The clause touches a UoA-sensitive topic (confidentiality) but the wording does not clearly match either the preferred or the conflicting pattern. A contract manager should verify that the definition of confidential information, disclosure carve-outs, and duration all meet UoA's standard.

---

### 🟢 Green — Confirmed alignment

> **File:** CDA example 2.pdf &nbsp;|&nbsp; **Clause:** M &nbsp;|&nbsp; **Ref:** POS-11 (Confidentiality)
>
> *"MUTUAL CONFIDENTIALITY & NON-DISCLOSURE AGREEMENT"*
>
> **Why flagged:** Matches the preferred pattern "MUTUAL CONFIDENTIALITY". Mutual obligations align with UoA's requirement that both parties bear symmetric confidentiality duties.

---

### 🔵 Blue — Informational

> **File:** CDA example 1.pdf &nbsp;|&nbsp; **Clause:** E
>
> *"Execution version"*
>
> **Why flagged:** The subject matter is not covered by any UoA Contracting Position. The clause is recorded for completeness but requires no action.

---

## Most-Triggered UoA Positions (Amber)

| Position | Topic | Count |
|----------|-------|-------|
| POS-11 | Confidentiality | 162 |
| POS-19 | Termination | 84 |
| POS-IP | Intellectual Property | 71 |
| POS-16 | Governing Law & Jurisdiction | 43 |
| POS-07 | Liability Limitations & Exclusions | 35 |

---

## Red Flag Breakdown

| Position | Topic | Count |
|----------|-------|-------|
| POS-07 | Liability Limitations & Exclusions | 6 |
| POS-16 | Governing Law & Jurisdiction | 3 |
| POS-10 | Warranties | 3 |
| POS-12 | Publication | 1 |

Red flags are concentrated in MTA and Collaboration Agreement contracts, which tend to contain explicit liability, warranty, and publication clauses written by the counterparty.

---

## Conclusion

The compare node is fast and fully reproducible (sub-second for 28 files). The dominant output is blue (informational) flags, reflecting that most clauses fall outside the current UoA Position ruleset. The 13 red flags represent genuine contract risk and are correctly surfaced for escalation. The high amber count (517) indicates broad topic coverage but also suggests that tightening the pattern rules could reduce noise and improve signal quality in downstream review.
# Augment Node Evaluation Report

**Run date:** 2026-05-22 &nbsp;|&nbsp; **Concurrency:** 10 &nbsp;|&nbsp; **Wall time:** 143.0s

> **Azure services used:** Azure OpenAI — GPT-4o for per-clause flag re-grading.

---

## Overview

The augment node is the only LLM-powered step that modifies flag severity. It takes the deterministic flags produced by the compare node (seed flags) and re-grades each one by reading the full clause text in context. The result is a more precise risk signal: ambiguous amber flags are resolved into red, green, or blue based on actual clause content.

**28 / 28 files processed successfully.**

> **Note on flag accuracy:** The re-grading results produced by this node have not been validated against expert annotation. Precision and recall cannot therefore be reported. A random spot-check of 30 flags found 28 correct (93%): in the 2 problematic cases the LLM mis-attributed a "no partnership" clause to the Warranties position (POS-10) rather than its actual topic. The 113 red flags should be treated as **escalation candidates requiring expert review**, not as confirmed contract risks. The core value of this node is reducing 517 unresolved amber flags to a shorter, prioritised list — not replacing specialist legal judgement.

---

## Flag Distribution: Before vs After

| Level | Compare (seed) | After Augment | Change |
|-------|---------------|---------------|--------|
| 🔴 Red | 13 | 113 | **+100** |
| 🟡 Amber | 517 | 305 | −212 |
| 🟢 Green | 13 | 100 | +87 |
| 🔵 Blue | 1,096 | 1,121 | +25 |
| **Total** | **1,639** | **1,639** | 0 |

The total flag count is unchanged — augment re-grades rather than adds. The dominant effect is resolving amber flags into more actionable levels.

The high volume of re-grades (212 amber changes alone) reflects a fundamental limitation of the compare node: its keyword-scoring rules match clause topics by surface pattern, not by meaning. A clause can trigger the confidentiality keyword list simply because it contains the word "confidential", regardless of whether it imposes, waives, or defines a confidentiality obligation. Augment's role is precisely to apply the semantic understanding that regex cannot — reading the actual clause text in context and deciding whether it conflicts with, complies with, or falls outside each UoA position.

---

## Level Change Breakdown

| Transition | Count | Interpretation |
|------------|-------|----------------|
| amber → red | 94 | LLM confirms genuine risk |
| amber → blue | 86 | LLM finds no issue; topic out of scope |
| amber → green | 53 | LLM confirms compliance with UoA position |
| blue → green | 38 | LLM identifies compliant clause from informational flags |
| blue → amber | 17 | LLM surfaces a concern missed by keyword rules |
| blue → red | 10 | LLM identifies direct conflict in a previously unscored clause |
| red → amber | 3 | LLM downgrades; risk present but not a direct conflict |
| red → green | 3 | LLM finds the clause acceptable in context |
| green → blue | 4 | LLM finds alignment less certain than keyword suggested |
| green → red | 2 | LLM identifies conflict despite positive keyword match |
| green → amber | 1 | — |

---

## Examples

### amber → 🔴 Red ⚠️ Incorrect upgrade

> **File:** CDA example 2.pdf &nbsp;|&nbsp; **Clause:** [11] &nbsp;|&nbsp; **Ref:** POS-11 (Confidentiality)
>
> *"The disclosure of Confidential Information under this Agreement may commence as of the Effective Date, but shall terminate one (1) year thereafter; provided, however, the termination of such disclosures under this Agreement shall not affect the rights and obligations of a Receiving Party hereunder with respect to the Confidential Information disclosed to such Receiving Party prior to such termination."*
>
> **Compare:** Keyword matched a termination/confidentiality topic but could not assess the duration — flagged amber for manual review.
>
> **Augment:** The LLM read "terminate one (1) year thereafter" as the confidentiality obligation lasting only 1 year, and upgraded to red. This is a misreading — the clause limits the **disclosure window** to 1 year, not the confidentiality obligation itself. The second half of the clause explicitly states that termination of disclosures does not affect ongoing obligations over already-disclosed information. The upgrade to red is **incorrect**; the clause does not conflict with POS-11.

---

### amber → 🟢 Green

> **File:** CDA example 1.pdf &nbsp;|&nbsp; **Clause:** [17] &nbsp;|&nbsp; **Ref:** POS-11 (Confidentiality)
>
> *"…becomes available to the Receiving Party from a source other than Disclosing Party not bound by confidentiality obligations…"*
>
> **Compare:** Same confidentiality topic — flagged amber alongside all other confidentiality clauses in the file.
>
> **Augment:** The LLM recognised that this clause explicitly carves out information received from an unbound third party, which is exactly the exclusion required by UoA POS-11 → downgraded to **green**.

The two examples are from the same file and same UoA position, illustrating the core limitation of keyword-based rules: all confidentiality clauses receive the same amber score from compare, while augment distinguishes compliant from non-compliant at the clause level.

---

## Performance

| Metric | Value |
|--------|-------|
| Avg wall time / file | 31s |
| Max wall time | 122s (Contract for Goods and Services Example.pdf) |
| Total wall time | 143s |

The slowest file is also the longest (213 clauses), consistent with per-clause LLM calls scaling linearly with clause count.

---

## Conclusion

Augment substantially improves signal quality: it resolves 233 amber flags out of 517 (147 upgraded or downgraded to actionable red/green; 86 closed out to blue as out-of-scope), and surfaces 10 additional red flags from previously blue clauses. The main trade-off is cost and latency — at 31s per file this is the second-slowest node after ingest. The remaining 305 amber flags after augmentation represent cases where the LLM could not reach a confident verdict, which is expected for clauses that require broader contract context or domain expertise to evaluate.
# Summary Node Evaluation Report

**Run date:** 2026-05-22 &nbsp;|&nbsp; **Wall time:** 25.1s

> **Azure services used:** Azure OpenAI — GPT-4o for plain-prose risk summary generation.

28 / 28 files succeeded; each file produces a 2–3 sentence plain-prose risk summary (avg 708 chars) describing overall risk posture, headline issues, and recommended next step — no quantitative quality metrics available, content is for human review only.
# Pipeline Total

| Node | Wall Time |
|------|-----------|
| Ingest | 705.7s |
| Classify | 31.3s |
| Compare | 0.86s |
| Augment | 143.0s |
| Summary | 25.1s |
| **Total** | **905.96s (~15 min)** |

Ingest accounts for 78% of total runtime. All other nodes combined take under 4 minutes. The pipeline ran on 28 contracts with concurrency 3–10 depending on node.
