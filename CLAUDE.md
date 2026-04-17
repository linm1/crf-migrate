# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CRF-Migrate is a Python desktop tool (Streamlit UI) for migrating SDTM annotations between aCRF PDF versions in clinical trials. It uses a configurable YAML rule engine to avoid hardcoded EDC-specific logic.

**Key constraint:** All adaptation to new CRF formats must happen via YAML profiles only — zero code changes. Enforce this strictly.

## Commands

```bash
# Run the app
streamlit run app.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_rule_engine.py

# Run a specific test
pytest tests/test_rule_engine.py::test_load_valid_profile

# Run tests with coverage
pytest --cov=src --cov-report=term-missing

# Install dependencies
pip install -e .
```

## Architecture

### Core Pipeline (4 Phases)

The app enforces strict sequential phases. Each phase writes a JSON artifact; editing a phase invalidates all downstream phases and requires re-running them.

| Phase | Module | Input | Output |
|-------|--------|-------|--------|
| 1 | `src/extractor.py` | `source_acrf.pdf` | `annotations.json` |
| 2 | `src/field_parser.py` | `target_crf.pdf` | `fields.json` |
| 3 | `src/matcher.py` | both JSON files | `matches.json` |
| 4 | `src/writer.py` | `matches.json` + target PDF | `output_acrf.pdf` + `qc_report.json` |

### Rule Engine (Critical Component)

`src/rule_engine.py` is deliberately free of PDF and UI dependencies. It consumes a `Profile` object (loaded/validated by `src/profile_loader.py`) and exposes three methods:
- `classify(content, subject) -> str` — evaluates `classification_rules` in order; first match wins
- `extract_form_name(page_texts) -> str`
- `extract_visit(page_text) -> str | None`

Rules use AND logic within a single rule; use separate rules for OR logic. The `fallback: true` condition always matches and should be the last rule.

### Profile Inheritance

A profile's `meta.parent` field names a parent profile. List fields use `_append` mode (extend parent) or `_replace` (default, overwrite). Dict/object fields are deep-merged. This is resolved in `src/profile_loader.py`.

### Session Workspace

`src/session.py` manages a `session_<timestamp>/` directory per session. On session start, the active profile YAML is copied into the workspace for reproducibility. `audit_log.json` captures all user modifications with timestamps.

### Data Models

All records defined in `src/models.py` (Pydantic v2):
- `AnnotationRecord` — source annotation with category, matched_rule, anchor_text, style, rotation
- `FieldRecord` — target CRF field with type (text_field / checkbox / date_field / table_row / section_header)
- `MatchRecord` — links annotation to field; status: pending / approved / rejected / modified

Profile schema (separate from record models) lives in `src/profile_models.py`.

### Matching Strategy (Phase 3)

Four cascading passes in `src/matcher.py`:
1. Exact form + field match (confidence = `exact_threshold`)
2. Fuzzy same-form match via `rapidfuzz token_sort_ratio` (threshold: `fuzzy_same_form_threshold`, default 0.80)
3. Cross-form fuzzy match (threshold: `fuzzy_cross_form_threshold`, default 0.90)
4. Position-based fallback (confidence = `position_fallback_confidence`, default 0.50)

Domain labels use absolute position placement, not field-relative placement.

### PDF Annotation Format

Source annotations are FreeText (PDF type 2) with:
- Font: Arial Bold Italic 18pt (DA string: `0 0 0 rg /Arial,BoldItalic 18 Tf`)
- Border color: cyan `[0.75, 1.0, 1.0]`
- Rich content: XHTML in the RC field

Output annotations must preserve this exact styling. New annotations use `style_defaults` from the active profile.

### FreeText Annotation Color — Critical PyMuPDF Behavior

For FreeText annotations, the PDF spec (ISO 32000 Table 177) defines `/C` as the **background fill color**, not the border color. PyMuPDF exposes `/C` as `annot.colors["stroke"]` for FreeText (confusingly named, but correct).

The border color is encoded inside the AP (Appearance) stream as a `0 0 0 RG` operator by PyMuPDF's `update()` — it is not a top-level PDF dictionary key.

