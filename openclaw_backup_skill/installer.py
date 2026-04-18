from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import __version__


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SKILL_SOURCE = REPO_ROOT / "SKILL.md"
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


def workspace_skill_scripts_dir(workspace: Path) -> Path:
    return workspace_skill_dir(workspace) / "scripts"


def skill_path(workspace: Path) -> Path:
    return workspace_skill_dir(workspace) / "SKILL.md"


def manage_script_target(workspace: Path) -> Path:
    return workspace_skill_scripts_dir(workspace) / "openclaw_backup_manage.py"


def install_openclaw(workspace: Path) -> dict[str, Any]:
    skill_target_dir = workspace_skill_dir(workspace)
    skill_scripts_target = workspace_skill_scripts_dir(workspace)
    legacy_scripts_target = workspace_scripts_dir(workspace)
    ensure_dir(skill_scripts_target)
    ensure_dir(legacy_scripts_target)
    copied_skill_scripts: list[str] = []
    copied_compat_scripts: list[str] = []
    removed_legacy_scripts: list[str] = []
    for name in SCRIPT_NAMES:
        src = SCRIPTS_DIR / name
        skill_dst = skill_scripts_target / name
        legacy_dst = legacy_scripts_target / name
        shutil.copy2(src, skill_dst)
        shutil.copy2(src, legacy_dst)
        copied_skill_scripts.append(str(skill_dst))
        copied_compat_scripts.append(str(legacy_dst))
    shutil.copy2(SKILL_SOURCE, skill_path(workspace))
    for name in LEGACY_SCRIPT_NAMES:
        legacy_workspace = legacy_scripts_target / name
        if legacy_workspace.exists():
            legacy_workspace.unlink()
            removed_legacy_scripts.append(str(legacy_workspace))
        legacy_skill = skill_scripts_target / name
        if legacy_skill.exists():
            legacy_skill.unlink()
            removed_legacy_scripts.append(str(legacy_skill))
    return {
        "version": __version__,
        "workspace": str(workspace),
        "skill_dir": str(skill_target_dir),
        "skill_path": str(skill_path(workspace)),
        "skill_scripts": copied_skill_scripts,
        "compat_scripts": copied_compat_scripts,
        "removed_legacy_scripts": removed_legacy_scripts,
        "next_steps": [
            "如果这次安装是在 OpenClaw 对话里发起的：立即继续 setup-plan 并进入配置交互",
            "如果这次安装是在终端里完成的：以后用户第一次表达启用、配置、开始使用备份时，再自动进入同一套配置交互",
            "配置完成后让 OpenClaw 调用 status 回显结果",
        ],
    }


def install_status(workspace: Path) -> dict[str, Any]:
    skill_scripts = {name: (workspace_skill_scripts_dir(workspace) / name).exists() for name in SCRIPT_NAMES}
    compat_scripts = {name: (workspace_scripts_dir(workspace) / name).exists() for name in SCRIPT_NAMES}
    return {
        "version": __version__,
        "workspace": str(workspace),
        "skill_installed": skill_path(workspace).exists() and all(skill_scripts.values()),
        "skill_path": str(skill_path(workspace)),
        "skill_scripts": skill_scripts,
        "compat_scripts": compat_scripts,
        "legacy_scripts": {
            name: (workspace_scripts_dir(workspace) / name).exists()
            or (workspace_skill_scripts_dir(workspace) / name).exists()
            for name in LEGACY_SCRIPT_NAMES
        },
    }


def pretty_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
