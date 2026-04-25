# Session Resume & Workspace Picker — Design Spec

**Date:** 2026-04-19  
**Status:** Approved  

---

## Problem

Every browser refresh (or new tab) unconditionally creates a `session_<timestamp>/` workspace directory, even when it will remain empty. Over time this produces dozens of ghost directories in `sessions/`. More importantly, users must re-upload their source aCRF and target CRF PDFs on every launch, repeating work they already completed.

---

## Goals

1. Auto-resume the most recent session that contains real work on startup.
2. Provide a sidebar dropdown to switch between existing sessions.
3. Provide a "New Session" button for users who genuinely want to start fresh.
4. Stop creating empty ghost session directories.
5. Correctly infer phase completion state on resume so nav buttons re-enable properly.

---

## Non-Goals

- Custom session names / labels (use timestamp IDs only).
- A dedicated sessions management page (no creation/deletion UI beyond the sidebar).
- Deleting or pruning old sessions automatically.

---

## Architecture

### 1. `Session` class additions (`src/session.py`)

Three new classmethods are added. No existing methods are modified; existing tests remain unaffected.

#### `Session.open(workspace: Path) -> Session`
Attaches to an already-existing workspace directory. Does **not** call `mkdir`. Sets `self.workspace = workspace` directly, bypassing `__init__`'s timestamp creation logic.

```python
@classmethod
def open(cls, workspace: Path) -> "Session":
    instance = cls.__new__(cls)
    instance.workspace = workspace
    return instance
```

#### `Session.list_sessions(base_dir: Path) -> list[str]`
Returns all `session_*` directory names under `base_dir`, sorted newest-first (reverse lexicographic — the timestamp format `YYYYMMDD_HHMMSS` sorts correctly as a string).

```python
@classmethod
def list_sessions(cls, base_dir: Path) -> list[str]:
    if not base_dir.exists():
        return []
    dirs = sorted(
        [d.name for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("session_")],
        reverse=True,
    )
    return dirs
```

#### `Session.latest(base_dir: Path) -> Session | None`
Iterates sessions newest-first; returns `Session.open(...)` for the first directory that contains `annotations.json`. Returns `None` if no qualifying session exists.

```python
@classmethod
def latest(cls, base_dir: Path) -> "Session | None":
    for name in cls.list_sessions(base_dir):
        candidate = base_dir / name
        if (candidate / "annotations.json").exists():
            return cls.open(candidate)
    return None
```

---

### 2. `_init_session_state` rewrite (`app.py`)

The guard `if "session" not in st.session_state` is preserved. Inside that block, the unconditional `Session(SESSION_BASE)` call is replaced with:

```python
sess = Session.latest(SESSION_BASE)
if sess is None:
    sess = Session(SESSION_BASE)   # only path that creates a new directory
```

After obtaining `sess`, the existing artifact restoration block runs unchanged.

Additionally, `phases_complete` is now inferred from what files actually exist in the workspace, replacing the `setdefault({1:False, ...})` hardcode:

```python
ws = sess.workspace
st.session_state["phases_complete"] = {
    1: (ws / "annotations.json").exists(),
    2: (ws / "fields.json").exists(),
    3: (ws / "matches.json").exists(),
    4: (ws / "output_acrf.pdf").exists(),
}
```

This fixes the pre-existing bug where restoring artifacts on startup didn't re-enable nav buttons.

---

### 3. `_load_session_into_state(name: str)` helper (`app.py`)

A new private function that loads all artifacts from a named session directory into `st.session_state`. Called both by startup (via `Session.latest`) and by the sidebar dropdown on switch.

