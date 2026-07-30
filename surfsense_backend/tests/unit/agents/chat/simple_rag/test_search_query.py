"""Behavior tests for simple_rag keyword-term expansion.

These assert on what the terms let the keyword leg match — the interior of a
date range, the topical words, the formats a corpus might use — never on term
order or exact list length, which are tuning details.
"""

from __future__ import annotations

import pytest

from app.agents.chat.simple_rag.search_query import build_search_terms

pytestmark = pytest.mark.unit


def test_date_range_expands_to_every_day_in_between():
    """The whole point: days named nowhere in the question become matchable."""
    terms = build_search_terms(
        "tổng hợp các thông tin từ ngày 06.07.2026 đến ngày 13.07.2026"
    )

    for day in range(6, 14):
        assert f"{day:02d}.07.2026" in terms, f"missing interior day {day}"


def test_range_expansion_covers_multiple_written_formats():
    """Postgres tokenizes 06/07/2026 and 06.07.2026 as unrelated lexemes."""
    terms = build_search_terms("thông tin từ 06.07.2026 đến 08.07.2026")

    assert "07/07/2026" in terms
    assert "07.07.2026" in terms
    assert "7/7/2026" in terms
    assert "2026-07-07" in terms


def test_instruction_words_are_dropped_but_topic_words_survive():
    terms = build_search_terms("tổng hợp các thông tin về dư luận ngày 06.07.2026")

    assert "dư" in terms and "luận" in terms
    for filler in ("tổng", "hợp", "các", "thông", "tin", "về", "ngày"):
        assert filler not in terms


def test_numeric_fragments_of_a_date_do_not_become_terms():
    """``06.07.2026`` must not leak in as the bare numbers 06, 07 and 2026."""
    terms = build_search_terms("thông tin từ 06.07.2026 đến 13.07.2026")

    assert "06" not in terms
    assert "2026" not in terms


def test_dates_are_read_day_first():
    """Vietnamese convention: 06.07.2026 is 6 July, so 12 July is in range."""
    terms = build_search_terms("từ 06.07.2026 đến 13.07.2026")

    assert "12/07/2026" in terms
    # Day-first, so the range never wanders into June-through-July territory.
    assert not any(term.endswith("/06/2026") for term in terms)


def test_yearless_endpoint_inherits_the_year_it_is_paired_with():
    terms = build_search_terms("tổng hợp từ ngày 6/7 đến ngày 13/7/2026")

    assert "06/07/2026" in terms
    assert "10/07/2026" in terms


def test_iso_dates_are_understood():
    terms = build_search_terms("summarize everything from 2026-07-06 to 2026-07-09")

    assert "08/07/2026" in terms
    assert "2026-07-08" in terms


def test_span_beyond_the_cap_keeps_endpoints_without_enumerating():
    terms = build_search_terms("thông tin từ 01.01.2026 đến 31.12.2026")

    assert "01/01/2026" in terms
    assert "31/12/2026" in terms
    # A year of daily terms would be noise, not signal.
    assert "15/06/2026" not in terms
    assert len(terms) < 40


def test_plain_question_without_dates_still_yields_topical_terms():
    terms = build_search_terms("what did the report say about inflation and housing")

    assert "inflation" in terms
    assert "housing" in terms
    for filler in ("what", "the", "about", "and"):
        assert filler not in terms


@pytest.mark.parametrize("question", ["", "   ", "các thông tin"])
def test_nothing_to_expand_falls_back_to_whole_query_matching(question):
    """An empty list is the caller's signal to keep the current AND behavior."""
    assert build_search_terms(question) == []


def test_terms_are_unique():
    terms = build_search_terms("dư luận dư luận từ 06.07.2026 đến 08.07.2026")

    assert len(terms) == len(set(terms))
