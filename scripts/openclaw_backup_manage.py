#!/usr/bin/env python3
"""Product-facing management CLI for the OpenClaw backup skill."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from openclaw_backup_cycle import (
    alert_fingerprint,
    format_alert_message,
    run_cycle,
)
from openclaw_backup_snapshot import (
    SnapshotError,
    create_restore_guard_snapshot,
    delete_snapshots,
    get_snapshot,
    list_snapshots,
    load_metadata,
    lock_backup_root,
    resolve_path,
    restore_snapshot,
)


def detect_installed_workspace() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent
    if candidate.name == "workspace":
        return candidate
    if (candidate / "skills").exists():
        return candidate
    return None


DEFAULT_WORKSPACE = detect_installed_workspace() or (Path.home() / ".openclaw" / "workspace")
DEFAULT_OPENCLAW_ROOT = DEFAULT_WORKSPACE.parent
DEFAULT_SETTINGS_PATH = DEFAULT_WORKSPACE / "data" / "openclaw_backup_settings.json"
DEFAULT_POLICY_PATH = DEFAULT_WORKSPACE / "data" / "openclaw_backup_policy.json"
DEFAULT_JOBS_PATH = DEFAULT_OPENCLAW_ROOT / "cron" / "jobs.json"
DEFAULT_RUNTIME_STATE = DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "runtime_state.json"
DEFAULT_REPORT_PATH = DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "latest_report.json"
DEFAULT_ALERT_STATE_PATH = DEFAULT_WORKSPACE / "memory" / "openclaw_backup" / "alert_state.json"
DEFAULT_LOCAL_ROOT = Path.home() / "OpenClawBackups" / "openclaw-snapshots"
DEFAULT_NAS_STAGING_ROOT = Path.home() / "OpenClawBackups" / "nas-staging"
DEFAULT_SOURCE = DEFAULT_OPENCLAW_ROOT
DEFAULT_WEEKLY_WEEKDAY = 0
DEFAULT_WEEKLY_TIME = "03:30"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_BACKUP_JOB_NAME = "OpenClaw备份轮转"
LEGACY_BACKUP_JOB_NAMES = {
    DEFAULT_BACKUP_JOB_NAME,
    "OpenClaw快照备份轮转",
}
DEFAULT_BACKUP_JOB_DESCRIPTION = "由 openclaw-backup-skill 安装；按产品配置执行本机快照与可选 NAS 归档。"
DEFAULT_JOB_MARKER = "openclaw-backup-skill"
DEFAULT_CHANGE_EXCLUDES = [
    "logs/**",
    "agents/*/sessions/**",
    "openclaw-weixin/accounts/*.sync.json",
    "workspace/memory/openclaw_backup/latest_report.json",
    "workspace/memory/openclaw_backup/runtime_state.json",
    "workspace/memory/openclaw_backup/alert_state.json",
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now_local().isoformat()


def detect_system_timezone() -> str:
    zoneinfo = Path("/etc/localtime")
    try:
        real = zoneinfo.resolve()
    except OSError:
        return DEFAULT_TIMEZONE
    parts = real.parts
    if "zoneinfo" in parts:
        idx = parts.index("zoneinfo")
        suffix = parts[idx + 1 :]
        if suffix:
            return "/".join(suffix)
    return DEFAULT_TIMEZONE


def parse_bool_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"on", "true", "yes", "1", "enable", "enabled", "开启", "开", "是"}:
        return True
    if normalized in {"off", "false", "no", "0", "disable", "disabled", "关闭", "关", "否"}:
        return False
    raise SnapshotError(f"Invalid boolean flag value: {value}")


def normalize_cadence(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "manual": "manual",
        "手动": "manual",
        "weekly": "weekly",
        "every-week": "weekly",
        "每周": "weekly",
        "每周一次": "weekly",
        "two-weeks": "two-weeks",
        "every-two-weeks": "two-weeks",
        "biweekly": "two-weeks",
        "两周": "two-weeks",
        "两周一次": "two-weeks",
        "每两周": "two-weeks",
        "每两周一次": "two-weeks",
        "custom": "custom",
        "自定义": "custom",
    }
    if normalized not in mapping:
        raise SnapshotError("--cadence must be one of: manual, weekly, two-weeks, custom")
    return mapping[normalized]


def cadence_label(value: str) -> str:
    return {
        "manual": "手动",
        "weekly": "每周一次",
        "two-weeks": "两周一次",
        "custom": "自定义",
    }.get(value, value)


def normalize_alert_delivery(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "recent-channel": "recent-channel",
        "recent_channel": "recent-channel",
        "recent": "recent-channel",
        "最近一次交互渠道": "recent-channel",
        "最近交互渠道": "recent-channel",
        "off": "off",
        "关闭": "off",
    }
    if normalized not in mapping:
        raise SnapshotError("--alert-delivery must be one of: recent-channel, off")
    return mapping[normalized]


def alert_delivery_label(value: str) -> str:
    return "最近一次交互渠道" if value == "recent-channel" else "关闭"


def parse_weekday(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    named = {
        "sun": 0,
        "sunday": 0,
        "mon": 1,
        "monday": 1,
        "tue": 2,
        "tuesday": 2,
        "wed": 3,
        "wednesday": 3,
        "thu": 4,
        "thursday": 4,
        "fri": 5,
        "friday": 5,
        "sat": 6,
        "saturday": 6,
        "周日": 0,
        "星期日": 0,
        "周天": 0,
        "星期天": 0,
        "周一": 1,
        "星期一": 1,
        "周二": 2,
        "星期二": 2,
        "周三": 3,
        "星期三": 3,
        "周四": 4,
        "星期四": 4,
        "周五": 5,
        "星期五": 5,
        "周六": 6,
        "星期六": 6,
    }
    if normalized in named:
        return named[normalized]
    try:
        raw = int(normalized)
    except ValueError as exc:
        raise SnapshotError(f"Invalid weekday: {value}") from exc
    if raw < 0 or raw > 6:
        raise SnapshotError(f"Weekday must be between 0 and 6, got: {value}")
    return raw


def parse_time_of_day(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    parts = normalized.split(":")
    if len(parts) != 2:
        raise SnapshotError(f"Time must be HH:MM, got: {value}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise SnapshotError(f"Time must be HH:MM, got: {value}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise SnapshotError(f"Time must be HH:MM, got: {value}")
    return f"{hour:02d}:{minute:02d}"


def cron_expr_for_weekly(weekday: int, time_of_day: str) -> str:
    hour_str, minute_str = time_of_day.split(":")
    return f"{int(minute_str)} {int(hour_str)} * * {weekday}"


def weekday_name(weekday: int) -> str:
    names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    return names[weekday]


def weekday_label(weekday: int) -> str:
    names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
    return names[weekday]


def build_default_settings() -> dict[str, Any]:
    timezone = detect_system_timezone()
    return {
        "version": 1,
        "source": str(DEFAULT_SOURCE),
        "auto": {
            "enabled": False,
            "cadence": "two-weeks",
            "check": {
                "kind": "weekly",
                "weekday": DEFAULT_WEEKLY_WEEKDAY,
                "time": DEFAULT_WEEKLY_TIME,
                "timezone": timezone,
            },
            "backup_interval_days": 14,
            "capacity_check_interval_hours": 168,
        },
        "local": {
            "enabled": True,
            "root": str(DEFAULT_LOCAL_ROOT),
            "keep": 3,
            "skip_if_unchanged": True,
            "change_detection_excludes": list(DEFAULT_CHANGE_EXCLUDES),
            "cleanup_incomplete_after_minutes": 5,
            "note_prefix": "automatic local snapshot",
        },
        "nas": {
            "enabled": False,
            "root": "",
            "staging_root": str(DEFAULT_NAS_STAGING_ROOT),
            "skip_if_unchanged": True,
            "cleanup_incomplete_after_minutes": 30,
            "note_prefix": "full archive upload",
            "protected_recent_count": 30,
        },
        "alerts": {
            "cooldown_hours": 72,
            "warn_backup_root_gib": 120,
            "critical_backup_root_gib": 180,
        },
        "runtime": {
            "state_file": str(DEFAULT_RUNTIME_STATE),
            "report_file": str(DEFAULT_REPORT_PATH),
            "alert_state_file": str(DEFAULT_ALERT_STATE_PATH),
        },
        "cron": {
            "job_name": DEFAULT_BACKUP_JOB_NAME,
            "job_description": DEFAULT_BACKUP_JOB_DESCRIPTION,
            "job_id": None,
            "session_target": "isolated",
            "wake_mode": "now",
            "alert_delivery": "recent-channel",
        },
    }


def merge_settings(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = value
    return result


def parse_weekly_cron_expr(expr: str | None) -> tuple[int, str] | None:
    if not expr:
        return None
    parts = str(expr).strip().split()
    if len(parts) != 5:
        return None
    minute, hour, day_of_month, month, weekday = parts
    if day_of_month != "*" or month != "*":
        return None
    if not minute.isdigit() or not hour.isdigit() or not weekday.isdigit():
        return None
    weekday_value = int(weekday)
    if weekday_value == 7:
        weekday_value = 0
    if weekday_value < 0 or weekday_value > 6:
        return None
    return weekday_value, f"{int(hour):02d}:{int(minute):02d}"


def migrate_legacy_policy(policy_path: Path) -> dict[str, Any]:
    policy = load_json(policy_path, None)
    if not isinstance(policy, dict):
        return {}

    automation = policy.get("automation") or {}
    local = policy.get("local") or {}
    nas = policy.get("nas") or {}
    alerts = policy.get("alerts") or {}
    migrated: dict[str, Any] = {}

    if policy.get("source"):
        migrated["source"] = str(policy["source"])

    auto_cfg: dict[str, Any] = {}
    if isinstance(automation, dict):
        enabled = bool(automation.get("enabled", False))
        backup_interval_days = int(automation.get("backup_interval_days", 14))
        auto_cfg["enabled"] = enabled
        if not enabled:
            auto_cfg["cadence"] = "manual"
        elif backup_interval_days == 7:
            auto_cfg["cadence"] = "weekly"
        elif backup_interval_days == 14:
            auto_cfg["cadence"] = "two-weeks"
        else:
            auto_cfg["cadence"] = "custom"
        auto_cfg["backup_interval_days"] = backup_interval_days
        auto_cfg["capacity_check_interval_hours"] = int(automation.get("capacity_check_interval_hours", 168))
        auto_cfg["check"] = {
            "kind": "weekly",
            "weekday": DEFAULT_WEEKLY_WEEKDAY,
            "time": DEFAULT_WEEKLY_TIME,
            "timezone": str(automation.get("scheduler_tz") or detect_system_timezone()),
        }
        parsed_check = parse_weekly_cron_expr(automation.get("scheduler_cron_expr"))
        if parsed_check is not None:
            weekday_value, time_value = parsed_check
            auto_cfg["check"]["weekday"] = weekday_value
            auto_cfg["check"]["time"] = time_value
    if auto_cfg:
        migrated["auto"] = auto_cfg

    if isinstance(local, dict):
        migrated["local"] = {
            "enabled": bool(local.get("enabled", True)),
            "root": str(local.get("root") or DEFAULT_LOCAL_ROOT),
            "keep": int(local.get("keep", 3)),
            "skip_if_unchanged": bool(local.get("skip_if_unchanged", True)),
            "change_detection_excludes": list(local.get("change_detection_excludes") or DEFAULT_CHANGE_EXCLUDES),
            "cleanup_incomplete_after_minutes": int(local.get("cleanup_incomplete_after_minutes", 5)),
            "note_prefix": str(local.get("note_prefix") or "automatic local snapshot"),
        }

    if isinstance(nas, dict):
        migrated["nas"] = {
            "enabled": bool(nas.get("enabled", False)),
            "root": str(nas.get("root") or ""),
            "staging_root": str(nas.get("staging_root") or DEFAULT_NAS_STAGING_ROOT),
            "skip_if_unchanged": bool(nas.get("skip_if_unchanged", True)),
            "cleanup_incomplete_after_minutes": int(nas.get("cleanup_incomplete_after_minutes", 30)),
            "note_prefix": str(nas.get("note_prefix") or "full archive upload"),
            "protected_recent_count": int(nas.get("protected_recent_count", 30)),
        }

    if isinstance(alerts, dict):
        migrated["alerts"] = {
            "cooldown_hours": int(alerts.get("cooldown_hours", 72)),
            "warn_backup_root_gib": int(alerts.get("warn_backup_root_gib", 120)),
            "critical_backup_root_gib": int(alerts.get("critical_backup_root_gib", 180)),
        }

    migrated["runtime"] = {
        "state_file": str(automation.get("state_file") or DEFAULT_RUNTIME_STATE),
        "report_file": str(alerts.get("report_file") or DEFAULT_REPORT_PATH),
        "alert_state_file": str(alerts.get("state_file") or DEFAULT_ALERT_STATE_PATH),
    }
    return migrated


def load_settings(path: Path, policy_path: Path | None = None) -> dict[str, Any]:
    existing = load_json(path, {})
    defaults = build_default_settings()
    if not isinstance(existing, dict):
        existing = {}
    merged = merge_settings(defaults, existing)
    if path.exists():
        return merged
    if policy_path is not None and Path(policy_path).exists():
        return merge_settings(merged, migrate_legacy_policy(Path(policy_path)))
    return merged


def render_policy(settings: dict[str, Any]) -> dict[str, Any]:
    runtime = settings["runtime"]
    auto = settings["auto"]
    local = settings["local"]
    nas = settings["nas"]
    alerts = settings["alerts"]
    return {
        "source": settings["source"],
        "automation": {
            "enabled": bool(auto["enabled"]),
            "scheduler_cron_expr": cron_expr_for_weekly(int(auto["check"]["weekday"]), str(auto["check"]["time"])),
            "scheduler_tz": str(auto["check"]["timezone"]),
            "backup_interval_days": int(auto["backup_interval_days"]),
            "capacity_check_interval_hours": int(auto["capacity_check_interval_hours"]),
            "state_file": runtime["state_file"],
        },
        "local": {
            "enabled": bool(local["enabled"]),
            "mode": "snapshot_dir",
            "root": local["root"],
            "keep": int(local["keep"]),
            "skip_if_unchanged": bool(local["skip_if_unchanged"]),
            "change_detection_excludes": list(local["change_detection_excludes"]),
            "cleanup_incomplete_after_minutes": int(local["cleanup_incomplete_after_minutes"]),
            "note_prefix": str(local["note_prefix"]),
        },
        "nas": {
            "enabled": bool(nas["enabled"]),
            "mode": "archive_upload",
            "root": nas["root"],
            "staging_root": nas["staging_root"],
            "skip_if_unchanged": bool(nas["skip_if_unchanged"]),
            "cleanup_incomplete_after_minutes": int(nas["cleanup_incomplete_after_minutes"]),
            "note_prefix": str(nas["note_prefix"]),
            "protected_recent_count": int(nas["protected_recent_count"]),
        },
        "alerts": {
            "state_file": runtime["alert_state_file"],
            "report_file": runtime["report_file"],
            "cooldown_hours": int(alerts["cooldown_hours"]),
            "warn_backup_root_gib": int(alerts["warn_backup_root_gib"]),
            "critical_backup_root_gib": int(alerts["critical_backup_root_gib"]),
        },
    }


def script_path_from_manage(manage_script: Path, name: str) -> Path:
    return manage_script.parent / name


def build_cycle_message(cycle_script: Path, manage_script: Path, policy_path: Path) -> str:
    return (
        "先只运行下面这个命令：\n\n"
        f"- `python3 {cycle_script} --respect-schedule --policy {policy_path}`\n\n"
        "然后严格按这套产品规则处理：\n"
        "1. 如果命令输出只有一行，且以 `BACKUP_OK:` 或 `BACKUP_SKIPPED:` 开头，不要回复用户任何消息。\n"
        "2. 如果命令输出只有一行，且以 `BACKUP_FAILED:` 开头，只回复那一行原文，不要改写。\n"
        "3. 如果命令输出只有一行，且以 `BACKUP_ALERT:` 开头，再运行：\n"
        f"   `python3 {manage_script} render-alert --policy {policy_path} --consume --only-pending`\n"
        "4. 对第二个命令：\n"
        "   - 若第一行是 `BACKUP_ALERT_NONE`，不要回复用户任何消息。\n"
        "   - 若第一行是 `BACKUP_ALERT_READY`，把后续正文原样发给用户，不要总结，不要改写。\n"
        "5. 不要手工改 `jobs.json`、不要改策略文件、不要自行创建或删除备份目录。"
    )


def load_jobs(jobs_path: Path) -> dict[str, Any]:
    payload = load_json(jobs_path, None)
    if payload is None:
        return {"version": 1, "jobs": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SnapshotError(f"Invalid jobs file: {jobs_path}")
    return payload


def looks_like_backup_job(job: dict[str, Any], expected_name: str) -> bool:
    payload = job.get("payload") or {}
    message = str(payload.get("message") or "")
    description = str(job.get("description") or "")
    return (
        str(job.get("name") or "") in (LEGACY_BACKUP_JOB_NAMES | {expected_name})
        and (
            DEFAULT_JOB_MARKER in description
            or "openclaw_backup_cycle.py" in message
            or "openclaw_backup_policy.json" in message
            or "OpenClaw 本机+NAS 备份轮转" in description
            or "OpenClaw 本机+NAS 备份" in description
        )
    )


def find_or_create_job(jobs_payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    jobs = jobs_payload["jobs"]
    cron_cfg = settings["cron"]
    saved_job_id = cron_cfg.get("job_id")
    if saved_job_id:
        for job in jobs:
            if job.get("id") == saved_job_id:
                return job
    for job in jobs:
        if looks_like_backup_job(job, cron_cfg["job_name"]):
            cron_cfg["job_id"] = job["id"]
            return job
    job = {
        "id": str(uuid.uuid4()),
        "agentId": "main",
        "name": cron_cfg["job_name"],
        "description": cron_cfg["job_description"],
        "enabled": False,
        "createdAtMs": int(now_local().timestamp() * 1000),
        "updatedAtMs": int(now_local().timestamp() * 1000),
        "schedule": {"kind": "cron", "expr": "30 3 * * 0", "tz": detect_system_timezone()},
        "sessionTarget": cron_cfg.get("session_target", "isolated"),
        "wakeMode": cron_cfg.get("wake_mode", "now"),
        "payload": {"kind": "agentTurn", "message": "", "timeoutSeconds": 14400},
        "delivery": {"mode": "last", "channel": "last"},
        "state": {},
    }
    jobs.append(job)
    cron_cfg["job_id"] = job["id"]
    return job


def delivery_mode_from_alert_delivery(alert_delivery: str) -> str:
    if alert_delivery == "off":
        return "none"
    return "last"


def upsert_cron_job(settings: dict[str, Any], jobs_path: Path, cycle_script: Path, manage_script: Path, policy_path: Path) -> dict[str, Any]:
    jobs_payload = load_jobs(jobs_path)
    job = find_or_create_job(jobs_payload, settings)
    auto = settings["auto"]
    check = auto["check"]
    cron_expr = cron_expr_for_weekly(int(check["weekday"]), str(check["time"]))
    job["enabled"] = bool(auto["enabled"])
    job["updatedAtMs"] = int(now_local().timestamp() * 1000)
    job["name"] = settings["cron"]["job_name"]
    job["description"] = settings["cron"]["job_description"]
    job["schedule"] = {
        "kind": "cron",
        "expr": cron_expr,
        "tz": str(check["timezone"]),
    }
    job["payload"] = {
        "kind": "agentTurn",
        "message": build_cycle_message(cycle_script, manage_script, policy_path),
        "timeoutSeconds": 14400,
    }
    job["delivery"] = {
        "mode": delivery_mode_from_alert_delivery(settings["cron"].get("alert_delivery", "recent-channel")),
        "channel": "last",
    }
    if isinstance(job.get("state"), dict):
        job["state"].pop("nextRunAtMs", None)
    save_json(jobs_path, jobs_payload)
    return {
        "id": job["id"],
        "name": job["name"],
        "enabled": job["enabled"],
        "expr": cron_expr,
        "tz": str(check["timezone"]),
        "delivery_mode": job["delivery"]["mode"],
    }


def find_existing_job(jobs_payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    cron_cfg = settings["cron"]
    saved_job_id = cron_cfg.get("job_id")
    for job in jobs_payload["jobs"]:
        if saved_job_id and job.get("id") == saved_job_id:
            return job
    for job in jobs_payload["jobs"]:
        if looks_like_backup_job(job, cron_cfg["job_name"]):
            return job
    return None


def build_status_payload(settings_path: Path, policy_path: Path, jobs_path: Path) -> dict[str, Any]:
    settings = load_settings(settings_path, policy_path)
    policy = render_policy(settings)
    jobs_payload = load_jobs(jobs_path)
    job_info = find_existing_job(jobs_payload, settings)
    runtime_state = load_json(resolve_path(settings["runtime"]["state_file"]), {}) or {}
    report = load_json(resolve_path(settings["runtime"]["report_file"]), {}) or {}
    alert_state = load_json(resolve_path(settings["runtime"]["alert_state_file"]), {}) or {}
    return {
        "settings_path": str(settings_path),
        "policy_path": str(policy_path),
        "auto_enabled": bool(settings["auto"]["enabled"]),
        "cadence": settings["auto"]["cadence"],
        "check_weekday": int(settings["auto"]["check"]["weekday"]),
        "check_time": str(settings["auto"]["check"]["time"]),
        "timezone": str(settings["auto"]["check"]["timezone"]),
        "backup_interval_days": int(settings["auto"]["backup_interval_days"]),
        "capacity_check_interval_hours": int(settings["auto"]["capacity_check_interval_hours"]),
        "local_enabled": bool(settings["local"]["enabled"]),
        "local_root": settings["local"]["root"],
        "local_keep": int(settings["local"]["keep"]),
        "nas_enabled": bool(settings["nas"]["enabled"]),
        "nas_root": settings["nas"]["root"],
        "warn_backup_root_gib": int(settings["alerts"]["warn_backup_root_gib"]),
        "critical_backup_root_gib": int(settings["alerts"]["critical_backup_root_gib"]),
        "alert_delivery": settings["cron"].get("alert_delivery", "recent-channel"),
        "cron_job": job_info,
        "runtime_state": runtime_state,
        "latest_report": report,
        "alert_state": alert_state,
        "rendered_policy": policy,
    }


def human_status(payload: dict[str, Any]) -> str:
    auto_state = "开启" if payload["auto_enabled"] else "关闭"
    job = payload.get("cron_job") or {}
    last_backup = payload.get("runtime_state", {}).get("last_backup_completed_at") or "-"
    last_capacity = payload.get("runtime_state", {}).get("last_capacity_check_completed_at") or "-"
    local_count = ((payload.get("latest_report", {}) or {}).get("local") or {}).get("snapshot_count", 0)
    nas_count = ((payload.get("latest_report", {}) or {}).get("nas") or {}).get("snapshot_count", 0)
    alert_delivery = payload.get("alert_delivery") or "recent-channel"
    lines = [
        "OpenClaw 备份状态",
        f"- 自动备份：{auto_state}",
        f"- 检查时间：每周 {weekday_label(payload['check_weekday'])} {payload['check_time']} @ {payload['timezone']}",
        f"- 实际备份规则：距离上次成功备份至少 {payload['backup_interval_days']} 天；只有到检查点时才会真正执行",
        f"- 本机快照：{'开启' if payload['local_enabled'] else '关闭'}",
        f"- 本机快照根目录：{payload['local_root']}",
        f"- 本机最多保留：{payload['local_keep']} 份",
        f"- 离机归档：{'开启' if payload['nas_enabled'] else '关闭'}",
        f"- 离机归档目录：{payload['nas_root'] or '-'}",
        f"- 容量提醒阈值：warning {payload['warn_backup_root_gib']} GiB / critical {payload['critical_backup_root_gib']} GiB",
        f"- 提醒回传：{alert_delivery_label(alert_delivery)}",
        f"- 上次成功备份：{last_backup}",
        f"- 上次容量检查：{last_capacity}",
        f"- 当前本机快照数：{local_count}",
        f"- 当前离机归档版本数：{nas_count}",
    ]
    pending_delivery = (payload.get("alert_state", {}) or {}).get("pending_delivery")
    if pending_delivery:
        lines.append("- 当前有一条待发送的提醒")
    if job:
        lines.append(f"- 定时任务：{job.get('name')} ({'开启' if job.get('enabled') else '关闭'})")
    return "\n".join(lines)


def build_setup_plan_payload(settings: dict[str, Any]) -> dict[str, Any]:
    auto = settings["auto"]
    local = settings["local"]
    nas = settings["nas"]
    alerts = settings["alerts"]
    cron = settings["cron"]
    cadence = str(auto["cadence"])
    alert_delivery = cron.get("alert_delivery", "recent-channel")
    return {
        "recommended_values": {
            "auto_enabled": {"value": "on", "label": "开启"},
            "cadence": {"value": cadence, "label": cadence_label(cadence)},
            "check_weekday": int(auto["check"]["weekday"]),
            "check_time": str(auto["check"]["time"]),
            "timezone": str(auto["check"]["timezone"]),
            "local_root": str(local["root"]),
            "local_keep": int(local["keep"]),
            "nas_enabled": {"value": "off", "label": "关闭"},
            "nas_root": "",
            "warn_backup_root_gib": int(alerts["warn_backup_root_gib"]),
            "critical_backup_root_gib": int(alerts["critical_backup_root_gib"]),
            "alert_delivery": {"value": alert_delivery, "label": alert_delivery_label(alert_delivery)},
        },
        "product_logic": {
            "cadence_explanation": (
                "两周一次：每周固定检查，实际至少间隔 14 天。"
            ),
            "local_snapshot_explanation": "本机快照始终是完整视图，未变化文件用硬链接复用，不会重复占空间。",
            "nas_explanation": "离机归档默认面向 NAS，也支持其他稳定可写的 SMB 共享目录。",
            "alert_explanation": "提醒默认回传到最近一次和你交互的渠道，只在失败或容量告警时发送，不会每次定时检查都打扰你。",
        },
        "recommended_questions": [
            {
                "id": "auto_enabled",
                "question": "要不要开启自动备份？",
                "recommended": "开启",
                "recommended_value": "on",
                "why": "推荐开启，否则只能手动跑备份。",
            },
            {
                "id": "cadence",
                "question": "自动备份要按手动、每周，还是两周节奏？",
                "recommended": "两周一次",
                "recommended_value": "two-weeks",
                "why": "推荐“两周一次”：检查时间固定，也保留足够恢复点间隔。",
            },
            {
                "id": "local_root",
                "question": "本机快照放在哪里？",
                "recommended": str(local["root"]),
                "why": "默认路径开箱即用，恢复时也最直接。",
            },
            {
                "id": "local_keep",
                "question": "本机最多保留几份快照？",
                "recommended": str(local["keep"]),
                "why": "默认 3 份，兼顾回滚空间和本机占用。",
            },
            {
                "id": "nas_enabled",
                "question": "要不要开启离机归档？",
                "recommended": "关闭",
                "recommended_value": "off",
                "why": "只有在 SMB 共享路径稳定可写时再开。",
            },
            {
                "id": "warn_backup_root_gib",
                "question": "归档仓库多大时开始 warning 提醒？",
                "recommended": str(alerts["warn_backup_root_gib"]),
                "why": "只看备份仓库自身占用，不扫描整盘。",
            },
            {
                "id": "critical_backup_root_gib",
                "question": "归档仓库多大时进入 critical 提醒？",
                "recommended": str(alerts["critical_backup_root_gib"]),
                "why": "critical 用来提示应该尽快删早期版本。",
            },
            {
                "id": "alert_delivery",
                "question": "出现失败或容量提醒时，要不要自动发回最近一次交互渠道？",
                "recommended": alert_delivery_label(alert_delivery),
                "recommended_value": alert_delivery,
                "why": "推荐“最近一次交互渠道”，这样只有真正出问题时才会主动提醒。",
            },
        ],
    }


def human_setup_plan(payload: dict[str, Any]) -> str:
    recommended = payload["recommended_values"]
    lines = [
        "OpenClaw 备份初始化建议",
        f"- 推荐自动备份：{recommended['auto_enabled']['label']}",
        f"- 推荐节奏：{recommended['cadence']['label']}",
        f"- 推荐检查时间：每周 {weekday_label(recommended['check_weekday'])} {recommended['check_time']} @ {recommended['timezone']}",
        f"- 推荐本机目录：{recommended['local_root']}",
        f"- 推荐本机保留：{recommended['local_keep']} 份",
        f"- 推荐离机归档：{recommended['nas_enabled']['label']}",
        f"- 推荐提醒回传：{recommended['alert_delivery']['label']}",
        "",
        "解释：",
        f"- {payload['product_logic']['cadence_explanation']}",
        f"- {payload['product_logic']['local_snapshot_explanation']}",
        f"- {payload['product_logic']['nas_explanation']}",
        f"- {payload['product_logic']['alert_explanation']}",
    ]
    return "\n".join(lines)


def apply_configure(args: argparse.Namespace, settings_path: Path, policy_path: Path, jobs_path: Path) -> dict[str, Any]:
    settings = load_settings(settings_path, policy_path)

    if args.source:
        settings["source"] = str(resolve_path(args.source))

    auto_enabled = parse_bool_flag(args.auto)
    if auto_enabled is not None:
        settings["auto"]["enabled"] = auto_enabled

    if args.cadence:
        normalized = normalize_cadence(args.cadence)
        settings["auto"]["cadence"] = normalized
        if normalized == "manual":
            settings["auto"]["enabled"] = False
        elif normalized == "weekly":
            settings["auto"]["enabled"] = True
            settings["auto"]["backup_interval_days"] = 7
        elif normalized == "two-weeks":
            settings["auto"]["enabled"] = True
            settings["auto"]["backup_interval_days"] = 14
        elif normalized == "custom":
            settings["auto"]["enabled"] = True

    weekday = parse_weekday(args.weekday)
    if weekday is not None:
        settings["auto"]["check"]["weekday"] = weekday

    time_of_day = parse_time_of_day(args.time)
    if time_of_day is not None:
        settings["auto"]["check"]["time"] = time_of_day

    if args.timezone:
        settings["auto"]["check"]["timezone"] = args.timezone.strip()

    if args.backup_interval_days is not None:
        settings["auto"]["backup_interval_days"] = int(args.backup_interval_days)
        if settings["auto"]["backup_interval_days"] == 7:
            settings["auto"]["cadence"] = "weekly"
        elif settings["auto"]["backup_interval_days"] == 14:
            settings["auto"]["cadence"] = "two-weeks"
        elif settings["auto"]["enabled"]:
            settings["auto"]["cadence"] = "custom"
    if args.capacity_check_hours is not None:
        settings["auto"]["capacity_check_interval_hours"] = int(args.capacity_check_hours)

    local_enabled = parse_bool_flag(args.local)
    if local_enabled is not None:
        settings["local"]["enabled"] = local_enabled
    if args.local_root:
        settings["local"]["root"] = str(resolve_path(args.local_root))
    if args.local_keep is not None:
        settings["local"]["keep"] = int(args.local_keep)
    local_skip_unchanged = parse_bool_flag(args.local_skip_unchanged)
    if local_skip_unchanged is not None:
        settings["local"]["skip_if_unchanged"] = local_skip_unchanged
    if args.local_cleanup_minutes is not None:
        settings["local"]["cleanup_incomplete_after_minutes"] = int(args.local_cleanup_minutes)
    if args.clear_change_excludes:
        settings["local"]["change_detection_excludes"] = []
    if args.change_exclude:
        existing = list(settings["local"].get("change_detection_excludes") or [])
        for item in args.change_exclude:
            if str(item) not in existing:
                existing.append(str(item))
        settings["local"]["change_detection_excludes"] = existing

    nas_enabled = parse_bool_flag(args.nas)
    if nas_enabled is not None:
        settings["nas"]["enabled"] = nas_enabled
    if args.nas_root is not None:
        settings["nas"]["root"] = str(resolve_path(args.nas_root)) if args.nas_root else ""
    if args.nas_staging_root:
        settings["nas"]["staging_root"] = str(resolve_path(args.nas_staging_root))
    nas_skip_unchanged = parse_bool_flag(args.nas_skip_unchanged)
    if nas_skip_unchanged is not None:
        settings["nas"]["skip_if_unchanged"] = nas_skip_unchanged
    if args.nas_cleanup_minutes is not None:
        settings["nas"]["cleanup_incomplete_after_minutes"] = int(args.nas_cleanup_minutes)
    if args.nas_protected_recent is not None:
        settings["nas"]["protected_recent_count"] = int(args.nas_protected_recent)

    if args.warn_gib is not None:
        settings["alerts"]["warn_backup_root_gib"] = int(args.warn_gib)
    if args.critical_gib is not None:
        settings["alerts"]["critical_backup_root_gib"] = int(args.critical_gib)
    if args.cooldown_hours is not None:
        settings["alerts"]["cooldown_hours"] = int(args.cooldown_hours)

    if args.alert_delivery is not None:
        settings["cron"]["alert_delivery"] = normalize_alert_delivery(args.alert_delivery)

    if int(settings["local"]["keep"]) < 1:
        raise SnapshotError("local keep must be at least 1")
    if int(settings["auto"]["backup_interval_days"]) < 1:
        raise SnapshotError("backup interval days must be at least 1")
    if int(settings["auto"]["capacity_check_interval_hours"]) < 1:
        raise SnapshotError("capacity check interval hours must be at least 1")
    if int(settings["local"]["cleanup_incomplete_after_minutes"]) < 1:
        raise SnapshotError("local cleanup minutes must be at least 1")
    if int(settings["nas"]["cleanup_incomplete_after_minutes"]) < 1:
        raise SnapshotError("nas cleanup minutes must be at least 1")
    if int(settings["nas"]["protected_recent_count"]) < 0:
        raise SnapshotError("nas protected recent count must be at least 0")
    if int(settings["alerts"]["cooldown_hours"]) < 0:
        raise SnapshotError("alert cooldown hours must be at least 0")
    if int(settings["alerts"]["warn_backup_root_gib"]) < 1:
        raise SnapshotError("warning threshold must be at least 1 GiB")
    if int(settings["alerts"]["critical_backup_root_gib"]) <= int(settings["alerts"]["warn_backup_root_gib"]):
        raise SnapshotError("critical threshold must be greater than warning threshold")
    if settings["local"]["enabled"] and not str(settings["local"]["root"]).strip():
        raise SnapshotError("local backup root cannot be empty")
    if settings["nas"]["enabled"] and not str(settings["nas"]["root"]).strip():
        raise SnapshotError("NAS backup is enabled, but nas root is empty")

    settings["last_configured_at"] = iso_now()
    policy = render_policy(settings)
    save_json(settings_path, settings)
    save_json(policy_path, policy)

    manage_script = Path(__file__).resolve()
    cycle_script = script_path_from_manage(manage_script, "openclaw_backup_cycle.py")
    job_info = upsert_cron_job(settings, jobs_path, cycle_script, manage_script, policy_path)
    return {
        "settings": settings,
        "policy": policy,
        "job": job_info,
    }


def load_latest_alert_context(settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    report = load_json(resolve_path(settings["runtime"]["report_file"]), {}) or {}
    alert_state = load_json(resolve_path(settings["runtime"]["alert_state_file"]), {}) or {}
    if not isinstance(report, dict):
        report = {}
    if not isinstance(alert_state, dict):
        alert_state = {}
    return report, alert_state


def render_alert_payload(settings: dict[str, Any], *, only_pending: bool) -> dict[str, Any] | None:
    report, alert_state = load_latest_alert_context(settings)
    issues = report.get("issues") or []
    suggestions = report.get("suggestions") or []
    if not issues:
        return None
    fingerprint = alert_fingerprint(issues, suggestions)
    if only_pending and not alert_state.get("pending_delivery"):
        return None
    if alert_state.get("fingerprint") and alert_state.get("fingerprint") != fingerprint:
        return None
    message = format_alert_message(
        report.get("local") or {},
        report.get("nas") or {},
        issues,
        suggestions,
        bool((report.get("results") or {}).get("nas_available")),
    )
    return {
        "fingerprint": fingerprint,
        "pending_delivery": bool(alert_state.get("pending_delivery")),
        "message": message,
        "issues": issues,
        "suggestions": suggestions,
    }


def mark_alert_delivered(settings: dict[str, Any], fingerprint: str) -> None:
    state_file = resolve_path(settings["runtime"]["alert_state_file"])
    state = load_json(state_file, {}) or {}
    if not isinstance(state, dict):
        state = {}
    if state.get("fingerprint") != fingerprint:
        return
    state["pending_delivery"] = False
    state["last_delivered_at"] = iso_now()
    save_json(state_file, state)


def restore_from_nas_archive(
    settings: dict[str, Any],
    *,
    snapshot_id: str | None,
    source_override: str | None,
    force: bool,
    make_guard_snapshot: bool,
) -> dict[str, Any]:
    if not force:
        raise SnapshotError("Restore is destructive. Re-run with --force.")

    source = resolve_path(source_override or settings["source"])
    local_root = resolve_path(settings["local"]["root"])
    nas_root = resolve_path(settings["nas"]["root"])
    versions_root = nas_root / "versions"
    if not versions_root.exists():
        raise SnapshotError(f"No NAS versions found under {versions_root}")

    candidates = []
    for entry in sorted(versions_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        metadata_path = entry / "metadata.json"
        meta = load_json(metadata_path, None)
        if not isinstance(meta, dict):
            continue
        archive_name = meta.get("archive_filename")
        if not archive_name:
            continue
        archive_path = entry / str(archive_name)
        if not archive_path.exists():
            continue
        candidates.append((entry.name, entry, metadata_path, archive_path, meta))
    if not candidates:
        raise SnapshotError(f"No readable NAS versions found under {versions_root}")

    selected = candidates[-1] if snapshot_id is None else next((item for item in candidates if item[0] == snapshot_id), None)
    if selected is None:
        raise SnapshotError(f"NAS archive version not found: {snapshot_id}")
    version_id, _entry, metadata_path, archive_path, meta = selected

    staging_root = resolve_path(settings["nas"]["staging_root"])
    ensure_dir(staging_root)
    extract_parent = Path(tempfile.mkdtemp(prefix=f"restore-{version_id}-", dir=str(staging_root)))

    guard_snapshot_id = None
    try:
        if shutil.which("zstd"):
            tar_result_cmd = ["tar", "--zstd", "-xf", str(archive_path), "-C", str(extract_parent)]
        else:
            tar_result_cmd = ["tar", "-xzf", str(archive_path), "-C", str(extract_parent)]

        unpack = subprocess.run(tar_result_cmd, text=True, capture_output=True, check=False)
        if unpack.returncode != 0:
            raise SnapshotError(
                "NAS archive extract failed.\n"
                f"stdout:\n{unpack.stdout}\n"
                f"stderr:\n{unpack.stderr}"
            )
        extracted_name = Path(str(meta.get("source") or source)).name
        extracted_root = extract_parent / extracted_name
        if not extracted_root.exists():
            children = [child for child in extract_parent.iterdir() if child.exists()]
            if len(children) == 1 and children[0].is_dir():
                extracted_root = children[0]
        if not extracted_root.exists():
            raise SnapshotError(f"Archive did not extract a source directory under {extract_parent}")
        if make_guard_snapshot:
            with lock_backup_root(local_root):
                guard_snapshot_id = create_restore_guard_snapshot(source, local_root).snapshot_id
        restore_result = subprocess.run(
            ["rsync", "-aH", "--delete", str(extracted_root) + "/", str(source)],
            text=True,
            capture_output=True,
            check=False,
        )
        if restore_result.returncode != 0:
            raise SnapshotError(
                "NAS archive restore failed.\n"
                f"stdout:\n{restore_result.stdout}\n"
                f"stderr:\n{restore_result.stderr}"
            )
    finally:
        shutil.rmtree(extract_parent, ignore_errors=True)

    meta["last_restored_at"] = iso_now()
    meta["last_restored_to"] = str(source)
    meta["last_restore_guard_snapshot"] = guard_snapshot_id
    save_json(metadata_path, meta)
    return {
        "snapshot_id": version_id,
        "restored_to": str(source),
        "guard_snapshot_id": guard_snapshot_id,
    }


def delete_nas_versions(settings: dict[str, Any], snapshot_ids: list[str]) -> list[str]:
    nas_root = resolve_path(settings["nas"]["root"])
    versions_root = nas_root / "versions"
    if not versions_root.exists():
        raise SnapshotError(f"No NAS versions found under {versions_root}")
    removed = []
    for snapshot_id in snapshot_ids:
        target = versions_root / snapshot_id
        metadata_path = target / "metadata.json"
        if not target.exists() or not metadata_path.exists():
            raise SnapshotError(f"NAS archive version not found: {snapshot_id}")
        shutil.rmtree(target)
        removed.append(snapshot_id)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw backup skill management CLI")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--jobs", default=str(DEFAULT_JOBS_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="Write user-facing settings and upsert the OpenClaw cron job.")
    configure.add_argument("--source")
    configure.add_argument("--auto", help="on/off")
    configure.add_argument("--cadence", help="manual / weekly / two-weeks / custom")
    configure.add_argument("--weekday", help="0-6 or Sun..Sat")
    configure.add_argument("--time", help="HH:MM")
    configure.add_argument("--timezone")
    configure.add_argument("--backup-interval-days", type=int)
    configure.add_argument("--capacity-check-hours", type=int)
    configure.add_argument("--local", help="on/off")
    configure.add_argument("--local-root")
    configure.add_argument("--local-keep", type=int)
    configure.add_argument("--local-skip-unchanged", help="on/off")
    configure.add_argument("--local-cleanup-minutes", type=int)
    configure.add_argument("--change-exclude", action="append", help="Repeatable path pattern excluded from change detection")
    configure.add_argument("--clear-change-excludes", action="store_true")
    configure.add_argument("--nas", help="on/off")
    configure.add_argument("--nas-root")
    configure.add_argument("--nas-staging-root")
    configure.add_argument("--nas-skip-unchanged", help="on/off")
    configure.add_argument("--nas-cleanup-minutes", type=int)
    configure.add_argument("--nas-protected-recent", type=int)
    configure.add_argument("--warn-gib", type=int)
    configure.add_argument("--critical-gib", type=int)
    configure.add_argument("--cooldown-hours", type=int)
    configure.add_argument("--alert-delivery", help="recent-channel / off")

    setup_plan = subparsers.add_parser("setup-plan", help="Show product-facing setup recommendations.")
    setup_plan.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Show current backup status.")
    status.add_argument("--json", action="store_true")

    subparsers.add_parser("run-now", help="Run one backup cycle immediately, ignoring schedule gating.")

    render_alert = subparsers.add_parser("render-alert", help="Render the latest alert message body.")
    render_alert.add_argument("--json", action="store_true")
    render_alert.add_argument("--only-pending", action="store_true")
    render_alert.add_argument("--consume", action="store_true")

    restore_local = subparsers.add_parser("restore-local", help="Restore one local snapshot into the source tree.")
    restore_local.add_argument("--snapshot", help="Snapshot ID to restore. Defaults to latest.")
    restore_local.add_argument("--force", action="store_true")
    restore_local.add_argument("--no-pre-restore-snapshot", action="store_true")

    restore_nas = subparsers.add_parser("restore-nas", help="Restore one NAS archive version into the source tree.")
    restore_nas.add_argument("--snapshot", help="NAS version ID to restore. Defaults to latest.")
    restore_nas.add_argument("--force", action="store_true")
    restore_nas.add_argument("--no-pre-restore-snapshot", action="store_true")

    list_local = subparsers.add_parser("list-local", help="List local snapshot restore points.")
    list_local.add_argument("--json", action="store_true")

    list_nas = subparsers.add_parser("list-nas", help="List NAS archive restore points.")
    list_nas.add_argument("--json", action="store_true")

    delete_local = subparsers.add_parser("delete-local", help="Delete one or more local snapshots.")
    delete_local.add_argument("--snapshot", action="append", required=True)

    delete_nas = subparsers.add_parser("delete-nas", help="Delete one or more NAS archive versions.")
    delete_nas.add_argument("--snapshot", action="append", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings_path = resolve_path(args.settings)
    policy_path = resolve_path(args.policy)
    jobs_path = resolve_path(args.jobs)

    try:
        if args.command == "configure":
            result = apply_configure(args, settings_path, policy_path, jobs_path)
            settings = result["settings"]
            job = result["job"]
            auto_state = "enabled" if settings["auto"]["enabled"] else "disabled"
            print(
                "BACKUP_CONFIGURED: "
                f"auto={auto_state} "
                f"cadence={cadence_label(settings['auto']['cadence'])} "
                f"check={weekday_name(int(settings['auto']['check']['weekday']))} {settings['auto']['check']['time']} "
                f"tz={settings['auto']['check']['timezone']} "
                f"local_keep={settings['local']['keep']} "
                f"nas={'on' if settings['nas']['enabled'] else 'off'} "
                f"alert_delivery={alert_delivery_label(settings['cron']['alert_delivery'])} "
                f"job={job['id']}"
            )
            return 0

        if args.command == "setup-plan":
            payload = build_setup_plan_payload(load_settings(settings_path, policy_path))
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(human_setup_plan(payload))
            return 0

        if args.command == "status":
            payload = build_status_payload(settings_path, policy_path, jobs_path)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(human_status(payload))
            return 0

        if args.command == "run-now":
            status, report = run_cycle(policy_path, respect_schedule=False)
            print(
                f"{status}: local={report['results'].get('local_snapshot_id', '-')} "
                f"local_pruned={len(report['results'].get('local_removed_ids', []))} "
                f"nas={report['results'].get('nas_snapshot_id', '-')} "
                f"issues={len(report.get('issues', []))}"
            )
            return 0

        settings = load_settings(settings_path, policy_path)

        if args.command == "render-alert":
            payload = render_alert_payload(settings, only_pending=bool(args.only_pending))
            if payload is None:
                if args.json:
                    print(json.dumps({"status": "BACKUP_ALERT_NONE"}, ensure_ascii=False, indent=2))
                else:
                    print("BACKUP_ALERT_NONE")
                return 0
            if args.consume:
                mark_alert_delivered(settings, payload["fingerprint"])
            if args.json:
                print(json.dumps({"status": "BACKUP_ALERT_READY", **payload}, ensure_ascii=False, indent=2))
            else:
                print("BACKUP_ALERT_READY")
                print(payload["message"])
            return 0

        if args.command == "restore-local":
            local_root = resolve_path(settings["local"]["root"])
            snapshot = get_snapshot(local_root, args.snapshot)
            result = restore_snapshot(
                resolve_path(settings["source"]),
                local_root,
                snapshot,
                force=args.force,
                make_guard_snapshot=not args.no_pre_restore_snapshot,
            )
            guard = result["guard_snapshot_id"] or "skipped"
            print(
                f"LOCAL_RESTORE_OK: snapshot={result['snapshot_id']} restored_to={result['restored_to']} guard={guard}"
            )
            return 0

        if args.command == "restore-nas":
            result = restore_from_nas_archive(
                settings,
                snapshot_id=args.snapshot,
                source_override=None,
                force=args.force,
                make_guard_snapshot=not args.no_pre_restore_snapshot,
            )
            guard = result["guard_snapshot_id"] or "skipped"
            print(
                f"NAS_RESTORE_OK: snapshot={result['snapshot_id']} restored_to={result['restored_to']} guard={guard}"
            )
            return 0

        if args.command == "list-local":
            local_root = resolve_path(settings["local"]["root"])
            snapshots = []
            for snapshot in list_snapshots(local_root):
                meta = load_metadata(snapshot)
                snapshots.append(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "created_at": meta.get("created_at"),
                        "size_bytes": meta.get("size_bytes"),
                        "linked_from": meta.get("linked_from"),
                        "path": str(snapshot.path),
                    }
                )
            if args.json:
                print(json.dumps({"snapshots": snapshots}, ensure_ascii=False, indent=2))
            else:
                for item in snapshots:
                    print(f"{item['snapshot_id']}  {item['created_at'] or '-'}  {item['path']}")
            return 0

        if args.command == "list-nas":
            nas_root = resolve_path(settings["nas"]["root"])
            versions_root = nas_root / "versions"
            items: list[dict[str, Any]] = []
            if versions_root.exists():
                for entry in sorted(versions_root.iterdir()):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    meta = load_json(entry / "metadata.json", {})
                    if not isinstance(meta, dict):
                        continue
                    items.append(
                        {
                            "snapshot_id": entry.name,
                            "created_at": meta.get("created_at"),
                            "archive_filename": meta.get("archive_filename"),
                            "archive_size_bytes": meta.get("archive_size_bytes"),
                            "path": str(entry),
                        }
                    )
            if args.json:
                print(json.dumps({"versions": items}, ensure_ascii=False, indent=2))
            else:
                for item in items:
                    print(f"{item['snapshot_id']}  {item['created_at'] or '-'}  {item['path']}")
            return 0

        if args.command == "delete-local":
            local_root = resolve_path(settings["local"]["root"])
            with lock_backup_root(local_root):
                removed = delete_snapshots(local_root, args.snapshot)
            print("LOCAL_DELETE_OK: " + ", ".join(removed))
            return 0

        if args.command == "delete-nas":
            removed = delete_nas_versions(settings, args.snapshot)
            print("NAS_DELETE_OK: " + ", ".join(removed))
            return 0

        raise SnapshotError(f"Unknown command: {args.command}")
    except SnapshotError as exc:
        print(f"BACKUP_MANAGE_FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"BACKUP_MANAGE_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
