# CRF-Migrate

A Python desktop tool for migrating SDTM annotations between annotated CRF (aCRF) PDF versions in clinical trials. Built with a Streamlit UI and a configurable YAML rule engine — no code changes are needed when adapting to new CRF formats or EDC systems.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Streamlit](https://img.shields.io/badge/streamlit-1.38%2B-red)
![Tests](https://img.shields.io/badge/tests-181%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-93%25-green)

## Overview

When a study updates its blank CRF between versions, all SDTM annotations from the previous annotated CRF must be manually re-applied to the new version — a time-consuming and error-prone process. CRF-Migrate automates this by:

1. **Extracting** existing SDTM annotations from the source aCRF PDF
2. **Extracting** form fields from the new blank target CRF PDF
3. **Matching** annotations to fields using exact, fuzzy, and position-based strategies
4. **Writing** approved annotations onto the target PDF with full style preservation

All classification and matching behavior is controlled by YAML profiles. The same codebase handles CDISC standard, Medidata Rave, Veeva Vault, and any custom EDC layout — just swap the profile.

## Screenshots

```
Sidebar navigation → Phase status bar → Per-phase review UI
Profile Editor     → Domain codes, classification rules, visit rules, matching thresholds
Phase 1            → Upload source aCRF → extract annotations → review/edit per page
Phase 2            → Upload target CRF  → extract fields      → review/edit per page
Phase 3            → Run matching → dashboard → approve/reject/assign → batch ops
Phase 4            → Generate output aCRF → download → QC report
```

## Installation

**Requirements:** Python 3.10+

```bash
git clone https://github.com/linm1/CRF-Migrate.git
cd CRF-Migrate
pip install -e .
```

### Optional: virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e .
```

## Usage

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. Follow the phase-locked navigation in the sidebar:

| Step | Page | What you do |
|------|------|-------------|
| 0 | Profile Editor | Select or configure a YAML profile for your EDC system |
| 1 | Phase 1 | Upload source aCRF PDF → extract annotations → review/edit |
| 2 | Phase 2 | Upload target CRF PDF → extract fields → review/edit |
| 3 | Phase 3 | Run matching → review results → approve/reject/assign |
| 4 | Phase 4 | Generate output aCRF → download PDF + QC report |

Each phase writes a JSON artifact to the session workspace. Editing a phase automatically invalidates all downstream phases.

## Project Structure

```
CRF-Migrate/
├── app.py                    # Streamlit entry point
├── ui/
│   ├── components.py         # Shared widgets, PDF utilities, phase invalidation
│   ├── profile_editor.py     # Profile selector, rule editor, rule tester
│   ├── phase1_review.py      # Annotation extraction & review
│   ├── phase2_review.py      # Field extraction & review
│   ├── phase3_review.py      # Match dashboard, approve/reject, manual assign
│   └── phase4_review.py      # Output generation & QC report
├── src/
│   ├── models.py             # Pydantic v2 data models (Annotation/Field/MatchRecord)
│   ├── profile_models.py     # Profile schema (Pydantic v2)
│   ├── profile_loader.py     # YAML load, validation, inheritance resolution
│   ├── rule_engine.py        # Stateless rule evaluation (no PDF/UI dependencies)
│   ├── extractor.py          # Phase 1: extract annotations from source PDF
│   ├── field_parser.py       # Phase 2: extract fields from target PDF
│   ├── matcher.py            # Phase 3: 4-pass matching algorithm
│   ├── writer.py             # Phase 4: write annotations to target PDF
│   ├── session.py            # Session workspace & audit log management
│   └── csv_handler.py        # CSV import/export for all record types
├── profiles/
│   ├── cdisc_standard.yaml   # Default CDISC profile
│   ├── rave_medidata.yaml    # Medidata Rave profile
│   └── veeva_vault.yaml      # Veeva Vault profile
└── tests/                    # 181 tests, 93% coverage
```

## Profiles

Profiles are the only mechanism for adapting to different CRF formats. No code changes are ever needed.

### Built-in profiles

| Profile | Description |
|---------|-------------|
| `cdisc_standard` | Default — CDISC-compliant annotation patterns |
| `rave_medidata` | Medidata Rave EDC conventions |
| `veeva_vault` | Veeva Vault EDC conventions |

### Profile sections

```yaml
meta:
  name: My Profile
  version: "1.0"
  parent: cdisc_standard   # optional: inherit from another profile

domain_codes: [DM, AE, VS, ...]

classification_rules:
  - conditions:
      contains: "[NOT SUBMITTED]"
    category: not_submitted
  - conditions:
      regex: "^([A-Z]{2,4})=(.+)$"
      domain_in: domain_codes
    category: domain_label
  - conditions:
      fallback: true
    category: sdtm_mapping

matching_config:
  exact_threshold: 1.0
  fuzzy_same_form_threshold: 0.80
  fuzzy_cross_form_threshold: 0.90
  position_fallback_confidence: 0.50

style_defaults:
  font: "Arial,BoldItalic"
  font_size: 18
  text_color: [0, 0, 0]
  border_color: [0.75, 1.0, 1.0]
```

### Profile inheritance

A child profile deep-merges with its parent. Lists support `_append` (extend) or `_replace` (overwrite) directives:

```yaml
meta:
  name: My Custom Profile
  parent: cdisc_standard

domain_codes:
  _append:
    - MYCUSTOM

classification_rules:
  _replace:
    - conditions:
        fallback: true
      category: sdtm_mapping
```

## Matching Algorithm

Phase 3 runs four cascading passes. Each pass only processes annotations not yet matched by a previous pass:

1. **Exact** — same `form_name` + identical `anchor_text`/`label` (case-insensitive)
2. **Fuzzy same-form** — `rapidfuzz token_sort_ratio` within the same form (default threshold: 80%)
3. **Fuzzy cross-form** — same algorithm across all remaining fields (default threshold: 90%)
4. **Position fallback** — coordinate scaling from source to target page dimensions; domain labels use absolute placement

Each match record carries a `confidence` score, `match_type`, and `status` (pending / approved / rejected / modified).

## CSV Workflow

All three record types support CSV round-trips for bulk editing outside the app:

```
Export CSV → edit in Excel/Numbers → Import CSV → review changes in app
```

- **Annotations CSV**: all fields including `rect`, `style` (JSON-serialized)
- **Fields CSV**: all fields including `rect`
- **Matches CSV**: all fields including `target_rect`, `field_id` (blank = unmatched)

## Session Workspace

Each app session creates a `sessions/session_<timestamp>/` directory containing:

```
annotations.json      # Phase 1 output
fields.json           # Phase 2 output
matches.json          # Phase 3 output
output_acrf.pdf       # Phase 4 output
qc_report.json        # Phase 4 QC summary
audit_log.json        # Timestamped record of all user actions
active_profile.yaml   # Snapshot of the profile used in this session
source_acrf.pdf       # Uploaded source PDF
target_crf.pdf        # Uploaded target PDF
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run a single test file
pytest tests/test_matcher.py -v
```

### Test coverage by module

| Module | Coverage |
|--------|----------|
| `models.py` | 100% |
| `profile_models.py` | 100% |
| `rule_engine.py` | 100% |
| `matcher.py` | 95% |
| `writer.py` | 95% |
| `field_parser.py` | 94% |
| `profile_loader.py` | 91% |
| `session.py` | 89% |
| `csv_handler.py` | 89% |
| `extractor.py` | 87% |
| **Total** | **93%** |

## License

PyMuPDF (used for PDF annotation read/write) is licensed under **AGPL-3.0**. If AGPL is not acceptable for your use case, `pypdf` (BSD-3-Clause) can be substituted with reduced annotation fidelity.

All other project code is provided as-is for internal/research use.
