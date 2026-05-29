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


def _anchor_in_text(text: str, heading: str, start: int) -> int:
    """Locate a heading in text, tolerating whitespace/OCR drift."""
    idx = text.find(heading, start)
    if idx >= 0:
        return idx
    tokens = heading.split()
    if len(tokens) < 2:
        return -1
    pattern = r"\s+".join(re.escape(t) for t in tokens[:6])
    m = re.search(pattern, text[start:])
    return start + m.start() if m else -1


def _clause_id_from_heading(heading: str, fallback_index: int) -> str:
    m = re.match(
        r"^\s*(?:Clause|Article|Section)?\s*"
        r"([\(\[]?\d+(?:[\.\-]\d+)*[\)\]]?|[A-Z](?:\.\d+)*)",
        heading,
    )
    if m:
        return m.group(1).strip("().[]")
    return str(fallback_index)


_LLM_SYSTEM_PROMPT = (
    "You are a contract structure analyser. Identify the first 40-80 "
    "characters of every clause, numbered section, or lettered "
    "subsection in the contract — copied VERBATIM from the document, "
    "in document order. Do not paraphrase, do not add or remove "
    "characters. Respond with strict JSON of the form "
    '{"headings": ["<verbatim opening 1>", "<verbatim opening 2>", ...]}.'
)


async def _llm_headings_for_chunk(chunk: str) -> list[str]:
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
    headings: list[str] = []
    for h in raw_headings:
        if isinstance(h, str):
            h = h.strip()
            if 4 <= len(h) <= 200:
                headings.append(h)
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
    all_headings: list[str] = []
    for off in offsets:
        chunk = text[off : off + _LLM_CHUNK_SIZE]
        headings = await _llm_headings_for_chunk(chunk)
        all_headings.extend(headings)

    found: list[tuple[int, str]] = []
    for h in all_headings:
        idx = _anchor_in_text(text, h, 0)
        if idx < 0:
            continue
        found.append((idx, h))

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


async def split_clauses_async(text: str) -> list[Clause]:
    """Async clause splitter — LLM anchor method is the primary path.

    Regex (`split_clauses`) is kept as a fallback for when LLM is not
    configured, fails, or produces too few clauses to be trustworthy.
    """
    from api_clients import is_configured

    if not is_configured() or len(text) < 1500:
        return split_clauses(text)

    try:
        llm_clauses = await _split_with_llm(text)
    except Exception:
        return split_clauses(text)

    if len(llm_clauses) < 3:
        return split_clauses(text)
    return llm_clauses
