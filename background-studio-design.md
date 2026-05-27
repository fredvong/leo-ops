# Background Studio — Design Document

**Status:** Active — Implementation ongoing  
**Date:** 2026-05-07 · Updated 2026-05-15  
**Authors:** Peter (Architect), Mary (UX), Fae (AI), Leo (Ops)  
**Reviewer:** Fred  
**Scope:** Background prompt iteration workflow; frontend retirement decision; API gaps; `/api/background-studio/*` retirement (T-166)

---

## 1. Context & Decision

Background Studio is a standalone Svelte dev tool that provided a GUI for iterating on background image descriptions and generating AI composites via ComfyUI. This document captures the team's investigation (T-089) and the decision to retire the frontend and replace it with a Leo-driven Cowork workflow backed by the existing image_search API.

### Why Retire the Frontend

- The backend already exposes a clean 4-step API that covers the full workflow.
- The frontend value was almost entirely visual (image browsing, result comparison) — not workflow logic.
- Fred will select background images and pass paths to Leo directly, eliminating the need for an in-browser file browser.
- Maintaining a separate Svelte repo (background-studio) with its own build pipeline, linting, and QA is overhead that is no longer justified.

### Preconditions for Retirement

- Fred's image selection is path-based — paths are copied via a keyboard shortcut in image_search.
- A lightweight result-review step is available: Leo returns a direct image URL for Fred to click.
- `PUT /api/text` endpoint is built (see Section 4).

### 2026-05-15 Update — `/api/background-studio/*` Retirement

Following end-to-end validation of the Leo-driven workflow, the team agreed on 2026-05-15 to retire all 11 `/api/background-studio/*` endpoints (T-166). Analysis confirmed every endpoint is fully replaced by existing APIs once Leo stores task state locally in `BACKGROUND_STUDIO.md`. No new endpoints are required. The `background-studio` standalone repo and `BackgroundStudio.svelte` in `image_search-client` (T-142) will also be removed. See Section 4.1 and Section 9 for the full replacement map.

---

## 2. Primary Use Case

Fred notices an augmented image he likes but wants to improve the subject or background section of the description for that image — removing unwanted elements, adding missing scene details, or moving a misplaced element from subject to background (or vice versa).

### Workflow (Step-by-Step)

1. **Step 1:** Fred sees an augmented image he likes. He copies its metadata using **Ctrl+Shift+A** in image_search.
2. **Step 2:** Fred pastes the metadata JSON into a Leo Cowork conversation. Leo reads the description file via `GET /api/text` and displays the background section.
3. **Step 3:** Fred and Leo discuss and refine the description. Leo submits a generation task via `POST /api/image-augmentation/tasks` (background_path as imagePath), stores the returned `task_id` in `BACKGROUND_STUDIO.md`.
4. **Step 4:** Fred may iterate — this is a **non-linear workflow**. Multiple tasks with different descriptions can be submitted in one session. Leo tracks all task IDs locally.
5. **Step 5:** Fred returns to Leo (same or later session) to check status. Leo polls `GET /api/image-augmentation/tasks?image=<background_path>`, matches the stored task_id, and when COMPLETE returns a clickable image link: `http://10.0.0.92:8080/api/image?path=<result>`.
6. **Step 6:** Fred reviews results, picks a winner, and asks Leo to update the description. Leo shows a before/after diff and waits for Fred's confirmation, then calls `PUT /api/text` on the background_path. Both subject and background fields are writable.
7. **Step 7:** "Save" means writing the refined description back to the background image's description file via `PUT /api/text`. The consumer picks it up on the next augmentation run. This is not automatic — Leo waits for Fred's explicit instruction.

---

## 3. Keyboard Shortcut & Metadata Format

A keyboard shortcut (**Ctrl+Shift+A**, implemented in image_search) copies the following JSON to the clipboard when Fred is viewing an augmented image:

```json
{
  "input_path": "inputs/photo.jpg",
  "background_path": "backgrounds/studio/cafe.png"
}
```

