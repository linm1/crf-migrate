"""Tests for find_nearest_label in src/pdf_utils.py.

Pure unit tests — no PDF files needed. All TextBlock instances use synthetic
data so these tests can run without PyMuPDF being able to open any file.
"""
import re

import pytest

from src.rule_engine import TextBlock
from src.pdf_utils import find_nearest_label


def _block(text: str, x0: float, y0: float, x1: float, y1: float) -> TextBlock:
    """Construct a synthetic TextBlock for testing."""
    return TextBlock(text=text, font_size=10.0, bold=False, rect=[x0, y0, x1, y1])


class TestFindNearestLabel:
    """Tests for find_nearest_label (left-column + vertical-distance algorithm)."""

    def test_basic_returns_nearest_left_column_block(self):
        """Basic: left-column block at x=50 returned; annotation at x=300, y=100."""
        blocks = [
            _block("Label A", 50.0, 90.0, 150.0, 110.0),  # left column, near annotation
        ]
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=100.0)
        assert result == "Label A"

    def test_right_column_block_excluded(self):
        """Left-column filtering: block at x=200 should be excluded when threshold is low."""
        blocks = [
            _block("Left Label", 50.0, 90.0, 150.0, 110.0),
            _block("Right Label", 200.0, 90.0, 300.0, 110.0),
        ]
        # min x0 = 50, threshold = 50 + 80 = 130 → x=200 excluded
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=80.0)
        assert result == "Left Label"

    def test_vertical_distance_closer_block_wins(self):
        """Vertical distance: two left-column blocks; block at y=80 wins over y=200."""
        blocks = [
            _block("Near Label", 50.0, 80.0, 150.0, 100.0),   # vert_dist=0 (overlaps)
            _block("Far Label", 50.0, 200.0, 150.0, 220.0),   # vert_dist=100
        ]
        # annotation y0=95, y1=115
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=100.0)
        assert result == "Near Label"

    def test_tiebreak_by_center_distance(self):
        """Tie-break by center distance: two blocks with same vert_dist, closer center wins."""
        # marker_rect y0=100, y1=102 → marker_cy=101
        # Both blocks have vert_dist=0 (they overlap the marker vertically)
        blocks = [
            _block("Close Center", 50.0, 99.0, 150.0, 103.0),  # center=101, center_dist=0
            _block("Far Center", 50.0, 95.0, 150.0, 107.0),    # center=101 too — same
        ]
        # Use blocks with different centers to force tie-break
        blocks2 = [
            _block("Close Center", 50.0, 99.0, 150.0, 103.0),  # center=101.0, dist=0
            _block("Far Center", 50.0, 80.0, 150.0, 100.0),    # center=90.0, dist=11, vert=0
        ]
        marker_rect = [300.0, 100.0, 400.0, 102.0]
        result = find_nearest_label(marker_rect, blocks2, left_column_tolerance_px=100.0)
        assert result == "Close Center"

    def test_exclude_patterns_skip_matching_blocks(self):
        """Exclude patterns: block text matching regex pattern should be skipped."""
        blocks = [
            _block("Page 1 of 10", 50.0, 90.0, 150.0, 110.0),  # matches exclude pattern
            _block("Subject ID", 50.0, 90.0, 150.0, 110.0),     # valid
        ]
        exclude_patterns = [re.compile(r"page\s+\d+", re.IGNORECASE)]
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(
            marker_rect, blocks, left_column_tolerance_px=100.0,
            exclude_patterns=exclude_patterns,
        )
        assert result == "Subject ID"

    def test_empty_blocks_list_returns_empty_string(self):
        """Empty blocks list should return empty string."""
        result = find_nearest_label([300.0, 95.0, 400.0, 115.0], [], 100.0)
        assert result == ""

    def test_all_blocks_filtered_by_left_threshold_returns_empty_string(self):
        """All blocks to the right of the threshold → returns empty string."""
        blocks = [
            _block("Far Right", 300.0, 90.0, 400.0, 110.0),
            _block("Also Far", 350.0, 90.0, 450.0, 110.0),
        ]
        # min x0 = 300, threshold = 300 + 10 = 310
        # marker rect x0 = 500 (irrelevant here — all blocks fail threshold)
        marker_rect = [500.0, 95.0, 600.0, 115.0]
        # tolerance=10 → threshold=300+10=310; both blocks at x0=300 pass (<=310)
        # But with tolerance=0: threshold=300; only x0==300 passes
        # Use a negative tolerance to exclude all
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=-1.0)
        assert result == ""

    def test_exclude_patterns_none_uses_no_filter(self):
        """When exclude_patterns is None, no pattern filtering is applied."""
        blocks = [
            _block("Page 1 of 10", 50.0, 90.0, 150.0, 110.0),
        ]
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(
            marker_rect, blocks, left_column_tolerance_px=100.0,
            exclude_patterns=None,
        )
        assert result == "Page 1 of 10"

    def test_returns_stripped_text(self):
        """Returned text is stripped of leading/trailing whitespace."""
        blocks = [
            _block("  Trimmed Label  ", 50.0, 90.0, 150.0, 110.0),
        ]
        marker_rect = [300.0, 95.0, 400.0, 115.0]
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=100.0)
        assert result == "Trimmed Label"

    def test_marker_rect_is_list_of_floats(self):
        """find_nearest_label accepts list[float] marker_rect (no fitz dependency)."""
        marker_rect = [10.0, 20.0, 30.0, 40.0]
        blocks = [_block("Label", 5.0, 19.0, 9.0, 21.0)]
        # Should not raise — fitz.Rect is NOT required
        result = find_nearest_label(marker_rect, blocks, left_column_tolerance_px=5.0)
        assert isinstance(result, str)
