"""Generate fixture PDFs for arrow extraction tests.

Run directly to write the fixture to disk:
    python tests/fixtures/create_arrow_fixtures.py

Or call create_arrow_source_pdf(path) from test code.
"""
from __future__ import annotations

from pathlib import Path

import fitz


def create_arrow_source_pdf(out_path: Path) -> Path:
    """Create a 1-page PDF with FreeText + Line (arrow) annotations for testing.

    Layout (PDF points, origin top-left):
    - FreeText at [50, 50, 200, 70]  content="SUOCCUR=Y"
    - Page text:
        "Option 1" block centre ≈ (288.9, 61.1) — within 20pt of head_vertex (290, 62)
        "Option 2" block centre ≈ (288.9, 103.1) — within 20pt of head_vertex (290, 102)
        "Option 3" block centre ≈ (288.9, 143.1)
    - Arrow 1: vertices [(290, 62), (200, 60)],  line_ends=(4, 0)  → head at v[0]=(290,62)
    - Arrow 2: vertices [(290, 102), (200, 65)], line_ends=(4, 0)  → head at v[0]=(290,102)
    - Arrow 3: vertices [(290, 142), (200, 70)], line_ends=(4, 4)  → ambiguous, skipped
    All arrows: stroke_color red (1, 0, 0), width 1.

    v[0] (arrowhead) is placed near the text blocks; v[1] (tail) near the FreeText
    annotation right edge so tail snap works with rect=[50,50,200,70].

    head_text_search_radius_pt=20 default:
      - "Option 1" centre≈(288.9,61.1) → distance to (290,62) ≈ 1.4pt ✓
      - "Option 2" centre≈(288.9,103.1) → distance to (290,102) ≈ 1.4pt ✓
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()
    page: fitz.Page = doc.new_page(width=595, height=842)

    # --- insert page text ---
    # We insert three small text blocks whose centres land close to the arrow heads.
    # fitz.Page.insert_text places the *baseline* at the given point.
    # Font size 10, so the block spans roughly [x, y-10, x+text_width, y].
    # For "Option 1" we want centre ≈ (295, 60); baseline at (270, 65).
    page.insert_text(fitz.Point(270, 65), "Option 1", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(270, 107), "Option 2", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(270, 147), "Option 3", fontsize=10, color=(0, 0, 0))

    # --- FreeText annotation (simulates a source SDTM annotation) ---
    ft_rect = fitz.Rect(50, 50, 200, 70)
    ft = page.add_freetext_annot(ft_rect, "SUOCCUR=Y")
    ft.update()

    # --- Arrow 1: line_ends=(4,0) → head at vertex[0] = (290, 62) ---
    # head_vertex = (290, 62) near "Option 1" text block centre (288.9, 61.1)
    # tail_vertex = (200, 60) near FreeText right edge at x=200
    a1 = page.add_line_annot(fitz.Point(290, 62), fitz.Point(200, 60))
    a1.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_NONE)
    a1.set_colors(stroke=(1, 0, 0))
    a1.update()

    # --- Arrow 2: line_ends=(4,0) → head at vertex[0] = (290, 102) ---
    # head_vertex = (290, 102) near "Option 2" text block centre (288.9, 103.1)
    # tail_vertex = (200, 65) near FreeText right edge at x=200
    a2 = page.add_line_annot(fitz.Point(290, 102), fitz.Point(200, 65))
    a2.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_NONE)
    a2.set_colors(stroke=(1, 0, 0))
    a2.update()

    # --- Arrow 3: line_ends=(4,4) → ambiguous, should be skipped ---
    a3 = page.add_line_annot(fitz.Point(290, 142), fitz.Point(200, 70))
    a3.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    a3.set_colors(stroke=(1, 0, 0))
    a3.update()

    doc.save(str(out_path))
    doc.close()
    return out_path


def create_arrow_target_pdf(path: Path) -> Path:
    """Create a 1-page target PDF with text blocks for arrow resolution tests.

    Layout (PDF points, origin top-left):
    - "Yes"     baseline at (210, 65) → block approx [210, 55, 260, 70]
    - "No"      baseline at (210, 105) → block approx [210, 95, 260, 110]
    - "Unknown" baseline at (210, 145) → block approx [210, 135, 260, 150]
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(210, 65), "Yes", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(210, 105), "No", fontsize=10, color=(0, 0, 0))
    page.insert_text(fitz.Point(210, 145), "Unknown", fontsize=10, color=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


if __name__ == "__main__":
    dest = Path(__file__).parent / "arrows_source.pdf"
    create_arrow_source_pdf(dest)
    print(f"Written: {dest}")
    dest2 = Path(__file__).parent / "arrows_target.pdf"
    create_arrow_target_pdf(dest2)
    print(f"Written: {dest2}")
