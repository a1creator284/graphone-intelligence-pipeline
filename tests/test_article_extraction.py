"""
Unit tests for ArticleExtractor (Task 2.2).

Every test here is deterministic and network-free:
  - trafilatura/newspaper3k are exercised directly against a local HTML
    fixture (tests/fixtures/sample_article.html), OR
  - the library boundary (trafilatura.extract / newspaper's Article) is
    monkeypatched to force a specific branch (success/fail/exception)
    deterministically -- same "mock the boundary, not the network"
    convention used elsewhere in this suite (see test_github_enrichment.py's
    use of httpx.MockTransport).

No test makes a real HTTP request.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.extraction import articles as articles_module
from src.extraction.articles import ArticleExtractor

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class _FakeNewspaperArticle:
    """Stand-in for newspaper.Article that never touches the network --
    set_html()/parse() operate purely on the HTML string handed to them."""

    text_to_return: str | None = None
    raise_on_parse: bool = False

    def __init__(self, url: str):
        self.url = url
        self._html = None
        self.text = ""

    def set_html(self, html: str) -> None:
        self._html = html

    def parse(self) -> None:
        if _FakeNewspaperArticle.raise_on_parse:
            raise RuntimeError("simulated newspaper3k parse failure")
        self.text = _FakeNewspaperArticle.text_to_return or ""


@pytest.fixture(autouse=True)
def _reset_fake_newspaper():
    """Ensure fake-newspaper class state never leaks between tests."""
    _FakeNewspaperArticle.text_to_return = None
    _FakeNewspaperArticle.raise_on_parse = False
    yield
    _FakeNewspaperArticle.text_to_return = None
    _FakeNewspaperArticle.raise_on_parse = False


# --- successful extraction with trafilatura (real fixture, unmocked libs) --


def test_extract_text_with_trafilatura_success():
    html = _read_fixture("sample_article.html")
    text = ArticleExtractor.extract_text(html, "https://example-news.test/article-1")

    assert text is not None
    assert len(text) >= ArticleExtractor.MIN_CONTENT_LENGTH
    assert "benchmark" in text.lower()
    assert "reasoning capabilities" in text.lower()
    # Boilerplate the extractor is supposed to strip did not make it through.
    assert "Advertisement" not in text
    assert "Privacy Policy" not in text
    assert "Subscribe" not in text


def test_extract_text_preserves_paragraph_structure():
    html = _read_fixture("sample_article.html")
    text = ArticleExtractor.extract_text(html, "https://example-news.test/article-1")

    assert text is not None
    assert text.count("\n") >= 1  # multiple source paragraphs not collapsed


# --- fallback to newspaper3k when trafilatura fails/returns too little -----


def test_extract_text_falls_back_to_newspaper_when_trafilatura_returns_none(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "A" * 150

    text = ArticleExtractor.extract_text("<html><body>irrelevant</body></html>", "https://example.test/a")

    assert text == "A" * 150


def test_extract_text_falls_back_to_newspaper_when_trafilatura_too_short(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: "too short")
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "B" * 200

    text = ArticleExtractor.extract_text("<html><body>irrelevant</body></html>", "https://example.test/b")

    assert text == "B" * 200


def test_extract_text_falls_back_to_newspaper_when_trafilatura_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("simulated trafilatura crash")

    monkeypatch.setattr(articles_module.trafilatura, "extract", _boom)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "C" * 120

    text = ArticleExtractor.extract_text("<html><body>irrelevant</body></html>", "https://example.test/c")

    assert text == "C" * 120


# --- both extraction mechanisms failing --------------------------------------


def test_extract_text_returns_none_when_both_extractors_fail(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = None

    text = ArticleExtractor.extract_text("<html><body>irrelevant</body></html>", "https://example.test/d")

    assert text is None


def test_extract_text_returns_none_when_both_extractors_raise(monkeypatch):
    def _boom_trafilatura(*a, **k):
        raise ValueError("simulated trafilatura failure")

    monkeypatch.setattr(articles_module.trafilatura, "extract", _boom_trafilatura)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.raise_on_parse = True

    text = ArticleExtractor.extract_text("<html><body>irrelevant</body></html>", "https://example.test/e")

    assert text is None


# --- content shorter than 100 characters is rejected --------------------------


def test_content_below_minimum_length_is_rejected(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: "x" * 99)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "y" * 42

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/f")

    assert text is None


def test_content_exactly_at_minimum_length_is_accepted(monkeypatch):
    monkeypatch.setattr(
        articles_module.trafilatura, "extract", lambda *a, **k: "z" * ArticleExtractor.MIN_CONTENT_LENGTH
    )

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/g")

    assert text == "z" * ArticleExtractor.MIN_CONTENT_LENGTH


# --- empty content is rejected -------------------------------------------------


def test_empty_string_content_is_rejected(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: "")
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = ""

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/h")

    assert text is None


def test_empty_html_input_short_circuits_without_calling_extractors(monkeypatch):
    calls = {"trafilatura": 0}

    def _tracked_extract(*a, **k):
        calls["trafilatura"] += 1
        return None

    monkeypatch.setattr(articles_module.trafilatura, "extract", _tracked_extract)

    text = ArticleExtractor.extract_text("", "https://example.test/i")

    assert text is None
    assert calls["trafilatura"] == 0


def test_none_html_input_returns_none():
    text = ArticleExtractor.extract_text(None, "https://example.test/none-html")  # type: ignore[arg-type]
    assert text is None


# --- whitespace-only content is rejected --------------------------------------


def test_whitespace_only_content_is_rejected(monkeypatch):
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: "   \n\t   \n  ")
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "\n\n   \t  "

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/j")

    assert text is None


# --- extraction failure never fabricates content ------------------------------


def test_extraction_failure_never_returns_placeholder_text(monkeypatch):
    """None is the only legal 'failed' return value -- never a title, an RSS
    description, or any other stand-in text pretending to be the article."""
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = None

    text = ArticleExtractor.extract_text(
        "<html><head><title>A Real Headline That Is Long Enough On Its Own</title></head>"
        "<body><nav>nav only, no article body</nav></body></html>",
        "https://example.test/k",
    )

    assert text is None  # NOT the title, NOT a synthesized summary


# --- truncation at 100,000 characters ------------------------------------------


def test_content_exceeding_max_length_is_truncated(monkeypatch):
    oversized = "word " * 25_000  # well over 100,000 characters
    assert len(oversized) > ArticleExtractor.MAX_CONTENT_LENGTH

    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: oversized)

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/l")

    assert text is not None
    assert len(text) == ArticleExtractor.MAX_CONTENT_LENGTH
    assert text == oversized[: ArticleExtractor.MAX_CONTENT_LENGTH]


def test_content_at_exactly_max_length_is_not_truncated(monkeypatch):
    exact = "a" * ArticleExtractor.MAX_CONTENT_LENGTH
    monkeypatch.setattr(articles_module.trafilatura, "extract", lambda *a, **k: exact)

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/m")

    assert text == exact
    assert len(text) == ArticleExtractor.MAX_CONTENT_LENGTH


# --- malformed HTML is handled gracefully (no crash) ---------------------------


@pytest.mark.parametrize(
    "malformed_html",
    [
        "<html><body><p>Unclosed paragraph and <div>mismatched tags",
        "<<<>>>not even html>>>",
        "",
        "   ",
        "<html>" + ("<div>" * 5000) + "deeply nested but no real content" + ("</div>" * 5000) + "</html>",
        "\x00\x01\x02binary-ish garbage\xff\xfe",
    ],
)
def test_malformed_html_never_raises(malformed_html):
    # Real (unmocked) trafilatura + newspaper3k must not propagate an
    # exception for any of these inputs -- result may legitimately be None.
    text = ArticleExtractor.extract_text(malformed_html, "https://example.test/malformed")
    assert text is None or isinstance(text, str)


def test_library_exception_is_caught_and_falls_back(monkeypatch):
    """If trafilatura raises outright (not just returns None), extraction
    must not crash -- it should fall back to newspaper3k."""

    def _raise(*a, **k):
        raise Exception("boom")

    monkeypatch.setattr(articles_module.trafilatura, "extract", _raise)
    monkeypatch.setattr(articles_module, "NewspaperArticle", _FakeNewspaperArticle)
    _FakeNewspaperArticle.text_to_return = "D" * 150

    text = ArticleExtractor.extract_text("<html></html>", "https://example.test/n")

    assert text == "D" * 150
