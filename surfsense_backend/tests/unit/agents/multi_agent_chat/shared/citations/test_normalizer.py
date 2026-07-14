"""Tests for rewriting model ``[n]`` ordinals into frontend citation markers."""

from __future__ import annotations

import pytest

from app.agents.chat.multi_agent_chat.shared.citations.models import CitationSourceType
from app.agents.chat.multi_agent_chat.shared.citations.normalizer import (
    normalize_citations,
)
from app.agents.chat.multi_agent_chat.shared.citations.registry import CitationRegistry

pytestmark = pytest.mark.unit


def _registry_with_chunks(*chunk_ids: int) -> CitationRegistry:
    registry = CitationRegistry()
    for chunk_id in chunk_ids:
        registry.register(CitationSourceType.KB_CHUNK, {"chunk_id": chunk_id})
    return registry


def test_single_ordinal_is_rewritten() -> None:
    registry = _registry_with_chunks(42)

    assert normalize_citations("We shipped it [1].", registry) == (
        "We shipped it [citation:42]."
    )


def test_adjacent_brackets_are_each_rewritten() -> None:
    registry = _registry_with_chunks(42, 7)

    assert normalize_citations("Both agree [1][2].", registry) == (
        "Both agree [citation:42][citation:7]."
    )


def test_comma_separated_brackets_are_each_rewritten() -> None:
    registry = _registry_with_chunks(42, 7)

    assert normalize_citations("Both agree [1], [2].", registry) == (
        "Both agree [citation:42], [citation:7]."
    )


def test_unknown_ordinal_is_dropped() -> None:
    registry = _registry_with_chunks(42)

    assert normalize_citations("Maybe [9] is real.", registry) == "Maybe  is real."


def test_unknown_ordinal_among_known_is_dropped() -> None:
    registry = _registry_with_chunks(42)

    assert normalize_citations("See [1][9].", registry) == "See [citation:42]."


def test_web_result_rewrites_to_url() -> None:
    registry = CitationRegistry()
    registry.register(CitationSourceType.WEB_RESULT, {"url": "https://example.com"})

    assert normalize_citations("Per the docs [1].", registry) == (
        "Per the docs [citation:https://example.com]."
    )


def test_word_glued_citation_is_rewritten() -> None:
    # The model frequently writes citations glued to the preceding word
    # (``docs[1]``); these must still resolve to a marker, not leak as raw text.
    registry = _registry_with_chunks(42)

    assert normalize_citations("verifying against docs[1].", registry) == (
        "verifying against docs[citation:42]."
    )


def test_word_glued_unknown_ordinal_drops() -> None:
    # A glued ordinal that doesn't resolve drops harmlessly (no broken marker,
    # no raw ``[n]`` leak) rather than being preserved as array-index syntax.
    registry = _registry_with_chunks(42)

    assert normalize_citations("see notes[9] later", registry) == "see notes later"


def test_array_index_inside_code_is_left_alone() -> None:
    # Genuine array/index syntax is protected by the code-region carve-out.
    registry = _registry_with_chunks(42)

    assert normalize_citations("Read `arr[1]` carefully.", registry) == (
        "Read `arr[1]` carefully."
    )


def test_ordinals_inside_inline_code_are_untouched() -> None:
    registry = _registry_with_chunks(42)

    assert normalize_citations("Use `list[1]` here [1].", registry) == (
        "Use `list[1]` here [citation:42]."
    )


def test_ordinals_inside_fenced_code_are_untouched() -> None:
    registry = _registry_with_chunks(42)
    text = "Before [1].\n```\nx = a[1]\n```\nAfter [1]."

    assert normalize_citations(text, registry) == (
        "Before [citation:42].\n```\nx = a[1]\n```\nAfter [citation:42]."
    )


def test_empty_text_is_returned_unchanged() -> None:
    assert normalize_citations("", _registry_with_chunks(42)) == ""


