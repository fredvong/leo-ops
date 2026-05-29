# T-235 Design: Background Rejection Report — Standalone Script

**Status:** Updated · 2026-05-29 (T-249: --max-kept-pct flag added)  
**Previous:** Design · 2026-05-26  
**Authors:** Ben, Leo  
**Reviewers:** Peter, Paul  
**Implementer:** Fae  
**Location:** `/Users/fvong/work/leo/background_rejection/`

---

## Background

T-060 implemented a background rejection report as `lib/background_rejection.py` inside the `image_search` repo. T-234 reversed that PR because a `lib/` module with no Flask route and no UI consumer violates the Feature Scope Gate. The underlying use case is valid and preserved here.

This document describes a **standalone Python script** that lives outside `image_search` entirely. It reads the portfolio filesystem directly, produces a ranked CSV, and feeds Leo's Background Studio description-improvement workflow.

---

## Goal

Identify background images that consistently produce **rejected** (trashed) augmented images, so Leo can open the offending backgrounds in Background Studio and improve their AI descriptions to generate better prompts next time.

**The primary metric is accepted-image yield: keepers per batch hour.** Every bad background description wastes compute and Leo's review time.

---

## Non-Goals

- No Flask route, no API endpoint, no `lib/` module inside `image_search`.
- No changes to the `image_search` repo at all.
- No automatic description updates — Leo reviews and acts on the CSV manually via Background Studio.

---

## File System Source of Truth

All naming conventions are defined in:
`/Users/fvong/work/fae/image_search/docs/FILE_STRUCTURE_AND_NAMING.md`

Key rules that drive this script:

**Year directories** — Only 4-digit directories at the portfolio root are scanned (e.g. `2024/`, `2025/`, `2026/`). Named collections (`2008 Families`), utility folders (`generated`, `old`, `test_images`), and the `backgrounds/` dir are all skipped. This matches the filter used by `OptimizedImageCache`.

**Augmented filename pattern:**
```
{input_name}___{background_name}[.v{N}].{ext}
```
Regex: `^(.+?)___(.+?)(?:\.v\d+)?\.png$`  
The triple-underscore (`___`) separator is the definitive augmented-image marker.

**Rejection signal — `.trash/` directory:**  
When the user discards an augmented image, it lands in `{session_dir}/.trash/` with its original filename preserved (including `___`). A trashed augmented image is a confirmed rejection of that input+background combination.

**Liked signal — Censored or PSD sibling:**  
If any augmented image for a background has a `-Censored.*` or `.psd` sibling in a session directory, it means the user already produced a result good enough to process further. These backgrounds may be deprioritised (see `--include-liked` flag below).

Sibling filename patterns (both are parsed for background stem using the same `___` separator):
- PSD: `{input}___{background}[.vN].psd`
- Censored: `{input}___{background}[.vN]-Censored.[png|jpg|jpeg]`

Detection regex (applied to each filename in `session_files`):
```python
LIKED_PATTERN = re.compile(
    r'^.+?___(.+?)(?:\.v\d+)?(?:\.psd|-Censored\.(?:png|jpg|jpeg))$',
    re.IGNORECASE
)
```
If this regex matches, the captured group is the background stem — set `has_liked=True` for that stem. Censored/PSD files inside `.trash/` do **not** trigger the liked signal.

**Background source directory:**  
All background source images live under `backgrounds/` at the portfolio root. The internal structure is arbitrary — subdirectories include organically numbered collections (`ideas`, `ideas1`–`ideas9`) and named theme dirs (`Christmas`, `Winter`, `sea`, `fall`, etc.). Images may be at any depth. Background resolution must be a **recursive scan of `backgrounds/`**, not a fixed subdirectory list.

**Description files:**  
`{session_dir}/.description/{image_filename}.txt` — these are the files Leo edits in Background Studio to improve AI prompts. The `background_filename` in the CSV output is what Leo passes to Background Studio; Background Studio resolves the filename to the current path on-demand.

---

## Data Model

### `BackgroundStats` (per background stem)

```python
@dataclass
class BackgroundStats:
    kept: int = 0
    rejected: int = 0
    has_liked: bool = False
    resolved_path: Optional[str] = None

    @property
    def total(self) -> int:
        return self.kept + self.rejected
```

