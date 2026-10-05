"""Incremental ingestion, LLM summarization, and Memos publishing."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .config import Config
from .errors import InfohubError, LLMError, MemosError, SourceError
from .llm import LLMClient
from .memos import MemosClient, remote_ids
from .render import body_hash, render_memo
from .sources import SourceRunResult, read_source, run_source
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
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


def ingest_sources(
    config: Config,
    store: Store,
    repo_root: Path,
    source_names: list[str] | None = None,
    limit: int | None = None,
) -> tuple[int, int, list[str]]:
    names = source_names or list(config.sources)
    ingested = 0
    changed = 0
    errors: list[str] = []
    processed = 0
    for name in names:
        source = config.sources.get(name)
        if source is None or not source.enabled:
            continue
        try:
            items = read_source(source, repo_root)
            if limit is not None:
                # Source adapters return chronological order; a run limit should
                # operate on the newest records so a first sync catches current news.
                items = items[-limit:]
            for item in items:
                is_new, is_changed = store.upsert_item(item)
                ingested += int(is_new)
                changed += int(is_changed)
                processed += 1
        except SourceError as exc:
            errors.append(exc.message)
    return ingested, changed, errors


def ensure_summary_queue(config: Config, store: Store, *, limit: int | None = None) -> int:
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


def summarize_pending(config: Config, store: Store, *, limit: int | None = None) -> tuple[int, int, list[str]]:
    client = LLMClient(config.llm, api_key=config.llm_api_key())
    success = 0
    failed = 0
    errors: list[str] = []
    for row in store.pending_summaries(limit=limit):
        summary_id = int(row["id"])
        store.mark_summary_running(summary_id)
        item = dict(row)
        try:
            result = client.summarize(item)
            raw_path = paths.llm_raw_dir() / f"{paths.safe_component(row['item_key'])}-{summary_id}.json"
            paths.write_json_atomic(raw_path, result.response)
            usage = result.response.get("usage")
            store.save_summary_result(
                summary_id,
                summary=result.summary,
                response_path=str(raw_path),
                input_chars=result.input_chars,
                duration_ms=result.duration_ms,
                usage_json=json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                status="ok",
            )
            success += 1
        except LLMError as exc:
            store.save_summary_result(summary_id, status="failed", error=exc.message)
            failed += 1
            errors.append(f"{row['item_key']}: {exc.message}")
            log.warning("摘要失败 %s: %s", row["item_key"], exc.message)
    return success, failed, errors


def publish_ready(config: Config, store: Store, *, limit: int | None = None) -> tuple[int, int, list[str]]:
    client = MemosClient(config.memos, config.memos_token())
    success = 0
    failed = 0
    errors: list[str] = []
    for row in store.ready_summaries(limit=limit):
        item = dict(row)
        summary_id = int(row["id"])
        content = render_memo(item, row["summary"], config.memos.tags)
        digest = body_hash(content)
        delivery = store.ensure_delivery(row["item_key"], summary_id, "memos", digest)
        if delivery["status"] == "published":
            continue
        store.mark_delivery_running(row["item_key"], summary_id, "memos")
        try:
            remote = client.create(content)
            name, uid = remote_ids(remote)
            store.save_delivery_result(
                row["item_key"],
                summary_id,
                "memos",
                status="published",
                remote_name=name,
                remote_uid=uid,
            )
            success += 1
        except MemosError as exc:
            store.save_delivery_result(
                row["item_key"], summary_id, "memos", status="failed", error=exc.message
            )
            failed += 1
            errors.append(f"{row['item_key']}: {exc.message}")
            log.warning("发布失败 %s: %s", row["item_key"], exc.message)
    return success, failed, errors


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
    started = time.monotonic()
    result = PipelineResult()
    run_id = None if dry_run else store.begin_run()
    result.run_id = run_id
    source_run_enabled = config.pipeline.run_sources if run_sources is None else run_sources
    publish_enabled = config.pipeline.publish_to_memos if do_publish is None else do_publish
    if dry_run:
        source_run_enabled = False
        do_summarize = False
        publish_enabled = False

    if source_run_enabled:
        for name in source_names or list(config.sources):
            source = config.sources.get(name)
            if source is None or not source.enabled:
                continue
            run_result = run_source(source, repo_root)
            result.source_status[name] = _source_result_dict(run_result)
            if run_result.status == "error":
                result.errors.append(f"{name}: {run_result.error}")

    if not dry_run:
        result.ingested, result.changed, ingest_errors = ingest_sources(
            config, store, repo_root, source_names, limit or config.pipeline.max_items_per_run
        )
        result.errors.extend(ingest_errors)
        ensure_summary_queue(config, store, limit=limit or config.pipeline.max_items_per_run)
        if do_summarize:
            result.summarized, result.summary_failed, errors = summarize_pending(
                config, store, limit=limit or config.pipeline.max_items_per_run
            )
            result.errors.extend(errors)
        if publish_enabled:
            result.published, result.publish_failed, errors = publish_ready(
                config, store, limit=limit or config.pipeline.max_items_per_run
            )
            result.errors.extend(errors)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    if run_id is not None:
        status = "error" if result.errors and not (result.ingested or result.summarized or result.published) else (
            "partial" if result.errors else "ok"
        )
        store.finish_run(
            run_id,
            status=status,
            source_status=result.source_status,
            ingested=result.ingested,
            summarized=result.summarized,
            published=result.published,
            error="; ".join(result.errors[:5]),
        )
    return result


def _source_result_dict(value: SourceRunResult) -> dict[str, Any]:
    return {
        "status": value.status,
        "returncode": value.returncode,
        "duration_ms": value.duration_ms,
        "error": value.error,
    }
