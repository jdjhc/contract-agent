"""Node test: run *only* the ingest stage (OCR + clause splitter) on a folder
of PDFs. Writes one JSON checkpoint per file plus a summary.json. The
checkpoints feed downstream node tests (run_classify, run_compare, …).

Per-file JSON shape:
    {
      "filename": "...",
      "text_chars": 12345,
      "n_clauses": 28,
      "ingest_metrics": {"n_calls": N, "input_tokens": ..., "output_tokens": ...,
                         "latency_ms": ..., "total_cost_usd": ...},
      "text": "<full extracted text>",
      "clauses": [{"id": "...", "title": "...", "text": "..."}, ...]
    }

Usage:
    uv run python eval/run_ingest.py
    uv run python eval/run_ingest.py --dir "data/Contract Reviewer Agent/Redacted examples"
    uv run python eval/run_ingest.py --concurrency 3 --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

from agent import ingest  # noqa: E402
from api_clients import is_configured  # noqa: E402

DEFAULT_DIR = ROOT / "data" / "Contract Reviewer Agent" / "Redacted examples"

# Same fragment skip-list used by run_classify; keeps the pipeline consistent.
FRAGMENT_MAP: dict[str, list[str]] = {
    "Master Services Agreement Example 1 (1).pdf": [
        "Master Services Agreement Example 1.5.pdf",
    ],
}
_FRAGMENT_FILES = {f for fs in FRAGMENT_MAP.values() for f in fs}


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


async def _process_one(pdf: Path, out_dir: Path, fragments: list[Path]) -> dict:
    name = pdf.name
    t0 = time.perf_counter()
    try:
        # Logical merge: append fragment text(s) to the parent before ingest.
        if fragments:
            from api_clients import track_usage
            from services.parser import extract_text_async, split_clauses_async
            from agent import StoredDocument
            import uuid

            with track_usage() as usage:
                parts = [await extract_text_async(name, pdf.read_bytes())]
                for fp in fragments:
                    ft = await extract_text_async(fp.name, fp.read_bytes())
                    parts.append(f"\n\n--- MERGED FROM {fp.name} ---\n\n{ft}")
                text = "".join(parts)
                clauses = await split_clauses_async(text)
            doc = StoredDocument(
                document_id=uuid.uuid4().hex,
                filename=name,
                text=text,
                clauses=clauses,
                ingest_calls=list(usage.calls),
            )
        else:
            doc = await ingest(name, pdf.read_bytes())

        wall_ms = (time.perf_counter() - t0) * 1000
        calls = doc.ingest_calls
        ingest_metrics = {
            "n_calls": len(calls),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "latency_ms": round(sum(c.latency_ms for c in calls), 1),
            "total_cost_usd": round(sum(c.cost_usd for c in calls), 6),
        }

        checkpoint = {
            "filename": name,
            "merged_fragments": [f.name for f in fragments],
            "text_chars": len(doc.text),
            "n_clauses": len(doc.clauses),
            "ingest_metrics": ingest_metrics,
            "wall_ms": round(wall_ms, 1),
            "text": doc.text,
            "clauses": [c.model_dump() for c in doc.clauses],
        }
        out_path = out_dir / f"{_slug(pdf.stem)}.json"
        out_path.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "filename": name,
            "ok": True,
            "text_chars": checkpoint["text_chars"],
            "n_clauses": checkpoint["n_clauses"],
            "ingest_metrics": ingest_metrics,
            "wall_ms": checkpoint["wall_ms"],
            "checkpoint": out_path.name,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "filename": name,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: ingest only")
    p.add_argument("--dir", default=str(DEFAULT_DIR))
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--pattern", default="*.pdf")
    p.add_argument(
        "--out", default=None,
        help="Output dir (default: eval/reports/ingest_<timestamp>/)",
    )
    args = p.parse_args(argv)

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

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"ingest_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    n_merged = len(all_pdfs) - len(pdfs)
    print(f"Ingesting {len(pdfs)} files  (LLM configured: {is_configured()})")
    if n_merged:
        print(f"Skipping {n_merged} fragment file(s); merged into parents.")
    print(f"Concurrency: {args.concurrency}  →  {out_dir.relative_to(ROOT)}\n", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _worker(pdf: Path) -> dict:
        nonlocal done
        fragments = [src / f for f in FRAGMENT_MAP.get(pdf.name, []) if (src / f).exists()]
        async with sem:
            res = await _process_one(pdf, out_dir, fragments)
        async with lock:
            done += 1
            if res["ok"]:
                print(
                    f"[{done:>2}/{len(pdfs)}] OK  {res['filename'][:50]:<50} "
                    f"chars={res['text_chars']:>6}  clauses={res['n_clauses']:>3}  "
                    f"{res['wall_ms']:>6.0f}ms",
                    flush=True,
                )
            else:
                print(
                    f"[{done:>2}/{len(pdfs)}] ERR {res['filename'][:50]:<50} {res['error']}",
                    flush=True,
                )
        return res

    started = time.perf_counter()
    results = await asyncio.gather(*[_worker(p) for p in pdfs])
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "ingest",
        "source_dir": str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src),
        "n_files": len(pdfs),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 1),
        "concurrency": args.concurrency,
        "total_tokens": sum(r["ingest_metrics"]["input_tokens"] + r["ingest_metrics"]["output_tokens"] for r in ok),
        "total_cost_usd": round(sum(r["ingest_metrics"]["total_cost_usd"] for r in ok), 4),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n=== Ingest summary ===")
    print(f"Files     : {summary['n_ok']}/{summary['n_files']} OK ({summary['n_failed']} failed)")
    print(f"Wall time : {wall_s:.1f}s")
    print(f"Tokens    : {summary['total_tokens']:,}")
    print(f"Est. cost : ${summary['total_cost_usd']:.4f}")
    print(f"Checkpoints → {out_dir.relative_to(ROOT)}")
    print(f"Summary     → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