| Field | Type | Description |
|-------|------|-------------|
| `kept` | int | Non-trash augmented PNGs (Leo kept them). Default `0`. |
| `rejected` | int | Augmented PNGs found inside any `.trash/` subdirectory. Default `0`. |
| `total` | int (derived) | `kept + rejected` — computed property, not stored separately. |
| `has_liked` | bool | True if any non-trash `-Censored.*` or `.psd` sibling exists for this background. Default `False`. |
| `resolved_path` | `Optional[str]` | Relative path from portfolio root — used internally to verify the file exists on disk. **Not written to CSV.** Default `None`; row is skipped in output if still `None` after resolution step. |

---

## Algorithm

### Pipeline

```
scan_portfolio(image_root)
    → dict[stem → BackgroundStats]

resolve_background_paths(stats, image_root)
    → mutates stats in place, sets resolved_path on each entry

filter_and_sort(stats, min_total, max_kept, include_liked)
    → List[BackgroundStats] sorted by kept/total ascending (worst first)

write_csv(rows, output_path)
    → CSV file
```

### Step 1: `scan_portfolio`

Single-pass `os.walk` of the portfolio:

1. At the root level, only enter directories matching `^\d{4}$` (year dirs).
2. For each year dir, iterate session subdirectories.
3. For each session dir, collect two sets:
   - `session_files`: filenames directly in the session dir (non-trash)
   - `trash_files`: filenames in `{session_dir}/.trash/` (if it exists)
4. Run `_process_session(stats, session_files, trash_files)`:
   - For each filename in `session_files` matching the augmented regex: increment `kept` for that background stem; also check for liked siblings using `LIKED_PATTERN` and set `has_liked=True` if matched.
   - For each filename in `trash_files` matching the augmented regex: increment `rejected` for that background stem.
   - `total` is **derived**: `total = kept + rejected`. It is not tracked as a separate counter — compute it when needed (e.g. in `filter_and_sort` and `write_csv`). The `BackgroundStats.total` field reflects this at read time.

Skip `.description/` and `.trash/` when walking — they are not session dirs.

### Step 2: `resolve_background_paths`

For each stem in `stats`:
- Recursively walk all of `backgrounds/` under `image_root` (no subdirectory filter — the internal structure is arbitrary and may change) to find a file whose stem (name without extension) matches.
- If found in exactly one location: set `resolved_path` to confirm the file exists on disk.
- If found in multiple locations: log error to stdout, use first match (alphabetical path order).
- If not found: log error to stdout, leave `resolved_path = None` (row skipped in CSV output).

### Step 3: `filter_and_sort`

Filter criteria (all must be satisfied):
- `total >= min_total` — background has been tried enough times to be meaningful
- `kept < max_kept` — absolute keeper count gate
- if `--max-kept-pct` is provided: `(kept / total) * 100 <= max_kept_pct` — percentage gate
- `resolved_path is not None` — background file exists on disk
- `has_liked is False`, unless `--include-liked` is passed

When both `--max-kept` and `--max-kept-pct` are provided, a background must satisfy **both** gates to appear in the output.

Sort: ascending by `kept / total` (fewest keepers relative to total, first).

### Step 4: `write_csv`

Columns: `background_filename, total, kept, rejected, has_liked`

`background_filename` is the bare filename (e.g. `sunset beach.jpg`) — the stable identity per the `backgrounds/` contract. Relative path is not written to CSV; it is not a stable contract (the file may be moved between subdirectories at any time). `has_liked` is `True` if any augmented image for this background has a Censored or PSD sibling in a session directory — indicating the user found at least one result good enough to process further. Silent overwrite if output file already exists.

---

## CLI Interface

```
python3 background_rejection_report.py \
    --portfolio ~/Portfolio \
    --output-csv rejected_backgrounds.csv \
    --min-total 50 \
    --max-kept 2 \
    [--max-kept-pct 10.0] \
    [--include-liked]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--portfolio` | `$IMAGE_SEARCH_PORTFOLIO_PATH` or `~/Portfolio` | Portfolio root directory |
