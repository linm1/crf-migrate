# Session Resume & Workspace Picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-resume the most recent session on startup and add a sidebar dropdown so users can switch between sessions without re-uploading PDFs.

**Architecture:** Add three classmethods to `Session` (`open`, `list_sessions`, `latest`), extract all artifact-loading logic into a single `_load_session_into_state` helper in `app.py`, and replace the sidebar workspace caption with a selectbox + "New Session" button.

**Tech Stack:** Python 3.12, Streamlit, Pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-04-19-session-resume-design.md`

---

## File Map

| File | Change |
|---|---|
| `src/session.py` | Add `open`, `list_sessions`, `latest` classmethods |
| `app.py` | Add `_load_session_into_state`, rewrite `_init_session_state`, update `_render_sidebar` |
| `tests/test_session.py` | Add 5 new tests for the new classmethods |

---

## Task 1: Add `Session.open` classmethod

**Files:**
- Modify: `src/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing test**

Add this test class to `tests/test_session.py` (after the existing `TestCopyProfile` class):

```python
class TestSessionOpen:
    def test_open_attaches_to_existing_workspace(self, tmp_path):
        """Session.open() attaches to an existing directory without creating anything new."""
        existing = tmp_path / "session_20260101_120000"
        existing.mkdir()
        before = list(tmp_path.iterdir())
        sess = Session.open(existing)
        after = list(tmp_path.iterdir())
        assert sess.workspace == existing
        assert before == after  # no new directories created

    def test_open_does_not_require_annotations(self, tmp_path):
        """Session.open() works on an empty directory."""
        existing = tmp_path / "session_20260101_120000"
        existing.mkdir()
        sess = Session.open(existing)
        assert sess.workspace == existing
```

- [ ] **Step 2: Run to verify FAIL**

```
pytest tests/test_session.py::TestSessionOpen -v
```

Expected: `AttributeError: type object 'Session' has no attribute 'open'`

- [ ] **Step 3: Implement `Session.open`**

In `src/session.py`, add after the `copy_profile` method:

```python
@classmethod
def open(cls, workspace: Path) -> "Session":
    """Attach to an existing workspace directory without creating a new one."""
    instance = cls.__new__(cls)
    instance.workspace = workspace
    return instance
```

- [ ] **Step 4: Run to verify PASS**

```
pytest tests/test_session.py::TestSessionOpen -v
```

Expected: 2 passed

- [ ] **Step 5: Verify existing tests still pass**

```
pytest tests/test_session.py -v
```

Expected: all existing tests pass

- [ ] **Step 6: Commit**

```
git add src/session.py tests/test_session.py
git commit -m "feat: add Session.open classmethod"
```

---

## Task 2: Add `Session.list_sessions` classmethod

**Files:**
- Modify: `src/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/test_session.py` (after `TestSessionOpen`):

```python
class TestListSessions:
    def test_returns_session_dirs_newest_first(self, tmp_path):
        """list_sessions returns session_* dirs sorted newest-first."""
        (tmp_path / "session_20260101_090000").mkdir()
        (tmp_path / "session_20260301_120000").mkdir()
        (tmp_path / "session_20260201_060000").mkdir()
        result = Session.list_sessions(tmp_path)
        assert result == [
            "session_20260301_120000",
            "session_20260201_060000",
            "session_20260101_090000",
        ]

    def test_ignores_non_session_dirs(self, tmp_path):
        """list_sessions ignores directories not starting with 'session_'."""
        (tmp_path / "session_20260101_090000").mkdir()
        (tmp_path / "some_other_dir").mkdir()
        (tmp_path / "temp_work").mkdir()
        result = Session.list_sessions(tmp_path)
        assert result == ["session_20260101_090000"]

    def test_returns_empty_when_base_missing(self, tmp_path):
        """list_sessions returns [] when base_dir does not exist."""
        missing = tmp_path / "nonexistent"
        result = Session.list_sessions(missing)
        assert result == []
```

- [ ] **Step 2: Run to verify FAIL**

```
pytest tests/test_session.py::TestListSessions -v
```

Expected: `AttributeError: type object 'Session' has no attribute 'list_sessions'`

- [ ] **Step 3: Implement `Session.list_sessions`**

In `src/session.py`, add after `Session.open`:

```python
@classmethod
def list_sessions(cls, base_dir: Path) -> list[str]:
    """Return all session_* directory names under base_dir, newest-first."""
    if not base_dir.exists():
        return []
    return sorted(
        [d.name for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("session_")],
        reverse=True,
    )
```

