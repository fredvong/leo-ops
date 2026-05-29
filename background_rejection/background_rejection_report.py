# Copyright (c) 2026 Fred Vong. All rights reserved.

"""
Background Rejection Report

Scans the portfolio filesystem for background images that consistently
produce rejected (trashed) augmented images. Outputs a ranked CSV so Leo
can open the worst offenders in Background Studio and improve their AI
descriptions.

Usage:
    python3 background_rejection_report.py \\
        --portfolio ~/Portfolio \\
        --output-csv data/rejected_backgrounds.csv \\
        [--min-total 50] \\
        [--max-kept 2] \\
        [--max-kept-pct 10.0] \\
        [--include-liked]

No dependencies beyond the Python standard library.
"""

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Patterns — must match FILE_STRUCTURE_AND_NAMING.md exactly
# ---------------------------------------------------------------------------

# Augmented filenames: {input}___{background}[.vN].png
# Triple-underscore is the definitive augmented-image marker.
AUGMENTED_PATTERN = re.compile(
    r'^(.+?)___(.+?)(?:\.v\d+)?\.png$',
    re.IGNORECASE,
)

# "Liked" siblings: a Censored or PSD version derived from an augmented image,
# indicating Leo already produced a result good enough to process further.
#   PSD:      {input}___{background}[.vN].psd
#   Censored: {input}___{background}[.vN]-Censored.[png|jpg|jpeg]
LIKED_PATTERN = re.compile(
    r'^.+?___(.+?)(?:\.v\d+)?(?:\.psd|-Censored\.(?:png|jpg|jpeg))$',
    re.IGNORECASE,
)

# Only scan exactly-4-digit year directories at the portfolio root.
YEAR_DIR_PATTERN = re.compile(r'^\d{4}$')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundStats:
    kept: int = 0
    rejected: int = 0
    has_liked: bool = False
    resolved_path: Optional[str] = None  # relative path from portfolio root; internal only

    @property
    def total(self) -> int:
        """Derived: kept + rejected. Not stored separately."""
        return self.kept + self.rejected


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def _process_session(
    stats: Dict[str, BackgroundStats],
    session_files: List[str],
    trash_files: List[str],
) -> None:
    """
    Update stats for one session directory.

    session_files: filenames directly in the session dir (non-trash).
    trash_files:   filenames in the session's .trash/ subdirectory.
    """
    for filename in session_files:
        aug_match = AUGMENTED_PATTERN.match(filename)
        if aug_match:
            stem = aug_match.group(2)
            if stem not in stats:
                stats[stem] = BackgroundStats()
            stats[stem].kept += 1

        liked_match = LIKED_PATTERN.match(filename)
        if liked_match:
            stem = liked_match.group(1)
            if stem not in stats:
                stats[stem] = BackgroundStats()
            stats[stem].has_liked = True

    for filename in trash_files:
        aug_match = AUGMENTED_PATTERN.match(filename)
        if aug_match:
            stem = aug_match.group(2)
            if stem not in stats:
                stats[stem] = BackgroundStats()
            stats[stem].rejected += 1
        # Censored/PSD files in .trash/ do NOT trigger has_liked.


def scan_portfolio(image_root: str) -> Dict[str, BackgroundStats]:
    """
    Single-pass scan of the portfolio directory tree.

    Enters only 4-digit year directories at the root. For each session
    subdirectory, collects kept files (directly in the session dir) and
    rejected files (in its .trash/ subdirectory).

    Skips hidden directories (e.g. .description, .trash) at the year level.

    Returns a dict mapping background stem → BackgroundStats.
    """
    stats: Dict[str, BackgroundStats] = {}

    for entry in sorted(os.scandir(image_root), key=lambda e: e.name):
        if not entry.is_dir() or not YEAR_DIR_PATTERN.match(entry.name):
            continue

        for session_entry in sorted(os.scandir(entry.path), key=lambda e: e.name):
            if not session_entry.is_dir() or session_entry.name.startswith('.'):
                continue

            session_dir = session_entry.path
            session_files: List[str] = []
            trash_files: List[str] = []

            for f in os.scandir(session_dir):
                if f.is_file():
                    session_files.append(f.name)

            trash_dir = os.path.join(session_dir, '.trash')
            if os.path.isdir(trash_dir):
                for f in os.scandir(trash_dir):
                    if f.is_file():
                        trash_files.append(f.name)

            _process_session(stats, session_files, trash_files)

    return stats


