<div align="center">

# openclaw-backup-skill

An OpenClaw backup skill for local deduplicated snapshots, offsite archives, scheduled rotation, and restore workflows.

[简体中文](./README.md) | [English](./README.en.md)

</div>

## Introduction

`openclaw-backup-skill` is a backup skill for OpenClaw.

It packages local snapshots, offsite archives, scheduled rotation, restore flows, and old-version cleanup into a single toolset, so users can get reliable restore points instead of stitching together temporary scripts and manual operations.

## Features

- Local deduplicated snapshots
  - Every snapshot is a full view
  - Unchanged files are reused through hard links
- Offsite archives
  - Package locally first, then upload to the target directory
  - Built for NAS-style workflows, but also works with any stable writable SMB share
- Scheduled backups
  - Executed by OpenClaw on schedule
  - Supports manual, weekly, biweekly, and custom intervals
- Capacity alerts
  - Only track the backup repository itself
- Restore and cleanup
  - Restore local snapshots
  - Restore offsite archives
  - Delete old snapshots and archive versions

## Installation

### Install from the command line

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git
cd openclaw-backup-skill
python3 install.py install-openclaw
```

If your OpenClaw workspace is not in the default location:

```bash
python3 install.py install-openclaw --workspace /path/to/.openclaw/workspace
```

### Ask OpenClaw to install it

Send this message directly to OpenClaw:

```text
Please install openclaw-backup-skill for me.

Repository:
https://github.com/Liu-Bot24/openclaw-backup-skill.git

Please finish the installation and go straight into the initial configuration.
If it fails, tell me why. If it succeeds, tell me the final configuration.
```

## Initial Configuration

After installation, OpenClaw should immediately start the setup flow.

The initial setup usually confirms the following:

| Setting | What it controls | Initial value | Recommended value |
| --- | --- | --- | --- |
| Automatic backups | Whether scheduled rotation is enabled | Off | On |
| Backup cadence | Manual, weekly, biweekly, or custom | Biweekly | Biweekly |
| Check day | Which day to run the scheduled check | Sunday | Sunday |
| Check time | What time to run the scheduled check | `03:30` | `03:30` |
| Time zone | Which time zone the schedule uses | System time zone | System time zone |
| Local snapshots | Whether local snapshots are enabled | On | On |
| Local snapshot directory | Where local snapshots are stored | `~/OpenClawBackups/openclaw-snapshots` | Same as default |
| Local snapshot retention | How many local snapshots to keep | `3` | `3` |
| Offsite archives | Whether offsite archiving is enabled | Off | Off |
| Archive directory | Where offsite archives are stored | Empty | Set after providing a stable writable SMB path |
| Warning threshold | Repository size for warning alerts | `120 GiB` | `120 GiB` |
| Critical threshold | Repository size for critical alerts | `180 GiB` | `180 GiB` |
| Alert delivery | Whether failures or alerts are sent back to the latest interaction channel | Latest interaction channel | Latest interaction channel |

Notes:

- “Offsite archives” often means NAS, but the target does not have to be a NAS device.
- Any stable writable SMB share can be used as the archive destination.

## Advanced Settings

These settings are also configurable beyond the initial setup:

| Setting | What it controls | Default value | CLI argument |
| --- | --- | --- | --- |
| Backup source directory | Which OpenClaw root gets backed up | Parent of the current workspace | `--source` |
| Minimum backup interval | Minimum number of days before a real backup is allowed | `14` days | `--backup-interval-days` |
| Capacity check interval | How often the repository size is checked | `168` hours | `--capacity-check-hours` |
| Skip unchanged local snapshots | Skip creating a new local snapshot if nothing meaningful changed | On | `--local-skip-unchanged on/off` |
| Local incomplete cleanup threshold | How long to keep incomplete local snapshot directories | `5` minutes | `--local-cleanup-minutes` |
| Change-detection exclusions | Paths excluded from “has anything changed?” detection | See the default list below | `--change-exclude` / `--clear-change-excludes` |
| Offsite staging directory | Local staging directory before upload | `~/OpenClawBackups/nas-staging` | `--nas-staging-root` |
| Skip unchanged offsite archives | Skip creating a new offsite archive if nothing meaningful changed | On | `--nas-skip-unchanged on/off` |
| Offsite incomplete cleanup threshold | How long to keep incomplete offsite archive directories | `30` minutes | `--nas-cleanup-minutes` |
| Protected recent versions | Minimum number of recent offsite versions kept during cleanup | `30` | `--nas-protected-recent` |
| Alert cooldown | Minimum interval before repeating the same alert | `72` hours | `--cooldown-hours` |

Default change-detection exclusions:

- `logs/**`
- `agents/*/sessions/**`
- `openclaw-weixin/accounts/*.sync.json`
- `workspace/memory/openclaw_backup/latest_report.json`
- `workspace/memory/openclaw_backup/runtime_state.json`
- `workspace/memory/openclaw_backup/alert_state.json`

These rules only affect whether a new backup should be created. They do not change what files are included when a backup actually runs.

## Common Commands

Check current status:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py status
```

Run one backup immediately:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py run-now
```

List local snapshots:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py list-local
```

List offsite archive versions:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py list-nas
```

Restore a local snapshot:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py restore-local --snapshot <snapshot_id> --force
```

Restore an offsite archive:

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py restore-nas --snapshot <snapshot_id> --force
```

## Repository Layout

```text
openclaw-backup-skill/
├── README.md
├── README.en.md
├── install.py
├── docs/
│   ├── OPENCLAW_INSTALL_PROMPT.md
│   ├── OPENCLAW_OPERATOR_GUIDE.md
│   ├── PRODUCT_LOGIC.md
│   └── REPOSITORY_METADATA.md
├── openclaw_backup_skill/
│   ├── __init__.py
│   └── installer.py
└── scripts/
    ├── openclaw_backup_manage.py
    ├── openclaw_backup_cycle.py
    └── openclaw_backup_snapshot.py
```

- [install.py](install.py)
  Installation entry point for copying the scripts and skill into an OpenClaw workspace
- [openclaw_backup_manage.py](scripts/openclaw_backup_manage.py)
  Product-facing entry point for configuration, status, immediate backups, restores, deletion, and alert rendering
- [openclaw_backup_cycle.py](scripts/openclaw_backup_cycle.py)
  Single backup-cycle runner
- [openclaw_backup_snapshot.py](scripts/openclaw_backup_snapshot.py)
  Local snapshot creation, deletion, and restore logic
- [OPENCLAW_INSTALL_PROMPT.md](docs/OPENCLAW_INSTALL_PROMPT.md)
  User-facing install request text to send to OpenClaw
- [OPENCLAW_OPERATOR_GUIDE.md](docs/OPENCLAW_OPERATOR_GUIDE.md)
  Operator reference for OpenClaw-style installation agents
- [REPOSITORY_METADATA.md](docs/REPOSITORY_METADATA.md)
  Bilingual repository descriptions and release metadata suggestions