```python
def _load_session_into_state(sess: Session) -> None:
    st.session_state["session"] = sess
    ws = sess.workspace

    # JSON artifacts
    try: st.session_state["annotations"] = sess.load_annotations()
    except FileNotFoundError: st.session_state["annotations"] = []

    try: st.session_state["fields"] = sess.load_fields()
    except FileNotFoundError: st.session_state["fields"] = []

    try: st.session_state["matches"] = sess.load_matches()
    except FileNotFoundError: st.session_state["matches"] = []

    try: st.session_state["qc_report"] = sess.load_qc_report()
    except FileNotFoundError: st.session_state["qc_report"] = None

    # PDF paths
    for key, fname in [
        ("source_pdf_path", "source_acrf.pdf"),
        ("target_pdf_path", "target_crf.pdf"),
        ("output_pdf_path", "output_acrf.pdf"),
    ]:
        p = ws / fname
        st.session_state[key] = p if p.exists() else None

    # Phase completion inferred from disk
    st.session_state["phases_complete"] = {
        1: (ws / "annotations.json").exists(),
        2: (ws / "fields.json").exists(),
        3: (ws / "matches.json").exists(),
        4: (ws / "output_acrf.pdf").exists(),
    }
```

`_init_session_state` is simplified to call `_load_session_into_state` rather than duplicating the restoration logic.

---

### 4. Sidebar workspace section (`app.py` — `_render_sidebar`)

A new "WORKSPACE" block is added in `_render_sidebar`, replacing the existing `st.caption(f"Workspace: {ws.workspace.name}")` line.

**Layout:**

```
WORKSPACE                      ← section label (pe-sidebar-label style)
[selectbox: session_20260419…] ← all sessions, newest-first; current pre-selected
[NEW SESSION]                  ← secondary button
```

**Behaviour:**

- The selectbox lists all entries from `Session.list_sessions(SESSION_BASE)`. If there are no sessions yet, it is hidden and only the "NEW SESSION" button is shown.
- On selectbox change: clear all session-scoped state keys, call `_load_session_into_state(Session.open(...))`, `st.rerun()`.
- "NEW SESSION" button: create `Session(SESSION_BASE)`, call `_load_session_into_state`, reset `current_page` to `"Profile Editor"`, `st.rerun()`.

**State keys cleared on session switch** (to avoid stale UI state bleeding across sessions):

```python
CLEARABLE_STATE_KEYS = [
    "annotations", "fields", "matches", "qc_report",
    "source_pdf_path", "target_pdf_path", "output_pdf_path",
    "phases_complete", "current_page",
    # phase-local UI keys
    "p1_page", "p2_page", "p3_page",
]
```

---

## Data Flow

```
App startup
    │
    ▼
Session.latest(SESSION_BASE)
    │
    ├─ found ──► Session.open(workspace) ──► _load_session_into_state()
    │
    └─ not found ──► Session(SESSION_BASE) [new dir] ──► _load_session_into_state()
    
Sidebar selectbox change
    │
    ▼
clear state ──► Session.open(selected_dir) ──► _load_session_into_state() ──► rerun()

"NEW SESSION" button
    │
    ▼
Session(SESSION_BASE) [new dir] ──► _load_session_into_state() ──► rerun()
```

---

## Error Handling

- If a session directory exists but individual JSON files are corrupt, the existing `try/except FileNotFoundError` blocks absorb the error and fall back to empty state. Corrupt JSON (non-`FileNotFoundError`) is allowed to surface as a visible Streamlit error — this is intentional and already matches the existing pattern.
- If `SESSION_BASE` does not exist, `Session.list_sessions` returns `[]` and startup creates a new session normally.

---

## Testing

New/updated test coverage in `tests/test_session.py`:

| Test | What it covers |
|---|---|
| `test_open_existing_workspace` | `Session.open()` attaches without creating directories |
| `test_list_sessions_sorted` | `list_sessions` returns dirs newest-first, ignores non-session dirs |
| `test_list_sessions_empty_base` | Returns `[]` when base dir doesn't exist |
| `test_latest_returns_most_recent_with_annotations` | Skips dirs without `annotations.json` |
| `test_latest_returns_none_when_no_qualified_session` | Returns `None` when all sessions are empty |

Existing `test_session.py` tests must still pass (no method signatures changed).

---

## Files Changed

| File | Change |
|---|---|
| `src/session.py` | Add `open`, `list_sessions`, `latest` classmethods |
| `app.py` | Rewrite `_init_session_state`, add `_load_session_into_state`, update `_render_sidebar` |
| `tests/test_session.py` | Add 5 new test cases |
