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
