"""Behavior tests for the workspace-noun gate.

The gate's whole claim is that question *form* does not separate a workspace
question from a content question — only the object does. So these assert on
pairs that share a form and differ in object, in both languages, rather than on
the vocabulary list, which is tuning.
"""

from __future__ import annotations

import pytest

from app.agents.chat.shared.workspace_intent import mentions_workspace

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question",
    [
        "how many folders do I have",
        "tôi có bao nhiêu thư mục",
        "tôi có bao nhiêu tài liệu",
        "list my documents",
        "các tệp đã tải lên",
        "what is in my knowledge base",
        "how many files are in the workspace",
    ],
)
def test_fires_on_questions_about_the_workspace(question):
    assert mentions_workspace(question)


@pytest.mark.parametrize(
    "question",
    [
        # Same shape as "how many folders do I have", different object.
        "how many casualties were reported",
        "tôi có bao nhiêu thời gian",
        # Same instruction verb as "liệt kê tài liệu", different object.
        "liệt kê các vụ tấn công",
        "tổng hợp tin tức về Ukraine",
    ],
)
def test_does_not_fire_on_questions_about_document_contents(question):
    assert not mentions_workspace(question)


def test_interrogatives_alone_are_not_enough():
    """An interrogative is not the signal — the workspace noun is."""
    assert not mentions_workspace("how many were there in total")
    assert not mentions_workspace("bao nhiêu người đã tham gia")


@pytest.mark.parametrize(
    "question",
    [
        "xem profile của anh ta",  # "file" inside "profile"
        "the docket was sealed",  # "doc" inside "docket"
    ],
)
def test_workspace_nouns_need_word_boundaries(question):
    assert not mentions_workspace(question)


def test_vietnamese_phrases_need_both_syllables():
    """Vietnamese is syllable-per-token; a lone syllable carries no signal."""
    assert not mentions_workspace("bức thư đó rất dài")
    assert mentions_workspace("bức thư đó nằm trong thư mục nào")


def test_gate_prefers_a_false_positive_to_a_false_negative():
    """A content question naming documents still fires — the cheap mistake.

    Costs a listing nobody reads. The opposite error hands the model no listing
    for a question that needs one, and it invents a number instead.
    """
    assert mentions_workspace("tóm tắt tài liệu về drone")


def test_empty_input_is_not_a_workspace_question():
    assert not mentions_workspace("")
    assert not mentions_workspace("   ")