| `--output-csv` | **Required** — exits with error if omitted | Output CSV path. Silent overwrite if file exists. |
| `--min-total` | `50` | Minimum total augmented images (inclusive) for a background to appear |
| `--max-kept` | `2` | Maximum kept images (exclusive) — default catches backgrounds with 0 or 1 keepers |
| `--max-kept-pct` | None (disabled) | Percentage gate (0–100). Flags backgrounds where `(kept/total)*100 <= MAX_KEPT_PCT`. Can be used alone or combined with `--max-kept`; when both are set, a background must satisfy both. |
| `--include-liked` | False | Include backgrounds that have Censored/PSD siblings (default: skip them) |

**Recommended invocation (Ada/Ben analysis, T-248):**
```
python3 background_rejection_report.py \
    --portfolio ~/Portfolio \
    --output-csv data/rejected_backgrounds.csv \
    --min-total 20 \
    --max-kept-pct 10.0
```
This flags ~158 backgrounds at min_total=20 — a realistic Background Studio workload for Leo. Drop to `--min-total 10` for a comprehensive pass (~538 backgrounds).

**Progress output to stdout:**
```
Scanning portfolio at /Users/fvong/Portfolio ...
  Found 1,842 augmented images across 94 unique backgrounds.
  Skipped 12 backgrounds with Censored/PSD files (use --include-liked to override).
Applying filter: total >= 50, kept < 2
  Flagged 7 backgrounds out of 82 total.
Report written to: rejected_backgrounds.csv
```

---

## Output CSV

```
background_filename,total,kept,rejected,has_liked
grey concrete wall.jpg,14,0,14,False
blurred neon lights.jpg,9,0,9,True
sunset beach.jpg,11,1,10,False
dark studio.jpg,7,1,6,True
```

Sorted worst-first (fewest keepers relative to total). Relative path is intentionally omitted — it is not a stable contract and will go stale if the background is moved between subdirectories. `has_liked` distinguishes backgrounds that occasionally produced a Censored/PSD result (description worth improving) from pure-rejection backgrounds (candidate for removal). Leo passes `background_filename` to Background Studio, which resolves it to the current path on-demand.

---

## Infrastructure Note

**Portfolio images are stored on a NAS.** Only Fred can access the NAS from his Mac. The sandbox (Cowork) cannot connect to NAS or any remote resource — there is no way to mount it as a Cowork folder.

As a result:
- Any Python script that reads the portfolio (NAS) **must be run by Fred** in his Mac Terminal. Ben and Leo write the script; Fred executes it.
- The sandbox cannot run this script, and Claude cannot run it on Ben's or Leo's behalf.

**One exception:** `10.0.0.92` Web APIs are reachable from the sandbox via the Chrome extension (`mcp__Claude_in_Chrome__*`). This applies only to HTTP endpoints — not to filesystem access on the production server.

```bash
# Fred runs this in his Mac Terminal:
cd /Users/fvong/work/leo/background_rejection
python3 background_rejection_report.py --portfolio ~/Portfolio --output-csv data/rejected_backgrounds.csv
```

No dependencies beyond the Python standard library (`os`, `re`, `csv`, `dataclasses`, `argparse`).

---

## Leo's Workflow

> **⚠️ Sandbox limitation:** The portfolio is on a NAS — only Fred can access it. The sandbox cannot reach NAS resources, and Cowork cannot mount them. **Fred must run this script** and share the CSV output with Leo. Do not ask Claude to execute it.

**One-time setup** (Fred, run once):
```bash
mkdir -p /Users/fvong/work/leo/background_rejection/data
```

**Run the report** (Fred, Mac Terminal):
```bash
cd /Users/fvong/work/leo/background_rejection
python3 background_rejection_report.py \
    --portfolio ~/Portfolio \
    --output-csv data/rejected_backgrounds.csv
```

The output lands at `/Users/fvong/work/leo/background_rejection/data/rejected_backgrounds.csv` — a connected Cowork folder Leo can open directly. The `data/` directory is gitignored; generated CSVs are not committed.

**Leo's review steps:**
1. Open `data/rejected_backgrounds.csv`. Review top entries (worst backgrounds first).
2. For each bad background, copy the `background_filename` value.
3. Open Background Studio and pass the filename — Background Studio resolves it to the current path on-demand.
4. Read the current description, revise it, submit a new generation task.
5. Review the new augmented images. Repeat until yield improves.

---

## Design Decisions — Resolved

