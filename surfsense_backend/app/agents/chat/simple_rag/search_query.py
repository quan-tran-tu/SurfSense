"""Turn the user's question into keyword-search terms.

This flow searches with the question verbatim, which is most of what it gives up
against the agent path. There, a capable model rewrites the question into a
search query before calling the tool — dropping the instruction words, keeping
the entities, and spelling a date out in several formats. That rewrite is the
step this module replaces, deterministically and without a model call.

Two things go wrong when a whole question is used as a query:

  * **The filler outweighs the content.** "tổng hợp các thông tin từ ngày X đến
    ngày Y" is eight instruction words wrapped around two dates. ``ts_rank_cd``
    applies no IDF, so those high-frequency words score as loudly as the dates.
  * **A date range names only its endpoints.** Asking for 06.07 → 13.07 puts no
    token for 07.07 or 08.07 anywhere in the query, so no amount of ranking can
    surface those days. They are not ranked low; they are unmatchable.

So :func:`build_search_terms` returns independent OR-terms: content words with
the instruction scaffolding stripped, plus every date in a detected range
written in the formats a corpus is likely to use. The caller passes them as
``keyword_terms``, which only ever widens the keyword leg — the semantic leg and
the reranker keep the original question, so expansion cannot dilute them.

Deliberately not a model call: a flow that exists because the model is too small
to choose a tool is not a flow that should trust that model to write its query.
"""

from __future__ import annotations

import re
from datetime import date

# Beyond this the enumeration stops paying for itself: a query with hundreds of
# date terms is mostly noise, and a span that long is a corpus-wide question
# rather than a range one. Endpoints only past the cap.
_MAX_RANGE_DAYS = 40

# Backstop on total terms. Content terms are emitted first, so truncation drops
# date variants (the redundant part) before it drops topical signal.
_MAX_TERMS = 220

_MIN_TERM_CHARS = 2

# Vietnamese is written syllable-per-token, so multi-syllable phrases are
# filtered syllable by syllable. These are the words that survive `plainto_tsquery`
# but carry no retrieval signal: instruction verbs ("tổng hợp", "liệt kê"),
# quantifiers, prepositions, and the calendar vocabulary that wraps every date.
_STOPWORDS = frozenset(
    """
    tổng hợp tóm tắt lược liệt kê nêu trình bày phân tích đánh giá
    cho tôi mình bạn hãy vui lòng giúp xin
    các những mọi tất cả toàn bộ nhiều một số
    thông tin tin dữ liệu nội dung chi tiết
    về của và hoặc với theo tại trong ngoài từ đến tới qua vào ra trên dưới
    ngày tháng năm giai đoạn khoảng thời gian kỳ giữa
    là gì nào ai đâu sao có được bị cần phải nên rằng thì mà
    hai này đó kia ấy cái sự việc
    hôm nay mai
    summarize summary summarise overview list tell give show explain describe
    all any the a an of and or from to for in on at about with please
    what which who whom when where how why
    information info data content between during period range
    day days date dates month months year years
    """.split()
)

# Order matters: ISO is consumed first so its components cannot be re-read as a
# day/month pair by the day-first pattern.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?\b")

_WORD = re.compile(r"\w+", re.UNICODE)


def build_search_terms(question: str) -> list[str]:
    """Independent keyword terms for ``question``, to be OR-ed by the caller.

    Returns ``[]`` when the question yields nothing better than itself, which
    tells the caller to fall back to plain whole-query matching.
    """
    if not question or not question.strip():
        return []

    dates, remainder = _extract_dates(question)
    terms = _content_terms(remainder)
    terms.extend(_date_terms(dates))

    deduped = _dedupe(terms)
    # A single term is what plain whole-query matching already does, so there is
    # nothing to gain from the OR path.
    return deduped[:_MAX_TERMS] if len(deduped) > 1 else []


def _content_terms(text: str) -> list[str]:
    """Topical words from ``text``: instruction scaffolding and bare numbers dropped."""
    terms = []
    for match in _WORD.finditer(text.lower()):
        word = match.group(0)
        if len(word) < _MIN_TERM_CHARS or word.isdigit() or word in _STOPWORDS:
            continue
        terms.append(word)
    return terms


