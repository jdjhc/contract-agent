"""Node test: deterministic comparator (services.comparator.compare_clauses).

Reads ingest + classify checkpoints and writes per-file flag JSON. No LLM —
fully reproducible, runs in seconds.

Per-file output JSON shape:
    {
      "filename": "...",
      "contract_type": "Material Transfer Agreement",
      "counts": {"red": 0, "amber": 8, "green": 0, "blue": 9},
      "flags": [{"level": "amber", "clause_id": "...", "clause_title": "...",
                 "snippet": "...", "rationale": "...", "standard_ref": "..."}]
    }

Usage:
    uv run python eval/run_compare.py \\
        --ingest eval/reports/ingest_<ts> \\
        --classify eval/reports/classify_<ts>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

from models import Clause, ContractType  # noqa: E402
from services.comparator import compare_clauses  # noqa: E402


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


def _load_checkpoints(d: Path) -> dict[str, dict]:
    """Return {filename: checkpoint_dict} for all *.json (excluding summary)."""
    out: dict[str, dict] = {}
    for p in d.glob("*.json"):
        if p.name == "summary.json":
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        out[data["filename"]] = data
    return out


def _process_one(filename: str, ingest_cp: dict, classify_cp: dict,
                 out_dir: Path) -> dict:
    t0 = time.perf_counter()
    try:
        clauses = [Clause(**c) for c in ingest_cp["clauses"]]
        ctype = ContractType(classify_cp["predicted_type"])
        flags = compare_clauses(clauses, ctype)
        counts = Counter(f.level.value for f in flags)
        counts_dict = {lvl: counts.get(lvl, 0) for lvl in ("red", "amber", "green", "blue")}
        wall_ms = (time.perf_counter() - t0) * 1000
        cp = {
            "filename": filename,
            "contract_type": ctype.value,
            "n_clauses": len(clauses),
            "counts": counts_dict,
            "n_flags": len(flags),
            "wall_ms": round(wall_ms, 2),
            "flags": [f.model_dump(mode="json") for f in flags],
        }
        out_path = out_dir / f"{_slug(Path(filename).stem)}.json"
        out_path.write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"filename": filename, "ok": True,
                "counts": counts_dict, "n_flags": len(flags),
                "wall_ms": cp["wall_ms"], "checkpoint": out_path.name}
    except Exception as e:  # noqa: BLE001
        return {"filename": filename, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 2)}


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: deterministic comparator")
    p.add_argument("--ingest", required=True, help="Ingest checkpoint dir")
    p.add_argument("--classify", required=True, help="Classify checkpoint dir")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None,
                   help="Output dir (default: eval/reports/compare_<timestamp>/)")
    args = p.parse_args(argv)

    ingest_dir = Path(args.ingest).resolve()
    classify_dir = Path(args.classify).resolve()
    for d in (ingest_dir, classify_dir):
        if not d.exists():
            print(f"ERROR: dir not found: {d}", file=sys.stderr)
            return 2

    ingest_cps = _load_checkpoints(ingest_dir)
    classify_cps = _load_checkpoints(classify_dir)

    # Inner-join on filename. Anything missing in either side gets reported.
    shared = sorted(set(ingest_cps) & set(classify_cps))
    only_ingest = sorted(set(ingest_cps) - set(classify_cps))
    only_classify = sorted(set(classify_cps) - set(ingest_cps))
    if args.limit:
        shared = shared[: args.limit]

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"compare_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Comparing {len(shared)} contracts (deterministic — no LLM)")
    if only_ingest:
        print(f"  ⚠ {len(only_ingest)} only in ingest: {only_ingest[:3]}{'...' if len(only_ingest) > 3 else ''}")
    if only_classify:
        print(f"  ⚠ {len(only_classify)} only in classify: {only_classify[:3]}{'...' if len(only_classify) > 3 else ''}")
    print(f"→ {out_dir.relative_to(ROOT)}\n", flush=True)

    started = time.perf_counter()
    results = []
    for i, fname in enumerate(shared, 1):
        res = _process_one(fname, ingest_cps[fname], classify_cps[fname], out_dir)
        if res["ok"]:
            c = res["counts"]
            print(f"[{i:>2}/{len(shared)}] OK  {fname[:50]:<50} "
                  f"R/A/G/B={c['red']}/{c['amber']}/{c['green']}/{c['blue']}  "
                  f"{res['wall_ms']:>5.1f}ms", flush=True)
        else:
            print(f"[{i:>2}/{len(shared)}] ERR {fname[:50]:<50} {res['error']}", flush=True)
        results.append(res)
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    totals = Counter()
    for r in ok:
        for lvl, n in r["counts"].items():
            totals[lvl] += n

    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "compare",
        "ingest_dir": str(ingest_dir.relative_to(ROOT)) if ingest_dir.is_relative_to(ROOT) else str(ingest_dir),
        "classify_dir": str(classify_dir.relative_to(ROOT)) if classify_dir.is_relative_to(ROOT) else str(classify_dir),
        "n_files": len(shared),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 2),
        "totals_by_level": dict(totals),
        "missing_in_classify": only_ingest,
        "missing_in_ingest": only_classify,
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Compare summary ===")
    print(f"Files     : {len(ok)}/{len(shared)} OK")
    print(f"Wall time : {wall_s:.2f}s")
    print(f"Totals    : R={totals['red']} A={totals['amber']} G={totals['green']} B={totals['blue']}")
    print(f"Checkpoints → {out_dir.relative_to(ROOT)}")
    print(f"Summary     → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