| # | Question | Decision |
|---|----------|----------|
| Q1 | Default thresholds | `min_total=50, max_kept=2` — per agreed spec. Fine as starting point. |
| Q2 | Per-session scope | Not a concern for first iteration. Deferred to T-230 if needed. |
| Q3 | `has_liked` suppression | Default ON — suppress backgrounds with Censored/PSD siblings. Override with `--include-liked`. |
| Q4 | Additional CSV columns | None. Columns: `background_filename, total, kept, rejected` only. |

---

## Ben's Role

> **⚠️ Sandbox limitation:** The portfolio is on a NAS — only Fred can access it. The sandbox cannot reach NAS resources, and Cowork cannot mount them. Ben cannot run this script or inspect the portfolio directly. Any live portfolio data Ben needs must come from Fred running commands in Terminal and pasting results back.

Ben reviews the algorithm above for correctness and completeness:
- Confirm the `.trash/` scan approach correctly captures all rejected augmented images across the portfolio.
- Confirm that a recursive scan of `backgrounds/` is the correct resolution strategy — no fixed subdirectory list.
- Sign off on the `has_liked` censored/PSD detection logic.

---

## Fae's Implementation Notes

**Directory layout:**
```
/Users/fvong/work/leo/
  background_rejection/
    background_rejection_report.py
    test_background_rejection.py
```

- No `__init__.py`, no imports from `image_search` — fully standalone.
- Copyright header required (Paul's gate).
- The augmented regex and year-dir filter must match `FILE_STRUCTURE_AND_NAMING.md` exactly — do not copy from memory, read the doc.
- Tests: `pytest` test file alongside the script. Use `tmp_path` fixtures to create fake portfolio trees. Required test cases:
  - `test_empty_portfolio` — no augmented images found, CSV has header only
  - `test_background_not_found_on_disk` — stem in stats but absent from `backgrounds/`, row skipped
  - `test_stem_in_multiple_dirs` — duplicate background filenames, first match used, error logged
  - `test_has_liked_detection` — Censored and PSD siblings correctly set `has_liked=True`; Censored in `.trash/` does NOT
  - `test_threshold_edge_cases` — exactly at `min_total` boundary (inclusive), exactly at `max_kept` boundary (exclusive)
  - `test_missing_output_csv_exits` — invoking the script without `--output-csv` exits with a non-zero code
  - `test_script_is_read_only` — see Paul's gate below
- No Flask, no `lib/` imports, no `sys.exit` in the core logic functions (keep them pure and testable).
- **Read-only gate (Paul's requirement):** The script must never write to the portfolio or NAS. `test_script_is_read_only` reads the script source as text and asserts none of the following Python syntax patterns are present. Use precise regexes to avoid false positives from comments:

  ```python
  FORBIDDEN_PATTERNS = [
      (r'\bopen\s*\([^)]*["\'](?:w|a|x|wb|ab|xb)["\']', "write-mode open()"),
      (r'\bos\.remove\s*\(',                              "os.remove"),
      (r'\bos\.unlink\s*\(',                              "os.unlink"),
      (r'\bos\.rename\s*\(',                              "os.rename"),
      (r'\bos\.makedirs\s*\(',                            "os.makedirs"),
      (r'\bos\.mkdir\s*\(',                               "os.mkdir"),
      (r'\bshutil\.(copy|move|rmtree)\s*\(',             "shutil write op"),
  ]
  ```

  The only permitted write operation is opening `--output-csv` for writing, which is handled by `write_csv` — Fae must ensure that function uses a clearly named local variable (e.g. `output_path`) and that the pattern above does not match it (it won't, since `csv.writer` uses the already-opened file handle, not a raw `open(... 'w')`). If the test flags a false positive, fix the implementation pattern rather than weakening the test.

---

## Related Tasks

| Task | Status | Notes |
|------|--------|-------|
| T-060 | Cancelled 2026-05-26 | Original implementation; reversed by T-234. `.pyc` preserved at `lib/__pycache__/background_rejection.cpython-311.pyc` — algorithm recovered for this design. |
| T-234 | Done 2026-05-26 | Removed T-060 implementation from repo. PR #108. |
| T-229 | Cancelled | Leo runner skill; re-raise after T-235 ships if a Leo skill wrapper is warranted. |
| T-230 | Cancelled | Per-session censored skip scope; see Open Question Q2 above. |
| T-231 | Cancelled | Flask endpoint; re-evaluate as future work once standalone script proves stable. |
| **T-235** | **In Design** | **This document.** |
