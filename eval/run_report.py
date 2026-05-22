"""Node test: final report assembly (services.reporter.build_report).

Stitches the previous checkpoints into the same ContractReview JSON the
backend produces from /api/review. No LLM calls — pure aggregation.

Per-file output JSON = ContractReview model dump (same shape the frontend
already consumes), so this file is a direct drop-in for the API result.

Usage:
    uv run python eval/run_report.py \\
        --ingest   eval/reports/ingest_<ts> \\
        --classify eval/reports/classify_<ts> \\
        --flags    eval/reports/augment_<ts> \\
        --summary  eval/reports/summary_<ts>
    # Skip the --summary flag if you only ran compare+augment.
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

from models import ContractType, FlagItem, ReviewMetrics  # noqa: E402
from services.reporter import build_report  # noqa: E402
from services.templates import template_filenames_for  # noqa: E402


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


def _flags_from(cp: dict) -> list[FlagItem]:
    raw = cp.get("augmented_flags") or cp.get("flags") or cp.get("seed_flags") or []
    return [FlagItem(**f) for f in raw]


def _ingest_metrics(cp: dict) -> ReviewMetrics:
    m = cp.get("ingest_metrics", {})
    return ReviewMetrics(
        n_calls=m.get("n_calls", 0),
        input_tokens=m.get("input_tokens", 0),
        output_tokens=m.get("output_tokens", 0),
        total_tokens=m.get("input_tokens", 0) + m.get("output_tokens", 0),
        latency_ms=m.get("latency_ms", 0.0),
        total_cost_usd=m.get("total_cost_usd", 0.0),
        backend="",
        model="",
    )


def _process_one(
    filename: str, ingest_cp: dict, classify_cp: dict, flags_cp: dict,
    summary_cp: dict | None, out_dir: Path,
) -> dict:
    t0 = time.perf_counter()
    try:
        ctype = ContractType(classify_cp["predicted_type"])
        flags = _flags_from(flags_cp)
        summary_text = summary_cp.get("summary") if summary_cp else None

        report = build_report(
            document_id=ingest_cp.get("document_id", _slug(filename)),
            filename=filename,
            contract_type=ctype,
            confidence=classify_cp.get("confidence", 0.0),
            flags=flags,
            summary=summary_text,
        )
        report.metrics = _ingest_metrics(ingest_cp)
        report.references_used = (
            ["UoA Preferred Contracting Positions (Sept 2025 draft)"]
            + [f"UoA Template — {f}" for f in template_filenames_for(ctype)]
        )

        out_path = out_dir / f"{_slug(Path(filename).stem)}.json"
        out_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "filename": filename, "ok": True,
            "contract_type": ctype.value,
            "n_flags": len(flags),
            "counts": report.counts,
            "summary_present": bool(summary_text),
            "wall_ms": round((time.perf_counter() - t0) * 1000, 2),
            "checkpoint": out_path.name,
        }
    except Exception as e:  # noqa: BLE001
        return {"filename": filename, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 2)}


async def amain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Node test: report assembly")
    p.add_argument("--ingest", required=True)
    p.add_argument("--classify", required=True)
    p.add_argument("--flags", required=True, help="augment or compare dir")
    p.add_argument("--summary", default=None, help="summary dir (optional)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    def _resolve(label: str, raw: str) -> Path:
        d = Path(raw).resolve()
        if not d.exists():
            print(f"ERROR: {label} dir not found: {d}", file=sys.stderr)
            sys.exit(2)
        return d

    ingest_dir = _resolve("ingest", args.ingest)
    classify_dir = _resolve("classify", args.classify)
    flags_dir = _resolve("flags", args.flags)
    summary_dir = _resolve("summary", args.summary) if args.summary else None

    ingest_cps = _load_dir(ingest_dir)
    classify_cps = _load_dir(classify_dir)
    flags_cps = _load_dir(flags_dir)
    summary_cps = _load_dir(summary_dir) if summary_dir else {}

    shared = sorted(set(ingest_cps) & set(classify_cps) & set(flags_cps))
    if args.limit:
        shared = shared[: args.limit]
    if not shared:
        print("No overlap across ingest+classify+flags dirs", file=sys.stderr)
        return 2

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = (Path(args.out) if args.out else ROOT / "eval" / "reports" / f"report_{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Assembling {len(shared)} reports (pure aggregation, no LLM)")
    if summary_dir is None:
        print("  ⚠ no --summary dir; reports will have auto-generated summaries")
    print(f"→ {out_dir.relative_to(ROOT)}\n", flush=True)

    started = time.perf_counter()
    results = []
    for i, fname in enumerate(shared, 1):
        res = _process_one(
            fname, ingest_cps[fname], classify_cps[fname], flags_cps[fname],
            summary_cps.get(fname), out_dir,
        )
        if res["ok"]:
            c = res["counts"]
            print(f"[{i:>2}/{len(shared)}] OK  {fname[:50]:<50} "
                  f"R/A/G/B={c['red']}/{c['amber']}/{c['green']}/{c['blue']}  "
                  f"{res['wall_ms']:>5.2f}ms", flush=True)
        else:
            print(f"[{i:>2}/{len(shared)}] ERR {fname[:50]:<50} {res['error']}", flush=True)
        results.append(res)
    wall_s = time.perf_counter() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "node": "report",
        "ingest_dir": str(ingest_dir.relative_to(ROOT)) if ingest_dir.is_relative_to(ROOT) else str(ingest_dir),
        "classify_dir": str(classify_dir.relative_to(ROOT)) if classify_dir.is_relative_to(ROOT) else str(classify_dir),
        "flags_dir": str(flags_dir.relative_to(ROOT)) if flags_dir.is_relative_to(ROOT) else str(flags_dir),
        "summary_dir": (str(summary_dir.relative_to(ROOT)) if summary_dir and summary_dir.is_relative_to(ROOT)
                        else (str(summary_dir) if summary_dir else None)),
        "n_files": len(shared),
        "n_ok": len(ok),
        "n_failed": len(bad),
        "wall_seconds": round(wall_s, 2),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Report summary ===")
    print(f"Files     : {len(ok)}/{len(shared)} OK")
    print(f"Wall time : {wall_s:.2f}s")
    print(f"Reports     → {out_dir.relative_to(ROOT)}")
    print(f"Index       → {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