- `input_path` — relative path to the input (foreground) image.
- `background_path` — relative path to the background image. Matches the `background_path` parameter used throughout the Background Studio API.
- No other fields. Simple, flat, unambiguous for Leo to parse in conversation.

---

## 4. API Design

### 4.1 Background Studio API Retirement (T-166)

All `/api/background-studio/*` endpoints are being retired. The table below shows each endpoint, its replacement, and the rationale.

| Method | Endpoint | Status | Replacement |
|--------|----------|--------|-------------|
| GET | `/api/background-studio/revisions` | ❌ Retire | Leo owns in `BACKGROUND_STUDIO.md` |
| POST | `/api/background-studio/revisions` | ❌ Retire | Leo owns in `BACKGROUND_STUDIO.md` |
| PUT | `/api/background-studio/revisions/<id>` | ❌ Retire | Leo owns in `BACKGROUND_STUDIO.md` |
| DELETE | `/api/background-studio/revisions/<id>` | ❌ Retire | Leo owns in `BACKGROUND_STUDIO.md` |
| GET | `/api/background-studio/revisions/filter` | ❌ Retire | Leo reads `BACKGROUND_STUDIO.md` |
| GET | `/api/background-studio/revisions/backgrounds` | ❌ Retire | Leo reads `BACKGROUND_STUDIO.md` |
| POST | `/api/background-studio/tasks` | ❌ Retire | `POST /api/image-augmentation/tasks` — pass `background_path` as `imagePath` |
| GET | `/api/background-studio/tasks` | ❌ Retire | `GET /api/image-augmentation/tasks?image=<background_path>` |
| GET | `/api/background-studio/tasks/<id>/status` | ❌ Retire | `GET /api/image-augmentation/tasks?image=<background_path>` — Leo matches stored `task_id` |
| GET | `/api/background-studio/subdirectories` | ❌ Retire | `GET /api/image/list?path=backgrounds` — Leo filters for `type: "directory"` |
| POST | `/api/background-studio/save` | ❌ Retire | `PUT /api/text` on `background_path` — save is a description update, not a file copy |

### 4.2 API Changes Required (text_routes.py)

All endpoints live in `text_routes.py`. No new blueprint.

> **T-126 – T-129 are merged.** T-164 and T-165 are backlogged.

| Method | Endpoint | Task | Status | Notes |
|--------|----------|------|--------|-------|
| GET | `/api/text` | **T-126** | ✅ Done 2026-05-14 | Add `subject_description` and `background_description` as separate JSON fields alongside existing `description` string (backward compat preserved) |
| PUT | `/api/text` | **T-127** | ✅ Done 2026-05-14 | Update subject and/or background section. Both fields writable. Trash copy made before every write. |
| GET | `/api/text/history` | **T-128** | ✅ Done 2026-05-14 | List archived versions for an image, newest first |
| GET | `/api/text/history/version` | **T-129** | ✅ Done 2026-05-14 | Read content of a specific archived version by timestamp |
| POST | `/api/text/batch` | **T-164** | ✅ Done 2026-05-16 | Fetch descriptions for multiple images in one call. See Section 4.7. |
| POST | `/api/text/batch-update` | **T-165** | ✅ Done 2026-05-16 | Update descriptions for multiple images in one call. See Section 4.8. |

---

### 4.3 GET /api/text — Extended Response

Current response returns a single `description` string with embedded markdown headers. Extended response adds split fields (backward compatible):

```json
{
  "description": "**Subject Description:**\n...\n\n**Background Description:**\n...",
  "subject_description": "A woman suspended in mid-air...",
  "background_description": "The background is completely black..."
}
```

`exist` field has been removed (T-126). Missing file returns **404**; malformed file (missing either section) returns **200** with empty strings for the missing fields (T-141).

> **Note:** `.description/<image>.txt` files contain exactly two sections — Subject Description and Background Description. Titles and Poem are in the separate Patreon content file (same directory as image, named after augmented image). See T-120.

---

### 4.4 PUT /api/text

#### Request

