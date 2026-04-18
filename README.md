# openclaw-backup-skill

OpenClaw 备份 skill，提供本机去重快照、离机归档、自动轮转与恢复。

[简体中文](./README.md) | [English](./README.en.md)

`openclaw-backup-skill` 把 OpenClaw 运行目录的备份、恢复和清理收进一套固定工具里。它既支持恢复速度快的本机快照，也支持面向 NAS 或其他稳定可写 SMB 共享目录的离机归档。

## 你可以用它做什么

- 为 OpenClaw 运行目录创建本机去重快照
- 按固定检查时间自动执行备份轮转
- 把完整归档版本上传到 NAS 或其他 SMB 共享目录
- 只在失败或容量告警时回传提醒，不用每次定时检查都打扰你
- 恢复本机快照或离机归档，并清理旧版本

## 安装

推荐直接把仓库放到 OpenClaw 的 skill 目录，这样它会作为真正的 skill 被自动发现和加载：

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git ~/.openclaw/workspace/skills/openclaw-backup
```

如果你的 OpenClaw workspace 不在默认位置，或者你想同时保留兼容用的 `workspace/scripts/` 副本：

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git
cd openclaw-backup-skill
python3 install.py install-openclaw --workspace /path/to/.openclaw/workspace
```

也可以直接让 OpenClaw 安装，参考 [docs/OPENCLAW_INSTALL_PROMPT.md](./docs/OPENCLAW_INSTALL_PROMPT.md)。

## 首次配置

安装后，OpenClaw 应当直接进入配置交互。首次配置通常会确认这些内容：

| 配置项 | 控制内容 | 默认值 | 建议值 |
| --- | --- | --- | --- |
| 自动备份 | 是否启用自动轮转 | 关闭 | 开启 |
| 自动节奏 | 手动、每周、两周或自定义 | 两周一次 | 两周一次 |
| 检查星期 | 每周哪一天执行检查 | 周日 | 周日 |
| 检查时间 | 每周几点执行检查 | `03:30` | `03:30` |
| 时区 | 按哪个时区解释检查时间 | 系统时区 | 系统时区 |
| 本机快照 | 是否启用本机快照 | 开启 | 开启 |
| 本机目录 | 本机快照存放目录 | `~/OpenClawBackups/openclaw-snapshots` | 同默认值 |
| 本机保留数 | 本机最多保留几份快照 | `3` | `3` |
| 离机归档 | 是否启用离机归档 | 关闭 | 关闭 |
| 归档目录 | 离机归档目标目录 | 空 | 提供稳定可写 SMB 路径后再设置 |
| 提醒阈值 warning | 备份仓库多大时开始提醒 | `120 GiB` | `120 GiB` |
| 提醒阈值 critical | 备份仓库多大时进入严重提醒 | `180 GiB` | `180 GiB` |
| 提醒回传 | 失败或告警是否回传最近一次交互渠道 | 最近一次交互渠道 | 最近一次交互渠道 |

离机归档默认面向 NAS，但目标设备不一定非得是 NAS。只要是稳定可写的 SMB 共享目录，都可以作为归档仓库。

本机快照目录、离机归档目录和离机暂存目录都必须放在备份源目录之外，不能配到被备份的 OpenClaw 目录内部。

## 重要配置项

| 配置项 | 控制内容 | 默认值 | CLI 参数 |
| --- | --- | --- | --- |
| 备份源目录 | 要备份的 OpenClaw 根目录 | 当前工作区上级目录 | `--source` |
| 实际最短备份间隔 | 至少隔多久才允许真的备份 | `14` 天 | `--backup-interval-days` |
| 容量检查间隔 | 多久做一次仓库容量检查 | `168` 小时 | `--capacity-check-hours` |
| 本机无变化跳过 | 无有效变化时是否跳过新快照 | 开启 | `--local-skip-unchanged on/off` |
| 变化判断排除规则 | 哪些路径不参与“是否有变化”判断 | 内置默认列表 | `--change-exclude` / `--clear-change-excludes` |
| 离机暂存目录 | 打包后准备上传到共享目录前的本地目录 | `~/OpenClawBackups/nas-staging` | `--nas-staging-root` |
| 离机无变化跳过 | 无有效变化时是否跳过新归档版本 | 开启 | `--nas-skip-unchanged on/off` |
| 保护最近版本数 | 清理旧归档时至少保留多少最近版本 | `30` | `--nas-protected-recent` |
| 告警冷却时间 | 相同告警最短间隔多久再提醒 | `72` 小时 | `--cooldown-hours` |

默认的变化判断排除规则：

- `logs/**`
- `agents/*/sessions/**`
- `openclaw-weixin/accounts/*.sync.json`
- `workspace/memory/openclaw_backup/latest_report.json`
- `workspace/memory/openclaw_backup/runtime_state.json`
- `workspace/memory/openclaw_backup/alert_state.json`

这些规则只影响“是否需要新建备份”，不影响真正执行备份时会包含哪些文件。

## 常用命令

```bash
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py status
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py run-now
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py list-local
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py list-nas
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py restore-local --snapshot <snapshot_id> --force
python3 ~/.openclaw/workspace/skills/openclaw-backup/scripts/openclaw_backup_manage.py restore-nas --snapshot <snapshot_id> --force
```

更多安装代理说明见 [docs/OPENCLAW_OPERATOR_GUIDE.md](./docs/OPENCLAW_OPERATOR_GUIDE.md)。