- [ ] **Step 4: Run to verify PASS**

```
pytest tests/test_session.py::TestListSessions -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```
git add src/session.py tests/test_session.py
git commit -m "feat: add Session.list_sessions classmethod"
```

---

## Task 3: Add `Session.latest` classmethod

**Files:**
- Modify: `src/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/test_session.py` (after `TestListSessions`):

```python
class TestLatestSession:
    def test_returns_most_recent_with_annotations(self, tmp_path):
        """latest() returns the newest session that has annotations.json."""
        old = tmp_path / "session_20260101_090000"
        old.mkdir()
        (old / "annotations.json").write_text("[]")

        newer = tmp_path / "session_20260301_120000"
        newer.mkdir()
        (newer / "annotations.json").write_text("[]")

        sess = Session.latest(tmp_path)
        assert sess is not None
        assert sess.workspace == newer

    def test_skips_sessions_without_annotations(self, tmp_path):
        """latest() skips empty session dirs and returns the newest that has annotations.json."""
        empty = tmp_path / "session_20260401_080000"
        empty.mkdir()  # no annotations.json

        with_work = tmp_path / "session_20260301_120000"
        with_work.mkdir()
        (with_work / "annotations.json").write_text("[]")

        sess = Session.latest(tmp_path)
        assert sess is not None
        assert sess.workspace == with_work

    def test_returns_none_when_no_qualified_session(self, tmp_path):
        """latest() returns None when no session has annotations.json."""
        (tmp_path / "session_20260101_090000").mkdir()  # empty
        (tmp_path / "session_20260201_060000").mkdir()  # empty
        sess = Session.latest(tmp_path)
        assert sess is None
```

- [ ] **Step 2: Run to verify FAIL**

```
pytest tests/test_session.py::TestLatestSession -v
```

Expected: `AttributeError: type object 'Session' has no attribute 'latest'`

- [ ] **Step 3: Implement `Session.latest`**

In `src/session.py`, add after `Session.list_sessions`:

```python
@classmethod
def latest(cls, base_dir: Path) -> "Session | None":
    """Return the most recent session that contains annotations.json, or None."""
    for name in cls.list_sessions(base_dir):
        candidate = base_dir / name
        if (candidate / "annotations.json").exists():
            return cls.open(candidate)
    return None
```

- [ ] **Step 4: Run to verify PASS**

```
pytest tests/test_session.py::TestLatestSession -v
```

Expected: 3 passed

- [ ] **Step 5: Run full test_session suite**

```
pytest tests/test_session.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```
git add src/session.py tests/test_session.py
git commit -m "feat: add Session.latest classmethod"
```

---

## Task 4: Extract `_load_session_into_state` helper in `app.py`

**Files:**
- Modify: `app.py`

This task introduces the shared loading helper and rewrites `_init_session_state` to use it. No visual change yet — the sidebar still shows the old caption.

- [ ] **Step 1: Add the `CLEARABLE_STATE_KEYS` constant**

In `app.py`, after the `SESSION_BASE` constant (around line 22), add:

```python
# Keys cleared when switching sessions
CLEARABLE_STATE_KEYS = [
    "annotations", "fields", "matches", "qc_report",
    "source_pdf_path", "target_pdf_path", "output_pdf_path",
    "phases_complete", "current_page",
    "p1_page", "p2_page", "p3_page",
]
```

- [ ] **Step 2: Add `_load_session_into_state` helper**

In `app.py`, immediately before `_init_session_state`, add this function:

```python
def _load_session_into_state(sess: "Session") -> None:
    """Load all artifacts from sess into st.session_state."""
    st.session_state["session"] = sess
    ws = sess.workspace

    try:
        st.session_state["annotations"] = sess.load_annotations()
    except FileNotFoundError:
        st.session_state["annotations"] = []

    try:
        st.session_state["fields"] = sess.load_fields()
    except FileNotFoundError:
        st.session_state["fields"] = []

    try:
        st.session_state["matches"] = sess.load_matches()
    except FileNotFoundError:
        st.session_state["matches"] = []

    try:
        st.session_state["qc_report"] = sess.load_qc_report()
    except FileNotFoundError:
        st.session_state["qc_report"] = None

    for key, fname in [
        ("source_pdf_path", "source_acrf.pdf"),
        ("target_pdf_path", "target_crf.pdf"),
        ("output_pdf_path", "output_acrf.pdf"),
    ]:
        p = ws / fname
        st.session_state[key] = p if p.exists() else None

    st.session_state["phases_complete"] = {
        1: (ws / "annotations.json").exists(),
        2: (ws / "fields.json").exists(),
        3: (ws / "matches.json").exists(),
        4: (ws / "output_acrf.pdf").exists(),
    }
```

