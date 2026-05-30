# T-247: VLM Image+Description Consistency Check — Write-up

**Authors:** Pat (VLM Scientist), Ben  
**Date:** 2026-05-30  
**Status:** Precision check complete. Ready for Leo to act on results.

---

## Background

Background images in the portfolio each have a `.description` file containing a **Background Description** section. This text is fed to an AI compositor (ComfyUI/Flux) as a scene prompt when generating augmented portrait images.

The problem: some descriptions contain human-subject language — references to a person, body part, pose, or action. When the AI compositor reads this language, it treats it as part of the scene and generates artefacts: ghost figures, misplaced body parts, or lighting calibrated for a human presence that shouldn't be there. The result is a rejected augmented image, wasted compute, and extra review time for Leo.

The motivating case was **DANU.png**: a forest scene whose description contained "above the woman." Leo fixed it manually (T-250). T-247 is the systematic audit to find all similar cases across the full portfolio.

---

## Approach

### Tool

**qwen3-vl:32b** running on the local eval machine at `http://10.0.0.179:11434` via Ollama. The model sees both the image (as a base64-encoded attachment) and the Background Description text simultaneously.

### Prompt (Pat's design, 2026-05-29)

```
You are evaluating whether a background image description is appropriate
for use in portrait photography compositing.

A good background description describes ONLY the environment — the scene,
lighting, location, atmosphere. It must not reference any human subject
(person, figure, body part, clothing, or action performed by a person).

Look at the image. Read the description below.

Description:
{background_description}

Answer with ONE word only:
- CLEAN   -> the description describes only the environment, no human subject
- FLAGGED -> the description references a human subject in any way

Answer:
```

The response parser extracts the first word, uppercases it, strips trailing punctuation, and maps anything other than `CLEAN` to `FLAGGED` (fail-safe).

### Script

`background_description_check.py` — a standalone Python script (no Flask, no image_search imports) following the same pattern as T-235. Reads portfolio images from `backgrounds/` recursively, loads each `.description` file, calls the VLM, and writes a flagged CSV. Fred runs it locally since the portfolio is on a NAS the sandbox cannot reach.

**Key design decisions:**
- Sequential (no parallelism) — qwen3-vl:32b is heavyweight, ~120s timeout per image
- Fail-safe: VLM errors count as FLAGGED, not silently skipped
- Read-only: the script never writes to the portfolio (Paul's QA gate, verified by static analysis test)
- Portfolio root: `/Volumes/fvong/Portfolio`

CLI:
```bash
cd /Users/fvong/work/leo/background_rejection
python3 background_description_check.py \
    --portfolio /Volumes/fvong/Portfolio \
    --output-csv data/flagged_backgrounds.csv
```

---

## Batch Run Results (2026-05-30)

| Metric | Count |
|--------|-------|
| Total background images | 1,634 |
| No description file (skipped) | 2 |
| No background section (skipped) | 9 |
| VLM errors (counted as FLAGGED) | 31 |
| **FLAGGED** | **712** |
| CLEAN | 911 |

712 flagged = 43.9% of evaluated images. This rate seemed high enough to warrant a precision check before Leo invested time correcting descriptions.

---

## Precision Check

### Method

A review app (`review_app.py`) was built and run locally. It sampled **30 rows** from the flagged CSV (25 non-error + 5 VLM-error rows, shuffled), served them at `http://localhost:8765`, and showed each background image alongside its full description. A human reviewer labeled each row **CLEAN / FLAGGED / UNSURE** with optional notes.

### Results

| Label | Count |
|-------|-------|
| Confirmed FLAGGED | 22 |
| Labeled CLEAN (false positive) | 6 |
| UNSURE | 2 |

**Breakdown by row type:**

**VLM-error rows (5 sampled):** All 5 were labeled CLEAN. Every timeout error in the sample was a false positive — the model didn't respond, the fail-safe fired. The 31 timeout errors in the full run are almost certainly all false positives.

**Non-error rows (25 sampled):**
- 22 confirmed FLAGGED
- 1 genuine false positive (no reviewer notes — isolated case)
- 2 UNSURE — both flagged as "broken description" (malformed `.description` file, data quality issue separate from the prompt)

**Precision on non-error rows: 22/23 = 95.7%**

### Notable observations from reviewer notes

- Some flagged cases are unambiguous: "the woman is visible", "above the person", "on which she lies"
- One subtle edge case: a gondolier in the background — reviewer noted "the prompt in image_search probably won't recognize the gondolier as human," suggesting that visual context may matter more than textual mention for compositing artefacts
- "Broken description" rows (2) have malformed section headers — they should be fixed as a separate data quality pass, independent of this task

---

## Conclusions

1. **The prompt is working well.** 95.7% precision on non-error rows — no prompt tuning required before Leo acts on results.

2. **The 31 VLM timeout errors are false positives.** All 5 timeout rows in the precision sample were labeled CLEAN. These should be re-run (or manually reviewed) and not sent to Background Studio.

3. **Adjusted estimate of true positives:** ~681 non-error flagged rows × 95.7% precision ≈ **651 genuinely problematic descriptions** out of 712 total flagged.

4. **Two malformed description files** need a separate fix — they have no parseable Background Description section and show up as UNSURE regardless of the VLM verdict.

---

## Recommended Next Steps

| # | Action | Owner |
|---|--------|-------|
| 1 | Re-run the 31 VLM-error rows (or manually review) | Fae / Fred |
| 2 | Fix 2 malformed description files | Leo (Background Studio) |
| 3 | Work through ~651 flagged backgrounds in Background Studio | Leo |
| 4 | Commit `background_description_check.py` + tests, open PR | Fae |

---

## Files

| File | Location |
|------|----------|
| Batch script | `/Users/fvong/work/leo/background_rejection/background_description_check.py` |
| Tests | `/Users/fvong/work/leo/background_rejection/test_background_description_check.py` |
| Review app | `/Users/fvong/work/leo/background_rejection/review_app.py` |
| Full flagged CSV | `/Users/fvong/work/leo/background_rejection/data/flagged_backgrounds.csv` |
| Precision check CSV | `/Users/fvong/work/leo/background_rejection/data/precision_check.csv` |