`path` is a query parameter (consistent with `GET /api/text`). Either or both fields in the body — at least one required.

```
PUT /api/text?path=backgrounds/studio/cafe.png
Body:
{
  "subject_description": "...",      // optional — at least one field required
  "background_description": "..."    // optional — at least one field required
}
```

**Update behaviour:**
- Partial update — omitted fields are preserved from the existing file unchanged.
- Both `subject_description` and `background_description` are writable.
- Edge case: subject-only file with no `**Background Description:**` section → append background section, preserve subject.

#### Trash (before every write)

Before overwriting the live file, the current version is archived:

```
{image_parent_dir}/.trash/.description/<image_stem>-<YYYYMMDD_HHMMSS>.txt
```

**Example:** `.description/TPI08783-Edit.jpg.txt` → `.trash/.description/TPI08783-Edit-20260507_143022.txt`

- Filename rule: take the image filename stem (without extension), append `-<YYYYMMDD_HHMMSS>.txt`. Both `TPI08783-Edit.jpg` and `TPI08783-Edit.png` archive to the same stem — `TPI08783-Edit-<timestamp>.txt`.
- `.trash/.description/` is auto-created if it doesn't exist.
- Trash write must succeed before the original is overwritten. Abort if trash write fails.
- Consistent with the existing `.trash/` convention in `FILE_STRUCTURE_AND_NAMING.md`.
- No auto-purge — trash accumulates indefinitely (dev tool).

**Malformed existing file:** If the description file exists but is missing one or both sections, treat missing sections as empty strings and proceed with the update. Do not 500 on a malformed source file.

**`path` normalization:** Apply `lstrip('/').replace('/', os.sep)` to the `path` query param before any file lookup — same pattern as `GET /api/text` line 144. All three endpoints (T-127, T-128, T-129) must follow this.

**`image_parent_dir` definition:** The directory containing the image file itself. For `path=2026/Sophie/photo.jpg`, this is `{IMAGE_PATH}/2026/Sophie/`. The trash archive lands at `{IMAGE_PATH}/2026/Sophie/.trash/.description/photo-<timestamp>.txt` — `.trash/.description/` sits adjacent to `.description/` in the same image directory.

**`_write_trash()` helper:** Extract trash-writing logic into a standalone `_write_trash(trash_path, content)` helper function. Keeps the main handler clean and makes test case 16 (500 on trash failure) directly mockable without affecting the source file write path.

**Timestamp timezone:** All `YYYYMMDD_HHMMSS` timestamps are generated in UTC (`datetime.utcnow()` or `datetime.now(timezone.utc)`). Applies consistently across T-127 (generates), T-128 (lists), and T-129 (accepts as input).

**`_parse_description_sections()` change:** Handled in **T-141** (prerequisite — must be merged before T-127 starts). T-141 removes the `raise ValueError` for missing sections and returns `''` instead, making `GET /api/text` consistent with `PUT /api/text`. T-127 relies on this lenient behaviour.

#### Response (200)

```json
{
  "status": "success",
  "subject_description": "A woman suspended in mid-air...",
  "background_description": "The updated background text that was written..."
}
```

Both fields returned so Leo can show Fred a before/after diff without a second `GET` call.

#### Error Responses

| Code | Condition |
|------|-----------|
| 400 | `path` query param missing |
| 400 | No JSON body |
| 400 | Neither `subject_description` nor `background_description` provided |
| 400 | Provided field is empty or whitespace-only |
| 404 | Description file does not exist — no silent create |
| 500 | Trash write failed — original file is not overwritten |

---

### 4.5 GET /api/text/history

Lists all archived versions for an image, sorted newest first. Leo uses this to show Fred what previous versions are available.

#### Request

```
GET /api/text/history?path=backgrounds/studio/cafe.png
```

#### Response (200)

```json
[
  { "timestamp": "20260507_150811", "filename": "TPI08783-Edit-20260507_150811.txt" },
  { "timestamp": "20260507_143022", "filename": "TPI08783-Edit-20260507_143022.txt" }
]
```