- [ ] **Step 3: Rewrite `_init_session_state`**

Replace the entire existing `_init_session_state` function body with:

```python
def _init_session_state() -> None:
    """Initialize all session state keys exactly once per browser session."""
    if "session" not in st.session_state:
        SESSION_BASE.mkdir(parents=True, exist_ok=True)
        sess = Session.latest(SESSION_BASE)
        if sess is None:
            sess = Session(SESSION_BASE)
        _load_session_into_state(sess)

    st.session_state.setdefault("current_page", "Profile Editor")

    if "profile" not in st.session_state:
        profiles = list_profiles(PROFILES_DIR)
        if profiles:
            default_name = profiles[0]
            st.session_state["profile_name"] = default_name
            try:
                profile_path = PROFILES_DIR / f"{default_name}.yaml"
                profile = load_profile(profile_path, PROFILES_DIR)
                st.session_state["profile"] = profile
                st.session_state["rule_engine"] = RuleEngine(profile)
            except Exception:
                pass
```

Note: `phases_complete` is no longer set here via `setdefault` — it is set inside `_load_session_into_state`.

- [ ] **Step 4: Manually verify the app still starts**

```
streamlit run app.py
```

Open `http://localhost:8501`. The app should load without errors. The sidebar should show the same workspace caption as before (unchanged at this step). Check browser console for JS errors.

- [ ] **Step 5: Commit**

```
git add app.py
git commit -m "refactor: extract _load_session_into_state, auto-resume on startup"
```

---

## Task 5: Add workspace dropdown and "New Session" button to sidebar

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Locate the existing caption line**

Find this block near the bottom of `_render_sidebar` (around line 540 in `app.py`):

```python
        st.divider()
        ws = st.session_state.get("session")
        if ws:
            st.caption(f"Workspace: {ws.workspace.name}")
```

- [ ] **Step 2: Replace caption with workspace controls**

Replace that entire block with:

```python
        st.divider()
        st.markdown(
            '<p class="pe-sidebar-label">WORKSPACE</p>',
            unsafe_allow_html=True,
        )
        all_sessions = Session.list_sessions(SESSION_BASE)
        current_sess = st.session_state.get("session")
        current_name = current_sess.workspace.name if current_sess else None

        if all_sessions:
            selected_idx = all_sessions.index(current_name) if current_name in all_sessions else 0
            selected = st.selectbox(
                "Workspace",
                all_sessions,
                index=selected_idx,
                key="sidebar_workspace",
                label_visibility="collapsed",
            )
            if selected != current_name:
                for k in CLEARABLE_STATE_KEYS:
                    st.session_state.pop(k, None)
                _load_session_into_state(Session.open(SESSION_BASE / selected))
                st.session_state.setdefault("current_page", "Profile Editor")
                st.rerun()

        if st.button("NEW SESSION", key="sidebar_new_session", use_container_width=True):
            for k in CLEARABLE_STATE_KEYS:
                st.session_state.pop(k, None)
            SESSION_BASE.mkdir(parents=True, exist_ok=True)
            new_sess = Session(SESSION_BASE)
            _load_session_into_state(new_sess)
            st.session_state["current_page"] = "Profile Editor"
            st.rerun()
```

- [ ] **Step 3: Manually verify the sidebar**

```
streamlit run app.py
```

Open `http://localhost:8501`. Verify:
1. Sidebar shows "WORKSPACE" label with a selectbox listing session directories.
2. The current session is pre-selected.
3. Clicking "NEW SESSION" resets to a blank Phase 1 state and the selectbox shows the new session.
4. Switching to an older session with `annotations.json` restores the annotation count and enables the Phase 2 nav button.

- [ ] **Step 4: Commit**

```
git add app.py
git commit -m "feat: add workspace dropdown and new session button to sidebar"
```

---

## Task 6: Verify full test suite passes

- [ ] **Step 1: Run all tests**

```
pytest --tb=short -q
```

Expected: no failures. If any pre-existing tests fail, do not modify them — investigate the cause.

- [ ] **Step 2: Confirm new session tests are included**

```
pytest tests/test_session.py -v
```

Expected: `TestSessionOpen`, `TestListSessions`, `TestLatestSession` all appear and pass.

- [ ] **Step 3: Final commit if any minor fixes were needed**

```
git add -A
git commit -m "test: verify session resume full suite green"
```
