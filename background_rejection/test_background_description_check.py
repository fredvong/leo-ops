# Copyright (c) 2026 Fred Vong. All rights reserved.

"""
Tests for background_description_check.py

Uses tmp_path fixtures — no real NAS, VLM, or network access.
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import background_description_check as bdc

SCRIPT_PATH = Path(__file__).parent / 'background_description_check.py'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_description_file(image_path: Path, subject: str = "A subject.", background: str = "") -> None:
    """Write a two-section description file alongside the image."""
    desc_dir = image_path.parent / '.description'
    desc_dir.mkdir(parents=True, exist_ok=True)
    content = f"**Subject Description:**\n{subject}"
    if background:
        content += f"\n\n**Background Description:**\n{background}"
    (desc_dir / f"{image_path.name}.txt").write_text(content, encoding='utf-8')


# ---------------------------------------------------------------------------
# parse_background_description
# ---------------------------------------------------------------------------

class TestParseBackgroundDescription:

    def test_both_sections_returns_background(self):
        content = (
            "**Subject Description:**\nA person.\n\n"
            "**Background Description:**\nA sunlit forest overhead."
        )
        result = bdc.parse_background_description(content)
        assert result == "A sunlit forest overhead."

    def test_no_background_section_returns_none(self):
        content = "**Subject Description:**\nA person."
        assert bdc.parse_background_description(content) is None

    def test_empty_background_section_returns_none(self):
        content = "**Subject Description:**\nA person.\n\n**Background Description:**\n"
        assert bdc.parse_background_description(content) is None

    def test_background_only_returns_text(self):
        content = "**Background Description:**\nA misty forest scene."
        result = bdc.parse_background_description(content)
        assert result == "A misty forest scene."

    def test_multiline_background_preserved(self):
        content = (
            "**Background Description:**\n"
            "A golden forest.\n"
            "Tall ancient trees."
        )
        result = bdc.parse_background_description(content)
        assert "golden forest" in result
        assert "ancient trees" in result


# ---------------------------------------------------------------------------
# call_vlm response parsing
# ---------------------------------------------------------------------------

class TestCallVlmParsing:
    """
    Verify the response-parsing branch of call_vlm without a real Ollama endpoint.
    We patch urllib.request.urlopen to return controlled responses.
    """

    def _make_response(self, text: str):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": text}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _call(self, response_text: str, tmp_path) -> str:
        img = tmp_path / "bg.jpg"
        img.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header
        with patch("urllib.request.urlopen", return_value=self._make_response(response_text)):
            return bdc.call_vlm(str(img), "A scene.", "http://localhost:11434", "qwen3-vl:32b")

    def test_clean_response(self, tmp_path):
        assert self._call("CLEAN", tmp_path) == "CLEAN"

    def test_flagged_response(self, tmp_path):
        assert self._call("FLAGGED", tmp_path) == "FLAGGED"

    def test_clean_with_trailing_punctuation(self, tmp_path):
        assert self._call("CLEAN.", tmp_path) == "CLEAN"

    def test_flagged_with_reasoning_prefix(self, tmp_path):
        # VLM may prepend reasoning tokens before the answer
        assert self._call("FLAGGED because the description mentions 'her attire'", tmp_path) == "FLAGGED"

    def test_garbage_response_defaults_to_flagged(self, tmp_path):
        assert self._call("I'm not sure", tmp_path) == "FLAGGED"

    def test_empty_response_defaults_to_flagged(self, tmp_path):
        assert self._call("", tmp_path) == "FLAGGED"

    def test_lowercase_clean(self, tmp_path):
        assert self._call("clean", tmp_path) == "CLEAN"


# ---------------------------------------------------------------------------
# scan_backgrounds — skip logic
# ---------------------------------------------------------------------------

class TestScanBackgrounds:

    def _mock_vlm(self, *args, **kwargs):
        return "CLEAN"

    def test_skips_image_with_no_description_file(self, tmp_path):
        portfolio = tmp_path / "portfolio"
        (portfolio / "backgrounds" / "ideas").mkdir(parents=True)
        (portfolio / "backgrounds" / "ideas" / "photo.jpg").write_bytes(b"fake")

        with patch("background_description_check.call_vlm", side_effect=self._mock_vlm):
            stats = bdc.scan_backgrounds(str(portfolio), "http://localhost:11434", "model", False)

        assert stats.no_description == 1
        assert stats.total_images == 1
        assert stats.clean == 0
        assert stats.flagged == 0

    def test_skips_image_with_no_background_section(self, tmp_path):
        portfolio = tmp_path / "portfolio"
        img = portfolio / "backgrounds" / "ideas" / "photo.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fake")
        make_description_file(img, subject="A subject.", background="")

        with patch("background_description_check.call_vlm", side_effect=self._mock_vlm):
            stats = bdc.scan_backgrounds(str(portfolio), "http://localhost:11434", "model", False)

        assert stats.no_background_section == 1
        assert stats.clean == 0

    def test_flagged_result_included_in_output(self, tmp_path):
        portfolio = tmp_path / "portfolio"
        img = portfolio / "backgrounds" / "ideas" / "photo.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fake")
        make_description_file(img, background="A forest above the woman.")

        with patch("background_description_check.call_vlm", return_value="FLAGGED"):
            stats = bdc.scan_backgrounds(str(portfolio), "http://localhost:11434", "model", False)

        assert stats.flagged == 1
        assert len(stats.results) == 1
        assert stats.results[0].verdict == "FLAGGED"
        assert "photo.jpg" in stats.results[0].background_filename

    def test_clean_result_excluded_unless_include_clean(self, tmp_path):
        portfolio = tmp_path / "portfolio"
        img = portfolio / "backgrounds" / "ideas" / "photo.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fake")
        make_description_file(img, background="A sunlit forest overhead.")

        with patch("background_description_check.call_vlm", return_value="CLEAN"):
            stats_no_clean = bdc.scan_backgrounds(
                str(portfolio), "http://localhost:11434", "model", include_clean=False
            )
            stats_with_clean = bdc.scan_backgrounds(
                str(portfolio), "http://localhost:11434", "model", include_clean=True
            )

        assert len(stats_no_clean.results) == 0
        assert len(stats_with_clean.results) == 1
        assert stats_with_clean.results[0].verdict == "CLEAN"

    def test_vlm_error_counts_as_flagged(self, tmp_path):
        portfolio = tmp_path / "portfolio"
        img = portfolio / "backgrounds" / "ideas" / "photo.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fake")
        make_description_file(img, background="A forest scene.")

        with patch("background_description_check.call_vlm",
                   side_effect=Exception("Connection refused")):
            stats = bdc.scan_backgrounds(str(portfolio), "http://localhost:11434", "model", False)

        assert stats.vlm_errors == 1
        assert stats.flagged == 1
        assert stats.results[0].error == "Connection refused"


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------

class TestWriteCsv:

    def test_csv_has_header_and_row(self, tmp_path):
        result = bdc.BackgroundCheckResult(
            background_path="backgrounds/ideas/photo.jpg",
            background_filename="photo.jpg",
            verdict="FLAGGED",
            description_excerpt="A forest above the woman.",
            error=None,
        )
        output = tmp_path / "out.csv"
        bdc.write_csv([result], str(output))
        lines = output.read_text().splitlines()
        assert lines[0] == "background_path,background_filename,verdict,description_excerpt,error"
        assert "FLAGGED" in lines[1]
        assert "photo.jpg" in lines[1]

    def test_empty_results_writes_header_only(self, tmp_path):
        output = tmp_path / "out.csv"
        bdc.write_csv([], str(output))
        lines = output.read_text().splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("background_path")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_missing_output_csv_exits(tmp_path):
    """Invoking the script without --output-csv must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--portfolio', str(tmp_path)],
        capture_output=True,
    )
    assert result.returncode != 0


