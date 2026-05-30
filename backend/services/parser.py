"""Extract text and clauses from uploaded PDF / DOCX / TXT files."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
import docx

from models import DUP_SENTINEL, Clause


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def extract_text(filename: str, raw: bytes) -> str:
    """Synchronous text extraction — text-layer only.

    Use `extract_text_async` to additionally OCR scanned pages via GPT-4o vision.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                pages = [(p.extract_text() or "") for p in pdf.pages]
            text = "\n".join(pages).strip()
            if text:
                return text
        except Exception:
            pass
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    if ext == ".docx":
        document = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in document.paragraphs if p.text)
    if ext in {".txt", ".md"}:
        return raw.decode("utf-8", errors="ignore")
    raise ValueError(f"Unsupported file type: {ext}")


async def extract_text_async(filename: str, raw: bytes) -> str:
    """Async extraction — for PDFs, falls back to GPT-4o vision OCR on any
    page that doesn't have an extractable text layer (scanned pages).

    DOCX/TXT/MD don't need OCR; they go through the sync path.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from services.vision_ocr import extract_text_with_vision_fallback
        return await extract_text_with_vision_fallback(raw)
    return extract_text(filename, raw)


# Numbered clause openers: "1.", "1.1", "1.1.2", "Article 5", "Section 3", "Clause 4".
# Require a trailing punctuation/space pattern so footnote markers ("Laws 2 ,")
# don't match.
_NUMBERED_HEAD = re.compile(
    r"""
    ^\s*
    (
        (?:Clause|Article|Section)\s+\d+(?:\.\d+)*       # Article 5 / Clause 4.2
        |
        \d+(?:\.\d+){1,3}                                 # 1.2 / 1.2.3 (must have a dot)
        |
        \d{1,2}\.                                         # 1. / 17.
        |
        \(\d{1,3}\)                                       # (1) / (12)
    )
    \s+
    (.{1,120}?)$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

# ALL-CAPS section heads as used in UoA templates: "PARTIES", "DEFINITIONS
# AND INTERPRETATION", "GENERAL TERMS", "CONFIDENTIALITY". Min 4 chars,
# allows ampersands, slashes and parentheses, must be alone on the line.
_CAPS_HEAD = re.compile(
    r"""
    ^\s*
    (
        [A-Z][A-Z0-9 \&/\(\)\-,]{3,80}
    )
    \s*$
    """,
    re.MULTILINE | re.VERBOSE,
)


def split_clauses(text: str) -> list[Clause]:
    """Split a contract into clauses.

    Tries (in order):
      1. Numbered clause headings — preferred, used by most external contracts.
      2. ALL-CAPS section headings — used by the UoA templates themselves.
      3. Paragraph fall-back — guarantees the UI always has something to show.
    """
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []

    numbered = list(_NUMBERED_HEAD.finditer(text))
    if len(numbered) >= 3:
        return _slice_by_matches(
            text, numbered,
            id_from_match=lambda m: m.group(1).strip().rstrip(".").strip("()"),
        )

    caps = [
        m for m in _CAPS_HEAD.finditer(text)
        # Reject obvious noise: lines that are mostly digits / punctuation.
        if sum(c.isalpha() for c in m.group(1)) >= 4
    ]
    if len(caps) >= 3:
        return _slice_by_matches(
            text, caps,
            id_from_match=lambda m, i=[0]: (i.__setitem__(0, i[0] + 1), str(i[0]))[1],
            title_from_match=lambda m: m.group(1).strip(),
        )

    # Final fall-back: paragraph chunks.
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    return [
        Clause(id=str(i + 1), title=_first_line(c), text=c)
        for i, c in enumerate(chunks)
    ]


def _slice_by_matches(text, matches, *, id_from_match, title_from_match=None) -> list[Clause]:
    clauses: list[Clause] = []
    for i, m in enumerate(matches):
        cid = id_from_match(m)
        if title_from_match:
            title = title_from_match(m).strip(" .:-—")
        else:
            # numbered case: group(2) is the inline title (may be empty)
            title = (m.group(2) or "").strip(" .:-—") if m.lastindex and m.lastindex >= 2 else ""
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        full = f"{title}\n{body}".strip() if title else body
        if not full:
            continue
        clauses.append(Clause(id=cid, title=title, text=full))
    return clauses


def _first_line(s: str, limit: int = 80) -> str:
    line = s.strip().split("\n", 1)[0]
    return line[:limit]


