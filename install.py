#!/usr/bin/env python3
from __future__ import annotations

import argparse

from openclaw_backup_skill.installer import (
    DEFAULT_OPENCLAW_WORKSPACE,
    install_openclaw,
    install_status,
    pretty_json,
    resolve_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Installer for the openclaw-backup skill")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install-openclaw", help="Install the skill into an OpenClaw workspace")
    install.add_argument("--workspace", help="OpenClaw workspace path. Defaults to ~/.openclaw/workspace")

    status = subparsers.add_parser("status", help="Show whether the skill is installed in an OpenClaw workspace")
    status.add_argument("--workspace", help="OpenClaw workspace path. Defaults to ~/.openclaw/workspace")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    workspace = resolve_path(getattr(args, "workspace", None), DEFAULT_OPENCLAW_WORKSPACE)

    if args.command == "install-openclaw":
        print(pretty_json(install_openclaw(workspace)))
        return 0

    if args.command == "status":
        print(pretty_json(install_status(workspace)))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