Empty array `[]` if the image and description file both exist but no matching trash entries are present.

**"Image exists" check:** Check for the image FILE on disk (not the description file). If the image file is missing → 404. If the image file exists but the description file does not → also 404 (deliberate — a description file that has never been written cannot have history; `[]` would be misleading). Only return `[]` when both the image file and description file exist but the `.trash/.description/` directory has no matching entries.

**File matching pattern:** For each file in `.trash/.description/`, apply `re.fullmatch(rf'{re.escape(stem)}-\d{8}_\d{6}\.txt', filename)` where `stem` is the image filename without extension derived from the `path` query param. Fullmatch anchors both ends — do not use prefix or substring matching, as `photo` would incorrectly match `photo-edit-<timestamp>.txt`. **Sort:** descending lexicographic on filename — the `YYYYMMDD_HHMMSS` embedding makes this equivalent to newest-first without parsing timestamps.

#### Error Responses

| Code | Condition |
|------|-----------|
| 400 | `path` query param missing |
| 404 | Image does not exist |

---

### 4.6 GET /api/text/history/version

Reads the content of a specific archived version. Returns `subject_description` and `background_description` only — no combined `description` field, no `timestamp` (the caller already knows the timestamp from the request).

#### Request

```
GET /api/text/history/version?path=backgrounds/studio/cafe.png&timestamp=20260507_143022
```

#### Response (200)

```json
{
  "subject_description": "A woman suspended in mid-air...",
  "background_description": "The original background text from that version..."
}
```

**Timestamp validation:** Validate `timestamp` against `\d{8}_\d{6}` before attempting any file lookup. A malformed value (e.g. `2026/05/07`, `hello`) returns 400 immediately — do not fall through to a 404.

**File construction:** Construct the archive filename as `<stem>-<timestamp>.txt` where `stem` is the image filename without extension, then look for that file in `.trash/.description/`. Return 404 if not found.

**Trash file parsing:** Use `_parse_description_sections()` (now lenient — returns `''` for missing sections) to parse the trash file content into `subject_description` and `background_description`. Consistent with how `GET /api/text` reads the live description file.

#### Error Responses

| Code | Condition |
|------|-----------|
| 400 | `path` or `timestamp` query param missing |
| 400 | `timestamp` does not match `YYYYMMDD_HHMMSS` format |
| 404 | No version found for that timestamp |

#### Version Restore

Restore is not a separate endpoint. Leo reads the desired version via `GET /api/text/history/version`, shows Fred the content, and on confirmation calls `PUT /api/text` with the old content. The restore itself creates a new trash entry preserving the current (overwritten) state.

---

## 5. Leo's Session Management

### 5.1 Workspace

Leo has a dedicated workspace directory: `/Users/fvong/work/leo`. This is separate from Fae's workspace (`/Users/fvong/work/fae`) and provides a clean home for Leo's managed files as Leo's responsibilities grow.

### 5.2 BACKGROUND_STUDIO.md

Leo maintains a running log of image pairs worked on across sessions at:

```
/Users/fvong/work/leo/BACKGROUND_STUDIO.md
```

Leo writes to this file implicitly when Fred provides a new input/background pair. Columns: `input_path`, `background_path`, `timestamp`. Fred can ask "what images have we worked on" and Leo reads from this file.

### 5.3 Revision Tracking Within a Session

A session may produce multiple revisions for the same background (non-linear workflow). Leo tracks revision number, revision ID, task ID, and status in-conversation. If Fred returns in a new session, Leo can reconstruct state by calling `GET /api/background-studio/revisions?background_path=<path>` — Fred must re-supply the `background_path` (available from `BACKGROUND_STUDIO.md` or a fresh clipboard paste).

---

## 6. Constraints & Rules

- Both subject and background description fields are writable by Leo via `PUT /api/text`.
- Leo always shows a before/after diff in conversation and waits for Fred's confirmation before calling `PUT /api/text`.
- Description update is only performed when Fred explicitly asks — not after every revision.
- Save-as-background is **not automatic**. Leo waits for explicit instruction from Fred.
- `filenamePrefix` is forbidden in task configuration — the backend assigns output paths.
- Image result URL format: `http://localhost:8080/api/image?path=<result_path>`
- Leo cannot access `.trash/.description/` directly — all version history access goes through `/api/text/history` and `/api/text/history/version`.