# ----------------------------------------------------------------------------
# LLM fallback for clause splitting
#
# When the regex heuristics fail (e.g. unusual numbering, OCR'd PDFs without
# clear heading patterns), we ask the LLM to point at the *verbatim* opening
# of each clause. The model never rewrites text — its output is only used as
# an anchor for slicing the original string. That keeps the downstream
# pipeline working on real contract language, not hallucinated paraphrases.
# ----------------------------------------------------------------------------

_LLM_INPUT_CHAR_BUDGET = 100000
_LLM_CHUNK_SIZE = 80000
_LLM_CHUNK_OVERLAP = 4000


def _needs_llm_fallback(text: str, clauses: list[Clause]) -> bool:
    """Kept for backwards compatibility / debugging — no longer the gate."""
    if len(text) < 1500:
        return False
    if len(clauses) < 3:
        return True
    longest = max((len(c.text) for c in clauses), default=0)
    return longest > 0.7 * len(text)


def _fuzzy_search(text: str, snippet: str, start: int) -> tuple[int, int]:
    """Find snippet in text[start:], tolerating whitespace/OCR drift.

    Returns (match_start, match_end) or (-1, -1).
    """
    idx = text.find(snippet, start)
    if idx >= 0:
        return idx, idx + len(snippet)
    tokens = snippet.split()
    if not tokens:
        return -1, -1
    pattern = r"\s+".join(re.escape(t) for t in tokens[: min(8, len(tokens))])
    m = re.search(pattern, text[start:])
    if m:
        return start + m.start(), start + m.end()
    return -1, -1


def _anchor_with_context(text: str, before: str, heading: str) -> int:
    """Locate a heading using its preceding-context snippet to disambiguate.

    The `before` snippet is what separates a real body heading from its
    table-of-contents twin: the body heading is preceded by the previous
    clause's wording, the TOC entry by another TOC line. We search for the
    `before+heading` pair and return the position where the heading starts.
    """
    if before:
        _, be = _fuzzy_search(text, before, 0)
        if be >= 0:
            hs, _ = _fuzzy_search(text, heading, be)
            if hs >= 0:
                return hs
    # No context, or context not found — fall back to plain heading search.
    hs, _ = _fuzzy_search(text, heading, 0)
    return hs


def _clause_id_from_heading(heading: str, fallback_index: int) -> str:
    m = re.match(
        r"^\s*(?:Clause|Article|Section)?\s*"
        r"([\(\[]?\d+(?:[\.\-]\d+)*[\)\]]?|[A-Z](?:\.\d+)*)",
        heading,
    )
    if m:
        return m.group(1).strip("().[]")
    return str(fallback_index)


_LLM_SYSTEM_PROMPT = """\
You are a contract structure analyser. Identify every clause or section \
HEADING that introduces real body content, and for each return two short \
verbatim snippets that let us locate it unambiguously in the source text.

For each heading return an object:
  {"before": "<last ~6 words of the text immediately PRECEDING the heading>",
   "heading": "<the heading and first few words after it, ~40-80 chars>"}

RULES — read carefully:
1. A heading is a numbered or lettered label introducing a new clause/section, \
e.g. "1.", "1.1", "2.1.3", "Article 5", "Section 3", "(a)", "DEFINITIONS", \
"14. Travel Stipends".
2. Return ONLY headings of the ACTUAL BODY. IGNORE the table of contents / \
index entirely (its lines end in page numbers). If the same heading appears \
both in a table of contents and in the body, return it ONCE, using the BODY \
occurrence's surrounding text.
3. "before" must be the verbatim text that comes right before the heading in \
the body (use "" only for the very first heading). This is what disambiguates \
a body heading from its table-of-contents twin.
4. Copy both snippets VERBATIM from the document.
5. Output in document order.
6. Respond with strict JSON: \
{"headings": [{"before": "...", "heading": "..."}, ...]}\
"""


async def _llm_headings_for_chunk(chunk: str) -> list[dict[str, str]]:
    from api_clients import call_llm

    user = f"Contract text:\n---\n{chunk}\n---"
    raw = await call_llm(
        _LLM_SYSTEM_PROMPT, user, json_mode=True,
        label="split_clauses", max_tokens=4000,
    )
    try:
        data = json.loads(raw)
        raw_headings = data.get("headings", [])
    except (json.JSONDecodeError, ValueError):
        return []
    headings: list[dict[str, str]] = []
    for h in raw_headings:
        if isinstance(h, dict):
            heading = str(h.get("heading", "")).strip()
            before = str(h.get("before", "")).strip()
            if 4 <= len(heading) <= 200:
                headings.append({"before": before, "heading": heading})
        elif isinstance(h, str):  # tolerate old flat-string shape
            h = h.strip()
            if 4 <= len(h) <= 200:
                headings.append({"before": "", "heading": h})
    return headings