def resolve_background_paths(
    stats: Dict[str, BackgroundStats],
    image_root: str,
) -> None:
    """
    Resolve each background stem to its current file path under backgrounds/.

    Performs a full recursive walk of backgrounds/ — no subdirectory filter,
    because the internal structure is arbitrary and the user may reorganise
    at will. Filename uniqueness across the entire tree is guaranteed by
    contract.

    Mutates stats in place, setting resolved_path on each entry.
    Logs errors to stdout for stems that are missing or duplicated.
    """
    backgrounds_dir = os.path.join(image_root, 'backgrounds')
    if not os.path.isdir(backgrounds_dir):
        print(f"  WARNING: backgrounds/ directory not found at {backgrounds_dir}")
        return

    # Collect all stem → [relative paths] across the entire backgrounds/ tree.
    path_index: Dict[str, List[str]] = {}
    for dirpath, dirnames, filenames in os.walk(backgrounds_dir):
        dirnames.sort()  # deterministic traversal order
        for filename in filenames:
            stem = os.path.splitext(filename)[0]
            rel_path = os.path.relpath(
                os.path.join(dirpath, filename), image_root
            )
            if stem not in path_index:
                path_index[stem] = []
            path_index[stem].append(rel_path)

    # Sort each bucket so first-match is alphabetically stable.
    for paths in path_index.values():
        paths.sort()

    for stem, bg_stats in stats.items():
        paths = path_index.get(stem, [])
        if not paths:
            print(f"  ERROR: background '{stem}' not found in backgrounds/ — skipping.")
        else:
            if len(paths) > 1:
                print(
                    f"  ERROR: duplicate background stem '{stem}' found in "
                    f"{len(paths)} locations — using '{paths[0]}'."
                )
            bg_stats.resolved_path = paths[0]


def filter_and_sort(
    stats: Dict[str, BackgroundStats],
    min_total: int,
    max_kept: int,
    include_liked: bool,
    max_kept_pct: Optional[float] = None,
) -> List[Tuple[str, BackgroundStats]]:
    """
    Filter and sort backgrounds by acceptance rate (worst first).

    Inclusion criteria (all must be satisfied):
    - total >= min_total              — enough attempts to be statistically meaningful
    - kept < max_kept                 — absolute keeper count below threshold
    - if max_kept_pct is not None:
      (kept / total) * 100 <= max_kept_pct  — percentage gate (0–100)
    - resolved_path is not None       — file confirmed to exist on disk
    - has_liked is False, unless include_liked is True

    When both max_kept and max_kept_pct are provided, a background must satisfy
    both gates to appear in the output.
    """
    rows = []
    for stem, bg_stats in stats.items():
        if bg_stats.total < min_total:
            continue
        if bg_stats.kept >= max_kept:
            continue
        if max_kept_pct is not None:
            if (bg_stats.kept / bg_stats.total) * 100 > max_kept_pct:
                continue
        if bg_stats.resolved_path is None:
            continue
        if bg_stats.has_liked and not include_liked:
            continue
        rows.append((stem, bg_stats))

    # Sort ascending by kept/total — fewest keepers relative to total first.
    rows.sort(key=lambda x: x[1].kept / x[1].total)
    return rows


