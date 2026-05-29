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
