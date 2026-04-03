<div align="center">

# openclaw-backup-skill

OpenClaw 备份技能，提供本机去重快照、离机归档、自动轮转与恢复。

[简体中文](./README.md) | [English](./README.en.md)

</div>

## 产品简介

`openclaw-backup-skill` 是一个给 OpenClaw 使用的备份技能。

它把 OpenClaw 运行目录的本机快照、离机归档、自动轮转、恢复和旧版本清理收进一套固定工具里，让用户可以稳定地拿到可恢复的备份，而不是靠临时脚本或手工操作拼起来。

## 功能

- 本机去重快照
  - 每个快照都是完整视图
  - 未变化文件通过硬链接复用，不重复占空间
- 离机归档
  - 先本地打包，再上传到目标目录
  - 默认面向 NAS 场景，也支持其他稳定可写的 SMB 共享目录
- 自动备份
  - 由 OpenClaw 定时执行
  - 支持手动、每周、两周和自定义间隔
- 容量提醒
  - 只关注备份仓库自身占用
- 恢复与清理
  - 支持恢复本机快照
  - 支持恢复离机归档
  - 支持删除旧快照和旧归档版本

## 安装

### 命令安装

```bash
git clone https://github.com/Liu-Bot24/openclaw-backup-skill.git
cd openclaw-backup-skill
python3 install.py install-openclaw
```

如果 OpenClaw 工作区不在默认位置：

```bash
python3 install.py install-openclaw --workspace /path/to/.openclaw/workspace
```

### 交给 OpenClaw 安装

把下面这段话直接发给 OpenClaw：

```text
请帮我安装 openclaw-backup-skill。

仓库地址：
https://github.com/Liu-Bot24/openclaw-backup-skill.git

请完成安装，并在安装后直接进入首次配置。
如果失败，告诉我原因；如果成功，告诉我最终配置结果。
```

同一份文本也放在 [docs/OPENCLAW_INSTALL_PROMPT.md](docs/OPENCLAW_INSTALL_PROMPT.md)。

## 首次配置

安装完成后，OpenClaw 应当直接进入配置交互。

首次配置通常会确认这些内容：

| 配置项 | 作用 | 初始值 | 建议值 |
| --- | --- | --- | --- |
| 自动备份 | 是否启用自动轮转 | 关闭 | 开启 |
| 自动节奏 | 手动、每周、两周或自定义 | 两周一次 | 两周一次 |
| 检查星期 | 每周哪一天执行检查 | 周日 | 周日 |
| 检查时间 | 每周几点执行检查 | `03:30` | `03:30` |
| 时区 | 按哪个时区理解检查时间 | 系统时区 | 系统时区 |
| 本机快照 | 是否启用本机快照 | 开启 | 开启 |
| 本机目录 | 本机快照存放目录 | `~/OpenClawBackups/openclaw-snapshots` | 同默认值 |
| 本机保留数 | 本机最多保留几份快照 | `3` | `3` |
| 离机归档 | 是否启用离机归档 | 关闭 | 关闭 |
| 归档目录 | 离机归档目标目录 | 空 | 提供稳定可写的 SMB 共享路径后再设置 |
| 提醒阈值 warning | 备份仓库多大时开始提醒 | `120 GiB` | `120 GiB` |
| 提醒阈值 critical | 备份仓库多大时进入严重提醒 | `180 GiB` | `180 GiB` |
| 提醒回传 | 失败或告警是否回传最近一次交互渠道 | 最近一次交互渠道 | 最近一次交互渠道 |

说明：

- “离机归档”通常对应 NAS，但目标设备不一定非得是 NAS。
- 只要是稳定可写的 SMB 共享目录，都可以作为归档仓库。

## 高级配置项

除了初始化时常见的配置项，这些也支持单独调整：

| 配置项 | 作用 | 默认值 | CLI 参数 |
| --- | --- | --- | --- |
| 备份源目录 | 要备份的 OpenClaw 根目录 | 当前工作区上级目录 | `--source` |
| 实际最短备份间隔 | 至少隔多久才允许真的备份 | `14` 天 | `--backup-interval-days` |
| 容量检查间隔 | 多久做一次仓库容量检查 | `168` 小时 | `--capacity-check-hours` |
| 本机无变化跳过 | 无有效变化时是否跳过新快照 | 开启 | `--local-skip-unchanged on/off` |
| 本机不完整目录清理阈值 | 本机半成品目录多久后清理 | `5` 分钟 | `--local-cleanup-minutes` |
| 变化判断排除规则 | 哪些路径不参与“是否有变化”判断 | 见下方默认列表 | `--change-exclude` / `--clear-change-excludes` |
| 离机暂存目录 | 打包后准备上传到共享目录前的本地目录 | `~/OpenClawBackups/nas-staging` | `--nas-staging-root` |
| 离机无变化跳过 | 无有效变化时是否跳过新归档版本 | 开启 | `--nas-skip-unchanged on/off` |
| 离机不完整目录清理阈值 | 半成品目录多久后清理 | `30` 分钟 | `--nas-cleanup-minutes` |
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

查看当前状态：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py status
```

立即执行一次备份：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py run-now
```

列出本机快照：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py list-local
```

列出离机归档版本：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py list-nas
```

恢复本机快照：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py restore-local --snapshot <snapshot_id> --force
```

恢复离机归档：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py restore-nas --snapshot <snapshot_id> --force
```

## 目录结构

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
  安装入口，把脚本和 skill 装进 OpenClaw 工作区
- [openclaw_backup_manage.py](scripts/openclaw_backup_manage.py)
  产品主入口，负责配置、状态、立即备份、恢复、删除、提醒渲染
- [openclaw_backup_cycle.py](scripts/openclaw_backup_cycle.py)
  单次备份轮转执行器
- [openclaw_backup_snapshot.py](scripts/openclaw_backup_snapshot.py)
  本机快照创建、删除、恢复
- [OPENCLAW_INSTALL_PROMPT.md](docs/OPENCLAW_INSTALL_PROMPT.md)
  给用户直接复制发给 OpenClaw 的安装请求
- [OPENCLAW_OPERATOR_GUIDE.md](docs/OPENCLAW_OPERATOR_GUIDE.md)
  给 OpenClaw 这类安装代理使用的操作参考
- [REPOSITORY_METADATA.md](docs/REPOSITORY_METADATA.md)
  仓库中英文描述与发布时可直接使用的元信息