---

## 7. Testing Requirements (Paul)

All tests go into `tests/unit/test_text_routes.py`. No new file needed — copyright header already present.

### PUT /api/text — TestPutTextEndpoint class

#### Real Filesystem Tests (use `temp_dir` fixture, no mocking file I/O)

| # | Case | Assert |
|---|------|--------|
| 1 | Both sections → update background only | Subject unchanged; background updated; file on disk correct |
| 2 | Both sections → update subject only | Background unchanged; subject updated; file on disk correct |
| 3 | Both sections → update both fields | Both updated; file on disk correct |
| 4 | Subject-only file → PUT with background | Both sections present in file after write |
| 5 | GET after PUT on same path | Returns updated fields, original untouched fields preserved |
| 6 | Response shape on 200 | `status`, `subject_description`, `background_description` all present |
| 7 | Trash file created with correct path and name format | `.trash/.description/<stem>-<timestamp>.txt` exists (no image extension in archive name) |
| 8 | Trash directory auto-created if missing | `.trash/.description/` created on first PUT |
| 9 | Original content preserved in trash | Trash file content matches pre-PUT file content byte-for-byte |
| 16 | Trash write fails → 500, original not overwritten | Simulate trash write failure (e.g. mock `open` to raise); assert 500 returned and source file is unchanged |

#### Unit Tests (400/404, mocking acceptable)

| # | Case | Expected |
|---|------|----------|
| 10 | `path` query param missing | 400 |
| 11 | No JSON body | 400 |
| 12 | Neither field provided | 400 |
| 13 | Provided field is empty string | 400 |
| 14 | Provided field is whitespace only | 400 |
| 15 | Description file does not exist | 404 |

### GET /api/text/history — TestGetTextHistoryEndpoint class

| # | Case | Type | Assert |
|---|------|------|--------|
| 16 | Image does not exist → 404 | Unit | 404 returned |
| 16b | Image exists, description file exists, no `.trash/.description/` directory → `[]` | Real fs | Empty array returned with 200 |
| 17 | 2 archived versions → list returned newest first | Real fs | 2 entries, sorted descending by timestamp |
| 18 | `path` param missing → 400 | Unit | 400 returned |

### GET /api/text/history/version — TestGetTextHistoryVersionEndpoint class

| # | Case | Type | Assert |
|---|------|------|--------|
| 19 | Valid timestamp → content returned | Real fs | `subject_description` and `background_description` present; no `description` field, no `timestamp` field |
| 20 | Malformed timestamp (e.g. `2026/05/07`) → 400 | Unit | 400 returned without file lookup |
| 20b | Valid format, version not found → 404 | Unit | 404 returned |
| 21 | `path` or `timestamp` param missing → 400 | Unit | 400 returned |

**Test #5** (GET after PUT) and **Test #9** (trash content match) are the primary regression guards.

---

### 4.7 POST /api/text/batch (T-164)

Fetch descriptions for multiple images in one call. Leo uses this instead of N sequential `GET /api/text` calls.

#### Request

```
POST /api/text/batch
Body: { "paths": ["backgrounds/ideas9/photo.png", "backgrounds/ideas8/photo.png"] }
```

#### Response (200)

```json
{
  "results": {
    "backgrounds/ideas9/photo.png": {
      "subject_description": "...",
      "background_description": "..."
    }
  },
  "errors": {
    "backgrounds/ideas8/missing.png": { "status": 404, "error": "Description file not found" }
  }
}
```

**Best-effort:** each path is processed independently. A failure for one path goes into `errors`; the rest continue. Leo identifies failed paths from the `errors` keys.

**Path normalisation:** `lstrip('/').replace('/', os.sep)` applied per path, same as `GET /api/text`.

