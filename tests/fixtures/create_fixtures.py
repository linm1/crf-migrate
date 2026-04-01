"""Generate synthetic test fixtures for CRF-Migrate tests.

Run this script to create tests/fixtures/sample_acrf.pdf.
The generated PDF has 3 pages with FreeText annotations simulating an aCRF.
"""
from pathlib import Path
import fitz  # PyMuPDF


FIXTURES_DIR = Path(__file__).parent


def create_sample_acrf() -> Path:
    """Create a synthetic aCRF PDF with FreeText annotations for testing."""
    output_path = FIXTURES_DIR / "sample_acrf.pdf"
    doc = fitz.open()

    # Page 1: Demographics form
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 50), "DEMOGRAPHICS", fontsize=18, fontname="helv")
    page1.insert_text((50, 100), "Date of Birth", fontsize=10, fontname="helv")
    page1.insert_text((50, 130), "Sex", fontsize=10, fontname="helv")
    page1.insert_text((50, 160), "Race", fontsize=10, fontname="helv")

    def add_freetext(page, rect, content, subject="", font_size=18):
        """Add a FreeText annotation to a page.

        In PyMuPDF >= 1.24 the border_color parameter is only accepted when
        richtext=True.  We omit it here; fill_color provides the cyan stroke
        color that appears in the colors["stroke"] dict after save/reload.
        """
        annot = page.add_freetext_annot(
            rect=fitz.Rect(rect),
            text=content,
            fontsize=font_size,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(0.75, 1.0, 1.0),
        )
        annot.set_info(content=content, subject=subject)
        annot.update()
        return annot

    # Page 1 annotations: DM domain
    add_freetext(page1, [150, 90, 350, 110], "BRTHDTC", subject="DM")
    add_freetext(page1, [150, 120, 350, 140], "SEX", subject="DM")
    add_freetext(page1, [150, 150, 350, 170], "RACE", subject="DM")
    add_freetext(page1, [50, 40, 150, 60], "DM (Demographics)", subject="DM")

    # Page 2: Vital Signs form
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((50, 50), "VITAL SIGNS", fontsize=18, fontname="helv")
    page2.insert_text((50, 100), "Systolic BP", fontsize=10, fontname="helv")
    page2.insert_text((50, 130), "Diastolic BP", fontsize=10, fontname="helv")

    add_freetext(page2, [200, 90, 400, 110], "VSORRES when VSTESTCD = SYSBP", subject="VS")
    add_freetext(page2, [200, 120, 400, 140], "VSORRES when VSTESTCD = DIABP", subject="VS")
    add_freetext(page2, [50, 40, 150, 60], "VS (Vital Signs)", subject="VS")
    # Multi-line annotation (note category)
    add_freetext(page2, [50, 200, 300, 250], "1=Normal\r\n2=Abnormal\r\n3=Not Done", subject="VS")
    # [NOT SUBMITTED] annotation
    add_freetext(page2, [50, 260, 300, 280], "[NOT SUBMITTED]", subject="VS")

    # Page 3: Adverse Events + sticky note (to be excluded)
    page3 = doc.new_page(width=595, height=842)
    page3.insert_text((50, 50), "ADVERSE EVENTS", fontsize=18, fontname="helv")
    page3.insert_text((50, 100), "Adverse Event Term", fontsize=10, fontname="helv")

    add_freetext(page3, [200, 90, 450, 110], "AETERM", subject="AE")
    add_freetext(page3, [200, 120, 450, 140], "AESTDTC", subject="AE")
    # Sticky note (Text annotation — should be excluded because it is not FreeText)
    sticky = page3.add_text_annot(fitz.Point(50, 225), "Reviewer note")
    sticky.set_info(content="Reviewer note", subject="Sticky Note")
    sticky.update()
    # Note: starting with "Note:"
    add_freetext(page3, [50, 260, 350, 280], "Note: RELREC applies here", subject="AE")

    doc.save(str(output_path))
    doc.close()
    return output_path


if __name__ == "__main__":
    path = create_sample_acrf()
    print(f"Created: {path}")
