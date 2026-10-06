"""Orchestrated pipeline for ingestion, summarization, and publishing.

Refactored from functional to class-based for better testability and clarity.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .config import Config
from .errors import LLMError, MemosError, SourceError
from .llm import LLMClient
from .memos import MemosClient, body_hash, remote_ids, render_memo
from .sources import SourceRunResult, read_source, run_source
from .store import Store

log = logging.getLogger(__name__)


def _llm_raw_dir() -> Path:
    """Get LLM raw response directory."""
    data_dir = paths.data_dir("infohub", "INFOHUB_DATA_DIR")
    llm_dir = data_dir / "raw" / "llm"
    llm_dir.mkdir(parents=True, exist_ok=True)
    return llm_dir


@dataclass
class RunOptions:
    """Options for a pipeline run."""
    source_names: list[str] | None = None
    run_sources: bool = True
    do_summarize: bool = True
    do_publish: bool = True
    dry_run: bool = False
    limit: int | None = None


@dataclass
class PipelineResult:
    """Result of a pipeline run."""
    run_id: int | None = None
    source_status: dict[str, Any] = field(default_factory=dict)
    ingested: int = 0
    changed: int = 0
    summarized: int = 0
    summary_failed: int = 0
    published: int = 0
    publish_failed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


class Pipeline:
    """Orchestrates infohub data flow: ingest → summarize → publish."""

    def __init__(self, config: Config, store: Store, repo_root: Path):
        self.config = config
        self.store = store
        self.repo_root = repo_root

    def run(self, opts: RunOptions) -> PipelineResult:
        """Execute a complete pipeline run."""
        started = time.monotonic()
        result = PipelineResult()

        run_id = None if opts.dry_run else self.store.begin_run()
        result.run_id = run_id

        if opts.dry_run:
            opts.run_sources = False
            opts.do_summarize = False
            opts.do_publish = False

        if opts.run_sources:
            self._run_sources(result, opts.source_names)

        if not opts.dry_run:
            self._ingest(result, opts.source_names, opts.limit)
            self._ensure_queue(opts.limit)

            if opts.do_summarize:
                self._summarize(result, opts.limit)

            if opts.do_publish:
                self._publish(result, opts.limit)

        result.duration_ms = int((time.monotonic() - started) * 1000)

        if run_id is not None:
            status = self._compute_status(result)
            self.store.finish_run(
                run_id,
                status=status,
                source_status=result.source_status,
                ingested=result.ingested,
                summarized=result.summarized,
                published=result.published,
                error="; ".join(result.errors[:5]),
            )

        return result

    def _run_sources(self, result: PipelineResult, source_names: list[str] | None) -> None:
        """Run upstream collectors (wxmp/xnews)."""
        names = source_names or list(self.config.sources)
        for name in names:
            source = self.config.sources.get(name)
            if source is None or not source.enabled:
                continue

            run_result = run_source(name, source, self.repo_root)
            result.source_status[name] = {
                "status": run_result.status,
                "returncode": run_result.returncode,
                "duration_ms": run_result.duration_ms,
                "error": run_result.error,
            }

            if run_result.status == "error":
                result.errors.append(f"{name}: {run_result.error}")

    def _ingest(
        self, result: PipelineResult, source_names: list[str] | None, limit: int | None
    ) -> None:
        """Ingest items from source databases into timeline.db."""
        names = source_names or list(self.config.sources)
        actual_limit = limit or self.config.pipeline.max_items_per_run

        for name in names:
            source = self.config.sources.get(name)
            if source is None or not source.enabled:
                continue

            try:
                items = read_source(name, source, self.repo_root)
                # Take newest items when limiting
                if actual_limit is not None:
                    items = items[-actual_limit:]

                for item in items:
                    is_new, is_changed = self.store.upsert_item(item)
                    result.ingested += int(is_new)
                    result.changed += int(is_changed)

            except SourceError as exc:
                result.errors.append(exc.message)

    def _ensure_queue(self, limit: int | None) -> None:
        """Ensure summary queue is populated for all items."""
        actual_limit = limit or self.config.pipeline.max_items_per_run
        items = self.store.list_items(limit=actual_limit)

        for item in items:
            self.store.ensure_summary(
                item["item_key"],
                item["content_hash"],
                self.config.llm.prompt_version,
                self.config.llm.model,
                retry_failed=self.config.pipeline.retry_failed,
            )

    def _summarize(self, result: PipelineResult, limit: int | None) -> None:
        """Process pending summaries via LLM."""
        actual_limit = limit or self.config.pipeline.max_items_per_run
        client = LLMClient(self.config.llm, api_key=self.config.llm_api_key())

        for row in self.store.pending_summaries(limit=actual_limit):
            summary_id = int(row["id"])
            self.store.mark_summary_running(summary_id)
            item = dict(row)

            try:
                llm_result = client.summarize(item)
                raw_path = (
                    _llm_raw_dir()
                    / f"{paths.safe_component(row['item_key'])}-{summary_id}.json"
                )
                paths.write_json_atomic(raw_path, llm_result.response)

                usage = llm_result.response.get("usage")
                self.store.save_summary_result(
                    summary_id,
                    summary=llm_result.summary,
                    response_path=str(raw_path),
                    input_chars=llm_result.input_chars,
                    duration_ms=llm_result.duration_ms,
                    usage_json=json.dumps(usage, ensure_ascii=False) if usage else None,
                    status="ok",
                )
                result.summarized += 1

            except LLMError as exc:
                self.store.save_summary_result(summary_id, status="failed", error=exc.message)
                result.summary_failed += 1
                result.errors.append(f"{row['item_key']}: {exc.message}")
                log.warning("摘要失败 %s: %s", row["item_key"], exc.message)

    def _publish(self, result: PipelineResult, limit: int | None) -> None:
        """Publish ready summaries to Memos."""
        actual_limit = limit or self.config.pipeline.max_items_per_run
        client = MemosClient(self.config.memos, self.config.memos_token())

        for row in self.store.ready_summaries(limit=actual_limit):
            item = dict(row)
            summary_id = int(row["id"])
            content = render_memo(item, row["summary"], self.config.memos.tags)
            digest = body_hash(content)

            delivery = self.store.ensure_delivery(row["item_key"], summary_id, "memos", digest)
            if delivery["status"] == "published":
                continue

            self.store.mark_delivery_running(row["item_key"], summary_id, "memos")

            try:
                remote = client.create(content)
                name, uid = remote_ids(remote)
                self.store.save_delivery_result(
                    row["item_key"],
                    summary_id,
                    "memos",
                    status="published",
                    remote_name=name,
                    remote_uid=uid,
                )
                result.published += 1

            except MemosError as exc:
                self.store.save_delivery_result(
                    row["item_key"], summary_id, "memos", status="failed", error=exc.message
                )
                result.publish_failed += 1
                result.errors.append(f"{row['item_key']}: {exc.message}")
                log.warning("发布失败 %s: %s", row["item_key"], exc.message)

    @staticmethod
    def _compute_status(result: PipelineResult) -> str:
        """Compute overall run status."""
        if result.errors and not (result.ingested or result.summarized or result.published):
            return "error"
        if result.errors:
            return "partial"
        return "ok"


# Legacy functional interface for backward compatibility
def run_pipeline(
    config: Config,
    store: Store,
    repo_root: Path,
    *,
    source_names: list[str] | None = None,
    run_sources: bool | None = None,
    do_summarize: bool = True,
    do_publish: bool | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> PipelineResult:
    """Legacy function wrapper around Pipeline class."""
    pipeline = Pipeline(config, store, repo_root)
    opts = RunOptions(
        source_names=source_names,
        run_sources=config.pipeline.run_sources if run_sources is None else run_sources,
        do_summarize=do_summarize,
        do_publish=config.pipeline.publish_to_memos if do_publish is None else do_publish,
        dry_run=dry_run,
        limit=limit,
    )
    return pipeline.run(opts)


def ensure_summary_queue(config: Config, store: Store, *, limit: int | None = None) -> int:
    """Ensure summary queue is populated."""
    items = store.list_items(limit=limit)
    for item in items:
        store.ensure_summary(
            item["item_key"],
            item["content_hash"],
            config.llm.prompt_version,
            config.llm.model,
            retry_failed=config.pipeline.retry_failed,
        )
    return len(items)


def summarize_pending(
    config: Config, store: Store, *, limit: int | None = None
) -> tuple[int, int, list[str]]:
    """Process pending summaries."""
    pipeline = Pipeline(config, store, Path.cwd())
    result = PipelineResult()
    pipeline._summarize(result, limit)
    return result.summarized, result.summary_failed, result.errors


def publish_ready(
    config: Config, store: Store, *, limit: int | None = None
) -> tuple[int, int, list[str]]:
    """Publish ready summaries."""
    pipeline = Pipeline(config, store, Path.cwd())
    result = PipelineResult()
    pipeline._publish(result, limit)
    return result.published, result.publish_failed, result.errors
