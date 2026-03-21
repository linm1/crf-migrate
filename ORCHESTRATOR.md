# ORCHESTRATOR.md

Living memory for this repo. Updated after every subagent run and every decision made in the orchestration thread.

---

## Repo Purpose

CRF-Migrate is a Python desktop tool (Streamlit UI) for migrating SDTM annotations between aCRF PDF versions in clinical trials. It uses a configurable YAML rule engine to avoid hardcoded EDC logic.

**Hard constraint**: All adaptation to new CRF formats must happen via YAML profiles only — zero code changes.

---

## Directory Structure

```
/home/user/crf-migrate/
├── app.py                        Streamlit entry point (195 lines)
├── pyproject.toml                Dependencies + pytest config
├── README.md
├── CLAUDE.md
├── ORCHESTRATOR.md               ← This file
├── src/
│   ├── models.py                 Pydantic v2 data models (AnnotationRecord, FieldRecord, MatchRecord, StyleInfo)
│   ├── profile_models.py         Profile schema (Profile, RuleCondition, MatchingConfig, etc.)
│   ├── rule_engine.py            Stateless rule evaluator (classify, extract_form_name, extract_visit)
│   ├── profile_loader.py         YAML loading + profile inheritance resolution
│   ├── extractor.py              Phase 1: annotation extraction from source aCRF
│   ├── field_parser.py           Phase 2: field extraction from target blank CRF
│   ├── matcher.py                Phase 3: 4-pass matching (exact → fuzzy same-form → fuzzy cross-form → position)
│   ├── writer.py                 Phase 4: write approved annotations to output PDF
│   ├── session.py                Session workspace management + audit_log.json
│   └── csv_handler.py            CSV import/export for all record types
├── ui/
│   ├── components.py             Shared Streamlit widgets (phase bar, page nav, badges, PDF utils)
│   ├── phase1_review.py          Phase 1 UI
│   ├── phase2_review.py          Phase 2 UI
│   ├── phase3_review.py          Phase 3 UI
│   ├── phase4_review.py          Phase 4 UI
│   └── profile_editor.py         Profile editor UI (tabs + rule tester)
├── profiles/
│   ├── cdisc_standard.yaml       Default profile (21 domains, 9 rules)
│   ├── rave_medidata.yaml         Inherits from cdisc_standard (no overrides yet)
│   └── veeva_vault.yaml           Inherits from cdisc_standard (no overrides yet)
└── tests/
    ├── conftest.py                Fixtures: sample_acrf_path, cdisc_profile, cdisc_engine
    ├── fixtures/create_fixtures.py
    ├── test_models.py
    ├── test_profile_models.py
    ├── test_profile_loader.py
    ├── test_rule_engine.py        TR.01–TR.16 test IDs
    ├── test_extractor.py
    ├── test_phase2_fields.py
    ├── test_matcher.py            T3.01–T3.10 test IDs
    ├── test_writer.py
    ├── test_session.py
    └── test_csv_handler.py
```

---

## Core Pipeline

Sequential phases. Each phase writes a JSON artifact. Editing a phase invalidates all downstream phases.

| Phase | Module | Input | Output |
|-------|--------|-------|--------|
| 1 | extractor.py | source_acrf.pdf + Profile | annotations.json |
| 2 | field_parser.py | target_crf.pdf + Profile | fields.json |
| 3 | matcher.py | annotations.json + fields.json + page dims | matches.json |
| 4 | writer.py | matches.json + target PDF | output_acrf.pdf + qc_report.json |

---

## Module Responsibilities and Key Details

### models.py
- `StyleInfo`: font, font_size, text_color, border_color (defaults: Arial,BoldItalic 18pt, black, cyan `[0.75, 1.0, 1.0]`)
- `AnnotationRecord`: id(UUID), page(1-indexed), content, domain, category, matched_rule, rect, anchor_text, form_name, visit, style, rotation
  - Categories: `domain_label | sdtm_mapping | not_submitted | note | _exclude`
- `FieldRecord`: id, page, label, form_name, visit, rect, field_type
  - Types: `text_field | checkbox | date_field | table_row | section_header`
- `MatchRecord`: annotation_id, field_id, match_type, confidence(0–1), target_rect, status, user_notes
  - Match types: `exact | fuzzy | position_only | manual | unmatched | new`
  - Statuses: `pending | approved | rejected | modified`

