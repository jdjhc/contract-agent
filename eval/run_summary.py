"""Node test: LLM summary writer (agent._llm_summary).

Reads classify + flag checkpoints (use augmented flags if available; else
seed flags from compare) and writes per-file summary JSON.

Per-file output JSON shape:
    {
      "filename": "...",
      "contract_type": "...",
      "flags_source": "augment" | "compare",
      "n_flags": 19,
      "counts": {"red": 2, "amber": 10, "green": 0, "blue": 9},
      "summary": "<2-3 sentence executive summary>",
      "wall_ms": 4321
    }

Usage:
    uv run python eval/run_summary.py \\
        --classify eval/reports/classify_<ts> \\
        --flags    eval/reports/augment_<ts>
    # or, skipping the augment node:
    uv run python eval/run_summary.py \\
        --classify eval/reports/classify_<ts> \\
        --flags    eval/reports/compare_<ts>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

from agent import StoredDocument, _llm_summary  # noqa: E402
from api_clients import is_configured  # noqa: E402
from models import ContractType, FlagItem  # noqa: E402


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


def _load_dir(d: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in d.glob("*.json"):
        if p.name == "summary.json":
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        out[data["filename"]] = data
    return out


def _pick_flags(cp: dict) -> tuple[list[dict], str]:
    """Return (flags, source_label). Prefer augmented_flags; fall back to seed/flags."""
    if "augmented_flags" in cp:
        return cp["augmented_flags"], "augment"
    if "flags" in cp:
        return cp["flags"], "compare"
    if "seed_flags" in cp:
        return cp["seed_flags"], "compare_seed"
    raise KeyError("no flag list in checkpoint")


def _counts(flags: list[dict]) -> dict[str, int]:
    c = Counter(f["level"] for f in flags)
    return {lvl: c.get(lvl, 0) for lvl in ("red", "amber", "green", "blue")}


async def _process_one(
    filename: str, classify_cp: dict, flags_cp: dict, out_dir: Path,
) -> dict:
    t0 = time.perf_counter()
    try:
        ctype = ContractType(classify_cp["predicted_type"])
        flags_dicts, source = _pick_flags(flags_cp)
        flag_items = [FlagItem(**f) for f in flags_dicts]
        # _llm_summary only reads filename + contract_type + flags off the doc.
        doc = StoredDocument(
            document_id=uuid.uuid4().hex,
            filename=filename,
            text="",
            clauses=[],
            ingest_calls=[],
        )
        summary_text = await _llm_summary(doc, ctype, flag_items)
        wall_ms = (time.perf_counter() - t0) * 1000

        cp = {
            "filename": filename,
            "contract_type": ctype.value,
            "flags_source": source,
            "n_flags": len(flag_items),
            "counts": _counts(flags_dicts),
            "summary": summary_text,
            "wall_ms": round(wall_ms, 1),
        }
        out_path = out_dir / f"{_slug(Path(filename).stem)}.json"
        out_path.write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"filename": filename, "ok": True,
                "n_flags": len(flag_items), "summary": summary_text,
                "wall_ms": cp["wall_ms"], "checkpoint": out_path.name}
    except Exception as e:  # noqa: BLE001
        return {"filename": filename, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: LLM summary writer")
    p.add_argument("--classify", required=True)
    p.add_argument("--flags", required=True, help="augment dir or compare dir")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    dirs = []
    for label, raw in [("classify", args.classify), ("flags", args.flags)]:
        d = Path(raw).resolve()
        if not d.exists():
            print(f"ERROR: {label} dir not found: {d}", file=sys.stderr)
            return 2
        dirs.append(d)
    classify_dir, flags_dir = dirs

    classify_cps = _load_dir(classify_dir)
    flags_cps = _load_dir(flags_dir)
    shared = sorted(set(classify_cps) & set(flags_cps))
    if args.limit:
        shared = shared[: args.limit]
    if not shared:
        print("No overlap between classify and flags dirs", file=sys.stderr)
        return 2

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"summary_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Summarising {len(shared)} contracts (LLM configured: {is_configured()})")
    print(f"Concurrency: {args.concurrency}  →  {out_dir.relative_to(ROOT)}\n", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _worker(fname: str) -> dict:
        nonlocal done
        async with sem:
            res = await _process_one(fname, classify_cps[fname], flags_cps[fname], out_dir)
        async with lock:
            done += 1
            if res["ok"]:
                preview = (res["summary"] or "(none)")[:80].replace("\n", " ")
                print(f"[{done:>2}/{len(shared)}] OK  {fname[:50]:<50} "
                      f"{res['wall_ms']:>5.0f}ms  «{preview}»", flush=True)
            else:
                print(f"[{done:>2}/{len(shared)}] ERR {fname[:50]:<50} {res['error']}", flush=True)
        return res

    started = time.perf_counter()
    results = await asyncio.gather(*[_worker(f) for f in shared])
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "summary",
        "classify_dir": str(classify_dir.relative_to(ROOT)) if classify_dir.is_relative_to(ROOT) else str(classify_dir),
        "flags_dir": str(flags_dir.relative_to(ROOT)) if flags_dir.is_relative_to(ROOT) else str(flags_dir),
        "n_files": len(shared),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 1),
        "concurrency": args.concurrency,
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Summary-node summary ===")
    print(f"Files     : {len(ok)}/{len(shared)} OK")
    print(f"Wall time : {wall_s:.1f}s")
    print(f"Checkpoints → {out_dir.relative_to(ROOT)}")
    print(f"Summary     → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