#### Error Responses

| Code | Condition |
|------|-----------|
| 400 | Missing or empty `paths` array |

Individual path failures are reported in `errors`, not as HTTP error codes.

---

### 4.8 POST /api/text/batch-update (T-165)

Update descriptions for multiple images in one call.

#### Request

```
POST /api/text/batch-update
Body: {
  "updates": [
    {
      "path": "backgrounds/ideas9/photo.png",
      "background_description": "..."         // at least one field required per item
    },
    {
      "path": "backgrounds/ideas8/photo.png",
      "subject_description": "...",
      "background_description": "..."
    }
  ]
}
```

#### Response (200)

```json
{
  "results": {
    "backgrounds/ideas9/photo.png": {
      "subject_description": "...",
      "background_description": "..."
    }
  },
  "errors": {
    "backgrounds/ideas8/missing.png": { "status": 404, "error": "Description file not found" }
  }
}
```

**Best-effort (not atomic):** each item processed independently. Failures go into `errors` keyed by path — Leo can see exactly which paths did not go through. Remaining items continue regardless.

**Trash backup non-negotiable per item:** `_write_trash()` must succeed before source file is overwritten. Trash failure → that path into `errors`, source file untouched.

**Per-item validation:** at least one of `subject_description` / `background_description` required; neither may be empty or whitespace. Validation failures go into `errors` without touching disk.

**Leo constraint:** Same as single `PUT /api/text` — Leo must show a before/after diff and wait for Fred's explicit confirmation before calling this endpoint. The batch form does not relax this rule.

#### Error Responses

| Code | Condition |
|------|-----------|
| 400 | Missing or empty `updates` array |

Individual item failures are reported in `errors`, not as HTTP error codes.

---

## 8. Known Gaps & Bugs

| # | Location | Description | Owner |
|---|----------|-------------|-------|
| G-01 | `background_studio_routes.py` lines 555–562 (`POST /api/background-studio/save`) | Description file written without `**Subject Description:**` / `**Background Description:**` headers — raw text joined with `\n\n`. File will not be parseable by `GET /api/text`. Must be fixed before T-089 implementation starts. ✅ **Resolved — T-132 merged 2026-05-08.** | T-132 ✅ |
| G-02 | `flux_generate` template (`image_processing_consumer.py`, `flux_generate_processor.py`, `task_validation.py`) | The `model` field (`"flux1"` / `"flux2"`) is an artificial abstraction — `flux1` and `flux2` use entirely separate ComfyUI templates with no shared contract. Split into `flux1_generate` and `flux2_generate` first-class templates. ✅ **Resolved — T-121 merged 2026-05-08.** Leo's `configuration` payload uses clean template names with no `model` field. | T-121 ✅ |
| G-03 | `background-studio` Leo skill (`/Users/fvong/Documents/Claude/Projects/Image production 2026/background-studio.skill`) | Skill references `/api/background-studio/revisions`, `/api/background-studio/tasks`, and `/api/background-studio/save` — all being retired in T-166. Once T-166 ships, the skill breaks. Must be updated to use `POST /api/image-augmentation/tasks`, `GET /api/image-augmentation/tasks?image=<background_path>`, and `PUT /api/text` before T-166 lands. Repackage and reinstall. ✅ **Resolved — skill updated and repacked 2026-05-16. Batch endpoints (T-164, T-165) also added.** | Leo triggers; packaged in Cowork session |

---

## 9. Implementation Items (Not Yet Scoped)

> **Remaining prerequisite before T-089 work begins:** G-01 (`POST /api/background-studio/save` header bug) must be resolved. T-121 ✅ already merged 2026-05-08.

