# Copyright (c) 2026 Fred Vong. All rights reserved.

"""
Background Description Consistency Check

Evaluates each background image against its AI-generated description using a
local VLM (qwen3-vl:32b). Flags backgrounds where the description contains
human-subject language — indicating the description is scene-specific rather
than background-appropriate, which causes compositing artefacts.

Usage:
    python3 background_description_check.py \\
        --portfolio /Volumes/fvong/Portfolio \\
        --output-csv data/flagged_backgrounds.csv \\
        [--ollama-url http://10.0.0.179:11434] \\
        [--model qwen3-vl:32b] \\
        [--include-clean]

Output CSV columns:
    background_path      — relative path from portfolio root
    background_filename  — bare filename (e.g. 'DANU.png')
    verdict              — FLAGGED or CLEAN
    description_excerpt  — first 120 chars of background_description

No dependencies beyond the Python standard library + urllib (stdlib).
Requests are sequential (no parallelism) — qwen3-vl:32b is heavyweight.
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DESCRIPTION_DIR_NAME = '.description'

# Year directories pattern — matches the portfolio scan from T-235
YEAR_DIR_PATTERN = re.compile(r'^\d{4}$')

# Pat's eval prompt (designed 2026-05-29)
EVAL_PROMPT = """You are evaluating whether a background image description is appropriate
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

