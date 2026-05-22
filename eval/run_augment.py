"""Node test: LLM flag augmenter (agent._augment_flags_with_llm).

Reads ingest + classify + compare checkpoints and writes per-file augmented
flag JSON. Each file's seed flags (from compare) get re-graded / extended by
the LLM. The deterministic flags from compare are preserved in the checkpoint
under `seed_flags` for diff inspection.

Per-file output JSON shape:
    {
      "filename": "...",
      "contract_type": "...",
      "n_seed_flags": 17,
      "n_augmented_flags": 19,
      "seed_counts": {"red": 0, "amber": 8, "green": 0, "blue": 9},
      "augmented_counts": {"red": 2, "amber": 10, "green": 0, "blue": 9},
      "level_changes": {"blue→amber": 2, ...},
      "wall_ms": 14234,
      "seed_flags": [...],
      "augmented_flags": [...]
    }

Usage:
    uv run python eval/run_augment.py \\
        --ingest eval/reports/ingest_<ts> \\
        --classify eval/reports/classify_<ts> \\
        --compare eval/reports/compare_<ts>
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

from agent import StoredDocument, _augment_flags_with_llm  # noqa: E402
from api_clients import is_configured  # noqa: E402
from models import Clause, ContractType, FlagItem  # noqa: E402


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


def _counts(flags: list[dict]) -> dict[str, int]:
    c = Counter(f["level"] for f in flags)
    return {lvl: c.get(lvl, 0) for lvl in ("red", "amber", "green", "blue")}


def _level_changes(seed: list[dict], augmented: list[dict]) -> dict[str, int]:
    """Map clause_id+title → level, then diff between seed and augmented."""
    seed_by_id = {(f.get("clause_id"), f.get("clause_title")): f["level"] for f in seed}
    changes: Counter = Counter()
    for f in augmented:
        key = (f.get("clause_id"), f.get("clause_title"))
        before = seed_by_id.get(key)
        after = f["level"]
        if before is None:
            changes[f"NEW→{after}"] += 1
        elif before != after:
            changes[f"{before}→{after}"] += 1
    seen = {(f.get("clause_id"), f.get("clause_title")) for f in augmented}
    for key, before in seed_by_id.items():
        if key not in seen:
            changes[f"{before}→REMOVED"] += 1
    return dict(changes)


async def _process_one(
    filename: str, ingest_cp: dict, classify_cp: dict, compare_cp: dict, out_dir: Path,
) -> dict:
    t0 = time.perf_counter()
    try:
        # Reconstruct a minimal StoredDocument the augmenter expects.
        doc = StoredDocument(
            document_id=uuid.uuid4().hex,
            filename=filename,
            text=ingest_cp["text"],
            clauses=[Clause(**c) for c in ingest_cp["clauses"]],
            ingest_calls=[],
        )
        ctype = ContractType(classify_cp["predicted_type"])
        seed_flags = [FlagItem(**f) for f in compare_cp["flags"]]

        augmented = await _augment_flags_with_llm(doc, ctype, seed_flags)
        wall_ms = (time.perf_counter() - t0) * 1000

        seed_dump = [f.model_dump(mode="json") for f in seed_flags]
        aug_dump = [f.model_dump(mode="json") for f in augmented]
        seed_counts = _counts(seed_dump)
        aug_counts = _counts(aug_dump)
        changes = _level_changes(seed_dump, aug_dump)

        cp = {
            "filename": filename,
            "contract_type": ctype.value,
            "n_seed_flags": len(seed_flags),
            "n_augmented_flags": len(augmented),
            "seed_counts": seed_counts,
            "augmented_counts": aug_counts,
            "level_changes": changes,
            "wall_ms": round(wall_ms, 1),
            "seed_flags": seed_dump,
            "augmented_flags": aug_dump,
        }
        out_path = out_dir / f"{_slug(Path(filename).stem)}.json"
        out_path.write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "filename": filename, "ok": True,
            "n_seed_flags": len(seed_flags), "n_augmented_flags": len(augmented),
            "seed_counts": seed_counts, "augmented_counts": aug_counts,
            "level_changes": changes, "wall_ms": cp["wall_ms"],
            "checkpoint": out_path.name,
        }
    except Exception as e:  # noqa: BLE001
        return {"filename": filename, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: LLM flag augmenter")
    p.add_argument("--ingest", required=True)
    p.add_argument("--classify", required=True)
    p.add_argument("--compare", required=True)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    dirs = []
    for label, raw in [("ingest", args.ingest), ("classify", args.classify), ("compare", args.compare)]:
        d = Path(raw).resolve()
        if not d.exists():
            print(f"ERROR: {label} dir not found: {d}", file=sys.stderr)
            return 2
        dirs.append(d)
    ingest_dir, classify_dir, compare_dir = dirs

    ingest_cps = _load_dir(ingest_dir)
    classify_cps = _load_dir(classify_dir)
    compare_cps = _load_dir(compare_dir)
    shared = sorted(set(ingest_cps) & set(classify_cps) & set(compare_cps))
    if args.limit:
        shared = shared[: args.limit]
    if not shared:
        print("No overlap across all three checkpoint dirs", file=sys.stderr)
        return 2

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"augment_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Augmenting {len(shared)} contracts (LLM configured: {is_configured()})")
    print(f"Concurrency: {args.concurrency}  →  {out_dir.relative_to(ROOT)}\n", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _worker(fname: str) -> dict:
        nonlocal done
        async with sem:
            res = await _process_one(
                fname, ingest_cps[fname], classify_cps[fname], compare_cps[fname], out_dir,
            )
        async with lock:
            done += 1
            if res["ok"]:
                a = res["augmented_counts"]
                s = res["seed_counts"]
                print(
                    f"[{done:>2}/{len(shared)}] OK  {fname[:50]:<50} "
                    f"seed→aug: R {s['red']}→{a['red']}  A {s['amber']}→{a['amber']}  "
                    f"G {s['green']}→{a['green']}  B {s['blue']}→{a['blue']}  "
                    f"{res['wall_ms']:>5.0f}ms",
                    flush=True,
                )
            else:
                print(f"[{done:>2}/{len(shared)}] ERR {fname[:50]:<50} {res['error']}", flush=True)
        return res

    started = time.perf_counter()
    results = await asyncio.gather(*[_worker(f) for f in shared])
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    all_changes: Counter = Counter()
    for r in ok:
        for k, v in r["level_changes"].items():
            all_changes[k] += v

    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "augment",
        "ingest_dir": str(ingest_dir.relative_to(ROOT)) if ingest_dir.is_relative_to(ROOT) else str(ingest_dir),
        "classify_dir": str(classify_dir.relative_to(ROOT)) if classify_dir.is_relative_to(ROOT) else str(classify_dir),
        "compare_dir": str(compare_dir.relative_to(ROOT)) if compare_dir.is_relative_to(ROOT) else str(compare_dir),
        "n_files": len(shared),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 1),
        "concurrency": args.concurrency,
        "aggregate_level_changes": dict(all_changes.most_common()),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Augment summary ===")
    print(f"Files     : {len(ok)}/{len(shared)} OK")
    print(f"Wall time : {wall_s:.1f}s")
    if all_changes:
        print(f"Level changes (top 8):")
        for k, v in all_changes.most_common(8):
            print(f"  {k:<20} {v}")
    print(f"Checkpoints → {out_dir.relative_to(ROOT)}")
    print(f"Summary     → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
