# CLAUDE.md

Guidance for Claude Code working in CRF-Migrate. Four principles govern every change.

---

## 1. Think Before Coding

CRF-Migrate is a pipeline (`Extract → Parse → Match → Write`) with a **configurable rule engine at its core**. Most bugs come from misunderstanding which layer owns a concern.

Before writing code, answer these in order:

- **Is this a profile concern or a code concern?** Adapting to a new EDC format, classification pattern, visit name, form layout, fuzzy threshold, style default, or domain code list is **always** a YAML change in `profiles/*.yaml`. Never hardcode EDC-specific logic in `src/`.
- **Which phase owns this?** The pipeline is strictly sequential. Each phase writes a JSON artifact and editing any phase invalidates everything downstream.

  | Phase | Module                 | Input                     | Output                             |
  | ----- | ---------------------- | ------------------------- | ---------------------------------- |
  | 1     | `src/extractor.py`     | `source_acrf.pdf`         | `annotations.json`                 |
  | 2     | `src/field_parser.py`  | `target_crf.pdf`          | `fields.json`                      |
  | 3     | `src/matcher.py`       | both JSONs                | `matches.json`                     |
  | 4     | `src/writer.py`        | `matches.json` + target   | `output_acrf.pdf` + `qc_report.json` |

- **Where does the rule engine fit?** `src/rule_engine.py` is **deliberately free of PDF and UI dependencies**. It takes a validated `Profile` and exposes `classify()`, `extract_form_name()`, `extract_visit()`. If you find yourself importing `fitz` or `streamlit` there, stop.
- **Is there a session artifact already?** Re-reading a JSON artifact is free. Recomputing is not. Check `src/session.py` before you parse a PDF again.
- **Have I read the relevant test file?** Tests in `tests/test_<module>.py` encode the contract. Read the test before changing the module.

If you cannot answer all five, don't touch code yet — re-read the source and the profile schema (`src/profile_models.py`).

---

## 2. Simplicity First

- **Prefer a profile edit over a code change.** Any classification / matching / styling behavior that differs between EDCs belongs in YAML, resolved via `profile_loader.py`'s inheritance (`_append` / `_replace` / deep-merge).
- **Prefer AND-composed rules over complex regex.** In `classification_rules`, conditions within one rule AND together; for OR, write two rules. Use `fallback: true` as the last rule.
- **Prefer re-reading a JSON artifact over re-parsing a PDF.** The session workspace is designed for this.
- **Prefer `rapidfuzz.token_sort_ratio` over custom string similarity.** The matcher already standardizes on it; don't introduce a second algorithm.
- **Prefer Pydantic v2 validation over hand-written guards.** All records (`AnnotationRecord`, `FieldRecord`, `MatchRecord`) and the profile schema are Pydantic. Validation belongs in the model, not scattered across callers.
- **Small, cohesive modules.** Current layout — 12 files in `src/`, each ≤ ~800 lines, one responsibility each — is the target. Don't create new top-level modules unless a clear new concern appears.
- **Do not introduce new dependencies** beyond the locked set in `pyproject.toml` (`pymupdf`, `pdfplumber`, `rapidfuzz`, `pandas`, `pyyaml`, `streamlit<1.56`, `pydantic>=2`, `openpyxl`) without explicit approval.

---

## 3. Surgical Changes

Changes must be minimal, localized, and reversible. The PDF-writing layer is especially unforgiving.

### Pipeline & session rules

- Editing a phase **must** invalidate downstream phase artifacts. `ui/components.py` already owns this — reuse it, don't reinvent.
- Never mutate existing records in place. Construct new Pydantic instances (`.model_copy(update=...)`). Immutability keeps the audit log correct and phase invalidation predictable.
- Every user-initiated change must be recorded in `audit_log.json` via `src/session.py`. If your change adds a new user action, add an audit entry.

### Matching (Phase 3) — change thresholds in YAML, not code

Four cascading passes in `src/matcher.py`:

1. Exact form + field → confidence = `exact_threshold`
2. Fuzzy same-form (`rapidfuzz token_sort_ratio`) → threshold `fuzzy_same_form_threshold` (default 0.80)
3. Fuzzy cross-form → threshold `fuzzy_cross_form_threshold` (default 0.90)
4. Position fallback → confidence `position_fallback_confidence` (default 0.50)

Domain labels use **absolute** position placement, not field-relative. Don't change that without reading `tests/test_matcher_placement.py`.

### FreeText annotation writing — critical PyMuPDF behavior

The source aCRF style must round-trip exactly. PyMuPDF's FreeText behavior has several non-obvious gotchas. Follow these rules precisely; they are the result of hard-won debugging.

**Color — in `src/writer.py`:**

```python
a = page.add_freetext_annot(rect=rect, text=..., fill_color=fill, text_color=text_color, ...)
a.set_border(width=..., dashes=...)
a.set_info(...)
a.update(fill_color=fill, text_color=text_color)
_patch_ap_border_color(doc, a, border_color)   # always call — see below
# STOP. Do not touch /C or /IC via xref_set_key after this.
```

- For FreeText, `/C` is the **background fill color** (ISO 32000 Table 177); PyMuPDF exposes it as `annot.colors["stroke"]` — confusingly named, but correct.
- Always pass `fill_color` and `text_color` to `a.update()` explicitly.
- **Never** `xref_set_key(xref, "C", ...)` after `update()` — it overwrites fill, producing a black background on re-render.
- **Never** `xref_set_key(xref, "IC", ...)` on FreeText — `/IC` is not a valid FreeText key.
- **Always** call `_patch_ap_border_color()` after `a.update()`, even when the border is black. PyMuPDF encodes `text_color` into the AP stream's `RG` operator during creation; without the patch, red-text annotations get a red border. Border color lives only in the AP stream's `RG` operator — read by `_parse_ap_border_color()` in `extractor.py`, written by `_patch_ap_border_color()` in `writer.py`.

