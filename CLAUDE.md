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