def _chunk_offsets(text: str) -> list[int]:
    """Return start offsets for sliding-window chunks of the document."""
    n = len(text)
    if n <= _LLM_INPUT_CHAR_BUDGET:
        return [0]
    offsets: list[int] = []
    step = _LLM_CHUNK_SIZE - _LLM_CHUNK_OVERLAP
    pos = 0
    while pos < n:
        offsets.append(pos)
        if pos + _LLM_CHUNK_SIZE >= n:
            break
        pos += step
    return offsets


async def _split_with_llm(text: str) -> list[Clause]:
    """Identify clause boundaries with an LLM, then slice the original text.

    The LLM only emits short verbatim openings (anchors). Contract body is
    never copied through the model — we locate each anchor in the original
    text and slice between them, so no hallucinated wording can leak in.

    For long documents (> _LLM_INPUT_CHAR_BUDGET), we slide an overlapping
    window across the text, gather all headings, then dedupe by position.
    """
    offsets = _chunk_offsets(text)
    all_headings: list[dict[str, str]] = []
    for off in offsets:
        chunk = text[off : off + _LLM_CHUNK_SIZE]
        headings = await _llm_headings_for_chunk(chunk)
        all_headings.extend(headings)

    found: list[tuple[int, str]] = []
    for h in all_headings:
        idx = _anchor_with_context(text, h.get("before", ""), h["heading"])
        if idx < 0:
            continue
        found.append((idx, h["heading"]))

    found.sort(key=lambda p: p[0])
    positions: list[tuple[int, str]] = []
    for pos, heading in found:
        if positions and pos - positions[-1][0] < 8:
            continue
        positions.append((pos, heading))

    if len(positions) < 3:
        return []

    clauses: list[Clause] = []
    seen_ids: dict[str, int] = {}
    for i, (start, heading) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        cid = _clause_id_from_heading(heading, i + 1)
        # Ensure ids are unique within a document — downstream code uses
        # clause_id as a dict key. The DUP_SENTINEL suffix is stripped
        # before sending to the LLM or rendering for users (see
        # models.display_clause_id), so user-visible ids remain clean.
        if cid in seen_ids:
            seen_ids[cid] += 1
            cid = f"{cid}{DUP_SENTINEL}{seen_ids[cid]}"
        else:
            seen_ids[cid] = 1
        title = heading.strip()[:200]
        clauses.append(Clause(id=cid, title=title, text=body))
    return clauses


# If any single clause exceeds this character count the split is considered
# poor and a retry is warranted (~500 words ≈ 3000 chars).
_RETRY_MAX_CLAUSE_CHARS = 3000
_SPLIT_MAX_RETRIES = 3


def _split_quality_ok(clauses: list[Clause]) -> bool:
    """Return True if no single clause exceeds the character-count threshold."""
    if not clauses:
        return False
    return max(len(c.text) for c in clauses) <= _RETRY_MAX_CLAUSE_CHARS


async def split_clauses_async(text: str) -> list[Clause]:
    """Async clause splitter — LLM anchor method is the primary path.

    Regex (`split_clauses`) is kept as a fallback for when LLM is not
    configured, fails, or produces too few clauses to be trustworthy.

    If the LLM produces a poor split (one clause > 40 % of total text),
    the call is retried up to _SPLIT_MAX_RETRIES times before falling back
    to the regex splitter.
    """
    from api_clients import is_configured

    if not is_configured() or len(text) < 1500:
        return split_clauses(text)

    best: list[Clause] = []

    for attempt in range(_SPLIT_MAX_RETRIES):
        try:
            llm_clauses = await _split_with_llm(text)
        except Exception:
            break

        if len(llm_clauses) >= 3 and _split_quality_ok(llm_clauses):
            return llm_clauses

        # Keep the attempt with the most clauses as a fallback.
        if len(llm_clauses) > len(best):
            best = llm_clauses

    # Use best LLM result if it produced enough clauses, else regex.
    if len(best) >= 3:
        return best
    return split_clauses(text)