# --------------------------------------------------------------------------
# Brackets naming several ordinals: `[1,2]`, `[57-61]`.
#
# Once a turn retrieves enough passages for the ordinals to get large, the model
# starts collapsing consecutive ones into a range. A range matched nothing, so it
# reached the frontend as literal `[57-61]`: unrendered and unclickable.
# --------------------------------------------------------------------------


def test_range_expands_to_one_marker_per_ordinal() -> None:
    registry = _registry_with_chunks(10, 20, 30, 40, 50)

    assert normalize_citations("As recorded [2-4].", registry) == (
        "As recorded [citation:20][citation:30][citation:40]."
    )


def test_comma_list_inside_one_bracket_expands() -> None:
    registry = _registry_with_chunks(10, 20, 30)

    assert normalize_citations("Both [1,3].", registry) == (
        "Both [citation:10][citation:30]."
    )


def test_mixed_list_and_range_expands() -> None:
    registry = _registry_with_chunks(10, 20, 30, 40)

    assert normalize_citations("See [1, 3-4].", registry) == (
        "See [citation:10][citation:30][citation:40]."
    )


def test_range_bounds_are_inclusive() -> None:
    registry = _registry_with_chunks(10, 20)

    assert normalize_citations("[1-2]", registry) == "[citation:10][citation:20]"


def test_single_element_range_is_one_marker() -> None:
    registry = _registry_with_chunks(10, 20)

    assert normalize_citations("Just [2-2].", registry) == "Just [citation:20]."


def test_repeated_ordinal_in_a_group_is_emitted_once() -> None:
    registry = _registry_with_chunks(10, 20)

    assert normalize_citations("Odd [1,1-2].", registry) == (
        "Odd [citation:10][citation:20]."
    )


def test_unresolvable_ordinals_within_a_range_are_dropped() -> None:
    # Only [2] is registered; the rest of the span has no source.
    registry = CitationRegistry()
    registry.register(CitationSourceType.KB_CHUNK, {"chunk_id": 1})
    registry.register(CitationSourceType.KB_CHUNK, {"chunk_id": 2})

    assert normalize_citations("Partial [2-6].", registry) == "Partial [citation:2]."


def test_year_range_that_resolves_to_nothing_is_left_alone() -> None:
    # `[1990-2020]` is prose, not a citation. Expanding and dropping it would
    # silently delete the author's text, so a group that resolves to nothing
    # survives verbatim.
    registry = _registry_with_chunks(42)

    assert normalize_citations("Active [1990-2020].", registry) == (
        "Active [1990-2020]."
    )


def test_implausibly_wide_range_is_left_alone() -> None:
    # Wider than any real registry: prose, not a citation. Left alone even though
    # its first ordinals would happen to resolve.
    registry = _registry_with_chunks(*range(1, 60))

    assert normalize_citations("Pages [1-500].", registry) == "Pages [1-500]."


def test_inverted_range_is_left_alone() -> None:
    registry = _registry_with_chunks(10, 20, 30)

    assert normalize_citations("Odd [3-1].", registry) == "Odd [3-1]."


def test_bare_unknown_ordinal_still_drops() -> None:
    # The single-ordinal case keeps its long-standing behaviour: an invented
    # citation disappears rather than leaking as raw text.
    registry = _registry_with_chunks(42)

    assert normalize_citations("Maybe [9].", registry) == "Maybe ."


def test_range_inside_code_is_untouched() -> None:
    registry = _registry_with_chunks(10, 20, 30)

    assert normalize_citations("Use `a[1-2]` here [1-2].", registry) == (
        "Use `a[1-2]` here [citation:10][citation:20]."
    )


def test_en_dash_range_is_expanded() -> None:
    # Models emit typographic dashes as readily as hyphens.
    registry = _registry_with_chunks(10, 20)

    assert normalize_citations("As noted [1–2].", registry) == (
        "As noted [citation:10][citation:20]."
    )