def test_missing_portfolio_exits(tmp_path):
    """Invoking with a non-existent --portfolio must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH),
         '--portfolio', str(tmp_path / 'nonexistent'),
         '--output-csv', str(tmp_path / 'out.csv')],
        capture_output=True,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# rerun_error_rows
# ---------------------------------------------------------------------------

class TestRerunErrorRows:
    """Tests for the targeted re-run path (--rerun-errors-csv)."""

    def _make_portfolio(self, tmp_path: Path, background: str = "A misty forest.") -> tuple:
        """Create a minimal portfolio with one background image + description."""
        portfolio = tmp_path / "portfolio"
        img = portfolio / "backgrounds" / "ideas" / "photo.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"\xff\xd8\xff")
        make_description_file(img, background=background)
        return portfolio, img

    def _make_error_csv(self, tmp_path: Path, portfolio: Path, img: Path, error: str = "timed out") -> Path:
        """Write a prior flagged CSV with one error row."""
        csv_path = tmp_path / "flagged.csv"
        rel = str(img.relative_to(portfolio))
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['background_path', 'background_filename', 'verdict',
                             'description_excerpt', 'error'])
            writer.writerow([rel, img.name, 'FLAGGED', 'A misty forest.', error])
        return csv_path

    def test_error_row_retried_and_comes_back_clean(self, tmp_path):
        portfolio, img = self._make_portfolio(tmp_path)
        csv_path = self._make_error_csv(tmp_path, portfolio, img)

        with patch("background_description_check.call_vlm", return_value="CLEAN"):
            stats = bdc.rerun_error_rows(
                str(portfolio), str(csv_path), "http://localhost:11434", "model", True
            )

        assert stats.total_images == 1
        assert stats.clean == 1
        assert stats.flagged == 0
        assert stats.vlm_errors == 0
        assert stats.results[0].verdict == "CLEAN"

    def test_error_row_still_times_out_counts_as_flagged(self, tmp_path):
        portfolio, img = self._make_portfolio(tmp_path)
        csv_path = self._make_error_csv(tmp_path, portfolio, img)

        with patch("background_description_check.call_vlm",
                   side_effect=Exception("timed out")):
            stats = bdc.rerun_error_rows(
                str(portfolio), str(csv_path), "http://localhost:11434", "model", False
            )

        assert stats.vlm_errors == 1
        assert stats.flagged == 1
        assert stats.results[0].error == "timed out"

    def test_no_error_rows_returns_empty_stats(self, tmp_path):
        portfolio, img = self._make_portfolio(tmp_path)
        # CSV with no error column values
        csv_path = tmp_path / "flagged.csv"
        rel = str(img.relative_to(portfolio))
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['background_path', 'background_filename', 'verdict',
                             'description_excerpt', 'error'])
            writer.writerow([rel, img.name, 'FLAGGED', 'A forest.', ''])  # no error

        with patch("background_description_check.call_vlm", return_value="FLAGGED"):
            stats = bdc.rerun_error_rows(
                str(portfolio), str(csv_path), "http://localhost:11434", "model", False
            )

        assert stats.total_images == 0
        assert len(stats.results) == 0

    def test_missing_image_on_disk_counted_as_no_description(self, tmp_path):
        portfolio, img = self._make_portfolio(tmp_path)
        csv_path = self._make_error_csv(tmp_path, portfolio, img)
        img.unlink()  # delete the actual image

        stats = bdc.rerun_error_rows(
            str(portfolio), str(csv_path), "http://localhost:11434", "model", False
        )

        assert stats.no_description == 1
        assert stats.total_images == 1
        assert stats.clean == 0

    def test_clean_excluded_without_include_clean(self, tmp_path):
        portfolio, img = self._make_portfolio(tmp_path)
        csv_path = self._make_error_csv(tmp_path, portfolio, img)

        with patch("background_description_check.call_vlm", return_value="CLEAN"):
            stats = bdc.rerun_error_rows(
                str(portfolio), str(csv_path), "http://localhost:11434", "model", False
            )

        assert stats.clean == 1
        assert len(stats.results) == 0  # CLEAN excluded when include_clean=False


# ---------------------------------------------------------------------------
# Read-only gate (Paul's requirement)
# ---------------------------------------------------------------------------

def test_script_is_read_only():
    """
    Verify the script contains no write operations targeting the portfolio.

    Lines referencing output_path are exempt (the only permitted write is the
    output CSV). This mirrors the read-only gate from test_background_rejection.py.
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
        if 'output_path' in line or 'output_csv' in line.lower():
            continue
        for pattern, name in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, line), (
                f"Forbidden write pattern '{name}' found in script source:\n"
                f"  {line.strip()}"
            )
