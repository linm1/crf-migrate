"""Tests for ui/loader.py."""


def test_loader_html_returns_string():
    from ui.loader import loader_html
    html = loader_html("Processing…")
    assert isinstance(html, str)


def test_loader_html_contains_message():
    from ui.loader import loader_html
    html = loader_html("Extracting…")
    assert "Extracting…" in html


def test_loader_html_contains_keyframes():
    from ui.loader import loader_html
    html = loader_html("x")
    assert "@keyframes crf-pageFloat" in html


def test_loader_html_contains_svg():
    from ui.loader import loader_html
    html = loader_html("x")
    assert "<svg" in html
    assert "crf-layer-top" in html
    assert "crf-layer-middle" in html
    assert "crf-layer-bottom" in html


def test_show_loader_delegates_to_loader_html(monkeypatch):
    """show_loader must call placeholder.html() with loader_html() output."""
    from ui.loader import loader_html, show_loader

    calls = []

    class FakePlaceholder:
        def html(self, content: str) -> None:
            calls.append(content)

    ph = FakePlaceholder()
    show_loader(ph, "Testing…")
    assert len(calls) == 1
    assert "Testing…" in calls[0]
    assert "@keyframes crf-pageFloat" in calls[0]


def test_clear_loader_calls_empty():
    """clear_loader must call placeholder.empty()."""
    from ui.loader import clear_loader

    calls = []

    class FakePlaceholder:
        def empty(self) -> None:
            calls.append(True)

    ph = FakePlaceholder()
    clear_loader(ph)
    assert calls == [True]
