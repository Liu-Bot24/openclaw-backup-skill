---
name: openclaw-backup
description: 管理 OpenClaw 运行目录的本机快照和可选离机归档。用于首次配置，以及后续任何启用备份、查看状态、立即备份、恢复、删除版本、修改 SMB/NAS 归档路径的请求。
---

# openclaw-backup

这是 OpenClaw 侧的产品化备份 skill。它的职责只有一件事：通过固定脚本管理 OpenClaw 的备份配置、自动备份、恢复点和离机归档。

必须遵守
- 只调用固定脚本：`python3 scripts/openclaw_backup_manage.py ...`
- 不要自己手工创建、删除、移动备份目录；所有备份动作都交给脚本
- 离机归档默认面向 NAS 场景，但任何稳定可写的 SMB 共享目录都可以使用
- 本机快照目录、离机归档目录、离机暂存目录都必须位于备份源目录之外
- 涉及恢复、删除这类破坏性操作，必须先确认用户要操作的快照 ID，再执行

## 1. 什么时候要立刻进入配置交互

- 如果当前这轮对话刚完成安装这个 skill，直接进入首次配置交互。
- 如果这个 skill 已经装好，而用户这次是在表达“我要启用备份”“帮我配好备份”“我要开始用备份”“把 SMB 路径改掉”这类真实需求，也直接进入配置交互。

## 2. 首次配置怎么做

先运行：

```bash
python3 scripts/openclaw_backup_manage.py setup-plan --json
```

然后把返回结果里的 `recommended_questions` 当作首次配置清单。

必须遵守：
- 默认先整理成一份“推荐配置摘要”，一次性发给用户确认，不要把首次配置拆成一问一答。
- 如果用户说“直接采用推荐配置”，就直接落盘。
- 如果用户说要修改，再一次性接收改动项；只有缺少条件项时才补问。
- `core_confirm_together_ids` 里的项目要体现在摘要里，但不需要逐条来回盘问。
- `conditional_groups` 里的项目，只在条件成立时再追问。
- 特别注意：
  - 如果离机归档保持关闭，不要继续追问归档目录、warning 阈值、critical 阈值。
  - 如果离机归档开启，必须补齐 `--nas-root`；需要时再确认 warning / critical 阈值。

拿到用户答案后，只通过一条 `configure` 命令落盘。例如：

```bash
python3 scripts/openclaw_backup_manage.py configure --auto on --cadence two-weeks --weekday 0 --time 03:30 --timezone Asia/Shanghai --local on --local-root "$HOME/OpenClawBackups/openclaw-snapshots" --local-keep 3 --nas off --alert-delivery recent-channel
```

如果用户开启离机归档，必须明确给出 `--nas-root`，必要时再给 `--nas-staging-root`、`--warn-gib`、`--critical-gib`。

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
python3 scripts/openclaw_backup_manage.py status
```

把最终状态简洁告诉用户。

## 3. 日常命令

查看当前状态：

```bash
python3 scripts/openclaw_backup_manage.py status
```

立即执行一次备份：

```bash
python3 scripts/openclaw_backup_manage.py run-now
```

列出本机快照：

```bash
python3 scripts/openclaw_backup_manage.py list-local
```

列出离机归档版本：

```bash
python3 scripts/openclaw_backup_manage.py list-nas
```

删除本机快照：

```bash
python3 scripts/openclaw_backup_manage.py delete-local --snapshot "<snapshot_id>"
```

删除离机归档版本：

```bash
python3 scripts/openclaw_backup_manage.py delete-nas --snapshot "<snapshot_id>"
```

恢复本机快照：

```bash
python3 scripts/openclaw_backup_manage.py restore-local --snapshot "<snapshot_id>" --force
```

恢复离机归档：

```bash
python3 scripts/openclaw_backup_manage.py restore-nas --snapshot "<snapshot_id>" --force
```

## 4. 自动提醒

自动备份的定时任务会静默运行。

- 正常 `BACKUP_OK` / `BACKUP_SKIPPED`：不要主动发消息打扰用户
- 失败：把失败那一行返回给用户
- 容量或离机归档可用性告警：由定时任务内部再调用 `render-alert`，只在确实需要提醒时才把正文发回最近一次交互渠道

## 5. 重新配置

当用户后续说“改成本机保留 5 份”“把离机归档打开并改路径”“改成每周一次”之类时，直接重跑 `configure`，把用户明确要求的参数覆盖进去，再用 `status` 回显。