Answer:"""

VALID_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundCheckResult:
    background_path: str          # relative path from portfolio root
    background_filename: str      # bare filename
    verdict: str                  # 'FLAGGED' or 'CLEAN'
    description_excerpt: str      # first 120 chars of background_description
    error: Optional[str] = None   # set if VLM call failed


@dataclass
class RunStats:
    total_images: int = 0
    no_description: int = 0
    no_background_section: int = 0
    vlm_errors: int = 0
    flagged: int = 0
    clean: int = 0
    results: List[BackgroundCheckResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Description file helpers
# ---------------------------------------------------------------------------

def get_description_file_path(image_path: str) -> str:
    """
    Return the .description file path for a given image.

    Convention (matches server/util.py):
        {image_dir}/.description/{image_filename}.txt
    """
    image_dir = os.path.dirname(image_path)
    image_filename = os.path.basename(image_path)
    return os.path.join(image_dir, DESCRIPTION_DIR_NAME, f"{image_filename}.txt")


def parse_background_description(content: str) -> Optional[str]:
    """
    Extract the Background Description section from a description file.

    Returns None if the section is absent or empty.
    """
    sections = {}
    current_section = None
    current_text = []

    for line in content.split('\n'):
        line_stripped = line.strip()
        if line_stripped.startswith('**') and '**' in line_stripped[2:]:
            if current_section:
                sections[current_section] = '\n'.join(current_text).strip()
            header_end = line_stripped.find('**', 2) + 2
            if header_end < len(line_stripped):
                header = line_stripped[:header_end]
                content_on_same_line = line_stripped[header_end:].strip()
                current_section = header
                current_text = [content_on_same_line] if content_on_same_line else []
            else:
                current_section = line_stripped
                current_text = []
        elif line_stripped and current_section:
            current_text.append(line_stripped)

    if current_section:
        sections[current_section] = '\n'.join(current_text).strip()

    bg = sections.get('**Background Description:**', '').strip()
    return bg if bg else None


# ---------------------------------------------------------------------------
# VLM call
# ---------------------------------------------------------------------------

def call_vlm(
    image_path: str,
    background_description: str,
    ollama_url: str,
    model: str,
) -> str:
    """
    Send an image + background description to the VLM and return 'CLEAN' or 'FLAGGED'.

    Falls back to 'FLAGGED' on any parse error (fail-safe).

    Args:
        image_path:             Absolute path to the background image file.
        background_description: The background_description text to evaluate.
        ollama_url:             Base Ollama URL (e.g. 'http://10.0.0.179:11434').
        model:                  Ollama model name (e.g. 'qwen3-vl:32b').

    Returns:
        'CLEAN' or 'FLAGGED'

    Raises:
        OSError: If the image file cannot be read.
        urllib.error.URLError: If the Ollama endpoint is unreachable.
        ValueError: If the API response is malformed.
    """
    with open(image_path, 'rb') as f:
        image_b64 = base64.b64encode(f.read()).decode('utf-8')

    prompt = EVAL_PROMPT.format(background_description=background_description)

    payload = {
        'model': model,
        'prompt': prompt,
        'images': [image_b64],
        'stream': False,
    }

    url = f"{ollama_url.rstrip('/')}/api/generate"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    with urllib.request.urlopen(req, timeout=120) as response:
        body = json.loads(response.read().decode('utf-8'))

    raw = body.get('response', '').strip()

    # Parse first word — robust to reasoning tokens or trailing punctuation
    first_word = raw.split()[0].upper().rstrip('.,;:') if raw.split() else ''
    if first_word == 'CLEAN':
        return 'CLEAN'
    # Anything else (FLAGGED, garbage, empty) → FLAGGED (fail-safe)
    return 'FLAGGED'


# ---------------------------------------------------------------------------
# Portfolio scan
# ---------------------------------------------------------------------------

def scan_backgrounds(
    portfolio_root: str,
    ollama_url: str,
    model: str,
    include_clean: bool,
) -> RunStats:
    """
    Walk backgrounds/ under portfolio_root, evaluate each image, and collect results.

    Skips images with no description file or no background_description section.
    Logs skip counts to stdout. VLM errors are recorded in the result (verdict=FLAGGED).
    """
    stats = RunStats()
    backgrounds_dir = os.path.join(portfolio_root, 'backgrounds')

    if not os.path.isdir(backgrounds_dir):
        print(f"  ERROR: backgrounds/ not found at {backgrounds_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect all background image paths first (for progress display)
    image_paths = []
    for dirpath, dirnames, filenames in os.walk(backgrounds_dir):
        dirnames.sort()
        for filename in sorted(filenames):
            ext = os.path.splitext(filename)[1].lower()
            if ext in VALID_IMAGE_EXTENSIONS:
                image_paths.append(os.path.join(dirpath, filename))

    stats.total_images = len(image_paths)
    print(f"  Found {stats.total_images:,} background images.")

    for i, abs_image_path in enumerate(image_paths, 1):
        rel_path = os.path.relpath(abs_image_path, portfolio_root)
        filename = os.path.basename(abs_image_path)

        # Progress line (overwrites in place)
        print(f"\r  [{i:>5}/{stats.total_images}] {filename[:60]:<60}", end='', flush=True)

        # Check description file
        desc_file = get_description_file_path(abs_image_path)
        if not os.path.isfile(desc_file):
            stats.no_description += 1
            continue

        try:
            with open(desc_file, encoding='utf-8') as f:
                raw_content = f.read()
        except OSError:
            stats.no_description += 1
            continue

        bg_desc = parse_background_description(raw_content)
        if not bg_desc:
            stats.no_background_section += 1
            continue

        # Call VLM
        try:
            verdict = call_vlm(abs_image_path, bg_desc, ollama_url, model)
            error = None
        except Exception as e:
            verdict = 'FLAGGED'
            error = str(e)
            stats.vlm_errors += 1

        if verdict == 'FLAGGED':
            stats.flagged += 1
        else:
            stats.clean += 1

        if verdict == 'FLAGGED' or include_clean:
            stats.results.append(BackgroundCheckResult(
                background_path=rel_path,
                background_filename=filename,
                verdict=verdict,
                description_excerpt=bg_desc[:120],
                error=error,
            ))

    print()  # newline after progress
    return stats


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_csv(results: List[BackgroundCheckResult], output_path: str) -> None:
    """
    Write check results to a CSV file.

    Columns: background_path, background_filename, verdict, description_excerpt, error
    Silently overwrites if file already exists.
    """
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['background_path', 'background_filename', 'verdict',
                         'description_excerpt', 'error'])
        for r in results:
            writer.writerow([
                r.background_path,
                r.background_filename,
                r.verdict,
                r.description_excerpt,
                r.error or '',
            ])


# ---------------------------------------------------------------------------
# Targeted re-run for prior error rows
# ---------------------------------------------------------------------------

def rerun_error_rows(
    portfolio_root: str,
    input_csv: str,
    ollama_url: str,
    model: str,
    include_clean: bool,
) -> RunStats:
    """
    Re-process only the rows with VLM errors from a prior output CSV.

    Reads background_path and description_excerpt from the input CSV, resolves
    each image path against portfolio_root, and re-evaluates with the VLM.
    Rows that are missing on disk are skipped (counted as no_description).

    This is the recommended path after a batch run with timeouts — all timeout
    errors are retried with fresh VLM calls rather than assumed FLAGGED.

    Args:
        portfolio_root: Portfolio root directory (used to resolve background_path).
        input_csv:      Prior output CSV from this script (with an 'error' column).
        ollama_url:     Base Ollama URL.
        model:          Ollama model name.
        include_clean:  If True, include CLEAN results in returned stats.results.

    Returns:
        RunStats populated with results for the error rows only.
    """
    stats = RunStats()

    if not os.path.isfile(input_csv):
        print(f"  ERROR: Input CSV not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    with open(input_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    error_rows = [r for r in all_rows if r.get('error', '').strip()]
    stats.total_images = len(error_rows)

    if not error_rows:
        print(f"  No error rows found in {input_csv}. Nothing to re-run.")
        return stats

    print(f"  Found {stats.total_images} error row(s) to re-run.")

    for i, row in enumerate(error_rows, 1):
        rel_path = row['background_path']
        filename = os.path.basename(rel_path)
        abs_image_path = os.path.join(portfolio_root, rel_path)

        print(f"\r  [{i:>4}/{stats.total_images}] {filename[:60]:<60}", end='', flush=True)

        if not os.path.isfile(abs_image_path):
            stats.no_description += 1
            continue

        # Re-read description from disk (not from the CSV excerpt — excerpt is truncated)
        desc_file = get_description_file_path(abs_image_path)
        if not os.path.isfile(desc_file):
            stats.no_description += 1
            continue

        try:
            with open(desc_file, encoding='utf-8') as f:
                raw_content = f.read()
        except OSError:
            stats.no_description += 1
            continue

        bg_desc = parse_background_description(raw_content)
        if not bg_desc:
            stats.no_background_section += 1
            continue

        try:
            verdict = call_vlm(abs_image_path, bg_desc, ollama_url, model)
            error = None
        except Exception as e:
            verdict = 'FLAGGED'
            error = str(e)
            stats.vlm_errors += 1

        if verdict == 'FLAGGED':
            stats.flagged += 1
        else:
            stats.clean += 1

        if verdict == 'FLAGGED' or include_clean:
            stats.results.append(BackgroundCheckResult(
                background_path=rel_path,
                background_filename=filename,
                verdict=verdict,
                description_excerpt=bg_desc[:120],
                error=error,
            ))

    print()
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    default_portfolio = os.environ.get(
        'IMAGE_SEARCH_PORTFOLIO_PATH',
        '/Volumes/fvong/Portfolio',
    )

    parser = argparse.ArgumentParser(
        description='Check background image descriptions for human-subject language using a VLM.'
    )
    parser.add_argument(
        '--portfolio',
        default=default_portfolio,
        help=(
            'Portfolio root directory '
            '(default: $IMAGE_SEARCH_PORTFOLIO_PATH or /Volumes/fvong/Portfolio)'
        ),
    )
    parser.add_argument(
        '--output-csv',
        required=True,
        help='Output CSV path (required). Silently overwrites if file exists.',
    )
    parser.add_argument(
        '--ollama-url',
        default='http://10.0.0.179:11434',
        help='Ollama base URL (default: http://10.0.0.179:11434)',
    )
    parser.add_argument(
        '--model',
        default='qwen3-vl:32b',
        help='Ollama model name (default: qwen3-vl:32b)',
    )
    parser.add_argument(
        '--include-clean',
        action='store_true',
        help='Include CLEAN results in the output CSV (default: flagged only)',
    )
    parser.add_argument(
        '--rerun-errors-csv',
        default=None,
        metavar='CSV',
        help=(
            'Re-run only the rows with VLM errors from a prior output CSV. '
            'Reads background_path and error columns; processes only those images. '
            'Useful after timeouts to confirm false-positive status.'
        ),
    )
    args = parser.parse_args()

    portfolio = os.path.expanduser(args.portfolio)
    if not os.path.isdir(portfolio):
        print(f"ERROR: Portfolio directory not found: {portfolio}", file=sys.stderr)
        sys.exit(1)

    print(f"Background description check")
    print(f"  Portfolio: {portfolio}")
    print(f"  Model:     {args.model} @ {args.ollama_url}")

    if args.rerun_errors_csv:
        print(f"Re-running error rows from: {args.rerun_errors_csv}")
        stats = rerun_error_rows(
            portfolio, args.rerun_errors_csv, args.ollama_url, args.model, args.include_clean
        )
    else:
        print(f"Scanning backgrounds/ ...")
        stats = scan_backgrounds(portfolio, args.ollama_url, args.model, args.include_clean)

    label = "error rows re-run" if args.rerun_errors_csv else "images"
    print(f"\nResults:")
    print(f"  Total {label}:".ljust(28) + f"{stats.total_images:>6,}")
    print(f"  No description file:    {stats.no_description:>6,}  (skipped)")
    print(f"  No background section:  {stats.no_background_section:>6,}  (skipped)")
    print(f"  VLM errors:             {stats.vlm_errors:>6,}  (counted as FLAGGED)")
    print(f"  FLAGGED:                {stats.flagged:>6,}")
    print(f"  CLEAN:                  {stats.clean:>6,}")

    write_csv(stats.results, args.output_csv)
    flagged_written = sum(1 for r in stats.results if r.verdict == 'FLAGGED')
    print(f"\nWrote {flagged_written:,} flagged background(s) to: {args.output_csv}")

    if stats.vlm_errors:
        print(f"\nWARNING: {stats.vlm_errors} VLM errors occurred. "
              f"Check the 'error' column in the CSV for details.", file=sys.stderr)


if __name__ == '__main__':
    main()
