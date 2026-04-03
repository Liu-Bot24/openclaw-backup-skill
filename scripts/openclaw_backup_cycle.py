#!/usr/bin/env python3
"""Run OpenClaw local snapshot rotation and optional NAS archive uploads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openclaw_backup_snapshot import (
    SnapshotError,
    cleanup_incomplete_snapshots,
    create_snapshot,
    human_size,
    latest_matching_snapshot,
    list_snapshots,
    load_metadata,
    lock_backup_root,
    now_local,
    prune_snapshots,
    resolve_path,
)


def detect_installed_workspace() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent
    if candidate.name == "workspace":
        return candidate
    if (candidate / "skills").exists():
        return candidate
    return None


DEFAULT_WORKSPACE = detect_installed_workspace() or (Path.home() / ".openclaw" / "workspace")
DEFAULT_POLICY_PATH = DEFAULT_WORKSPACE / "data" / "openclaw_backup_policy.json"
DEFAULT_RUNTIME_STATE = DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "runtime_state.json"
DEFAULT_ARCHIVE_NAME = "openclaw-full.tar.zst"
SNAPSHOT_TIME_FMT = "%Y%m%d-%H%M%S"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def du_bytes(path: Path) -> int | None:
    try:
        result = run(["du", "-sk", str(path)])
        kib = int(result.stdout.split()[0])
        return kib * 1024
    except Exception:
        return None


def is_writable_dir(path: Path) -> bool:
    try:
        ensure_dir(path)
        probe = path / ".openclaw-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def iso_now() -> str:
    return now_local().isoformat()


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=now_local().tzinfo)
    return dt


def is_due(last_run: Any, *, days: int | None = None, hours: int | None = None) -> bool:
    last_dt = parse_iso(last_run)
    if last_dt is None:
        return True
    if days is not None:
        threshold = timedelta(days=int(days))
    elif hours is not None:
        threshold = timedelta(hours=int(hours))
    else:
        return True
    return now_local() - last_dt >= threshold


def placeholder_report(root: str) -> dict[str, Any]:
    return {
        "root": root,
        "snapshot_count": 0,
        "total_size_bytes": 0,
        "snapshots": [],
    }


def summarize_local_root(root: Path) -> dict[str, Any]:
    snapshots = list_snapshots(root)
    items: list[dict[str, Any]] = []
    for snapshot in snapshots:
        meta = load_metadata(snapshot)
        items.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "created_at": meta.get("created_at") or meta.get("completed_at"),
                "size_bytes": meta.get("size_bytes"),
                "linked_from": meta.get("linked_from"),
                "path": str(snapshot.path),
                "kind": "snapshot_dir",
            }
        )
    all_sizes_known = all(item["size_bytes"] is not None for item in items)
    any_linked = any(item["linked_from"] for item in items)
    if items and all_sizes_known and not any_linked:
        total_size = sum(int(item["size_bytes"]) for item in items)
    else:
        total_size = du_bytes(root)
    return {
        "root": str(root),
        "snapshot_count": len(snapshots),
        "total_size_bytes": total_size,
        "snapshots": items,
    }


def versions_dir(root: Path) -> Path:
    return root / "versions"


def list_archive_versions(root: Path) -> list[dict[str, Any]]:
    base = versions_dir(root)
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        metadata_path = entry / "metadata.json"
        if not metadata_path.exists():
            continue
        meta = load_json(metadata_path, {})
        if not isinstance(meta, dict):
            continue
        archive_name = meta.get("archive_filename") or DEFAULT_ARCHIVE_NAME
        archive_path = entry / archive_name
        size_bytes = meta.get("archive_size_bytes")
        if size_bytes is None and archive_path.exists():
            try:
                size_bytes = archive_path.stat().st_size
            except OSError:
                size_bytes = None
        items.append(
            {
                "snapshot_id": entry.name,
                "created_at": meta.get("created_at") or meta.get("completed_at"),
                "size_bytes": size_bytes,
                "linked_from": None,
                "path": str(entry),
                "kind": "archive_bundle",
                "archive_filename": archive_name,
            }
        )
    return items


def summarize_archive_root(root: Path) -> dict[str, Any]:
    items = list_archive_versions(root)
    known_sizes = [int(item["size_bytes"]) for item in items if item["size_bytes"] is not None]
    total_size = sum(known_sizes) if known_sizes else 0
    return {
        "root": str(root),
        "snapshot_count": len(items),
        "total_size_bytes": total_size,
        "snapshots": items,
    }


def make_unique_id(existing_parent: Path, final_parent: Path | None = None) -> str:
    base_id = now_local().strftime(SNAPSHOT_TIME_FMT)
    candidate = base_id
    suffix = 1
    while True:
        in_existing = (existing_parent / candidate).exists()
        in_final = final_parent is not None and (final_parent / candidate).exists()
        if not in_existing and not in_final:
            return candidate
        candidate = f"{base_id}-{suffix:02d}"
        suffix += 1


def create_archive_file(source: Path, output_path: Path) -> str:
    ensure_dir(output_path.parent)
    if shutil.which("zstd"):
        archive_name = DEFAULT_ARCHIVE_NAME
        output_real = output_path.with_name(archive_name)
        tar_proc = subprocess.Popen(
            ["tar", "-C", str(source.parent), "-cf", "-", source.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        assert tar_proc.stdout is not None
        zstd_proc = subprocess.Popen(
            ["zstd", "-T0", "-q", "-o", str(output_real)],
            stdin=tar_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        tar_proc.stdout.close()
        _, zstd_stderr = zstd_proc.communicate()
        tar_stderr = tar_proc.stderr.read() if tar_proc.stderr else b""
        tar_rc = tar_proc.wait()
        if tar_rc != 0 or zstd_proc.returncode != 0:
            raise SnapshotError(
                "archive creation failed.\n"
                f"tar stderr:\n{tar_stderr.decode(errors='replace')}\n"
                f"zstd stderr:\n{zstd_stderr.decode(errors='replace')}"
            )
        return archive_name

    archive_name = "openclaw-full.tar.gz"
    output_real = output_path.with_name(archive_name)
    result = run(["tar", "-C", str(source.parent), "-czf", str(output_real), source.name], check=False)
    if result.returncode != 0:
        raise SnapshotError(
            "archive creation failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return archive_name


def create_archive_bundle(
    source: Path,
    staging_root: Path,
    note: str,
    *,
    source_size_bytes: int | None = None,
) -> dict[str, Any]:
    ensure_dir(staging_root)
    version_id = make_unique_id(staging_root)
    stage_dir = staging_root / f".{version_id}.building"
    if stage_dir.exists():
        raise SnapshotError(f"Temporary archive dir already exists: {stage_dir}")
    ensure_dir(stage_dir)
    created_at = iso_now()
    archive_placeholder = stage_dir / DEFAULT_ARCHIVE_NAME
    archive_name = create_archive_file(source, archive_placeholder)
    archive_path = stage_dir / archive_name
    archive_size = archive_path.stat().st_size if archive_path.exists() else None
    if source_size_bytes is None:
        source_size_bytes = du_bytes(source)
    metadata = {
        "snapshot_id": version_id,
        "snapshot_kind": "archive_bundle",
        "source": str(source),
        "created_at": created_at,
        "completed_at": iso_now(),
        "note": note,
        "archive_filename": archive_name,
        "archive_size_bytes": archive_size,
        "source_size_bytes": source_size_bytes,
        "compression": "zstd" if archive_name.endswith(".zst") else "gzip",
    }
    save_json(stage_dir / "metadata.json", metadata)
    return {
        "snapshot_id": version_id,
        "stage_dir": stage_dir,
        "archive_name": archive_name,
        "archive_size_bytes": archive_size,
    }


def cleanup_incomplete_archive_versions(root: Path, older_than_minutes: int) -> list[str]:
    base = versions_dir(root)
    if not base.exists():
        return []
    cutoff = now_local().timestamp() - older_than_minutes * 60
    removed_ids: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        metadata_path = entry / "metadata.json"
        complete = metadata_path.exists() and not entry.name.startswith(".")
        if complete:
            continue
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime > cutoff:
            continue
        shutil.rmtree(entry)
        removed_ids.append(entry.name)
    return removed_ids


def upload_archive_bundle(stage_dir: Path, dest_root: Path, snapshot_id: str) -> Path:
    base = versions_dir(dest_root)
    ensure_dir(base)
    temp_remote = base / f".{snapshot_id}.uploading"
    final_remote = base / snapshot_id
    if temp_remote.exists() or final_remote.exists():
        raise SnapshotError(f"Remote archive target already exists for snapshot {snapshot_id}")
    result = run(["rsync", "-a", str(stage_dir) + "/", str(temp_remote)], check=False)
    if result.returncode != 0:
        raise SnapshotError(
            "archive upload failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    os.replace(temp_remote, final_remote)
    shutil.rmtree(stage_dir, ignore_errors=True)
    return final_remote


def gib(value: Any) -> int | None:
    if value is None:
        return None
    return int(value) * 1024 * 1024 * 1024


def rank_issue(level: str) -> int:
    if level == "critical":
        return 2
    if level == "warning":
        return 1
    return 0


def build_nas_issues(report: dict[str, Any], policy: dict[str, Any], nas_available: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not nas_available:
        issues.append(
            {
                "level": "warning",
                "reason": "NAS 备份路径当前不可用或不可写，本次跳过了 NAS 备份。",
            }
        )
        return issues

    total_size = report.get("total_size_bytes")
    warn_root = gib(policy.get("warn_backup_root_gib"))
    critical_root = gib(policy.get("critical_backup_root_gib"))

    if total_size is not None and critical_root is not None and total_size >= critical_root:
        issues.append({"level": "critical", "reason": f"NAS 备份仓库已占用 {human_size(total_size)}，达到 critical 阈值。"})
    elif total_size is not None and warn_root is not None and total_size >= warn_root:
        issues.append({"level": "warning", "reason": f"NAS 备份仓库已占用 {human_size(total_size)}，达到 warning 阈值。"})

    return issues


def build_delete_suggestions(
    report: dict[str, Any],
    issues: list[dict[str, str]],
    retention_policy: dict[str, Any],
    alert_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not issues:
        return []
    snapshots = report.get("snapshots", [])
    protected_recent_count = int(retention_policy.get("protected_recent_count", 30))
    if len(snapshots) <= protected_recent_count:
        return []
    candidates = snapshots[:-protected_recent_count]
    target_reclaim = 0
    warn_root = gib(alert_policy.get("warn_backup_root_gib"))
    total_size = report.get("total_size_bytes")

    if total_size is not None and warn_root is not None and total_size > warn_root:
        target_reclaim = max(target_reclaim, total_size - warn_root)
    if target_reclaim <= 0:
        return []

    suggestions: list[dict[str, Any]] = []
    reclaimed = 0
    for candidate in candidates:
        size_bytes = candidate.get("size_bytes") or 0
        reclaimed += size_bytes
        suggestions.append(candidate)
        if target_reclaim and reclaimed >= target_reclaim:
            break
    return suggestions


def alert_fingerprint(issues: list[dict[str, str]], suggestions: list[dict[str, Any]]) -> str:
    payload = {"issues": issues, "suggested_ids": [item.get("snapshot_id") for item in suggestions]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def should_queue_alert(state_file: Path, cooldown_hours: int, fingerprint: str) -> bool:
    state = load_json(state_file, {})
    if not state:
        return True
    if state.get("pending_delivery") and state.get("fingerprint") == fingerprint:
        return True
    if state.get("fingerprint") != fingerprint:
        return True
    last_delivered = parse_iso(state.get("last_delivered_at"))
    if last_delivered is None:
        return True
    return now_local() - last_delivered >= timedelta(hours=cooldown_hours)


def format_alert_message(
    local_report: dict[str, Any],
    nas_report: dict[str, Any],
    issues: list[dict[str, str]],
    suggestions: list[dict[str, Any]],
    nas_available: bool,
) -> str:
    lines = [
        "【OpenClaw 备份提醒】",
        "",
        "当前状态：",
        f"- 本机快照：{local_report.get('snapshot_count', 0)} 份",
        f"- 本机仓库占用：{human_size(local_report.get('total_size_bytes'))}",
        f"- NAS 路径：{nas_report.get('root')}",
        f"- NAS 可写：{'是' if nas_available else '否'}",
        f"- NAS 版本数：{nas_report.get('snapshot_count', 0)} 份",
        f"- NAS 备份仓库占用：{human_size(nas_report.get('total_size_bytes'))}",
    ]
    lines.extend(["", "触发原因："])
    for issue in issues:
        lines.append(f"- [{issue['level']}] {issue['reason']}")
    if suggestions:
        lines.extend(["", "建议优先删除这些较早版本："])
        for item in suggestions:
            lines.append(f"- {item.get('snapshot_id')}  约 {human_size(item.get('size_bytes'))}  路径：{item.get('path')}")
    else:
        lines.extend(["", "当前没有足够多的早期版本可安全建议删除。"])
    lines.extend(["", "如果你确认要删，可以直接对我说：", "`删除 NAS 备份版本 <snapshot_id>` 或一次删多个。"])
    return "\n".join(lines)


def load_policy(path: Path) -> dict[str, Any]:
    policy = load_json(path, None)
    if not isinstance(policy, dict):
        raise SnapshotError(f"Invalid backup policy file: {path}")
    return policy


def local_root_from_cfg(cfg: dict[str, Any]) -> Path | None:
    root = str(cfg.get("root") or "").strip()
    return resolve_path(root) if root else None


def nas_root_from_cfg(cfg: dict[str, Any]) -> Path | None:
    root = str(cfg.get("root") or "").strip()
    return resolve_path(root) if root else None


def local_backup_result(source: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    if not cfg.get("enabled", True):
        return {
            "status": "disabled",
            "snapshot_id": "disabled",
            "removed_ids": [],
            "snapshot_size_bytes": None,
            "cleaned_incomplete_ids": [],
            "source_changed": True,
        }
    root = local_root_from_cfg(cfg)
    if root is None:
        return {
            "status": "unconfigured",
            "snapshot_id": "unconfigured",
            "removed_ids": [],
            "snapshot_size_bytes": None,
            "cleaned_incomplete_ids": [],
            "source_changed": True,
        }
    note = f"{cfg.get('note_prefix', 'automatic local snapshot')} {now_local().strftime('%Y-%m-%d %H:%M:%S')}"
    compare_excludes = [str(item).strip() for item in (cfg.get("change_detection_excludes") or []) if str(item).strip()]
    with lock_backup_root(root):
        cleaned_incomplete = cleanup_incomplete_snapshots(
            root,
            older_than_minutes=int(cfg.get("cleanup_incomplete_after_minutes", 5)),
        )
        if cfg.get("skip_if_unchanged", True):
            reusable = latest_matching_snapshot(source, root, exclude_patterns=compare_excludes)
        else:
            reusable = None
        if reusable is not None:
            removed = prune_snapshots(root, int(cfg.get("keep", 3)))
            meta = load_metadata(reusable)
            return {
                "status": "unchanged",
                "snapshot_id": reusable.snapshot_id,
                "removed_ids": removed,
                "snapshot_size_bytes": meta.get("size_bytes"),
                "cleaned_incomplete_ids": cleaned_incomplete,
                "source_changed": False,
            }
        snapshot = create_snapshot(source, root, note=note, allow_fallback_copy=True)
        removed = prune_snapshots(root, int(cfg.get("keep", 3)))
        meta = load_metadata(snapshot)
    return {
        "status": "ok",
        "snapshot_id": snapshot.snapshot_id,
        "removed_ids": removed,
        "snapshot_size_bytes": meta.get("size_bytes"),
        "cleaned_incomplete_ids": cleaned_incomplete,
        "source_changed": True,
    }


def nas_archive_result(source: Path, cfg: dict[str, Any], source_size_bytes: int | None) -> tuple[dict[str, Any], bool]:
    if not cfg.get("enabled", False):
        return (
            {
                "status": "disabled",
                "snapshot_id": "disabled",
                "removed_ids": [],
                "cleaned_incomplete_ids": [],
                "archive_size_bytes": None,
            },
            False,
        )
    root = nas_root_from_cfg(cfg)
    if root is None:
        return (
            {
                "status": "unconfigured",
                "snapshot_id": "unconfigured",
                "removed_ids": [],
                "cleaned_incomplete_ids": [],
                "archive_size_bytes": None,
            },
            False,
        )
    if not is_writable_dir(root):
        return (
            {
                "status": "unavailable",
                "snapshot_id": "unavailable",
                "removed_ids": [],
                "cleaned_incomplete_ids": [],
                "archive_size_bytes": None,
            },
            False,
        )

    staging_root = resolve_path(cfg.get("staging_root") or (Path.home() / "OpenClawBackups" / "nas-staging"))
    ensure_dir(staging_root)
    with lock_backup_root(root):
        cleaned_incomplete = cleanup_incomplete_archive_versions(
            root,
            int(cfg.get("cleanup_incomplete_after_minutes", 30)),
        )
        note = f"{cfg.get('note_prefix', 'full archive upload')} {now_local().strftime('%Y-%m-%d %H:%M:%S')}"
        bundle = create_archive_bundle(source, staging_root, note, source_size_bytes=source_size_bytes)
        upload_archive_bundle(bundle["stage_dir"], root, bundle["snapshot_id"])
    return (
        {
            "status": "ok",
            "snapshot_id": bundle["snapshot_id"],
            "removed_ids": [],
            "cleaned_incomplete_ids": cleaned_incomplete,
            "archive_size_bytes": bundle["archive_size_bytes"],
        },
        True,
    )


def build_reports(local_cfg: dict[str, Any], nas_cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    local_root = local_root_from_cfg(local_cfg)
    if local_root and local_root.exists():
        local_report = summarize_local_root(local_root)
    else:
        local_report = placeholder_report(str(local_root or local_cfg.get("root") or ""))

    nas_root = nas_root_from_cfg(nas_cfg)
    if nas_root and nas_root.exists():
        nas_mode = str(nas_cfg.get("mode") or "archive_upload")
        nas_report = summarize_archive_root(nas_root) if nas_mode == "archive_upload" else placeholder_report(str(nas_root))
    else:
        nas_report = placeholder_report(str(nas_root or nas_cfg.get("root") or ""))
    return local_report, nas_report


def backup_completed(local_result: dict[str, Any], nas_result: dict[str, Any], local_cfg: dict[str, Any], nas_cfg: dict[str, Any]) -> bool:
    local_enabled = bool(local_cfg.get("enabled", True))
    nas_enabled = bool(nas_cfg.get("enabled", False))
    local_ok = (not local_enabled) or local_result.get("status") in {"ok", "unchanged"}
    nas_ok = (not nas_enabled) or nas_result.get("status") in {"ok", "unchanged"}
    return local_ok and nas_ok


def update_alert_state(
    *,
    alert_state_file: Path,
    issues: list[dict[str, str]],
    suggestions: list[dict[str, Any]],
    cooldown_hours: int,
) -> tuple[bool, dict[str, Any]]:
    previous = load_json(alert_state_file, {})
    if not issues:
        next_state = {
            "pending_delivery": False,
            "fingerprint": None,
            "last_delivered_at": previous.get("last_delivered_at") if isinstance(previous, dict) else None,
            "last_issue_level": None,
            "last_evaluated_at": iso_now(),
            "last_cleared_at": iso_now(),
        }
        save_json(alert_state_file, next_state)
        return False, next_state

    fingerprint = alert_fingerprint(issues, suggestions)
    pending_delivery = should_queue_alert(alert_state_file, cooldown_hours, fingerprint)
    next_state = {
        "pending_delivery": pending_delivery,
        "fingerprint": fingerprint,
        "last_delivered_at": previous.get("last_delivered_at") if isinstance(previous, dict) else None,
        "last_issue_level": max((issue["level"] for issue in issues), key=rank_issue),
        "last_evaluated_at": iso_now(),
    }
    if pending_delivery:
        next_state["pending_created_at"] = iso_now()
    elif isinstance(previous, dict) and previous.get("pending_created_at") and previous.get("fingerprint") == fingerprint:
        next_state["pending_created_at"] = previous.get("pending_created_at")
    save_json(alert_state_file, next_state)
    return pending_delivery, next_state


def run_cycle(policy_path: Path, *, respect_schedule: bool) -> tuple[str, dict[str, Any]]:
    policy = load_policy(policy_path)
    source = resolve_path(policy["source"])
    local_cfg = policy.get("local", {})
    nas_cfg = policy.get("nas", {})
    alerts_cfg = policy.get("alerts", {})
    automation_cfg = policy.get("automation", {})

    runtime_state_path = resolve_path(automation_cfg.get("state_file") or DEFAULT_RUNTIME_STATE)
    runtime_state = load_json(runtime_state_path, {})

    if respect_schedule:
        if not automation_cfg.get("enabled", False):
            local_report, nas_report = build_reports(local_cfg, nas_cfg)
            report = {
                "generated_at": iso_now(),
                "policy_path": str(policy_path),
                "reason": "automation disabled in config",
                "local": local_report,
                "nas": nas_report,
                "results": {"backup_due": False, "capacity_check_due": False},
                "issues": [],
                "suggestions": [],
            }
            return "BACKUP_SKIPPED", report
        backup_due = is_due(
            runtime_state.get("last_backup_completed_at"),
            days=int(automation_cfg.get("backup_interval_days", 14)),
        )
        capacity_due = is_due(
            runtime_state.get("last_capacity_check_completed_at"),
            hours=int(automation_cfg.get("capacity_check_interval_hours", 168)),
        )
        if not backup_due and not capacity_due:
            local_report, nas_report = build_reports(local_cfg, nas_cfg)
            report = {
                "generated_at": iso_now(),
                "policy_path": str(policy_path),
                "reason": "no backup or capacity check is due",
                "local": local_report,
                "nas": nas_report,
                "results": {"backup_due": False, "capacity_check_due": False},
                "issues": [],
                "suggestions": [],
            }
            return "BACKUP_SKIPPED", report
    else:
        backup_due = True
        capacity_due = True

    local_result = {
        "status": "not-run",
        "snapshot_id": "skipped",
        "removed_ids": [],
        "snapshot_size_bytes": None,
        "cleaned_incomplete_ids": [],
    }
    nas_result = {
        "status": "not-run",
        "snapshot_id": "skipped",
        "removed_ids": [],
        "cleaned_incomplete_ids": [],
        "archive_size_bytes": None,
    }
    nas_available = False

    if backup_due:
        local_result = local_backup_result(source, local_cfg)
        source_size_bytes = local_result.get("snapshot_size_bytes") or du_bytes(source)
        if local_result.get("source_changed", True):
            nas_result, nas_available = nas_archive_result(source, nas_cfg, source_size_bytes)
        else:
            nas_root = nas_root_from_cfg(nas_cfg)
            nas_available = bool(nas_root and nas_root.exists() and is_writable_dir(nas_root))
            nas_result = {
                "status": "unchanged",
                "snapshot_id": "unchanged",
                "removed_ids": [],
                "cleaned_incomplete_ids": [],
                "archive_size_bytes": None,
            }
    else:
        source_size_bytes = du_bytes(source)
        nas_root = nas_root_from_cfg(nas_cfg)
        nas_available = bool(nas_root and nas_root.exists() and is_writable_dir(nas_root))

    local_report, nas_report = build_reports(local_cfg, nas_cfg)

    issues: list[dict[str, str]] = []
    suggestions: list[dict[str, Any]] = []
    nas_root = nas_root_from_cfg(nas_cfg)
    need_nas_check = bool(nas_cfg.get("enabled", False)) and nas_root is not None and (
        capacity_due or nas_result.get("status") in {"unavailable", "unconfigured"}
    )
    if need_nas_check:
        issues = build_nas_issues(nas_report, alerts_cfg, nas_available)
        suggestions = build_delete_suggestions(nas_report, issues, nas_cfg, alerts_cfg)

    backup_completed_ok = backup_completed(local_result, nas_result, local_cfg, nas_cfg)
    report = {
        "generated_at": iso_now(),
        "policy_path": str(policy_path),
        "local": local_report,
        "nas": nas_report,
        "results": {
            "backup_due": backup_due,
            "capacity_check_due": capacity_due,
            "backup_completed": backup_completed_ok,
            "local_snapshot_id": local_result["snapshot_id"],
            "local_removed_ids": local_result["removed_ids"],
            "local_cleaned_incomplete_ids": local_result["cleaned_incomplete_ids"],
            "nas_snapshot_id": nas_result["snapshot_id"],
            "nas_removed_ids": nas_result["removed_ids"],
            "nas_cleaned_incomplete_ids": nas_result["cleaned_incomplete_ids"],
            "nas_available": nas_available,
            "source_size_bytes": source_size_bytes,
        },
        "issues": issues,
        "suggestions": suggestions,
    }

    report_file = resolve_path(
        alerts_cfg.get("report_file") or (DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "latest_report.json")
    )
    save_json(report_file, report)

    if backup_due and backup_completed_ok:
        runtime_state["last_backup_completed_at"] = iso_now()
    if capacity_due:
        runtime_state["last_capacity_check_completed_at"] = iso_now()
    runtime_state["last_report_file"] = str(report_file)
    save_json(runtime_state_path, runtime_state)

    alert_state_file = resolve_path(
        alerts_cfg.get("state_file") or (DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "alert_state.json")
    )
    pending_delivery, _alert_state = update_alert_state(
        alert_state_file=alert_state_file,
        issues=issues,
        suggestions=suggestions,
        cooldown_hours=int(alerts_cfg.get("cooldown_hours", 72)),
    )

    if issues:
        status = "BACKUP_ALERT"
    else:
        status = "BACKUP_OK"

    if pending_delivery:
        report["results"]["pending_alert_delivery"] = True
    else:
        report["results"]["pending_alert_delivery"] = False

    return status, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OpenClaw backup rotation and capacity checks.")
    parser.add_argument(
        "--policy",
        default=str(DEFAULT_POLICY_PATH),
        help=f"Backup policy JSON path. Default: {DEFAULT_POLICY_PATH}",
    )
    parser.add_argument(
        "--respect-schedule",
        action="store_true",
        help="Respect automation.enabled and interval settings in the policy file.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    policy_path = resolve_path(args.policy)
    try:
        status, report = run_cycle(policy_path, respect_schedule=args.respect_schedule)
        print(
            f"{status}: local={report['results'].get('local_snapshot_id', '-')} "
            f"local_pruned={len(report['results'].get('local_removed_ids', []))} "
            f"nas={report['results'].get('nas_snapshot_id', '-')} "
            f"issues={len(report.get('issues', []))}"
        )
        return 0
    except SnapshotError as exc:
        print(f"BACKUP_FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"BACKUP_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
