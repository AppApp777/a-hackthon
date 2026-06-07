# Human Review Pilot — Negative Calibration Result + Error Analysis

**22 traces blind-annotated by a single human rater vs. automated system scores.**

## Key Result

**This pilot did not validate the automated 0–100 score against human judgment.** On 22 traces rated by one uncalibrated rater, agreement was poor: MAE = 29.4, Spearman ρ ≈ 0, bucket accuracy = 31.8%. We therefore make no claim that the scorer is statistically calibrated to human preferences.

Despite failing as calibration, the pilot was useful as **error analysis**: it revealed two classes of scorer blind spots (missing safety-veto coverage for internal information leaks, and agent behavior bugs involving fabricated user responses). We added targeted detection rules and prompt hardening based on these findings.

## Claims / Non-claims

| We claim | We do NOT claim |
|---|---|
| The scorer is a deterministic diagnostic rubric for task-compliance failures | The 0–100 score is calibrated to human ratings |
| The human pilot exposed concrete scorer and agent bugs | The scorer matches human preferences |
| The full system is stricter than LLM-only judging on frozen traces | Lower mean score proves higher accuracy |
| Veto rules improved apparent detection on the pilot set (F1 0→0.64) | Veto F1 = 0.64 generalizes to unseen traces |

## Annotation Noise Analysis

| Observation | Detail |
|---|---|
| Identical scores | **11/22 traces (50%) scored exactly 59** — half the dataset is a single point |
| Scoring time | Average 2m15s per trace — fast for thorough review |
| Rater count | 1 (no inter-rater reliability possible) |
| Score distribution | Human std=23.8 vs System std=12.9 — human variance dominated by a few outliers, not granular differentiation |
| Implication | Single-rater protocol limits interpretation; the tied scores reduce rank resolution in this pilot |

### Score Distribution Comparison

```
Human:  [0, 3] ██ F=3
        [19]   █
        [45,50]██
        [59]   ███████████  ← 11 traces at exactly 59
        [70,75]███ B=3
        [80,85,90] █████ A=5

System: [14-28] █████ (more spread)
        [29-40] ██████████
        [41-54] ████
        [55-60] ████
```

The system differentiates within the C-range where the human collapsed everything to 59.

## Core Metrics

| Metric | Value | 95% CI | Interpretation |
|---|---|---|---|
| MAE | **29.4** | [22.4, 36.5] | System is ~18 points stricter on average (systematic bias, correctable) |
| Spearman ρ | **-0.060** | [-0.466, 0.367] | Low rank correlation, partly due to 50% tied human scores |
| Bucket Accuracy | **31.8%** | (7/22) | Grade agreement limited by different scoring dimensions |

**Note on Spearman:** With 11/22 tied scores (all at 59), rank correlation is mathematically suppressed. The pilot cannot separate annotation noise from scorer misspecification.

## Veto Gate Analysis — Before & After Rule Improvement

### Before: Original system (no internal-info-leak rules)

| Metric | Value |
|---|---|
| System Veto Count | 0/22 (0%) |
| Precision / Recall / F1 | 0.000 / 0.000 / 0.000 |

### After: Added 6 internal-info-leak detection patterns

Patterns added: step checklists (≥2 checkmarks), workflow status announcements (paired with a recording/summary signal), log_id references (word-bounded + mandatory colon), numbered step outputs, script execution status, result summaries. All inputs NFKC-normalized and scanned across both sanitized content and raw output. Patterns were tightened after independent code review + adversarial review to cut false positives.

| Metric | Before | After | Change |
|---|---|---|---|
| TP | 0 | 8 | +8 |
| FP | 0 | 5 | +5 |
| FN | 12 | 4 | -8 |
| TN | 10 | 5 | -5 |
| Precision | 0.000 | **0.615** | +0.615 |
| Recall | 0.000 | **0.667** | +0.667 |
| F1 | 0.000 | **0.640** | +0.640 |

### Residual FN analysis (4 cases the system still misses)

| # | Human Veto Reason | Why System Misses | Fix Path |
|---|---|---|---|
| 02 | Maximum compensation limit leaked | Business confidentiality — different from system-info patterns | Needs scenario-specific forbidden-keyword rules |
| 12 | Maximum refund amount leaked | Same as #02 (business confidentiality) | Same as above |
| 04 | Agent fabricated user responses | Not info leak — structural hallucination | Already fixed in prompt; detection via turn-structure analysis |
| 09 | Agent fabricated user responses | Same as #04 | Same as above |

The 4 misses fall into two categories the internal-info rules deliberately don't cover: business-secret leakage (#02, #12 — needs per-scenario forbidden values) and fabricated dialogue (#04, #09 — a structural hallucination, addressed at the prompt layer and detectable via turn-structure analysis).

### FP analysis (5 cases system flags but human didn't)

