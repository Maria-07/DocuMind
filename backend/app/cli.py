"""Operational commands.

    python -m app.cli reindex --stale     # documents not indexed with the active embedding config
    python -m app.cli reindex --failed    # retry documents whose processing failed
    python -m app.cli reindex --all       # every live document (e.g. after a parser improvement)

    python -m app.cli evaluate --dataset <id> [--baseline <run-id>] \\
        [--fail-under retrieval.ndcg@5=0.7 --fail-under answer.faithfulness=0.8] [--max-drop 0.05]

`evaluate` runs a dataset in-process with the current configuration and exits non-zero when a metric
is below its floor or dropped more than --max-drop versus the baseline (default: the dataset's last
complete run) — use it in CI whenever the embedding model, chunking, retrieval settings, prompt or LLM
change. Re-indexing never makes a document unsearchable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import create_engine, create_sessionmaker
from app.models import EvalDataset, EvalRun
from app.rag.evaluation.runner import EvaluationRunner, regressions
from app.rag.generation import build_judge
from app.rag.runtime import build_rag_runtime
from app.services.embeddings import queue_reindex
from app.services.job_queue import ArqJobQueue


async def reindex(mode: str) -> None:
    settings = get_settings()
    engine = create_engine(settings, null_pool=True)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        queued = await queue_reindex(create_sessionmaker(engine), ArqJobQueue(pool), mode)  # type: ignore[arg-type]
        print(f"Queued {queued} document(s) for re-indexing (mode={mode}).")
    finally:
        await pool.aclose()
        await engine.dispose()


async def evaluate(dataset_id: uuid.UUID, baseline: uuid.UUID | None, floors: dict[str, float], max_drop: float) -> int:
    settings = get_settings()
    engine = create_engine(settings, null_pool=True)
    sessionmaker = create_sessionmaker(engine)
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    )
    try:
        async with sessionmaker() as s:
            dataset = await s.get(EvalDataset, dataset_id)
            if dataset is None:
                print("Dataset not found", file=sys.stderr)
                return 2
            if baseline is None:
                baseline = await s.scalar(
                    select(EvalRun.id)
                    .where(EvalRun.dataset_id == dataset_id, EvalRun.status == "complete")
                    .order_by(EvalRun.created_at.desc())
                    .limit(1)
                )
            run = EvalRun(
                dataset_id=dataset_id,
                organization_id=dataset.organization_id,
                triggered_by=dataset.created_by,
                status="queued",
                baseline_run_id=baseline,
                config={},
                metrics={},
                case_count=0,
                completed_cases=0,
            )
            s.add(run)
            await s.commit()
            base_metrics = (await s.get(EvalRun, baseline)).metrics if baseline else {}  # type: ignore[union-attr]

        rag = build_rag_runtime(settings, sessionmaker, qdrant)
        runner = EvaluationRunner(
            sessionmaker=sessionmaker,
            settings=settings,
            retriever=rag.retriever,
            llm=rag.llm,
            judge=build_judge(settings) or rag.llm,
        )
        result = await runner.run(run.id)
        if result.status != "complete":
            print(f"Run failed: {result.error}", file=sys.stderr)
            return 2

        print(f"\nRun {result.id} — {result.metrics.get('cases', 0)} cases")
        print(f"{'metric':<32}{'value':>10}{'baseline':>10}")
        for key in sorted(k for k in result.metrics if k != "cases"):
            b = base_metrics.get(key)
            print(f"{key:<32}{result.metrics[key]:>10.3f}{(f'{b:.3f}' if isinstance(b, int | float) else '-'):>10}")

        failures = [
            f"{k} {result.metrics.get(k, 0):.3f} < {v}" for k, v in floors.items() if result.metrics.get(k, 0) < v
        ]
        failures += regressions(result.metrics, base_metrics, max_drop) if base_metrics else []
        if failures:
            print("\nFAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
            return 1
        print("\nPASSED")
        return 0
    finally:
        await qdrant.close()
        await engine.dispose()


def _floor(value: str) -> tuple[str, float]:
    key, _, number = value.partition("=")
    return key.strip(), float(number)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    reindex_cmd = sub.add_parser("reindex", help="Queue documents for (re-)indexing")
    group = reindex_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--stale", action="store_const", const="stale", dest="mode")
    group.add_argument("--all", action="store_const", const="all", dest="mode")
    group.add_argument("--failed", action="store_const", const="failed", dest="mode")
    eval_cmd = sub.add_parser("evaluate", help="Run an evaluation dataset and gate on metrics")
    eval_cmd.add_argument("--dataset", required=True, type=uuid.UUID)
    eval_cmd.add_argument("--baseline", type=uuid.UUID)
    eval_cmd.add_argument("--fail-under", action="append", default=[], type=_floor, metavar="METRIC=VALUE")
    eval_cmd.add_argument("--max-drop", type=float, default=0.05)
    args = parser.parse_args()
    if args.command == "reindex":
        asyncio.run(reindex(args.mode))
    else:
        sys.exit(asyncio.run(evaluate(args.dataset, args.baseline, dict(args.fail_under), args.max_drop)))


if __name__ == "__main__":
    main()
