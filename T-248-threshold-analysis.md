# T-248 — Threshold Analysis: Background Rejection Report

**Date:** 2026-05-28
**Authors:** Ada, Ben
**Related tasks:** T-235 (background rejection report), T-248 (update defaults), T-249 (--max-kept-pct flag)

---

## Background

T-235 shipped with default thresholds of `--min-total 50, --max-kept 2`. When run against the live portfolio (`/Volumes/fvong/Portfolio`) it flagged **0 backgrounds** because 99.9% of backgrounds have fewer than 50 uses (54% sit in the 11–20 use bucket). The thresholds were chosen before any real portfolio data was available.

Ada ran a distribution analysis on the full portfolio CSV (1,477 resolved backgrounds) to find data-driven defaults.

---

## Dataset

- **Total resolved backgrounds:** 1,477
- **has_liked=True:** 512 (backgrounds with at least one Censored/PSD result)
- **has_liked=False:** 965 (no liked result — primary triage candidates)

---

## Rejection Ratio Distribution (has_liked=False)

| Bucket | Count | % |
|---|---|---|
| 0% kept (pure rejection) | 226 | 23.4% |
| 1–10% kept | 382 | 39.6% |
| 11–25% kept | 272 | 28.2% |
| 26–50% kept | 58 | 6.0% |
| 51–75% kept | 9 | 0.9% |
| 76–100% kept | 18 | 1.9% |

**Key finding:** The distribution is strongly left-skewed — 63% of has_liked=False backgrounds have ≤10% keep rate. There is no natural bimodal breakpoint in the ratio. The ratio alone is not sufficient to set a threshold; `min_total` is the controlling lever.

---

## Total Count Distribution (has_liked=False)

| Bucket | Count | % |
|---|---|---|
| 1–5 uses | 112 | 11.6% |
| 6–10 uses | 102 | 10.6% |
| 11–20 uses | 521 | 54.0% |
| 21–50 uses | 229 | 23.7% |
| 51+ uses | 1 | 0.1% |

**Key finding:** 54% of backgrounds have 11–20 uses. The original `min_total=50` excluded virtually the entire portfolio. `min_total=50` is too aggressive.

---

## Pure Rejection (ratio=0) by min_total (has_liked=False)

| min_total | Flagged |
|---|---|
| ≥ 3 | 190 |
| ≥ 5 | 181 |
| ≥ 10 | 156 |
| ≥ 15 | 78 |
| ≥ 20 | 25 |
| ≥ 30 | 3 |
| ≥ 50 | 0 |

---

## Low Yield (kept/total ≤ 10%) by min_total (has_liked=False)

| min_total | Flagged |
|---|---|
| ≥ 3 | 572 |
| ≥ 5 | 563 |
| ≥ 10 | 538 |
| ≥ 15 | 328 |
| ≥ 20 | 158 |
| ≥ 30 | 30 |
| ≥ 50 | 0 |

---

## Recommendations

### Ada
- **Ratio gate:** `kept/total ≤ 10%` (captures the meaningful failure signal without being too permissive)
- **Minimum total:** `min_total=10` for comprehensive review; `min_total=20` for a manageable Leo workload (~158 backgrounds)
- The `--max-kept-pct` flag does not yet exist in T-235 — the script uses `--max-kept` (absolute count). See T-249.

### Ben
- **Recommended default run:** `min_total=10, kept/total ≤ 10%, has_liked=False`
- 538 backgrounds at this setting — more than one session but workable over time
- `min_total=20` produces 158 — a realistic Background Studio batch
- Sort: ratio ascending, then rejected descending (worst-first)

### Leo
- Start with `min_total=20` for first triage pass — 158 backgrounds is a week's work in Background Studio sessions
- After clearing those, drop to `min_total=10` to catch the next tier

---

## Open Questions

1. Should `--max-kept-pct` replace or complement `--max-kept`? (T-249)
2. Should the updated defaults ship as script defaults, or be documented as recommended CLI args? (T-248)
3. Does `has_liked=True` warrant its own separate analysis? (deferred)

---

## Motivating Example

**DANU.png** (`backgrounds/ideas6/Screenshot 2026-03-10 at 21-26-22 DANU.png`)
- 35 total uses, 0 kept, ratio = 0.0, has_liked = False
- Description contained human-subject language ("above the woman", "her attire") — background prompt described a scene with a person, causing compositing failures
- Would be flagged at any min_total ≤ 35 with ratio gate ≤ 10%

This pattern motivated T-246 (heuristic keyword gate) and T-247 (VLM consistency check).
