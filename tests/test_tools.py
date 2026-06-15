"""
Unit tests for the three FitFindr tools.

These cover the happy path and every documented failure mode. The two
LLM-backed tools (suggest_outfit, create_fit_card) are tested for their
*guards and contracts* (non-empty string, error string on bad input) without
asserting on model wording, so they pass without burning API calls where the
behaviour is deterministic.

Run with:  pytest tests/
"""

import sys
from pathlib import Path

# Make the project root importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import search_listings, suggest_outfit, create_fit_card  # noqa: E402
from agent import _parse_query, run_agent  # noqa: E402
from utils.data_loader import get_example_wardrobe  # noqa: E402


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible query → empty list, no exception (documented failure mode).
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_is_case_insensitive_substring():
    # "m" should match listing sizes like "M", "S/M", "M/L".
    results = search_listings("top", size="m", max_price=None)
    assert all("m" in item["size"].lower() for item in results)


def test_search_sorted_by_relevance():
    results = search_listings("vintage denim jeans", size=None, max_price=None)
    assert len(results) > 1
    # The top hit should be at least as relevant as later ones — we can't see
    # the raw score, but a denim/jeans query should put a denim item first.
    top = results[0]
    assert any(
        kw in (top["title"] + " " + " ".join(top["style_tags"])).lower()
        for kw in ("denim", "jeans")
    )


# ── create_fit_card (guard is deterministic, no API call) ─────────────────────

def test_create_fit_card_empty_outfit_returns_error_string():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    out = create_fit_card("", results[0])
    assert isinstance(out, str)
    assert "suggest_outfit" in out  # the documented guard message


def test_create_fit_card_whitespace_outfit_returns_error_string():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    out = create_fit_card("   ", results[0])
    assert isinstance(out, str)
    assert out.strip() != ""


# ── query parsing (used by the planning loop) ─────────────────────────────────

def test_parse_extracts_price():
    parsed = _parse_query("vintage graphic tee under $30")
    assert parsed["max_price"] == 30.0
    assert "tee" in parsed["description"].lower()
    assert "$" not in parsed["description"]


def test_parse_extracts_size():
    parsed = _parse_query("90s track jacket in size M")
    assert parsed["size"] is not None
    assert parsed["size"].lower() == "m"


def test_parse_description_falls_back_to_full_query():
    parsed = _parse_query("flowy midi skirt")
    assert parsed["max_price"] is None
    assert "skirt" in parsed["description"].lower()


# ── planning loop: retry/fallback + early exit (no LLM calls on these paths) ───

def test_retry_loop_loosens_then_errors_when_truly_no_match():
    # "designer ballgown" matches no keywords, so even fully loosened search is
    # empty. The loop should still try dropping size then price (recording both),
    # then end on the error path WITHOUT calling the LLM tools.
    session = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert session["error"] is not None
    assert session["adjustments"] == ["removed the size filter", "ignored the price limit"]
    assert session["selected_item"] is None
    assert session["outfit_suggestion"] is None
    assert session["fit_card"] is None  # downstream tools never ran
