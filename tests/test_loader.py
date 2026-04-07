"""Tests for ui/loader.py."""


def test_loader_html_returns_string():
    from ui.loader import loader_html
    html = loader_html("Processing…")
    assert isinstance(html, str)


def test_loader_html_contains_message():
    from ui.loader import loader_html
    html = loader_html("Extracting…")
    assert "Extracting…" in html


def test_loader_html_contains_smil_animate_transform():
    from ui.loader import loader_html
    html = loader_html("x")
    assert "<animateTransform" in html
    assert 'attributeName="transform"' in html
    assert 'type="translate"' in html
    assert 'repeatCount="indefinite"' in html


def test_loader_html_contains_smil_animate_opacity():
    from ui.loader import loader_html
    html = loader_html("x")
    assert "<animate" in html
    assert 'attributeName="fill-opacity"' in html


def test_loader_html_contains_three_layers_with_staggered_delays():
    from ui.loader import loader_html
    html = loader_html("x")
    assert 'begin="0s"' in html
    assert 'begin="0.1s"' in html
    assert 'begin="0.2s"' in html


def test_loader_html_contains_svg():
    from ui.loader import loader_html
    html = loader_html("x")
    assert "<svg" in html
    assert "#FF9800" in html   # bottom / orange
    assert "#B5135A" in html   # middle / magenta
    assert "#E91E8C" in html   # top / pink


def test_show_loader_delegates_to_loader_html():
    from ui.loader import loader_html, show_loader

    calls = []

    class FakePlaceholder:
        def html(self, content: str) -> None:
            calls.append(content)

    ph = FakePlaceholder()
    show_loader(ph, "Testing…")
    assert len(calls) == 1
    assert "Testing…" in calls[0]
    assert "<animateTransform" in calls[0]


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