**Font (Bold / Italic / Bold-Italic) — 4-step AP patch via `_apply_font_style()`:**

Domain labels must render bold *and stay bold after viewer interaction*. When the user touches an annotation, the viewer regenerates the AP stream from `/DA`. If `/DA` references an unknown font name (e.g. PyMuPDF's internal alias `hebo`), the viewer silently falls back to regular Helvetica.

Always use the **standard PDF Base-14 names** — never PyMuPDF aliases — in `/DA`:

| Use case    | Base-14 name              | Never use     |
| ----------- | ------------------------- | ------------- |
| Bold        | `Helvetica-Bold`          | `hebo`        |
| Italic      | `Helvetica-Oblique`       | `heit`        |
| Bold-Italic | `Helvetica-BoldOblique`   | `hebi`        |

All four steps in `_apply_font_style()` are required — dropping any one produces an invisible or wrong font:

1. `page.insert_font(fontname=pdf_font_name)` — register by standard name.
2. Rewrite `/DA`: `doc.xref_set_key(annot.xref, "DA", f"({r} {g} {b} rg /{pdf_font_name} {fontsize} Tf)")`.
3. Patch AP stream bytes: `re.sub(rb"/Helv\b", f"/{pdf_font_name}".encode(), stream)` — regex word boundary, not `replace(b"/Helv ", ...)`.
4. Register font in the AP stream's own `/Resources/Font` dict (the AP is a self-contained Form XObject).

Call `_apply_font_style()` **after** `a.update()` — `update()` overwrites `/DA`. Source-PDF font names (`Arial,BoldItalic`, `Arial-BoldItalicMT`, etc.) are mapped to Base-14 by `_normalise_font_name()` using family + bold/italic flag parsing.

### Style replication mode

`style_defaults.use_source_style` (profile YAML, default `false`):

- **`false`** — category-driven: `domain_label` → Helvetica-Bold; `cross_reference` → cyan text; others → profile's `font_size` and black text.
- **`true`** — each annotation inherits source aCRF font weight, size, text color, fill color, and border color (normalized via `_normalise_font_name()`).

Exposed in the profile editor's **Style** tab (`ui/profile_editor.py`). Toggling it requires no code change. In both modes, **border color is read from the source AP stream — never hardcoded.**

---

## 4. Goal-Driven Execution

Every task has a concrete goal. State it, verify it, stop when it's met.

### Before finishing any change

- Run `pytest` — the suite currently targets ≥94% coverage; don't regress.
- If you touched `src/matcher.py`, `src/writer.py`, or `src/extractor.py`, run the phase-specific tests explicitly: `pytest tests/test_matcher.py tests/test_writer.py tests/test_extractor.py -v`.
- If you touched profile schema or loading, run `pytest tests/test_profile_loader.py tests/test_rule_engine.py`.
- If you touched any PDF writing path, verify on a real sample in `tests/fixtures/` and visually inspect the output — unit tests cannot catch viewer-rendering regressions (see font/color notes above).
- Verify phase invalidation still fires by running the app end-to-end for the affected phase.

### Commands

```bash
streamlit run app.py                               # run the UI
pytest                                             # full suite
pytest tests/test_matcher.py::test_exact_match     # single test
pytest --cov=src --cov-report=term-missing         # coverage
pip install -e ".[dev]"                            # dev setup
```

### Definition of done

- Goal stated in the task is observably met (artifact produced, annotation renders correctly, test passes).
- No hardcoded EDC-specific values introduced in `src/`.
- No new dependency added.
- Tests green; coverage not regressed.
- If a PDF-writing rule in Section 3 was involved, re-read it and verified each step is present.
- `audit_log.json` gains an entry for any new user action.

---

## Project map (at a glance)

```
app.py                       Streamlit entry point
src/
  models.py                  Pydantic v2 records (Annotation / Field / Match)
  profile_models.py          Profile schema (Pydantic v2)
  profile_loader.py          YAML load, validate, resolve inheritance
  rule_engine.py             Stateless — NO fitz, NO streamlit imports
  extractor.py               Phase 1
  field_parser.py            Phase 2
  matcher.py                 Phase 3 (4 cascading passes)
  writer.py                  Phase 4 — PyMuPDF rules in Section 3 apply
  session.py                 Workspace + audit log
  csv_handler.py             CSV round-trip for all record types
  pdf_utils.py               Shared PDF helpers
ui/
  components.py              Shared widgets + phase invalidation (reuse!)
  phase1_review.py … phase4_review.py
  profile_editor.py          Rule editor + rule tester
  style_helpers.py           UI-side style preview helpers
profiles/                    cdisc_standard.yaml (default), rave_medidata, veeva_vault, taimi
tests/                       test_<module>.py; fixtures/ for sample PDFs & YAMLs
sessions/                    Runtime: session_<timestamp>/ per session
graphify-out/                Knowledge graph — read GRAPH_REPORT.md first for arch questions
```

## Licensing note

PyMuPDF is **AGPL-3.0**. If that's unacceptable downstream, `pypdf` (BSD) is the fallback with reduced annotation fidelity — see PRD Open Question #4. Don't add another PDF library without explicit approval.

## graphify

Knowledge graph at `graphify-out/`.

- Before answering architecture questions, read `graphify-out/GRAPH_REPORT.md` for god nodes and community structure.
- If `graphify-out/wiki/index.md` exists, prefer it over raw files.
- After modifying code in a session, rebuild: `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"`