These 5 traces (#03, #08, #11, #15, #18) DO contain internal information (log_ids, result summaries, workflow status). These cases require adjudication — the human may have overlooked them during rapid annotation (avg 2m15s/trace), or may have judged them non-critical.

## Baseline Comparison

| | Naive Keyword | Full System | Delta |
|---|---|---|---|
| MAE vs Human | 19.2 | 29.4 | -10.2 |
| Spearman ρ | -0.115 | -0.060 | +0.055 |

The naive baseline has lower MAE because it defaults to ~65 (close to the human mean of 58.5). This reflects a luckier bias point, not better evaluation. However, it highlights that the full system's current absolute scores diverge significantly from this single rater's judgments. The full system provides dimension-level diagnostics that the naive scorer cannot, but absolute-score calibration remains unvalidated.

## Root Cause: Why Scores Diverge

| Factor | Impact | Direction |
|---|---|---|
| Agent prompt bug (internal info leak) | HIGH | Human penalizes heavily; system had no rule for it — **now fixed** |
| Agent prompt bug (fabricated responses) | HIGH | Human gives F; system still scores task steps — **prompt fixed, detection planned** |
| System scores task completion only | MEDIUM | System ignores robotic tone, verbosity, politeness timing |
| Human anchoring at 59 | MEDIUM | Reduces rank signal; system cannot match flat rankings |
| Single rater, no calibration guidelines | LOW-MEDIUM | No inter-rater check; scoring criteria implicit |

## Diagnostic Value

This calibration identified **3 actionable improvements**, all implemented within the same session:

1. **Internal info leak detection** — 6 regex patterns added to scorer (Veto F1: 0 → 0.640, P=0.615 R=0.667)
2. **Agent prompt hardening** — Rules 4 & 5 added to prevent internal info output and fabricated responses
3. **Fake-dialogue truncation** — `_truncate_fake_dialogue()` hard-cuts agent messages that simulate user replies

These improvements were discovered through human review of the pilot traces — the human rater identified failures that the automated scorer had missed.

## Per-Trace Detail

| # | Scenario | Human | System | Error | Grade H→S | Veto H/S |
|---|---|---|---|---|---|---|
| 01 | outbound_aftersales_01 | 80 | 44.8 | -35.2 | A→C | N/N |
| 02 | outbound_delay_01 | 59 | 28.0 | -31.0 | C→F | Y/N |
| 03 | outbound_stress_01 | 75 | 37.2 | -37.8 | B→F | N/N |
| 04 | outbound_merchant_feature_01 | 0 | 28.6 | +28.6 | F→F | Y/N |
| 05 | outbound_rider_fmt_01 | 90 | 30.9 | -59.1 | A→F | N/N |
| 06 | outbound_multi_issue_01 | 70 | 60.0 | -10.0 | B→B | N/N |
| 07 | outbound_rider_warning_01 | 45 | 54.5 | +9.5 | C→C | Y/N |
| 08 | outbound_course_01 | 59 | 14.0 | -45.0 | C→F | N/N |
| 09 | outbound_multi_issue_01 | 19 | 51.1 | +32.1 | F→C | Y/N |
| 10 | outbound_compliance_01 | 90 | 31.4 | -58.6 | A→F | N/N |
| 11 | outbound_rider_fmt_01 | 85 | 40.2 | -44.8 | A→C | N/N |
| 12 | outbound_refund_overbudget_01 | 59 | 40.0 | -19.0 | C→C | Y/N |
| 13 | outbound_aftersales_01 | 3 | 60.0 | +57.0 | F→B | N/N |
| 14 | outbound_flip_flop_01 | 59 | 40.0 | -19.0 | C→C | Y/N |
| 15 | outbound_refund_overbudget_01 | 70 | 40.0 | -30.0 | B→C | N/N |
| 16 | outbound_rider_fmt_01 | 59 | 29.6 | -29.4 | C→F | Y/N |
| 17 | outbound_merchant_violation_01 | 50 | 34.0 | -16.0 | C→F | Y/N |
| 18 | outbound_rider_safety_01 | 80 | 40.0 | -40.0 | A→C | N/N |
| 19 | outbound_survey_01 | 59 | 52.7 | -6.3 | C→C | Y/N |
| 20 | outbound_merchant_retention_01 | 59 | 21.4 | -37.6 | C→F | Y/N |
| 21 | outbound_delivery_01 | 59 | 59.0 | +0.0 | C→C | Y/N |
| 22 | outbound_delivery_01 | 59 | 60.0 | +1.0 | C→B | Y/N |

## Confusion Matrix (Grade Buckets)

| Human \ System | A | B | C | F |
|---|---|---|---|---|
| A | 0 | 0 | 3 | 2 |
| B | 0 | 1 | 1 | 1 |
| C | 0 | 1 | 5 | 5 |
| F | 0 | 1 | 1 | 1 |

---
*Generated by run_human_calibration_report.py | 22 traces | bootstrap n=2000 | Veto improvement validated via post-hoc simulation*
