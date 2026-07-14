"""Rewrite model ``[n]`` citations into frontend ``[citation:<payload>]`` markers.

The model cites with tiny ordinals ``[n]``. Several citations may be several
brackets (``[1][2]``, ``[1], [2]``) or a single bracket naming several ordinals —
a list (``[1,2]``) or, once a turn retrieves enough passages that the ordinals get
large, a range (``[57-61]``). Every ordinal named by a bracket is resolved through
the registry and replaced with a marker the citation renderer understands.
Unknown or not-yet-renderable ordinals are dropped, so a bad citation disappears
rather than misleads. Code spans are left untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .markers import to_frontend_payload
from .registry import CitationRegistry

# Fenced (```...```) and inline (`...`) code; mirrors the frontend's single
# code-region pattern so ordinals inside examples are never rewritten.
_CODE_REGION = re.compile(r"```[\s\S]*?```|`[^`\n]+`")

# A bracket naming one or more ordinals: `[1]`, `[12]`, `[1,2]`, `[57-61]`,
# `[1, 3-5]`. We deliberately match even when glued to the preceding word
# (`docs[17]`) because the model very frequently writes citations that way —
# requiring a non-word char before `[` (to dodge `arr[1]`) silently dropped those
# citations, leaving raw `[n]` that both fails to render and reads like array
# indexing. Genuine code/array syntax is instead protected by the code-region
# carve-out below. Adjacent citations `[1][2]` are each rewritten.
_ITEM = r"\d+(?:\s*[-–—]\s*\d+)?"
_GROUP = re.compile(rf"\[\s*({_ITEM}(?:\s*,\s*{_ITEM})*)\s*\]")

# A bare `[n]`: the only shape whose failure to resolve is unambiguous enough to
# delete. See _rewriter.
_BARE = re.compile(r"\d+")

# A range spanning more than this many ordinals is not a citation — it is prose
# the pattern happens to fit, e.g. a year range `[1990-2020]` or a page range.
# Registries stay small (one entry per retrieved passage), so a real citation
# range is short.
_MAX_RANGE = 50


def normalize_citations(text: str, registry: CitationRegistry) -> str:
    """Replace each citation bracket with its resolved markers; drop the unresolved."""
    if not text:
        return text

    rewrite = _rewriter(registry)
    return _outside_code(text, lambda span: _GROUP.sub(rewrite, span))


def _ordinals(inner: str) -> list[int] | None:
    """The ordinals a bracket names, in order, deduped.

    ``None`` means "this bracket is not a citation" — an inverted or implausibly
    wide range — and the caller leaves the text alone.
    """
    found: list[int] = []
    for part in inner.split(","):
        bounds = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", part)
        if bounds:
            start, end = int(bounds.group(1)), int(bounds.group(2))
            if end < start or end - start + 1 > _MAX_RANGE:
                return None
            span = range(start, end + 1)
        else:
            span = [int(part.strip())]
        found.extend(n for n in span if n not in found)
    return found


def _rewriter(registry: CitationRegistry) -> Callable[[re.Match[str]], str]:
    """Build the substitution that turns one bracket into markers (or drops it)."""

    def rewrite(match: re.Match[str]) -> str:
        inner = match.group(1)
        ordinals = _ordinals(inner)
        if ordinals is None:
            return match.group(0)

        markers = []
        for n in ordinals:
            entry = registry.resolve(n)
            payload = to_frontend_payload(entry) if entry else None
            if payload is not None:
                markers.append(f"[citation:{payload}]")
        if markers:
            return "".join(markers)

        # Nothing resolved. A bare `[9]` is a citation the model invented, and
        # deleting it is the long-standing behaviour. But a list or range that
        # resolves to nothing is far more likely to be prose we misread — a year
        # or page range — and deleting *that* would silently eat the author's
        # text. Leave it exactly as written.
        return "" if _BARE.fullmatch(inner.strip()) else match.group(0)

    return rewrite


def _outside_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` to non-code spans only; code regions pass through verbatim."""
    parts = []
    last = 0
    for region in _CODE_REGION.finditer(text):
        parts.append(transform(text[last : region.start()]))
        parts.append(region.group(0))
        last = region.end()
    parts.append(transform(text[last:]))
    return "".join(parts)


__all__ = ["normalize_citations"]
