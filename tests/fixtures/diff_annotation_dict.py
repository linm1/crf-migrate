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
import re
import sys
from pathlib import Path

import fitz

_INDIRECT_REF_RE = re.compile(r"^(\d+)\s+0\s+R$")
_FONT_RESOURCE_RE = re.compile(r"/(\S+)\s+\d+\s+0\s+R")


def _annotation_dict(doc: fitz.Document, xref: int) -> dict[str, tuple[str, str]]:
    return {key: doc.xref_get_key(xref, key) for key in doc.xref_get_keys(xref)}


def _list_annotations(pdf_path: Path) -> list[tuple[int, int, str, fitz.Rect]]:
    """Returns (page_index, xref, content, rect) as plain data — not live fitz.Annot
    objects, which become invalid ("not bound to any page") once their owning Document
    is closed or garbage-collected, which happens as soon as this function returns."""
    doc = fitz.open(str(pdf_path))
    out = []
    for page_index, page in enumerate(doc):
        for annot in page.annots():
            if annot.type[1] != "FreeText":
                continue
            out.append((page_index, annot.xref, annot.info.get("content", ""), annot.rect))
    doc.close()
    return out


# Keys whose raw value is inherently per-file noise even when dereferenced (P: the page
# object itself, huge and irrelevant to annotation structure; NM: a randomly-generated
# annotation name/GUID) — always skip these rather than reporting them as "differences".
# /AP is handled separately below via a real structural + stream-content comparison.
_ALWAYS_SKIP = {"P", "NM", "AP"}


def _deref_dict(doc: fitz.Document, ref_value: str) -> dict[str, tuple[str, str]] | None:
    """If ref_value looks like a single indirect reference ('N 0 R'), dereference it one
    level and return its own key/value dict — so e.g. two structurally-identical /Popup
    objects at different xref numbers compare as equal instead of as noise."""
    m = _INDIRECT_REF_RE.match(ref_value.strip())
    if not m:
        return None
    xref = int(m.group(1))
    try:
        return {key: doc.xref_get_key(xref, key) for key in doc.xref_get_keys(xref)}
    except Exception:  # noqa: BLE001 — best-effort dereference for a diagnostic script
        return None


def _diff_one_pair(doc_a, xref_a: int, content_a: str, rect_a,
                    doc_b, xref_b: int, content_b: str, rect_b) -> None:
    content_mismatch = content_a.strip() != content_b.strip()
    rect_mismatch = (
        abs(rect_a.width - rect_b.width) > 1.0 or abs(rect_a.height - rect_b.height) > 1.0
    )
    if content_mismatch or rect_mismatch:
        print(f"  WARNING: these annotations may not be an equivalent pair:")
        if content_mismatch:
            print(f"    content A: {content_a[:80]!r}")
            print(f"    content B: {content_b[:80]!r}")
        if rect_mismatch:
            print(f"    rect A: {rect_a}  (w={rect_a.width:.0f} h={rect_a.height:.0f})")
            print(f"    rect B: {rect_b}  (w={rect_b.width:.0f} h={rect_b.height:.0f})")
        print("  (structural differences below may just reflect a non-equivalent pairing, not")
        print("   a Kofax-vs-PyMuPDF authoring difference — verify before drawing conclusions)")

    dict_a = _annotation_dict(doc_a, xref_a)
    dict_b = _annotation_dict(doc_b, xref_b)
    all_keys = sorted((set(dict_a) | set(dict_b)) - _ALWAYS_SKIP)

    for key in all_keys:
        va = dict_a.get(key, ("absent", "absent"))
        vb = dict_b.get(key, ("absent", "absent"))
        if va == vb:
            continue
        # If both sides are single indirect refs, compare the referenced objects'
        # contents instead of the (meaningless, file-specific) xref numbers.
        deref_a = _deref_dict(doc_a, va[1]) if va[0] == "xref" else None
        deref_b = _deref_dict(doc_b, vb[1]) if vb[0] == "xref" else None
        if deref_a is not None and deref_b is not None:
            if deref_a == deref_b:
                continue
            print(f"  /{key} (dereferenced object differs):")
            print(f"    A: {deref_a}")
            print(f"    B: {deref_b}")
            continue
        print(f"  /{key}:")
        print(f"    A: {va}")
        print(f"    B: {vb}")

    _diff_ap(doc_a, xref_a, doc_b, xref_b)