def write_csv(rows: List[Tuple[str, BackgroundStats]], output_path: str) -> None:
    """
    Write the ranked report to a CSV file.

    Columns: background_filename, total, kept, rejected, has_liked
    background_filename is the bare filename (e.g. 'sunset beach.jpg') —
    the stable identity. Relative path is intentionally omitted; it is not
    a stable contract and will go stale if the background is moved.

    has_liked indicates whether any augmented image for this background has a
    Censored or PSD sibling in a session directory — meaning the user found at
    least one result good enough to process further.

    Silently overwrites if the file already exists.
    """
    with open(output_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['background_filename', 'total', 'kept', 'rejected', 'has_liked'])
        for _stem, bg_stats in rows:
            filename = os.path.basename(bg_stats.resolved_path)
            writer.writerow([
                filename,
                bg_stats.total,
                bg_stats.kept,
                bg_stats.rejected,
                bg_stats.has_liked,
            ])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    default_portfolio = os.environ.get(
        'IMAGE_SEARCH_PORTFOLIO_PATH',
        os.path.expanduser('~/Portfolio'),
    )

    parser = argparse.ArgumentParser(
        description='Identify low-yield background images for description improvement.'
    )
    parser.add_argument(
        '--portfolio',
        default=default_portfolio,
        help=(
            'Portfolio root directory '
            '(default: $IMAGE_SEARCH_PORTFOLIO_PATH or ~/Portfolio)'
        ),
    )
    parser.add_argument(
        '--output-csv',
        required=True,
        help='Output CSV path (required). Silently overwrites if file exists.',
    )
    parser.add_argument(
        '--min-total',
        type=int,
        default=50,
        help='Minimum total augmented images for a background to appear (default: 50)',
    )
    parser.add_argument(
        '--max-kept',
        type=int,
        default=2,
        help=(
            'Maximum kept images (exclusive) — default 2 catches backgrounds '
            'with 0 or 1 keepers'
        ),
    )
    parser.add_argument(
        '--max-kept-pct',
        type=float,
        default=None,
        help=(
            'Maximum kept percentage gate (0–100). If provided, only backgrounds '
            'with (kept / total) * 100 <= MAX_KEPT_PCT are flagged. '
            'Can be combined with --max-kept; both gates must be satisfied.'
        ),
    )
    parser.add_argument(
        '--include-liked',
        action='store_true',
        help=(
            'Include backgrounds that have Censored/PSD siblings '
            '(default: skip them)'
        ),
    )
    args = parser.parse_args()

    if args.max_kept_pct is not None and not (0.0 <= args.max_kept_pct <= 100.0):
        print(
            f"ERROR: --max-kept-pct must be between 0 and 100, got {args.max_kept_pct}",
            file=sys.stderr,
        )
        sys.exit(1)

    portfolio = os.path.expanduser(args.portfolio)
    if not os.path.isdir(portfolio):
        print(f"ERROR: Portfolio directory not found: {portfolio}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning portfolio at {portfolio} ...")
    stats = scan_portfolio(portfolio)
    total_augmented = sum(s.total for s in stats.values())
    print(
        f"  Found {total_augmented:,} augmented images "
        f"across {len(stats):,} unique backgrounds."
    )

    resolve_background_paths(stats, portfolio)

    liked_skipped = sum(
        1 for s in stats.values()
        if s.has_liked and s.resolved_path is not None
    )
    if not args.include_liked and liked_skipped > 0:
        print(
            f"  Skipped {liked_skipped} backgrounds with Censored/PSD files "
            f"(use --include-liked to override)."
        )

    filter_desc = f"total >= {args.min_total}, kept < {args.max_kept}"
    if args.max_kept_pct is not None:
        filter_desc += f", kept/total <= {args.max_kept_pct}%"
    print(f"Applying filter: {filter_desc}")
    rows = filter_and_sort(
        stats, args.min_total, args.max_kept, args.include_liked, args.max_kept_pct
    )

    eligible = sum(
        1 for s in stats.values()
        if s.total >= args.min_total
        and s.resolved_path is not None
        and (args.include_liked or not s.has_liked)
    )
    print(f"  Flagged {len(rows)} backgrounds out of {eligible} total.")

    write_csv(rows, args.output_csv)
    print(f"Report written to: {args.output_csv}")


if __name__ == '__main__':
    main()
