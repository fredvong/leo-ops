# Copyright (c) 2026 Fred Vong. All rights reserved.

"""
Tests for background_rejection_report.py

Uses tmp_path fixtures to create fake portfolio trees — no real NAS access.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the script under test
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))
import background_rejection_report as br

SCRIPT_PATH = Path(__file__).parent / 'background_rejection_report.py'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_portfolio(base: Path) -> Path:
    """Create a minimal portfolio directory structure."""
    portfolio = base / 'portfolio'
    portfolio.mkdir()
    (portfolio / 'backgrounds').mkdir()
    return portfolio


def make_session(portfolio: Path, year: str, session: str) -> Path:
    """Create a year/session directory pair."""
    session_dir = portfolio / year / session
    session_dir.mkdir(parents=True)
    return session_dir


def make_bg(portfolio: Path, subdir: str, filename: str) -> Path:
    """Create a background image file under backgrounds/."""
    bg_dir = portfolio / 'backgrounds' / subdir
    bg_dir.mkdir(parents=True, exist_ok=True)
    bg_file = bg_dir / filename
    bg_file.touch()
    return bg_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_portfolio(tmp_path):
    """No augmented images → stats is empty, CSV has header only."""
    portfolio = make_portfolio(tmp_path)
    make_session(portfolio, '2025', '2025-01-01 Test')

    stats = br.scan_portfolio(str(portfolio))
    assert stats == {}

    output_csv = tmp_path / 'out.csv'
    br.write_csv([], str(output_csv))
    lines = output_csv.read_text().splitlines()
    assert lines == ['background_filename,total,kept,rejected,has_liked']


def test_background_not_found_on_disk(tmp_path, capsys):
    """Stem present in stats but missing from backgrounds/ → resolved_path stays None, row skipped."""
    portfolio = make_portfolio(tmp_path)
    session = make_session(portfolio, '2025', '2025-01-01 Test')
    (session / 'img___missing_bg.png').touch()

    stats = br.scan_portfolio(str(portfolio))
    assert 'missing_bg' in stats
    assert stats['missing_bg'].kept == 1

    br.resolve_background_paths(stats, str(portfolio))
    assert stats['missing_bg'].resolved_path is None

    captured = capsys.readouterr()
    assert 'missing_bg' in captured.out
    assert 'not found' in captured.out

    # filter_and_sort drops rows with resolved_path=None
    rows = br.filter_and_sort(stats, min_total=1, max_kept=99, include_liked=True)
    assert rows == []


def test_stem_in_multiple_dirs(tmp_path, capsys):
    """Duplicate background filename across subdirs → error logged, first alphabetical match used."""
    portfolio = make_portfolio(tmp_path)
    session = make_session(portfolio, '2025', '2025-01-01 Test')
    (session / 'img___dup_bg.png').touch()

    # Same stem in two subdirectories
    make_bg(portfolio, 'alpha', 'dup_bg.jpg')
    make_bg(portfolio, 'zeta', 'dup_bg.jpg')

    stats = br.scan_portfolio(str(portfolio))
    br.resolve_background_paths(stats, str(portfolio))

    captured = capsys.readouterr()
    assert 'duplicate' in captured.out.lower()
    assert 'dup_bg' in captured.out

    # First alphabetical match is used
    assert stats['dup_bg'].resolved_path is not None
    assert 'alpha' in stats['dup_bg'].resolved_path


def test_has_liked_detection(tmp_path):
    """
    Censored and PSD siblings in session dir set has_liked=True.
    Censored files in .trash/ do NOT set has_liked.
    """
    portfolio = make_portfolio(tmp_path)

    # Session 1: PSD sibling → has_liked
    s1 = make_session(portfolio, '2025', '2025-01-01 PSD')
    (s1 / 'img___psd_bg.png').touch()
    (s1 / 'img___psd_bg.psd').touch()

    # Session 2: Censored sibling → has_liked
    s2 = make_session(portfolio, '2025', '2025-01-02 Censored')
    (s2 / 'img___censored_bg.png').touch()
    (s2 / 'img___censored_bg-Censored.png').touch()

    # Session 3: Censored only in .trash/ → should NOT trigger has_liked
    s3 = make_session(portfolio, '2025', '2025-01-03 TrashCensored')
    (s3 / 'img___trash_liked_bg.png').touch()
    trash = s3 / '.trash'
    trash.mkdir()
    (trash / 'img___trash_liked_bg-Censored.png').touch()

    stats = br.scan_portfolio(str(portfolio))

    assert stats['psd_bg'].has_liked is True
    assert stats['censored_bg'].has_liked is True
    assert stats['trash_liked_bg'].has_liked is False


def test_threshold_edge_cases(tmp_path):
    """
    Boundary conditions for min_total and max_kept.

    min_total=50 is inclusive: total==50 passes, total==49 fails.
    max_kept=2 is exclusive: kept==1 passes, kept==2 fails.
    """
    portfolio = make_portfolio(tmp_path)

    def make_bg_with_counts(stem: str, kept: int, rejected: int) -> None:
        """Create fake stats by directly populating session files."""
        session = make_session(portfolio, '2025', f'2025-01-01 {stem}')
        for i in range(kept):
            (session / f'img{i}___{stem}.png').touch()
        trash = session / '.trash'
        trash.mkdir()
        for i in range(rejected):
            (trash / f'img{i}___{stem}.png').touch()
        make_bg(portfolio, 'ideas', f'{stem}.jpg')

    # Exactly at min_total boundary
    make_bg_with_counts('exactly50', kept=0, rejected=50)   # total=50, should appear
    make_bg_with_counts('only49', kept=0, rejected=49)      # total=49, should not appear

    # At max_kept boundary
    make_bg_with_counts('kept1', kept=1, rejected=50)       # kept=1 < 2, should appear
    make_bg_with_counts('kept2', kept=2, rejected=50)       # kept=2 >= 2, should not appear

    stats = br.scan_portfolio(str(portfolio))
    br.resolve_background_paths(stats, str(portfolio))
    rows = br.filter_and_sort(stats, min_total=50, max_kept=2, include_liked=True)

    stems_in_output = {stem for stem, _ in rows}
    assert 'exactly50' in stems_in_output
    assert 'only49' not in stems_in_output
    assert 'kept1' in stems_in_output
    assert 'kept2' not in stems_in_output


def test_max_kept_pct_filters_correctly(tmp_path):
    """
    --max-kept-pct filters by percentage: (kept/total)*100 <= threshold passes.

    Uses max_kept=9999 to disable the absolute gate so the pct gate is the
    only active filter.
    """
    portfolio = make_portfolio(tmp_path)

    def make_bg_with_counts(stem: str, kept: int, rejected: int) -> None:
        session = make_session(portfolio, '2025', f'2025-01-01 {stem}')
        for i in range(kept):
            (session / f'img{i}___{stem}.png').touch()
        trash = session / '.trash'
        trash.mkdir()
        for i in range(rejected):
            (trash / f'img{i}___{stem}.png').touch()
        make_bg(portfolio, 'ideas', f'{stem}.jpg')

    # 0/10 = 0% kept → passes 10% gate
    make_bg_with_counts('zero_pct', kept=0, rejected=10)
    # 1/10 = 10% kept → exactly at gate (inclusive) → passes
    make_bg_with_counts('ten_pct', kept=1, rejected=9)
    # 2/10 = 20% kept → above gate → filtered out
    make_bg_with_counts('twenty_pct', kept=2, rejected=8)

    stats = br.scan_portfolio(str(portfolio))
    br.resolve_background_paths(stats, str(portfolio))
    rows = br.filter_and_sort(
        stats, min_total=5, max_kept=9999, include_liked=True, max_kept_pct=10.0
    )

    stems_in_output = {stem for stem, _ in rows}
    assert 'zero_pct' in stems_in_output
    assert 'ten_pct' in stems_in_output
    assert 'twenty_pct' not in stems_in_output


def test_max_kept_pct_and_max_kept_both_must_pass(tmp_path):
    """
    When both gates are active, a background must satisfy both to appear.

    bg_a: kept=0/10 → passes pct (0%), passes absolute (0 < 2) → IN
    bg_b: kept=1/10 → passes pct (10%), passes absolute (1 < 2) → IN
    bg_c: kept=2/10 → fails absolute (2 >= 2) → OUT even though 20% might pass a looser pct gate
    bg_d: kept=3/10 → passes absolute (3 < 5), but fails pct (30% > 10%) → OUT
    """
    portfolio = make_portfolio(tmp_path)

    def make_bg_with_counts(stem: str, kept: int, rejected: int) -> None:
        session = make_session(portfolio, '2025', f'2025-01-01 {stem}')
        for i in range(kept):
            (session / f'img{i}___{stem}.png').touch()
        trash = session / '.trash'
        trash.mkdir()
        for i in range(rejected):
            (trash / f'img{i}___{stem}.png').touch()
        make_bg(portfolio, 'ideas', f'{stem}.jpg')

    make_bg_with_counts('bg_a', kept=0, rejected=10)
    make_bg_with_counts('bg_b', kept=1, rejected=9)
    make_bg_with_counts('bg_c', kept=2, rejected=8)
    make_bg_with_counts('bg_d', kept=3, rejected=7)

    stats = br.scan_portfolio(str(portfolio))
    br.resolve_background_paths(stats, str(portfolio))
    rows = br.filter_and_sort(
        stats, min_total=5, max_kept=2, include_liked=True, max_kept_pct=10.0
    )

    stems_in_output = {stem for stem, _ in rows}
    assert 'bg_a' in stems_in_output
    assert 'bg_b' in stems_in_output
    assert 'bg_c' not in stems_in_output
    assert 'bg_d' not in stems_in_output


def test_max_kept_pct_none_does_not_affect_existing_behaviour(tmp_path):
    """
    Passing max_kept_pct=None (the default) leaves existing filter behaviour unchanged.
    """
    portfolio = make_portfolio(tmp_path)
    session = make_session(portfolio, '2025', '2025-01-01 Test')
    for i in range(3):
        (session / f'img{i}___hi_yield_bg.png').touch()
    trash = session / '.trash'
    trash.mkdir()
    for i in range(7):
        (trash / f'img{i}___hi_yield_bg.png').touch()
    make_bg(portfolio, 'ideas', 'hi_yield_bg.jpg')

    stats = br.scan_portfolio(str(portfolio))
    br.resolve_background_paths(stats, str(portfolio))

    # With no pct gate, kept=3 < max_kept=5 → appears
    rows_no_pct = br.filter_and_sort(
        stats, min_total=5, max_kept=5, include_liked=True, max_kept_pct=None
    )
    assert any(stem == 'hi_yield_bg' for stem, _ in rows_no_pct)


def test_max_kept_pct_out_of_range_exits(tmp_path):
    """Invoking the script with --max-kept-pct outside 0–100 must exit non-zero."""
    import subprocess
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            '--portfolio', str(tmp_path),
            '--output-csv', str(tmp_path / 'out.csv'),
            '--max-kept-pct', '101',
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b'--max-kept-pct' in result.stderr


def test_missing_output_csv_exits(tmp_path):
    """Invoking the script without --output-csv must exit with a non-zero code."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--portfolio', str(tmp_path)],
        capture_output=True,
    )
    assert result.returncode != 0


def test_script_is_read_only():
    """
    Verify the script contains no write operations that could target the portfolio.

    Scans the source for forbidden Python write patterns. Lines that contain
    'output_path' are exempt — the only permitted write is to the --output-csv
    file, which is handled by write_csv() and always references output_path.
    """
    source = SCRIPT_PATH.read_text()

    FORBIDDEN_PATTERNS = [
        (r'\bopen\s*\([^)]*["\'](?:w|a|x|wb|ab|xb)["\']', "write-mode open()"),
        (r'\bos\.remove\s*\(', "os.remove"),
        (r'\bos\.unlink\s*\(', "os.unlink"),
        (r'\bos\.rename\s*\(', "os.rename"),
        (r'\bos\.makedirs\s*\(', "os.makedirs"),
        (r'\bos\.mkdir\s*\(', "os.mkdir"),
        (r'\bshutil\.(copy|move|rmtree)\s*\(', "shutil write op"),
    ]

    for line in source.splitlines():
        # The only permitted write is to output_path (the --output-csv argument)
        if 'output_path' in line:
            continue
        for pattern, name in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, line), (
                f"Forbidden write pattern '{name}' found in script source:\n"
                f"  {line.strip()}"
            )
