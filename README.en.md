# openclaw-backup-skill

![Stars](https://img.shields.io/github/stars/Liu-Bot24/openclaw-backup-skill?style=flat&label=Stars&cache=20260704) ![Forks](https://img.shields.io/github/forks/Liu-Bot24/openclaw-backup-skill?style=flat&label=Forks&cache=20260704) ![Views 14d](https://github-stats.liu-qi.cn/api/badge/Liu-Bot24/openclaw-backup-skill/views14d.svg?v=4) ![Clones 14d](https://github-stats.liu-qi.cn/api/badge/Liu-Bot24/openclaw-backup-skill/clones14d.svg?v=4)

An OpenClaw backup skill for local deduplicated snapshots, offsite archives, scheduled rotation, and restore workflows.

[简体中文](./README.md) | [English](./README.en.md)

`openclaw-backup-skill` gives OpenClaw one consistent way to back up its runtime directory, keep restore points locally, archive full versions offsite, and clean up old versions without relying on ad hoc scripts.

## What It Does

- Creates local deduplicated snapshots for fast restores
- Runs scheduled backup checks and rotation inside OpenClaw
- Uploads full archive versions to a NAS or any stable writable SMB share
- Sends alerts only for failures or real capacity issues
- Restores local snapshots or offsite archives and deletes old versions

## Installation

Recommended: install it as a real OpenClaw skill by placing the repository in your workspace skill directory:

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git ~/.openclaw/workspace/skills/openclaw-backup
```

If your workspace is elsewhere, or you also want compatibility copies under `workspace/scripts/`:

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git
cd openclaw-backup-skill
python3 install.py install-openclaw --workspace /path/to/.openclaw/workspace
```

You can also ask OpenClaw to install it for you. A ready-to-send prompt is available in [docs/OPENCLAW_INSTALL_PROMPT.md](./docs/OPENCLAW_INSTALL_PROMPT.md).

## Initial Setup

After installation, OpenClaw should go straight into setup. The first run usually confirms:

| Setting | What it controls | Default | Recommended |
| --- | --- | --- | --- |
| Automatic backups | Whether scheduled rotation is enabled | Off | On |
| Backup cadence | Manual, weekly, biweekly, or custom | Biweekly | Biweekly |
| Check day | Which day runs the scheduled check | Sunday | Sunday |
| Check time | What time runs the scheduled check | `03:30` | `03:30` |
| Time zone | Which time zone the schedule uses | System time zone | System time zone |
| Local snapshots | Whether local snapshots are enabled | On | On |
| Local snapshot directory | Where local snapshots are stored | `~/OpenClawBackups/openclaw-snapshots` | Same as default |
| Local retention | How many local snapshots to keep | `3` | `3` |
| Offsite archives | Whether offsite archiving is enabled | Off | Off |
| Archive directory | Where offsite archives are stored | Empty | Set it after providing a stable writable SMB path |
| Warning threshold | Repository size that triggers warning alerts | `120 GiB` | `120 GiB` |
| Critical threshold | Repository size that triggers critical alerts | `180 GiB` | `180 GiB` |
| Alert delivery | Whether alerts go back to the latest interaction channel | Latest interaction channel | Latest interaction channel |

Offsite archives are often described as NAS backups, but the destination does not need to be a NAS device. Any stable writable SMB share works.

The local snapshot directory, offsite archive directory, and offsite staging directory must stay outside the backup source tree. Do not place them inside the OpenClaw directory being backed up.

## Important Settings

| Setting | What it controls | Default | CLI argument |
| --- | --- | --- | --- |
| Backup source directory | Which OpenClaw root is backed up | Parent of the current workspace | `--source` |
| Minimum backup interval | Minimum days required before a real backup can run | `14` days | `--backup-interval-days` |
| Capacity check interval | How often repository usage is checked | `168` hours | `--capacity-check-hours` |
| Skip unchanged local snapshots | Skip a new local snapshot if nothing meaningful changed | On | `--local-skip-unchanged on/off` |
| Change-detection exclusions | Paths ignored when deciding whether anything changed | Built-in defaults | `--change-exclude` / `--clear-change-excludes` |
| Offsite staging directory | Local staging path before upload | `~/OpenClawBackups/nas-staging` | `--nas-staging-root` |
| Skip unchanged offsite archives | Skip a new archive if nothing meaningful changed | On | `--nas-skip-unchanged on/off` |
| Protected recent versions | Minimum recent offsite versions kept during cleanup | `30` | `--nas-protected-recent` |
| Alert cooldown | Minimum interval before repeating the same alert | `72` hours | `--cooldown-hours` |

Default change-detection exclusions:

- `logs/**`
- `agents/*/sessions/**`
- `openclaw-weixin/accounts/*.sync.json`
- `workspace/memory/openclaw_backup/latest_report.json`
- `workspace/memory/openclaw_backup/runtime_state.json`
- `workspace/memory/openclaw_backup/alert_state.json`

These rules affect only whether a new backup is needed. They do not change which files are included in the backup itself.

## Common Commands

```bash
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py status
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py run-now
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py list-local
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py list-nas
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py restore-local --snapshot <snapshot_id> --force
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py restore-nas --snapshot <snapshot_id> --force
```

For OpenClaw-side operator instructions, see [docs/OPENCLAW_OPERATOR_GUIDE.md](./docs/OPENCLAW_OPERATOR_GUIDE.md).