### profile_models.py
- `RuleCondition`: conditions are AND logic. `extra="forbid"` (TR.03: unknown condition types rejected).
  - Fields: contains, starts_with, regex, domain_in, multi_line, max_length, min_length, subject_is, fallback
  - Regex syntax validated via `@model_validator`
- `Profile`: has 9 top-level sections (meta, domain_codes, classification_rules, form_name_rules, visit_rules, anchor_text_config, annotation_filter, matching_config, style_defaults)

### rule_engine.py
- Stateless, no PDF/UI dependencies
- `classify(content, subject) -> (category, matched_rule_description)`: first match wins. Falls back to `sdtm_mapping`.
- `extract_form_name(text_blocks) -> str`: largest bold text >= min_font_size, excluding exclude_patterns. Returns "" if no candidates.
- `extract_visit(page_text) -> str | None`: first regex match wins. Supports capture group substitution `{1}`.
- AND logic within rule; use separate rules for OR.

### profile_loader.py
- `list_profiles(dir) -> list[str]`: sorted list of YAML file stems
- `load_profile(path, profiles_dir=None) -> Profile`: load YAML, resolve inheritance, validate
- Inheritance: `meta.parent` names parent. List fields: `_append` extends parent, `_replace` (default) overwrites. Dict/object fields deep-merged.
- Circular inheritance detected via visited set.

### extractor.py (Phase 1)
- FreeText annotations only
- DA string format: `"0 0 0 rg /Arial,BoldItalic 18 Tf"` → StyleInfo
- Anchor text: radial search within radius_px, ranked by direction preference then Euclidean distance
- Bold flag: `flags & 16` (PyMuPDF spec)
- annotation_filter applied first: include_types, exclude_empty, min_content_length

### field_parser.py (Phase 2)
- Field type heuristics (priority order):
  1. date_field: date placeholder patterns
  2. checkbox: Yes/No/Y/N or checkbox glyphs (☐☑✓)
  3. section_header: bold or font_size >= min_font_size
  4. text_field: 3+ consecutive underscores
  5. None if no match
- Same `_get_text_blocks()` logic as extractor.py

### matcher.py (Phase 3)
- Pass 1 — exact: form_name == form_name AND anchor_text == label (case-insensitive). Requires non-empty anchor_text. Confidence = exact_threshold (1.0).
- Pass 2 — fuzzy same-form: token_sort_ratio >= fuzzy_same_form_threshold*100 within same form. Confidence = score/100.
- Pass 3 — fuzzy cross-form: token_sort_ratio >= fuzzy_cross_form_threshold*100 across all fields.
- Pass 4 — position: domain_labels use absolute rect; others scaled from source dims to target dims if form found, else unmatched.
- `apply_manual_match()`: immutable, raises ValueError if annotation_id not found
- `batch_approve_exact()`: immutable

### writer.py (Phase 4)
- Only processes status in ("approved", "modified")
- FreeText annotation with exact source style preserved
- `build_qc_report()`: total_matches, written, skipped, counts_by_match_type, unmatched_annotation_ids, rejected_annotation_ids

### session.py
- Creates `session_<YYYYMMDD_HHMMSS>/` directory
- Artifacts: annotations.json, fields.json, matches.json, output_acrf.pdf, qc_report.json, audit_log.json, active_profile.yaml, source_acrf.pdf, target_crf.pdf
- `log_action()`: read-modify-write to audit_log.json

### csv_handler.py
- Nested fields (rect, style, target_rect) serialized to JSON strings
- NaN → empty string for optional string fields
- Import rules: row with id updates existing; empty id → new UUID; existing not in CSV → flagged (not deleted)

### app.py
- Streamlit session_state holds: session, profile, profile_name, rule_engine, annotations, fields, matches, qc_report, source_pdf_path, target_pdf_path, output_pdf_path, phases_complete, current_page
- Sidebar: profile selector + phase-locked navigation (Phase N locked until Phase N-1 complete)
- Phase 1 always enabled; Phase 2 requires Phase 1 complete; etc.

---

## Import Dependency Tree

```
app.py → ui/* → src/extractor, field_parser, matcher, writer, csv_handler
rule_engine.py → profile_models.py
extractor.py, field_parser.py → fitz, rule_engine
matcher.py → rapidfuzz, models
writer.py → fitz, models
session.py → models
csv_handler.py → pandas, models
profile_loader.py → yaml, profile_models
```

