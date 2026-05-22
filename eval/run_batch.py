"""Batch-run the review pipeline across every PDF in a folder.

Usage:
    uv run python eval/run_batch.py
    uv run python eval/run_batch.py --dir "data/Contract Reviewer Agent/Redacted examples"
    uv run python eval/run_batch.py --concurrency 3
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

from agent import ingest, review  # noqa: E402

DEFAULT_DIR = ROOT / "data" / "Contract Reviewer Agent" / "Redacted examples"


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


async def _process_one(pdf: Path, out_dir: Path) -> dict:
    name = pdf.name
    started = time.perf_counter()
    try:
        raw = pdf.read_bytes()
        doc = await ingest(name, raw)
        rep = await review(doc)
        wall_ms = (time.perf_counter() - started) * 1000

        out_path = out_dir / f"{_slug(pdf.stem)}.json"
        out_path.write_text(
            json.dumps(rep.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return {
            "filename": name,
            "ok": True,
            "contract_type": rep.contract_type.value,
            "confidence": round(rep.contract_type_confidence, 3),
            "n_clauses": len(doc.clauses),
            "counts": rep.counts,
            "wall_ms": round(wall_ms, 1),
            "tokens": rep.metrics.total_tokens,
            "cost_usd": rep.metrics.total_cost_usd,
            "report": out_path.relative_to(ROOT).as_posix(),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "filename": name,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "wall_ms": round((time.perf_counter() - started) * 1000, 1),
        }


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Batch review every contract in a folder")
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="Folder of PDFs to review")
    p.add_argument("--concurrency", type=int, default=2, help="Parallel reviews")
    p.add_argument("--limit", type=int, default=0, help="Process only N files (0 = all)")
    p.add_argument("--pattern", default="*.pdf", help="Glob for files")
    args = p.parse_args(argv)

    src = Path(args.dir)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"ERROR: directory not found: {src}", file=sys.stderr)
        return 2

    pdfs = sorted(src.glob(args.pattern))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"No files matching {args.pattern} in {src}", file=sys.stderr)
        return 2

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = ROOT / "eval" / "reports" / f"batch_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(pdfs)} files → {out_dir.relative_to(ROOT)}")
    print(f"Concurrency: {args.concurrency}\n")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _worker(pdf: Path) -> dict:
        nonlocal done
        async with sem:
            res = await _process_one(pdf, out_dir)
        async with lock:
            done += 1
            status = "OK " if res["ok"] else "ERR"
            extra = (
                f"{res.get('contract_type', '—'):<35} "
                f"R/A/G/B={res.get('counts', {}).get('red', 0)}/"
                f"{res.get('counts', {}).get('amber', 0)}/"
                f"{res.get('counts', {}).get('green', 0)}/"
                f"{res.get('counts', {}).get('blue', 0)}  "
                f"{res.get('wall_ms', 0):.0f}ms"
                if res["ok"] else res.get("error", "")
            )
            print(f"[{done:>2}/{len(pdfs)}] {status}  {res['filename'][:50]:<50}  {extra}")
        return res

    started = time.perf_counter()
    results = await asyncio.gather(*[_worker(p) for p in pdfs])
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]

    type_counts: dict[str, int] = {}
    for r in ok:
        type_counts[r["contract_type"]] = type_counts.get(r["contract_type"], 0) + 1

    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src),
        "n_files": len(pdfs),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 1),
        "concurrency": args.concurrency,
        "total_tokens": sum(r.get("tokens", 0) for r in ok),
        "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in ok), 4),
        "contract_type_distribution": dict(
            sorted(type_counts.items(), key=lambda kv: -kv[1])
        ),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Summary ===")
    print(f"Files     : {summary['n_ok']}/{summary['n_files']} OK ({summary['n_failed']} failed)")
    print(f"Wall time : {wall_s:.1f}s")
    print(f"Tokens    : {summary['total_tokens']:,}")
    print(f"Est. cost : ${summary['total_cost_usd']:.4f}")
    print("Types     :")
    for t, n in summary["contract_type_distribution"].items():
        print(f"  {n:>2}  {t}")
    if bad:
        print("\nFailures:")
        for r in bad:
            print(f"  - {r['filename']}: {r['error']}")
    print(f"\nReports → {out_dir.relative_to(ROOT)}")
    print(f"Summary → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