**Correct pattern in `src/writer.py`:**
```python
a = page.add_freetext_annot(rect=rect, text=..., fill_color=fill, text_color=text_color, ...)
a.set_border(width=..., dashes=...)
a.set_info(...)
a.update(fill_color=fill, text_color=text_color)
# STOP HERE. Do not touch /C or /IC via xref_set_key.
```

**Rules — never violate these:**
- Always pass `fill_color` and `text_color` to `a.update()` explicitly so PyMuPDF sets `/C` correctly.
- **Never** call `xref_set_key(xref, "C", ...)` after `update()` — it overwrites the fill color, causing black background on viewer re-render.
- **Never** call `xref_set_key(xref, "IC", ...)` on FreeText — `/IC` is not a valid FreeText key; most viewers ignore it, and some may misinterpret it.
- The border color (black) lives only in the AP stream; this is correct and survives viewer interaction as long as `/C` is not overwritten.

### FreeText Bold — Must Use Standard PDF Font Name, Not PyMuPDF Alias

Domain labels must render bold **and stay bold after viewer interaction**. AP stream byte-patch alone fails: when user touches annotation, viewer regenerates AP from `/DA`. If `/DA` references unknown font name, viewer silently falls back to regular Helvetica.

**Root cause:** PyMuPDF alias `hebo` is internal only. No PDF viewer recognizes it. Standard Base-14 name `Helvetica-Bold` is universally understood.

**Correct pattern in `src/writer.py` — `_apply_bold_font()`:**
```python
# 1. Register by standard PDF Base-14 name (not "hebo")
page.insert_font(fontname="Helvetica-Bold")
hb_xref = next(f[0] for f in page.get_fonts() if f[4] == "Helvetica-Bold")

# 2. DA with standard name — viewer reads this on touch to regenerate AP
doc.xref_set_key(annot.xref, "DA", f"(0 0 0 rg /Helvetica-Bold {fontsize} Tf)")

# 3. Patch AP stream bytes
n_num = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
stream = doc.xref_stream(n_num)
patched = re.sub(rb"/Helv\b", b"/Helvetica-Bold", stream)
doc.update_stream(n_num, patched)

# 4. Register in AP stream's own /Resources/Font dict (AP is self-contained Form XObject)
doc.xref_set_key(n_num, "Resources/Font/Helvetica-Bold", f"{hb_xref} 0 R")
```

**Rules — never violate these:**
- Always use `Helvetica-Bold` (standard PDF name), never `hebo` in `/DA`.
- All four steps required: missing step 4 → `/Helvetica-Bold` unresolvable inside AP Form XObject → invisible or wrong font.
- Call `_apply_bold_font()` **after** `a.update()` — `update()` overwrites `/DA`.
- `re.sub(rb"/Helv\b", ...)` not `replace(b"/Helv ", ...)` — regex word boundary matches regardless of trailing whitespace.

## Key Conventions

- **PyMuPDF (`fitz`)** for all annotation read/write; **pdfplumber** for text extraction fallback
- **Pydantic v2** for all data models and profile validation
- Session artifacts are JSON; CSV import/export is handled by `src/csv_handler.py`
- Tests follow `test_<module>.py` naming; fixtures (sample PDFs, YAML profiles) in `tests/fixtures/`
- Test IDs in PRD (TR.01–TR.16, T1.01–T1.16, etc.) should map to test function names for traceability

## Profiles Directory

`profiles/` ships with three templates:
- `cdisc_standard.yaml` — default, covers common CDISC patterns
- `rave_medidata.yaml` — Medidata Rave conventions
- `veeva_vault.yaml` — Veeva Vault conventions

Profile sections: `meta`, `domain_codes`, `classification_rules`, `form_name_rules`, `visit_rules`, `anchor_text_config`, `annotation_filter`, `matching_config`, `style_defaults`.

## PyMuPDF License Note

PyMuPDF is AGPL-3.0. If the license is not acceptable, the fallback is `pypdf` (BSD) with reduced annotation fidelity — see Open Question #4 in the PRD.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