No circular imports. `rule_engine.py` is intentionally free of fitz/streamlit.

---

## Key Conventions

1. **PyMuPDF (fitz)** for annotation read/write; pdfplumber as fallback for text extraction.
2. **Pydantic v2** for all models — use `model_dump()` / `model_validate()`.
3. **Immutable function semantics** for all core pipeline functions — return new lists, don't mutate inputs.
4. **YAML profiles only** for CRF format adaptation — no code changes.
5. Test files: `test_<module>.py`; fixtures in `tests/fixtures/`.
6. Test IDs: TR.01–TR.16 (rule engine), T3.01–T3.10 (matcher).
7. JSON artifacts: all record fields serialized via `model_dump()`.
8. Session artifacts written by `session.py`; never written directly by pipeline modules.
9. Phase invalidation: editing Phase N clears downstream phases N+1 onward via `invalidate_phases()`.
10. Domain label annotations: absolute positioning (no scaling). All others: scaled by page dimension ratio.

---

## Known Fragile Areas

| Area | Location | Risk | Notes |
|------|----------|------|-------|
| PyMuPDF AGPL-3.0 license | pyproject.toml | HIGH | Fallback (pypdf) not implemented |
| Rule evaluation order | rule_engine.py:29–40 | MEDIUM | First-match-wins; reordering silently changes behavior |
| Domain label absolute positioning | matcher.py:172–184 | MEDIUM | Assumes domain labels at margins; may fail for some EDCs |
| Bold font flag parsing | extractor.py:200, field_parser.py:95 | MEDIUM | `flags & 16` is PDF-generator-specific |
| Anchor text direction penalty weights | extractor.py:234–253 | LOW | index*10.0 penalty — not tuned |
| Matching threshold defaults | profile_models.py:68–71 | MEDIUM | 0.80/0.90 may be wrong for different CRF styles |
| CSV NaN handling | csv_handler.py:35,124,224 | LOW | Assumes pandas NaN-for-missing behavior |
| Multi-sized PDFs | matcher.py:223–225 | MEDIUM | Assumes consistent page sizes per document |
| Session timestamp collision | session.py:15 | LOW | Two sessions in same second will collide |
| Profile mutated externally | rule_engine.py:22–23 | LOW | Engine stores profile directly, no immutability enforcement |

---

## Profile Structure (cdisc_standard.yaml)

9 classification rules (in order):
1. max_length=0 → `_exclude`
2. subject_is="Sticky Note" → `_exclude`
3. subject_is="Typewritten Text" + max_length=0 → `_exclude`
4. contains "[NOT SUBMITTED]" → `not_submitted`
5. regex `^([A-Z]{2,4})=(.+)$` + domain_in → `domain_label`
6. multi_line=true → `note`
7. starts_with "Note:" → `note`
8. contains "RELREC" → `note`
9. fallback → `sdtm_mapping`

21 domain codes: DM, IE, MH, CM, AE, VS, EG, PE, QS, DS, DA, EX, SC, LB, SV, SE, CO, FA, TU, TR, RS

Visit rules: Screening, Baseline, Week {1}, End of Study, Running Records

---

## Test Coverage (Baseline)

| File | Coverage |
|------|----------|
| models.py | 100% |
| profile_models.py | 100% |
| rule_engine.py | 100% |
| matcher.py | 95% |
| writer.py | 95% |
| field_parser.py | 94% |
| profile_loader.py | 91% |
| session.py | 89% |
| csv_handler.py | 89% |
| extractor.py | 87% |
| **Overall** | **93%** |

---

## Decisions Made

_(None yet — populated as decisions are made in this thread.)_

---

## Current State

- Codebase explored: 2026-03-21
- No changes made yet
- Branch: claude/explore-codebase-architecture-3RuZg

---

## Subagent Instructions Template

When spawning a subagent, always provide:
1. **Goal**: what to implement/fix
2. **Files it owns**: files it may edit
3. **Files it must not touch**: e.g., models.py unless explicitly required
4. **Conventions to follow**: from the "Key Conventions" section above
5. **How to verify**: which tests to run, what to check
6. **Branch**: `claude/explore-codebase-architecture-3RuZg`

Standard verification: `pytest` (all tests must pass). For coverage: `pytest --cov=src --cov-report=term-missing`.
