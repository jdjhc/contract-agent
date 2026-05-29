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