| Item | Task | Status | Notes |
|------|------|--------|-------|
| T-121 — `flux_generate` model refactor | T-121 | ✅ Done 2026-05-08 | `flux1_generate` + `flux2_generate` are now first-class templates |
| G-01 fix — `POST /api/background-studio/save` description headers | T-132 | ✅ Done 2026-05-08 | Fixed before T-089 implementation |
| `GET /api/text` response extension | T-126 | ✅ Done 2026-05-14 | `subject_description` + `background_description` split fields; shared parser helper |
| `PUT /api/text` | T-127 | ✅ Done 2026-05-14 | Trash backup before every write; partial update; 16 test cases |
| `GET /api/text/history` | T-128 | ✅ Done 2026-05-14 | List archived versions; `[]` on no history; 404 on missing image |
| `GET /api/text/history/version` | T-129 | ✅ Done 2026-05-14 | Read specific archived version; restore via `PUT /api/text` |
| `POST /api/text/batch` | T-164 | ✅ Done 2026-05-16 | Fetch descriptions for multiple images in one call; best-effort with `results`/`errors` split |
| `POST /api/text/batch-update` | T-165 | ✅ Done 2026-05-16 | Update descriptions for multiple images in one call; best-effort, trash-backed |
| Retire `/api/background-studio/*` (backend) | T-166 | ✅ Done 2026-05-16 | Delete `background_studio_routes.py`, drop DB tables, update `API_DOCUMENTATION.md`. Depends on T-142. |
| Remove `BackgroundStudio.svelte` from client | T-142 | ✅ Done 2026-05-16 | Delete component and remove import + route from `App.svelte`. Must ship before T-166. |
| Remove `background-studio` standalone repo | — | ✅ Done 2026-05-16 | Fred archives GitHub repo; Fae removes `/Users/fvong/work/fae/background-studio` locally. After T-142 ships. |
| Update `background-studio` Leo skill | — | Pending | Update skill endpoints: remove all `/api/background-studio/*` references; update to use `POST /api/image-augmentation/tasks`, `GET /api/image-augmentation/tasks`, `PUT /api/text`. Repackage and reinstall. |
| Extend `BACKGROUND_STUDIO.md` schema | — | Pending | Add `task_id` and `status` columns. Leo owns. |
| Keyboard shortcut **Ctrl+Shift+A** | T-125 | Backlog | Copies `{input_path, background_path}` JSON from image_search UI |

---

## 10. Team Positions

| Member | Position |
|--------|----------|
| **Peter (Architect)** | All text endpoints in `text_routes.py`. `path` as query param throughout. 404 on missing file — no silent create. Both subject and background fields writable. History endpoints replace direct `.trash/` file access. Leo workspace at `/Users/fvong/work/leo` approved. Batch endpoints (T-164, T-165) use `results`/`errors` split — best-effort, not atomic. `/api/background-studio/*` retirement approved (T-166) — all 11 endpoints replaced by existing APIs. No new endpoints required. |
| **Mary (UX)** | Clipboard format locked to flat JSON with `input_path` and `background_path`. Image URL at `10.0.0.92:8080` is sufficient. No additional UI required. `BackgroundStudio.svelte` removal from `image_search-client` (T-142) must land before or alongside T-166. `background-studio` standalone repo to be archived by Fred after T-142 ships. |
| **Fae (AI)** | Implements all text endpoint changes (T-126–T-129 ✅ done). Owns T-166 backend retirement: delete `background_studio_routes.py`, drop DB tables, remove test file, update `API_DOCUMENTATION.md`. Owns T-142 frontend cleanup. Responsible for removing `/Users/fvong/work/fae/background-studio` local repo and GitHub repo after T-142 ships. |
| **Leo (Ops)** | JSON format for all description API interactions. PUT response returns both fields for inline diff. History workflow: list → read version → confirm → PUT to restore. `BACKGROUND_STUDIO.md` provides cross-session continuity — extended with `task_id` and `status` columns. Polls `GET /api/image-augmentation/tasks?image=<background_path>` for status; matches stored `task_id` locally. Cannot access `.trash/` directly — history endpoints required. |
| **Paul (QA)** | 21 test cases across 3 new test classes in `test_text_routes.py` ✅ done. T-166 retirement: confirm `test_background_studio_routes.py` deleted and no orphaned imports remain. T-142: confirm `BackgroundStudio.svelte` route removed from `App.svelte`. |