def _diff_ap(doc_a, xref_a: int, doc_b, xref_b: int) -> None:
    ap_a = doc_a.xref_get_key(xref_a, "AP/N")
    ap_b = doc_b.xref_get_key(xref_b, "AP/N")
    if ap_a[0] == "null" or ap_b[0] == "null":
        print("  AP stream comparison skipped (missing /AP/N on one side)")
        return

    try:
        n_a = int(ap_a[1].split()[0])
        n_b = int(ap_b[1].split()[0])
    except (ValueError, IndexError) as exc:
        print(f"  AP stream comparison failed to parse /AP/N: {exc}")
        return

    # Stream bytes (the drawing operators themselves).
    stream_a = doc_a.xref_stream(n_a)
    stream_b = doc_b.xref_stream(n_b)
    if stream_a != stream_b:
        print(f"  AP stream bytes differ (A: {len(stream_a)}b, B: {len(stream_b)}b)")
        print(f"    A: {stream_a[:200]!r}")
        print(f"    B: {stream_b[:200]!r}")
    else:
        print("  AP stream bytes: identical")

    # The Form XObject's own dict entries that affect rendering but aren't part of the
    # stream bytes — a prior version of this tool only compared stream bytes and missed
    # these, which can silently change what's actually rendered (e.g. font substitution
    # via /Resources/Font while the drawing operators referencing that font name stay
    # byte-identical).
    for sub_key in ("BBox", "Matrix"):
        va = doc_a.xref_get_key(n_a, sub_key)
        vb = doc_b.xref_get_key(n_b, sub_key)
        if va != vb:
            print(f"  AP /{sub_key} differs:")
            print(f"    A: {va}")
            print(f"    B: {vb}")

    fonts_a = _ap_font_basenames(doc_a, n_a)
    fonts_b = _ap_font_basenames(doc_b, n_b)
    if fonts_a != fonts_b:
        print("  AP /Resources/Font differs (resource name -> BaseFont):")
        print(f"    A: {fonts_a}")
        print(f"    B: {fonts_b}")


def _ap_font_basenames(doc: fitz.Document, ap_n_xref: int) -> dict[str, str]:
    """Return {font resource name: BaseFont value} for the AP Form XObject's own
    /Resources/Font dict, dereferencing each font object — this is exactly what
    src/writer.py's _apply_font_style() rewrites, so it's a prime place for a real
    rendering difference to hide behind identical stream bytes."""
    fonts_key = doc.xref_get_key(ap_n_xref, "Resources/Font")
    if fonts_key[0] == "null":
        return {}
    names = _FONT_RESOURCE_RE.findall(fonts_key[1])
    result = {}
    for name in names:
        base_font = doc.xref_get_key(ap_n_xref, f"Resources/Font/{name}/BaseFont")
        result[name] = base_font[1] if base_font[0] != "null" else "(absent)"
    return result


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

    for i, ((page_a, xref_a, content_a, rect_a), (page_b, xref_b, content_b, rect_b)) in enumerate(
        zip(annots_a, annots_b)
    ):
        print(f"\n=== Pair {i}: A p{page_a + 1} xref{xref_a}  vs  "
              f"B p{page_b + 1} xref{xref_b} ===")
        _diff_one_pair(doc_a, xref_a, content_a, rect_a,
                        doc_b, xref_b, content_b, rect_b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_a", type=Path)
    parser.add_argument("pdf_b", type=Path)
    parser.add_argument("--match", help="only compare annotations whose content contains this substring")
    args = parser.parse_args()
    diff_pdfs(args.pdf_a, args.pdf_b, args.match)


if __name__ == "__main__":
    main()
