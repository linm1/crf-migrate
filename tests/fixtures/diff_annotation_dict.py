"""Structurally diff FreeText annotation dictionaries between two PDFs (github.com/linm1/
crf-migrate issue #5) — e.g. a Kofax-native-authored sample vs. CRF-Migrate's PyMuPDF output,
or (as a self-test, since no Kofax-native sample exists yet) our two local variants.

"Equivalent sample" for this comparison to be meaningful means: same annotation content text,
similar rect/geometry, matched border/fill color and font weight — otherwise differences found
are just incidental authoring noise, not evidence. This script checks content equality as a
basic sanity flag and warns if annotations look unrelated before diffing them.

Usage:
    python tests/fixtures/diff_annotation_dict.py fileA.pdf fileB.pdf
    python tests/fixtures/diff_annotation_dict.py fileA.pdf fileB.pdf --match "DOMAIN LABEL"
"""
import argparse
import sys
from pathlib import Path

import fitz


def _annotation_dict(doc: fitz.Document, xref: int) -> dict[str, tuple[str, str]]:
    return {key: doc.xref_get_key(xref, key) for key in doc.xref_get_keys(xref)}


def _list_annotations(pdf_path: Path) -> list[tuple[int, fitz.Annot, str]]:
    doc = fitz.open(str(pdf_path))
    out = []
    for page_index, page in enumerate(doc):
        for annot in page.annots():
            if annot.type[1] != "FreeText":
                continue
            out.append((page_index, annot, annot.info.get("content", "")))
    return out


# Keys whose raw value is an indirect object reference that differs incidentally between
# any two separately-authored PDFs (different xref numbering) — comparing them verbatim
# would just report authoring noise, not a meaningful structural difference. /AP is handled
# separately below via an actual stream-content comparison instead of a raw ref comparison.
_SKIP_RAW_COMPARE = {"P", "NM", "AP"}


def _diff_one_pair(doc_a, xref_a: int, content_a: str,
                    doc_b, xref_b: int, content_b: str) -> None:
    if content_a.strip()[:20] != content_b.strip()[:20]:
        print(f"  WARNING: content differs — these annotations may not be equivalent:")
        print(f"    A: {content_a[:60]!r}")
        print(f"    B: {content_b[:60]!r}")
        print("  (structural differences below may just reflect different content, not a")
        print("   Kofax-vs-PyMuPDF authoring difference — verify before drawing conclusions)")

    dict_a = _annotation_dict(doc_a, xref_a)
    dict_b = _annotation_dict(doc_b, xref_b)
    all_keys = sorted((set(dict_a) | set(dict_b)) - _SKIP_RAW_COMPARE)

    for key in all_keys:
        va = dict_a.get(key, ("absent", "absent"))
        vb = dict_b.get(key, ("absent", "absent"))
        if va != vb:
            print(f"  /{key}:")
            print(f"    A: {va}")
            print(f"    B: {vb}")

    ap_a = doc_a.xref_get_key(xref_a, "AP/N")
    ap_b = doc_b.xref_get_key(xref_b, "AP/N")
    if ap_a[0] != "null" and ap_b[0] != "null":
        try:
            n_a = int(ap_a[1].split()[0])
            n_b = int(ap_b[1].split()[0])
            stream_a = doc_a.xref_stream(n_a)
            stream_b = doc_b.xref_stream(n_b)
            if stream_a != stream_b:
                print(f"  AP stream bytes differ (A: {len(stream_a)}b, B: {len(stream_b)}b)")
                print(f"    A: {stream_a[:200]!r}")
                print(f"    B: {stream_b[:200]!r}")
            else:
                print("  AP stream bytes: identical")
        except Exception as exc:  # noqa: BLE001 — diagnostic script, report and continue
            print(f"  AP stream comparison failed: {exc}")
    else:
        print("  AP stream comparison skipped (missing /AP/N on one side)")


def diff_pdfs(path_a: Path, path_b: Path, match: str | None) -> None:
    annots_a = _list_annotations(path_a)
    annots_b = _list_annotations(path_b)

    if match:
        annots_a = [a for a in annots_a if match.lower() in a[2].lower()]
        annots_b = [b for b in annots_b if match.lower() in b[2].lower()]

    if not annots_a or not annots_b:
        print(f"No matching FreeText annotations found (A: {len(annots_a)}, B: {len(annots_b)}).")
        sys.exit(1)

    if len(annots_a) != len(annots_b):
        print(f"WARNING: annotation counts differ (A: {len(annots_a)}, B: {len(annots_b)}) — "
              "pairing by order, extras ignored.")

    doc_a = fitz.open(str(path_a))
    doc_b = fitz.open(str(path_b))

    for i, ((page_a, annot_a, content_a), (page_b, annot_b, content_b)) in enumerate(
        zip(annots_a, annots_b)
    ):
        print(f"\n=== Pair {i}: A p{page_a + 1} xref{annot_a.xref}  vs  "
              f"B p{page_b + 1} xref{annot_b.xref} ===")
        _diff_one_pair(doc_a, annot_a.xref, content_a, doc_b, annot_b.xref, content_b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_a", type=Path)
    parser.add_argument("pdf_b", type=Path)
    parser.add_argument("--match", help="only compare annotations whose content contains this substring")
    args = parser.parse_args()
    diff_pdfs(args.pdf_a, args.pdf_b, args.match)


if __name__ == "__main__":
    main()
