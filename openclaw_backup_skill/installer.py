from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import __version__


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_OPENCLAW_ROOT = Path.home() / ".openclaw"
DEFAULT_OPENCLAW_WORKSPACE = DEFAULT_OPENCLAW_ROOT / "workspace"
SKILL_SLUG = "openclaw-backup"
SCRIPT_NAMES = [
    "openclaw_backup_snapshot.py",
    "openclaw_backup_cycle.py",
    "openclaw_backup_manage.py",
]
LEGACY_SCRIPT_NAMES = [
    "openclaw_backup_admin.py",
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def resolve_path(raw: str | None, default: Path) -> Path:
    return Path(raw).expanduser().resolve() if raw else default


def workspace_scripts_dir(workspace: Path) -> Path:
    return workspace / "scripts"


def workspace_skill_dir(workspace: Path) -> Path:
    return workspace / "skills" / SKILL_SLUG


def skill_path(workspace: Path) -> Path:
    return workspace_skill_dir(workspace) / "SKILL.md"


def manage_script_target(workspace: Path) -> Path:
    return workspace_scripts_dir(workspace) / "openclaw_backup_manage.py"


def build_skill_text(manage_script: Path) -> str:
    return f"""---
name: openclaw-backup
description: 管理 OpenClaw 运行目录的本机快照和可选离机归档。用于安装后的首次配置，以及后续任何启用、停用、重配、查看状态、立即备份、恢复、删除请求。
---

# openclaw-backup

这是 OpenClaw 侧的产品化备份 skill。它的职责只有一件事：通过固定脚本管理 OpenClaw 的备份配置、自动备份、恢复点和离机归档。

必须遵守
- 只调用固定脚本：`python3 {manage_script} ...`
- 不要自己手工创建、删除、移动备份目录；所有备份动作都交给脚本
- 离机归档默认面向 NAS 场景，但任何稳定可写的 SMB 共享目录都可以使用
- 涉及恢复、删除这类破坏性操作，必须先确认用户要操作的快照 ID，再执行

## 1. 什么时候要立刻进入配置交互

- 如果当前这轮对话刚完成安装这个 skill，直接进入首次配置交互。
- 如果这个 skill 已经装好，而用户这次是在表达“我要启用备份”“帮我配好备份”“我要开始用备份”“把 SMB 路径改掉”这类真实需求，也直接进入配置交互。

## 2. 首次配置怎么做

先运行：

```bash
python3 {manage_script} setup-plan --json
```

然后根据返回内容与用户确认配置。默认建议是：
- 自动备份：开启
- 节奏：两周一次
- 本机快照：开启，默认目录，最多保留 3 份
- 离机归档：默认关闭，只有在 SMB 共享路径稳定可写时再开
- 提醒回传：最近一次交互渠道

拿到用户答案后，只通过一条 `configure` 命令落盘。例如：

```bash
python3 {manage_script} configure --auto on --cadence two-weeks --weekday 0 --time 03:30 --local-root "$HOME/OpenClawBackups/openclaw-snapshots" --local-keep 3 --nas off --alert-delivery recent-channel
```

如果用户开启离机归档，必须明确给出 `--nas-root`，必要时再给 `--nas-staging-root`。

如果用户额外提出高级需求，也可以继续加这些参数：

- `--backup-interval-days`
- `--capacity-check-hours`
- `--local on/off`
- `--local-skip-unchanged on/off`
- `--local-cleanup-minutes`
- `--change-exclude`
- `--clear-change-excludes`
- `--nas-skip-unchanged on/off`
- `--nas-cleanup-minutes`
- `--nas-protected-recent`
- `--cooldown-hours`

配置完成后再运行：

```bash
python3 {manage_script} status
```

把最终状态简洁告诉用户。

## 3. 日常命令

查看当前状态：

```bash
python3 {manage_script} status
```

立即执行一次备份：

```bash
python3 {manage_script} run-now
```

列出本机快照：

```bash
python3 {manage_script} list-local
```

列出离机归档版本：

```bash
python3 {manage_script} list-nas
```

删除本机快照：

```bash
python3 {manage_script} delete-local --snapshot "<snapshot_id>"
```

删除离机归档版本：

```bash
python3 {manage_script} delete-nas --snapshot "<snapshot_id>"
```

恢复本机快照：

```bash
python3 {manage_script} restore-local --snapshot "<snapshot_id>" --force
```

恢复离机归档：

```bash
python3 {manage_script} restore-nas --snapshot "<snapshot_id>" --force
```

## 4. 自动提醒

自动备份的定时任务会静默运行。

- 正常 `BACKUP_OK` / `BACKUP_SKIPPED`：不要主动发消息打扰用户
- 失败：把失败那一行返回给用户
- 容量或离机归档可用性告警：由定时任务内部再调用 `render-alert`，只在确实需要提醒时才把正文发回最近一次交互渠道

## 5. 重新配置

当用户后续说“改成本机保留 5 份”“把离机归档打开并改路径”“改成每周一次”之类时，直接重跑 `configure`，把用户明确要求的参数覆盖进去，再用 `status` 回显。
"""


def install_openclaw(workspace: Path) -> dict[str, Any]:
    scripts_target = workspace_scripts_dir(workspace)
    ensure_dir(scripts_target)
    copied_scripts: list[str] = []
    removed_legacy_scripts: list[str] = []
    for name in SCRIPT_NAMES:
        src = SCRIPTS_DIR / name
        dst = scripts_target / name
        shutil.copy2(src, dst)
        copied_scripts.append(str(dst))
    for name in LEGACY_SCRIPT_NAMES:
        legacy = scripts_target / name
        if legacy.exists():
            legacy.unlink()
            removed_legacy_scripts.append(str(legacy))

    skill_target = skill_path(workspace)
    write_text(skill_target, build_skill_text(manage_script_target(workspace)))
    return {
        "version": __version__,
        "workspace": str(workspace),
        "skill_path": str(skill_target),
        "scripts": copied_scripts,
        "removed_legacy_scripts": removed_legacy_scripts,
        "next_steps": [
            "如果这次安装是在 OpenClaw 对话里发起的：立即继续 setup-plan 并进入配置交互",
            "如果这次安装是在终端里完成的：以后用户第一次表达启用、配置、开始使用备份时，再自动进入同一套配置交互",
            "配置完成后让 OpenClaw 调用 status 回显结果",
        ],
    }


def install_status(workspace: Path) -> dict[str, Any]:
    scripts = {name: str((workspace_scripts_dir(workspace) / name)) for name in SCRIPT_NAMES}
    return {
        "version": __version__,
        "workspace": str(workspace),
        "skill_installed": skill_path(workspace).exists(),
        "skill_path": str(skill_path(workspace)),
        "scripts": {name: Path(path).exists() for name, path in scripts.items()},
        "legacy_scripts": {name: (workspace_scripts_dir(workspace) / name).exists() for name in LEGACY_SCRIPT_NAMES},
    }


def pretty_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
