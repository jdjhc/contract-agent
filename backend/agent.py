"""
Agent orchestration.

The agent is a thin pipeline:

    upload  →  parse text + clauses
            →  classify contract type   (keyword baseline; LLM refines if configured)
            →  compare clauses to UoA positions  (deterministic; LLM augments if configured)
            →  build report

LLM hooks live in api_clients.py — leave them as-is for the offline demo,
or wire in your provider when ready.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from api_clients import CallRecord, call_llm, is_configured, track_usage
from models import (
    Clause,
    ContractReview,
    ContractType,
    FlagItem,
    FlagLevel,
    ReviewMetrics,
    display_clause_id,
)
from services.classifier import classify_by_keywords
from services.comparator import compare_clauses, load_positions
from services.parser import extract_text_async, split_clauses_async
from services.reporter import build_report
from services.templates import template_filenames_for, template_text_for


@dataclass
class StoredDocument:
    document_id: str
    filename: str
    text: str
    clauses: list[Clause] = field(default_factory=list)
    ingest_calls: list[CallRecord] = field(default_factory=list)


# Tiny in-memory store. Swap for Redis / DB before any real deployment.
_store: dict[str, StoredDocument] = {}


def get_document(document_id: str) -> StoredDocument | None:
    return _store.get(document_id)


async def ingest(filename: str, raw: bytes) -> StoredDocument:
    """Ingest a file. Uses GPT-4o vision OCR for every PDF page (kills both
    scanned-page blackouts and multi-column interleaving)."""
    with track_usage() as ingest_usage:
        text = await extract_text_async(filename, raw)
        clauses = await split_clauses_async(text)
    doc = StoredDocument(
        document_id=uuid.uuid4().hex,
        filename=filename,
        text=text,
        clauses=clauses,
        ingest_calls=list(ingest_usage.calls),
    )
    _store[doc.document_id] = doc
    return doc


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

async def classify(doc: StoredDocument) -> tuple[ContractType, float, str]:
    """Return (contract_type, confidence, rationale)."""
    base_type, base_conf, base_reason = classify_by_keywords(doc.text)

    if not is_configured():
        return base_type, base_conf, base_reason

    valid = ", ".join(t.value for t in ContractType if t != ContractType.UNKNOWN)
    system = (
        "You are a research-contracts triage assistant. Classify the contract "
        "into exactly one of the listed categories. Respond with strict JSON "
        '{"contract_type": <one of the categories>, "confidence": <0..1>, '
        '"rationale": <short reason>}.'
    )
    user = (
        f"Allowed categories: {valid}.\n"
        f"Keyword baseline suggests: {base_type.value} "
        f"(confidence {base_conf}, {base_reason}).\n\n"
        f"Document excerpt:\n---\n{doc.text[:6000]}\n---\n"
    )
    raw = await call_llm(system, user, json_mode=True, label="classify")
    try:
        data = json.loads(raw)
        ct = ContractType(data["contract_type"])
        conf = float(data.get("confidence", base_conf))
        rationale = str(data.get("rationale", base_reason))
        return ct, max(0.0, min(1.0, conf)), rationale
    except (json.JSONDecodeError, KeyError, ValueError):
        return base_type, base_conf, base_reason


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------

async def review(doc: StoredDocument) -> ContractReview:
    with track_usage() as usage:
        contract_type, confidence, _ = await classify(doc)
        flags = compare_clauses(doc.clauses, contract_type)

        if is_configured():
            flags = await _augment_flags_with_llm(doc, contract_type, flags)

        summary = await _llm_summary(doc, contract_type, flags) if is_configured() else None
        report = build_report(
            document_id=doc.document_id,
            filename=doc.filename,
            contract_type=contract_type,
            confidence=confidence,
            flags=flags,
            summary=summary,
        )

    all_calls = list(doc.ingest_calls) + list(usage.calls)
    sample_call = all_calls[0] if all_calls else None
    report.metrics = ReviewMetrics(
        n_calls=len(all_calls),
        input_tokens=sum(c.input_tokens for c in all_calls),
        output_tokens=sum(c.output_tokens for c in all_calls),
        total_tokens=sum(c.input_tokens + c.output_tokens for c in all_calls),
        latency_ms=round(sum(c.latency_ms for c in all_calls), 1),
        total_cost_usd=round(sum(c.cost_usd for c in all_calls), 6),
        backend=sample_call.backend if sample_call else "",
        model=sample_call.model if sample_call else "",
    )
    report.references_used = (
        ["UoA Preferred Contracting Positions (Sept 2025 draft)"]
        + [f"UoA Template — {f}" for f in template_filenames_for(contract_type)]
    )
    return report


_AUGMENT_SYSTEM = (
    "You are an expert research-contracts reviewer for the University of Auckland (UoA). "
    "You will be given THREE inputs:\n"
    "  1. UoA Preferred Contracting Positions — the cross-type policy (rules).\n"
    "  2. The UoA Standard Template for this contract type — the canonical "
    "wording UoA itself would draft. (Absent for some types.)\n"
    "  3. A SINGLE clause from the contract under review, plus candidate "
    "flags raised against it by a deterministic comparator.\n\n"
    "Refine the flag list FOR THIS CLAUSE ONLY. The flag system maps to "
    "the Positions tiers:\n"
    "  • Clause matches Preferred / matches the UoA Template       → green\n"
    "  • Clause matches Acceptable tier (deviation pre-approved)   → amber\n"
    "  • Clause falls outside Acceptable / triggers Escalation OR \n"
    "    materially deviates from the UoA Template wording         → red\n"
    "  • Clause topic not covered by any Position AND no template  → blue\n\n"
    "Important reasoning rules:\n"
    "  • When evaluating liability/indemnity, take any monetary cap into account "
    "(a low cap can convert a literal 'assumes all liability' phrasing from RED "
    "to AMBER if total exposure stays well under NZD$500K).\n"
    "  • When evaluating publication, recognise that 'co-author + acknowledge + "
    "provide a copy' preserves the right to publish and is GREEN.\n"
    "  • When evaluating exclusion of indirect/consequential losses, recognise "
    "phrasings other than 'excluded' (e.g. 'will not extend to', 'shall not include').\n"
    "  • Any Intellectual Property clause (results ownership, IP assignment, "
    "background/foreground IP, patent rights) is AMBER and the rationale must "
    "refer the reader to Auckland UniServices — UoA Positions does not own IP.\n\n"
    "Return strict JSON: "
    '{"flags": [{"level":"green|amber|red|blue","clause_id":"..","clause_title":"..",'
    '"snippet":"..","rationale":"..","standard_ref":"UoA Position #... or UoA Template"}]}. '
    "Rationale should name the topic, name which UoA reference (Position id or "
    "the template) it ties to, state which tier the clause matches, and (for red) "
    "name the escalation route. Be concrete; don't hedge. "
    "Output flags ONLY for this clause — do not invent flags for other clauses."
)

# Cap clause text fed to LLM to avoid pathological cases (e.g. one giant clause
# absorbing most of the document). 6000 chars is well over the size of any
# normal contract clause.
_CLAUSE_TEXT_CAP = 6000

# Per-clause LLM calls run in parallel; cap to avoid hammering the provider.
_AUGMENT_CONCURRENCY = 10


def _augment_user_prompt(
    contract_type: ContractType,
    clause: Clause,
    clause_seeds: list[FlagItem],
    relevant_positions: list[dict],
    template_text: str,
) -> str:
    clause_body = clause.text[:_CLAUSE_TEXT_CAP]
    if len(clause.text) > _CLAUSE_TEXT_CAP:
        clause_body += f"\n... [truncated, full clause is {len(clause.text)} chars]"

    # Strip the internal duplicate-disambiguation suffix from any id the LLM sees.
    display_id = display_clause_id(clause.id)
    seed_dumps = []
    for f in clause_seeds:
        d = f.model_dump()
        d["clause_id"] = display_clause_id(d.get("clause_id", ""))
        seed_dumps.append(d)

    parts = [f"Contract type: {contract_type.value}\n"]
    parts.append(
        "## UoA Preferred Contracting Positions (only those applicable to this type)\n"
        + json.dumps(relevant_positions, indent=2)
    )
    if template_text:
        parts.append(
            "## UoA Standard Template for this contract type (canonical wording)\n"
            + template_text
        )
    else:
        parts.append(
            "## UoA Standard Template\n"
            "(No UoA template registered for this contract type — rely on the "
            "Positions document only.)"
        )
    parts.append(
        f"## Clause under review\n"
        f"id: {display_id}\n"
        f"title: {clause.title}\n"
        f"text:\n{clause_body}"
    )
    parts.append(
        "## Candidate flags raised against this clause by the deterministic comparator\n"
        + json.dumps(seed_dumps, indent=2)
    )
    return "\n\n".join(parts)


def _parse_augment_response(raw: str) -> list[FlagItem] | None:
    try:
        data = json.loads(raw)
        items = data.get("flags", [])
    except (json.JSONDecodeError, ValueError):
        return None
    out: list[FlagItem] = []
    for item in items:
        try:
            out.append(FlagItem(
                level=FlagLevel(item["level"]),
                clause_id=str(item.get("clause_id", "")),
                clause_title=str(item.get("clause_title", "")),
                snippet=str(item.get("snippet", "")),
                rationale=str(item.get("rationale", "")),
                standard_ref=item.get("standard_ref"),
            ))
        except (KeyError, ValueError):
            continue
    return out


async def _augment_one_clause(
    contract_type: ContractType,
    clause: Clause,
    clause_seeds: list[FlagItem],
    relevant_positions: list[dict],
    template_text: str,
    sem: asyncio.Semaphore,
) -> list[FlagItem]:
    user = _augment_user_prompt(
        contract_type, clause, clause_seeds, relevant_positions, template_text
    )
    async with sem:
        try:
            raw = await call_llm(
                _AUGMENT_SYSTEM, user, json_mode=True,
                max_tokens=2000, label="augment_clause",
            )
        except Exception:
            return clause_seeds
    refined = _parse_augment_response(raw)
    if refined is None:
        return clause_seeds
    # Trust our own clause id — LLM only sees the stripped display id, so we
    # always overwrite with the true unique internal id (and the verbatim title).
    for f in refined:
        f.clause_id = clause.id
        f.clause_title = clause.title
    return refined or clause_seeds


async def _augment_flags_with_llm(
    doc: StoredDocument,
    contract_type: ContractType,
    seed_flags: list[FlagItem],
) -> list[FlagItem]:
    """Refine deterministic flags via one LLM call PER CLAUSE.

    Strategy: group seed flags by clause_id, then dispatch one LLM call for
    each clause with seeds. This keeps every response well under any token
    cap and lets us parallelise. Clauses with no seed flags are skipped —
    the deterministic comparator gates the LLM, so we don't burn calls on
    clauses nobody flagged.
    """
    if not seed_flags:
        return []

    positions = load_positions()
    relevant_positions = [
        p for p in positions
        if "any" in p.get("applies_to", []) or contract_type.value in p.get("applies_to", [])
    ]
    template_text = template_text_for(contract_type)

    clause_lookup: dict[str, Clause] = {c.id: c for c in doc.clauses}

    groups: dict[str, list[FlagItem]] = defaultdict(list)
    for f in seed_flags:
        groups[f.clause_id].append(f)

    sem = asyncio.Semaphore(_AUGMENT_CONCURRENCY)
    tasks = []
    fallback_seeds: list[list[FlagItem]] = []
    for cid, seeds in groups.items():
        clause = clause_lookup.get(cid)
        if clause is None:
            fallback_seeds.append(seeds)
            continue
        tasks.append(
            _augment_one_clause(
                contract_type, clause, seeds,
                relevant_positions, template_text, sem,
            )
        )

    results = await asyncio.gather(*tasks) if tasks else []
    refined: list[FlagItem] = []
    for grp in results:
        refined.extend(grp)
    for grp in fallback_seeds:
        refined.extend(grp)
    return refined or seed_flags


async def _llm_summary(
    doc: StoredDocument,
    contract_type: ContractType,
    flags: list[FlagItem],
) -> str | None:
    if not flags:
        return None
    system = (
        "You write 2-3 sentence executive summaries for a research-contracts "
        "review report. Mention overall risk posture, the headline issues, "
        "and a recommended next step. Plain prose, no bullets."
    )
    flag_dumps = []
    for f in flags:
        d = f.model_dump()
        d["clause_id"] = display_clause_id(d.get("clause_id", ""))
        flag_dumps.append(d)
    user = (
        f"Contract type: {contract_type.value}\n"
        f"Filename: {doc.filename}\n"
        f"Flags: {json.dumps(flag_dumps)}\n"
    )
    text = await call_llm(system, user, max_tokens=400, label="summary")
    return text.strip() or None


# --------------------------------------------------------------------------
# Chat (open-ended Q&A about the loaded document)
# --------------------------------------------------------------------------

async def chat(
    document_id: str | None,
    history: list[dict],
    message: str,
) -> str:
    doc = _store.get(document_id) if document_id else None
    context = ""
    if doc:
        context = (
            f"\nLoaded document: {doc.filename}\n"
            f"Excerpt:\n---\n{doc.text[:4000]}\n---\n"
        )
    if not is_configured():
        return (
            "[mock chat] No LLM provider configured. Set ANTHROPIC_API_KEY in "
            "backend/.env to enable real conversation."
            + context
        )
    system = (
        "You are a research-contracts adviser assistant for the University of "
        "Auckland. Answer concisely. You do NOT provide legal advice. Always "
        "remind the user that final decisions sit with the Research Contracts team."
        + context
    )
    transcript = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history[-10:])
    user = f"{transcript}\nUSER: {message}"
    return await call_llm(system, user, max_tokens=800)
