#!/usr/bin/env python3
"""Create and restore filesystem snapshots for an OpenClaw runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


def detect_installed_workspace() -> Path | None:
    for candidate in Path(__file__).resolve().parents:
        if candidate.name == "workspace" and (candidate / "skills").exists():
            return candidate
    return None


DEFAULT_WORKSPACE = detect_installed_workspace()
DEFAULT_SOURCE = DEFAULT_WORKSPACE.parent if DEFAULT_WORKSPACE is not None else (Path.home() / ".openclaw")
DEFAULT_DEST = Path.home() / "OpenClawBackups" / "openclaw-snapshots"
SNAPSHOT_TIME_FMT = "%Y%m%d-%H%M%S"


class SnapshotError(RuntimeError):
    """Controlled error for user-facing failures."""


@dataclass
class Snapshot:
    snapshot_id: str
    path: Path
    data_path: Path
    metadata_path: Path


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def now_local() -> datetime:
    return datetime.now().astimezone()


def human_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{num_bytes}B"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_path(path_str: str | Path) -> Path:
    return Path(path_str).expanduser().resolve()


def snapshots_dir(dest: Path) -> Path:
    return dest / "snapshots"


def latest_file(dest: Path) -> Path:
    return dest / "LATEST"


def read_latest_snapshot_id(dest: Path) -> str | None:
    latest = latest_file(dest)
    if not latest.exists():
        return None
    content = latest.read_text(encoding="utf-8").strip()
    return content or None


def write_latest_snapshot_id(dest: Path, snapshot_id: str) -> None:
    latest = latest_file(dest)
    tmp = latest.with_name(f"{latest.name}.tmp")
    tmp.write_text(snapshot_id + "\n", encoding="utf-8")
    os.replace(tmp, latest)


def load_metadata(snapshot: Snapshot) -> dict:
    try:
        return json.loads(snapshot.metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def list_snapshots(dest: Path) -> list[Snapshot]:
    base = snapshots_dir(dest)
    if not base.exists():
        return []
    snapshots: list[Snapshot] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        data_path = entry / "data"
        metadata_path = entry / "metadata.json"
        if data_path.exists() and metadata_path.exists():
            snapshots.append(
                Snapshot(
                    snapshot_id=entry.name,
                    path=entry,
                    data_path=data_path,
                    metadata_path=metadata_path,
                )
            )
    return snapshots


def latest_snapshot(dest: Path) -> Snapshot | None:
    snapshots = list_snapshots(dest)
    if not snapshots:
        return None
    latest_id = read_latest_snapshot_id(dest)
    if latest_id:
        for snapshot in snapshots:
            if snapshot.snapshot_id == latest_id:
                return snapshot
    return snapshots[-1]


def get_snapshot(dest: Path, snapshot_id: str | None) -> Snapshot:
    snapshots = list_snapshots(dest)
    if not snapshots:
        raise SnapshotError(f"No snapshots found under {dest}")
    if snapshot_id is None:
        latest_id = read_latest_snapshot_id(dest)
        if latest_id:
            for snapshot in snapshots:
                if snapshot.snapshot_id == latest_id:
                    return snapshot
        return snapshots[-1]
    for snapshot in snapshots:
        if snapshot.snapshot_id == snapshot_id:
            return snapshot
    raise SnapshotError(f"Snapshot not found: {snapshot_id}")


def path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_layout(source: Path, dest: Path) -> None:
    if not source.exists():
        raise SnapshotError(f"Source does not exist: {source}")
    if not source.is_dir():
        raise SnapshotError(f"Source is not a directory: {source}")
    if path_contains(source, dest):
        raise SnapshotError(
            "Backup destination cannot live inside the source directory. "
            "Choose a path outside ~/.openclaw."
        )


@contextmanager
def lock_backup_root(dest: Path) -> Iterable[None]:
    ensure_dir(dest)
    lock_dir = dest / ".lock"
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        raise SnapshotError(
            f"Backup root is locked: {dest}. Another snapshot or restore may be running."
        ) from exc
    try:
        (lock_dir / "owner.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "started_at": now_local().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        yield
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=check,
        encoding="utf-8",
        errors="replace",
    )


def rsync_base_args() -> list[str]:
    return ["rsync", "-aH"]


def normalize_excludes(exclude_patterns: Iterable[str] | None) -> list[str]:
    return [str(item).strip() for item in (exclude_patterns or []) if str(item).strip()]


def tree_has_changes(source: Path, target: Path, *, exclude_patterns: Iterable[str] | None = None) -> bool:
    cmd = rsync_base_args() + ["-n", "--delete", "--itemize-changes"]
    for pattern in normalize_excludes(exclude_patterns):
        cmd.append(f"--exclude={pattern}")
    cmd.extend([str(source) + "/", str(target) + "/"])
    result = run(
        cmd,
        check=False,
    )
    if result.returncode != 0:
        raise SnapshotError(
            "rsync comparison failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
    )
    return any(line.strip() for line in result.stdout.splitlines())


def latest_matching_snapshot(
    source: Path,
    dest: Path,
    *,
    exclude_patterns: Iterable[str] | None = None,
) -> Snapshot | None:
    previous = latest_snapshot(dest)
    if previous is None:
        return None
    if tree_has_changes(source, previous.data_path, exclude_patterns=exclude_patterns):
        return None
    return previous


def compute_size_bytes(path: Path) -> int | None:
    try:
        result = run(["du", "-sk", str(path)])
        kib = int(result.stdout.split()[0])
        return kib * 1024
    except Exception:
        return None


def write_metadata(snapshot: Snapshot, payload: dict) -> None:
    snapshot.metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_snapshot_id(dest: Path) -> str:
    base_id = now_local().strftime(SNAPSHOT_TIME_FMT)
    candidate = base_id
    suffix = 1
    while (snapshots_dir(dest) / candidate).exists():
        candidate = f"{base_id}-{suffix:02d}"
        suffix += 1
    return candidate


def attempt_rsync_create(
    source: Path,
    target_data: Path,
    previous: Snapshot | None,
    use_link_dest: bool,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    cmd = rsync_base_args()
    linked = False
    if use_link_dest and previous is not None:
        cmd.append(f"--link-dest={previous.data_path}")
        linked = True
    cmd.extend([str(source) + "/", str(target_data)])
    result = run(cmd, check=False)
    return result, linked


def create_snapshot(
    source: Path,
    dest: Path,
    *,
    note: str | None,
    allow_fallback_copy: bool,
    measure_size: bool = True,
    size_bytes_override: int | None = None,
) -> Snapshot:
    validate_layout(source, dest)
    ensure_dir(snapshots_dir(dest))
    snapshot_id = make_snapshot_id(dest)
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        path=snapshots_dir(dest) / snapshot_id,
        data_path=snapshots_dir(dest) / snapshot_id / "data",
        metadata_path=snapshots_dir(dest) / snapshot_id / "metadata.json",
    )
    if snapshot.path.exists():
        raise SnapshotError(f"Snapshot already exists: {snapshot.path}")
    previous = None
    previous_id = read_latest_snapshot_id(dest)
    if previous_id:
        try:
            previous = get_snapshot(dest, previous_id)
        except SnapshotError:
            previous = list_snapshots(dest)[-1] if list_snapshots(dest) else None

    ensure_dir(snapshot.data_path)
    started_at = now_local().isoformat()
    result, linked = attempt_rsync_create(source, snapshot.data_path, previous, True)
    if result.returncode != 0 and linked and allow_fallback_copy:
        shutil.rmtree(snapshot.path, ignore_errors=True)
        ensure_dir(snapshot.data_path)
        result, linked = attempt_rsync_create(source, snapshot.data_path, previous, False)
    if result.returncode != 0:
        shutil.rmtree(snapshot.path, ignore_errors=True)
        raise SnapshotError(
            "rsync snapshot creation failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    finished_at = now_local().isoformat()
    if size_bytes_override is not None:
        size_bytes = size_bytes_override
    elif measure_size:
        size_bytes = compute_size_bytes(snapshot.data_path)
    else:
        size_bytes = None
    write_metadata(
        snapshot,
        {
            "snapshot_id": snapshot.snapshot_id,
            "source": str(source),
            "dest_root": str(dest),
            "created_at": started_at,
            "completed_at": finished_at,
            "hostname": socket.gethostname(),
            "linked_from": previous.snapshot_id if linked and previous else None,
            "size_bytes": size_bytes,
            "note": note,
            "tool": "openclaw_backup_snapshot.py",
            "restore_of": None,
        },
    )
    write_latest_snapshot_id(dest, snapshot.snapshot_id)
    return snapshot


def create_restore_guard_snapshot(source: Path, dest: Path) -> Snapshot:
    note = "Automatic pre-restore safety snapshot"
    return create_snapshot(source, dest, note=note, allow_fallback_copy=True)


def restore_snapshot(
    source: Path,
    dest: Path,
    snapshot: Snapshot,
    *,
    force: bool,
    make_guard_snapshot: bool,
) -> dict:
    validate_layout(source, dest)
    if not force:
        raise SnapshotError("Restore is destructive. Re-run with --force.")
    guard_snapshot_id = None
    if make_guard_snapshot:
        guard_snapshot_id = create_restore_guard_snapshot(source, dest).snapshot_id

    cmd = rsync_base_args() + ["--delete", str(snapshot.data_path) + "/", str(source)]
    result = run(cmd, check=False)
    if result.returncode != 0:
        raise SnapshotError(
            "rsync restore failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    metadata = load_metadata(snapshot)
    metadata["last_restored_at"] = now_local().isoformat()
    metadata["last_restored_to"] = str(source)
    metadata["last_restore_guard_snapshot"] = guard_snapshot_id
    write_metadata(snapshot, metadata)
    return {
        "snapshot_id": snapshot.snapshot_id,
        "restored_to": str(source),
        "guard_snapshot_id": guard_snapshot_id,
    }


def prune_snapshots(dest: Path, keep: int) -> list[str]:
    if keep < 1:
        raise SnapshotError("--keep must be at least 1")
    snapshots = list_snapshots(dest)
    if len(snapshots) <= keep:
        return []
    doomed = snapshots[:-keep]
    removed_ids: list[str] = []
    for snapshot in doomed:
        shutil.rmtree(snapshot.path)
        removed_ids.append(snapshot.snapshot_id)
    remaining = list_snapshots(dest)
    if remaining:
        write_latest_snapshot_id(dest, remaining[-1].snapshot_id)
    return removed_ids


def delete_snapshots(dest: Path, snapshot_ids: list[str]) -> list[str]:
    if not snapshot_ids:
        raise SnapshotError("At least one snapshot ID is required.")
    snapshots = {snapshot.snapshot_id: snapshot for snapshot in list_snapshots(dest)}
    missing = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in snapshots]
    if missing:
        raise SnapshotError(f"Snapshot not found: {', '.join(missing)}")
    removed_ids: list[str] = []
    for snapshot_id in snapshot_ids:
        shutil.rmtree(snapshots[snapshot_id].path)
        removed_ids.append(snapshot_id)
    remaining = list_snapshots(dest)
    if remaining:
        write_latest_snapshot_id(dest, remaining[-1].snapshot_id)
    else:
        latest = latest_file(dest)
        if latest.exists():
            latest.unlink()
    return removed_ids


def cleanup_incomplete_snapshots(dest: Path, older_than_minutes: int = 5) -> list[str]:
    base = snapshots_dir(dest)
    if not base.exists():
        return []
    cutoff = now_local().timestamp() - older_than_minutes * 60
    removed_ids: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        metadata_path = entry / "metadata.json"
        if metadata_path.exists():
            continue
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime > cutoff:
            continue
        shutil.rmtree(entry)
        removed_ids.append(entry.name)
    remaining = list_snapshots(dest)
    if remaining:
        write_latest_snapshot_id(dest, remaining[-1].snapshot_id)
    return removed_ids


def print_snapshot_table(dest: Path) -> None:
    snapshots = list_snapshots(dest)
    latest_id = read_latest_snapshot_id(dest)
    if not snapshots:
        print(f"No snapshots under {dest}")
        return
    print(f"Backup root: {dest}")
    print("ID                 Latest  Size     Linked From         Note")
    print("-----------------  ------  -------  ------------------  ------------------------------")
    for snapshot in snapshots:
        meta = load_metadata(snapshot)
        latest_marker = "yes" if snapshot.snapshot_id == latest_id else ""
        size = human_size(meta.get("size_bytes"))
        linked_from = meta.get("linked_from") or "-"
        note = (meta.get("note") or "").strip()
        if len(note) > 30:
            note = note[:27] + "..."
        print(
            f"{snapshot.snapshot_id:<17}  {latest_marker:<6}  {size:<7}  "
            f"{linked_from:<18}  {note}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot backup helper for an OpenClaw runtime directory.",
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help=f"OpenClaw runtime directory to snapshot. Default: {DEFAULT_SOURCE}",
    )
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help=(
            "Backup root directory. Use a local path or a mounted NAS path. "
            f"Default: {DEFAULT_DEST}"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new snapshot.")
    create_parser.add_argument("--note", help="Optional note stored in snapshot metadata.")
    create_parser.add_argument(
        "--no-copy-fallback",
        action="store_true",
        help="Fail instead of retrying without hard-link dedupe.",
    )

    subparsers.add_parser("list", help="List snapshots.")

    prune_parser = subparsers.add_parser("prune", help="Keep only the newest N snapshots.")
    prune_parser.add_argument("--keep", type=int, required=True, help="Number of newest snapshots to keep.")

    delete_parser = subparsers.add_parser("delete", help="Delete one or more snapshots by ID.")
    delete_parser.add_argument(
        "--snapshot",
        action="append",
        required=True,
        help="Snapshot ID to delete. Repeat this flag to delete multiple snapshots.",
    )

    restore_parser = subparsers.add_parser("restore", help="Restore a snapshot into --source.")
    restore_parser.add_argument("--snapshot", help="Snapshot ID to restore. Defaults to latest.")
    restore_parser.add_argument(
        "--force",
        action="store_true",
        help="Required confirmation flag because restore overwrites the source tree.",
    )
    restore_parser.add_argument(
        "--no-pre-restore-snapshot",
        action="store_true",
        help="Skip creating an automatic safety snapshot before restore.",
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup-incomplete",
        help="Remove stale snapshot directories that do not have metadata.json.",
    )
    cleanup_parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=5,
        help="Only remove incomplete snapshot directories older than this many minutes. Default: 5",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    source = resolve_path(args.source)
    dest = resolve_path(args.dest)

    try:
        with lock_backup_root(dest):
            if args.command == "create":
                snapshot = create_snapshot(
                    source,
                    dest,
                    note=args.note,
                    allow_fallback_copy=not args.no_copy_fallback,
                )
                meta = load_metadata(snapshot)
                print(f"Created snapshot: {snapshot.snapshot_id}")
                print(f"Source: {source}")
                print(f"Backup root: {dest}")
                print(f"Snapshot path: {snapshot.path}")
                print(f"Size: {human_size(meta.get('size_bytes'))}")
                if meta.get("linked_from"):
                    print(f"Linked from: {meta['linked_from']}")
                else:
                    print("Linked from: -")
                return 0

            if args.command == "list":
                print_snapshot_table(dest)
                return 0

            if args.command == "prune":
                removed = prune_snapshots(dest, args.keep)
                if removed:
                    print("Removed snapshots:")
                    for snapshot_id in removed:
                        print(snapshot_id)
                else:
                    print("Nothing pruned.")
                return 0

            if args.command == "delete":
                removed = delete_snapshots(dest, args.snapshot)
                print("Removed snapshots:")
                for snapshot_id in removed:
                    print(snapshot_id)
                return 0

            if args.command == "cleanup-incomplete":
                removed = cleanup_incomplete_snapshots(dest, args.older_than_minutes)
                if removed:
                    print("Removed incomplete snapshots:")
                    for snapshot_id in removed:
                        print(snapshot_id)
                else:
                    print("Nothing to clean.")
                return 0

            if args.command == "restore":
                snapshot = get_snapshot(dest, args.snapshot)
                result = restore_snapshot(
                    source,
                    dest,
                    snapshot,
                    force=args.force,
                    make_guard_snapshot=not args.no_pre_restore_snapshot,
                )
                print(f"Restored snapshot: {result['snapshot_id']}")
                print(f"Destination: {result['restored_to']}")
                if result["guard_snapshot_id"]:
                    print(f"Safety snapshot: {result['guard_snapshot_id']}")
                return 0
    except SnapshotError as exc:
        eprint(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        eprint("Interrupted.")
        return 130

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
