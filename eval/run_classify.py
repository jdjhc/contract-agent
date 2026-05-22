"""Node test: run *only* the classification node.

Two input modes:
  1. From PDFs        — runs ingest + classify (slow, but self-contained).
  2. From ingest dir  — reads checkpoints written by run_ingest.py, classifies
                         only (fast: no OCR re-run).

Per-file output JSON shape:
    {
      "filename": "...",
      "predicted_type": "Material Transfer Agreement",
      "confidence": 0.95,
      "rationale": "...",
      "expected_hint": "Material Transfer Agreement" | null,
      "name_match": true | false | null,
      "wall_ms": 1234,
      "source_text_chars": 16050,
      "source_clauses": 19
    }

Usage:
    uv run python eval/run_classify.py
    uv run python eval/run_classify.py --from eval/reports/ingest_<ts>
    uv run python eval/run_classify.py --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

from agent import StoredDocument, classify, ingest  # noqa: E402
from api_clients import is_configured, track_usage  # noqa: E402
from models import Clause  # noqa: E402
from services.parser import extract_text_async, split_clauses_async  # noqa: E402

DEFAULT_DIR = ROOT / "data" / "Contract Reviewer Agent" / "Redacted examples"


FRAGMENT_MAP: dict[str, list[str]] = {
    "Master Services Agreement Example 1 (1).pdf": [
        "Master Services Agreement Example 1.5.pdf",
    ],
}
_FRAGMENT_FILES = {f for fs in FRAGMENT_MAP.values() for f in fs}


_NAME_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^CDA\b|^NDA\b|Confidential", re.I), "Confidential Disclosure Agreement"),
    (re.compile(r"Collaboration Agreement", re.I), "Collaboration Agreement"),
    (re.compile(r"Consultancy", re.I), "Consultancy Services Agreement"),
    (re.compile(r"\bMTA\b|Material Transfer", re.I), "Material Transfer Agreement"),
    (re.compile(r"\bDTA\b|Data Transfer", re.I), "Data Transfer Agreement"),
    (re.compile(r"\bDAA\b|Data Access", re.I), "Data Access Agreement"),
    (re.compile(r"Master Services", re.I), "Master Services Agreement"),
    (re.compile(r"Service Provider|Provision of Services", re.I), "Provision of Services Agreement"),
    (re.compile(r"Subcontract", re.I), "Research Subcontract"),
    (re.compile(r"Student Research", re.I), "Student Research Agreement"),
    (re.compile(r"Goods and Services", re.I), "Provision of Services Agreement"),
]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


def _expected_from_name(name: str) -> str | None:
    for pat, value in _NAME_HINTS:
        if pat.search(name):
            return value
    return None


def _doc_from_checkpoint(cp_path: Path) -> StoredDocument:
    """Reconstruct a StoredDocument from a run_ingest checkpoint."""
    data = json.loads(cp_path.read_text(encoding="utf-8"))
    return StoredDocument(
        document_id=uuid.uuid4().hex,
        filename=data["filename"],
        text=data["text"],
        clauses=[Clause(**c) for c in data["clauses"]],
        ingest_calls=[],
    )


async def _ingest_merged(parent: Path, fragments: list[Path]) -> StoredDocument:
    with track_usage() as usage:
        parts = [await extract_text_async(parent.name, parent.read_bytes())]
        for fp in fragments:
            ft = await extract_text_async(fp.name, fp.read_bytes())
            parts.append(f"\n\n--- MERGED FROM {fp.name} ---\n\n{ft}")
        text = "".join(parts)
        clauses = await split_clauses_async(text)
    return StoredDocument(
        document_id=uuid.uuid4().hex,
        filename=parent.name,
        text=text,
        clauses=clauses,
        ingest_calls=list(usage.calls),
    )


async def _process_pdf(pdf: Path, out_dir: Path, fragments: list[Path]) -> dict:
    name = pdf.name
    t0 = time.perf_counter()
    try:
        if fragments:
            doc = await _ingest_merged(pdf, fragments)
        else:
            doc = await ingest(name, pdf.read_bytes())
        contract_type, confidence, rationale = await classify(doc)
        return _finalize(out_dir, name, doc, contract_type.value, confidence, rationale, t0,
                         merged=[f.name for f in fragments])
    except Exception as e:  # noqa: BLE001
        return {"filename": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def _process_checkpoint(cp_path: Path, out_dir: Path) -> dict:
    t0 = time.perf_counter()
    try:
        doc = _doc_from_checkpoint(cp_path)
        contract_type, confidence, rationale = await classify(doc)
        return _finalize(out_dir, doc.filename, doc, contract_type.value, confidence, rationale, t0,
                         merged=[])
    except Exception as e:  # noqa: BLE001
        return {"filename": cp_path.name, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _finalize(out_dir: Path, name: str, doc: StoredDocument,
              predicted: str, confidence: float, rationale: str,
              t0: float, merged: list[str]) -> dict:
    wall_ms = (time.perf_counter() - t0) * 1000
    hint = _expected_from_name(name)
    cp = {
        "filename": name,
        "predicted_type": predicted,
        "confidence": round(confidence, 3),
        "rationale": rationale,
        "expected_hint": hint,
        "name_match": (None if hint is None else (hint == predicted)),
        "wall_ms": round(wall_ms, 1),
        "source_text_chars": len(doc.text),
        "source_clauses": len(doc.clauses),
        "merged_fragments": merged,
    }
    out_path = out_dir / f"{_slug(Path(name).stem)}.json"
    out_path.write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **cp, "checkpoint": out_path.name}


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: classification only")
    p.add_argument("--from", dest="from_dir", default=None,
                   help="Directory of ingest checkpoints (skip OCR)")
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="PDF source dir (fallback)")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--pattern", default="*.pdf")
    p.add_argument("--out", default=None,
                   help="Output dir (default: eval/reports/classify_<timestamp>/)")
    args = p.parse_args(argv)

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"classify_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.from_dir:
        src = Path(args.from_dir).resolve()
        if not src.exists():
            print(f"ERROR: ingest dir not found: {src}", file=sys.stderr)
            return 2
        cps = sorted(p for p in src.glob("*.json") if p.name != "summary.json")
        if args.limit:
            cps = cps[: args.limit]
        if not cps:
            print(f"No checkpoints in {src}", file=sys.stderr)
            return 2
        print(f"Classifying {len(cps)} checkpoints from {src.relative_to(ROOT)}")
        print(f"Concurrency: {args.concurrency}  →  {out_dir.relative_to(ROOT)}\n", flush=True)
        targets: list = cps
        n_targets = len(cps)
        worker = lambda c: _process_checkpoint(c, out_dir)
    else:
        src = Path(args.dir).resolve()
        if not src.exists():
            print(f"ERROR: directory not found: {src}", file=sys.stderr)
            return 2
        all_pdfs = sorted(src.glob(args.pattern))
        pdfs = [p for p in all_pdfs if p.name not in _FRAGMENT_FILES]
        if args.limit:
            pdfs = pdfs[: args.limit]
        if not pdfs:
            print(f"No files matching {args.pattern} in {src}", file=sys.stderr)
            return 2
        n_merged = len(all_pdfs) - len(pdfs)
        print(f"Classifying {len(pdfs)} PDFs  (LLM configured: {is_configured()})")
        if n_merged:
            print(f"Skipping {n_merged} fragment file(s); merged into parents.")
        print(f"Concurrency: {args.concurrency}  →  {out_dir.relative_to(ROOT)}\n", flush=True)
        targets = pdfs
        n_targets = len(pdfs)

        def _make_worker():
            async def _w(pdf: Path) -> dict:
                fragments = [src / f for f in FRAGMENT_MAP.get(pdf.name, []) if (src / f).exists()]
                return await _process_pdf(pdf, out_dir, fragments)
            return _w
        worker = _make_worker()

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _run(item) -> dict:
        nonlocal done
        async with sem:
            res = await worker(item)
        async with lock:
            done += 1
            if res.get("ok"):
                hint = res.get("expected_hint")
                mark = "  " if hint is None else (" ✓" if res.get("name_match") else "✗ ")
                print(
                    f"[{done:>2}/{n_targets}] {mark} {res['filename'][:50]:<50}  "
                    f"→ {res['predicted_type']:<37}  conf={res['confidence']:<5}  "
                    f"clauses={res['source_clauses']}",
                    flush=True,
                )
            else:
                print(f"[{done:>2}/{n_targets}] ERR {res['filename'][:50]:<50}  {res.get('error')}",
                      flush=True)
        return res

    started = time.perf_counter()
    results = await asyncio.gather(*[_run(t) for t in targets])
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    matched = sum(1 for r in ok if r.get("name_match") is True)
    with_hint = sum(1 for r in ok if r.get("expected_hint") is not None)
    mismatches = [r for r in ok if r.get("name_match") is False]

    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "classify",
        "input_mode": "ingest_checkpoints" if args.from_dir else "pdfs",
        "input_source": (str(Path(args.from_dir)) if args.from_dir
                         else str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src)),
        "n_files": n_targets,
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 1),
        "concurrency": args.concurrency,
        "name_hint_accuracy": (f"{matched}/{with_hint}" if with_hint else "n/a"),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Classify summary ===")
    print(f"Files       : {summary['n_ok']}/{summary['n_files']} OK ({summary['n_failed']} failed)")
    print(f"Wall time   : {wall_s:.1f}s")
    print(f"Name match  : {summary['name_hint_accuracy']}  (filename-vs-prediction)")
    if mismatches:
        print(f"\nName-hint mismatches ({len(mismatches)}):")
        for r in mismatches:
            print(f"  {r['filename']}")
            print(f"    expected={r['expected_hint']}")
            print(f"    predicted={r['predicted_type']}  (conf {r['confidence']})")
    print(f"\nCheckpoints → {out_dir.relative_to(ROOT)}")
    print(f"Summary     → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