def _extract_dates(question: str) -> tuple[list[date], str]:
    """Parse every date in ``question``; also return it with those spans removed.

    Removing the spans keeps :func:`_content_terms` from re-reading ``06.07.2026``
    as the three meaningless numbers ``06``, ``07`` and ``2026``.
    """
    found: list[date] = []
    # Years seen so far, used to complete a bare "6/7". Vietnamese questions
    # routinely date the endpoints once: "từ 6/7 đến 13/7/2026".
    fallback_year: int | None = None

    def _take_iso(match: re.Match[str]) -> str:
        nonlocal fallback_year
        year, month, day = (int(part) for part in match.groups())
        parsed = _as_date(year, month, day)
        if parsed is None:
            return match.group(0)
        found.append(parsed)
        fallback_year = parsed.year
        return " "

    remainder = _ISO_DATE.sub(_take_iso, question)

    def _take_dmy(match: re.Match[str]) -> str:
        nonlocal fallback_year
        first, second, raw_year = match.group(1), match.group(2), match.group(3)
        year = _as_year(raw_year) if raw_year else fallback_year
        if year is None:
            # A year may still be established further right ("từ 6/7 đến
            # 13/7/2026"); a second pass below picks these up.
            return match.group(0)
        parsed = _as_dmy(int(first), int(second), year)
        if parsed is None:
            return match.group(0)
        found.append(parsed)
        fallback_year = parsed.year
        return " "

    remainder = _DMY_DATE.sub(_take_dmy, remainder)
    if fallback_year is not None and _DMY_DATE.search(remainder):
        # Second pass: yearless dates that appeared before the first dated one.
        remainder = _DMY_DATE.sub(_take_dmy, remainder)

    return found, remainder


def _as_year(raw: str) -> int:
    """Two-digit years are this century; four-digit years pass through."""
    year = int(raw)
    return year + 2000 if year < 100 else year


def _as_dmy(first: int, second: int, year: int) -> date | None:
    """Read a numeric pair as day/month, the Vietnamese convention.

    Falls back to month/day only when day-first cannot be true (``7/25``), which
    keeps an ISO-ish or US-ish stray date in a mostly-Vietnamese corpus readable.
    """
    parsed = _as_date(year, second, first)
    if parsed is not None:
        return parsed
    return _as_date(year, first, second)


def _as_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_terms(dates: list[date]) -> list[str]:
    """Surface forms for every date implied by ``dates``, range interior included."""
    return [form for value in _expand_range(dates) for form in _formats(value)]


def _expand_range(dates: list[date]) -> list[date]:
    """Fill in the days between the earliest and latest date, when that is a range.

    The interior is the whole point: a question naming 06.07 and 13.07 is asking
    about the eight days those two bound, and the six in the middle appear
    nowhere in its text.
    """
    if len(dates) < 2:
        return dates
    start, end = min(dates), max(dates)
    span = (end - start).days
    if span < 1 or span > _MAX_RANGE_DAYS:
        return sorted(set(dates))
    return [date.fromordinal(start.toordinal() + offset) for offset in range(span + 1)]


def _formats(value: date) -> list[str]:
    """The ways a corpus writes one date.

    Each is a separate term because Postgres tokenizes them differently —
    ``06/07/2026`` and ``06.07.2026`` are unrelated lexemes, so matching one
    says nothing about the other. Emitting all of them costs an OR branch each
    and removes the need to know which convention the corpus follows.
    """
    day, month, year = value.day, value.month, value.year
    return [
        f"{day:02d}/{month:02d}/{year}",
        f"{day:02d}.{month:02d}.{year}",
        f"{day}/{month}/{year}",
        f"{day:02d}-{month:02d}-{year}",
        f"{year}-{month:02d}-{day:02d}",
    ]


def _dedupe(terms: list[str]) -> list[str]:
    """Drop repeats, keeping first-seen order (content terms stay ahead of dates)."""
    seen: set[str] = set()
    unique = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique.append(term)
    return unique


__all__ = ["build_search_terms"]
