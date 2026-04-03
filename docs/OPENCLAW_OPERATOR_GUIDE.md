# OpenClaw Operator Guide

本文件供 OpenClaw 这类安装代理执行安装、首次配置和后续重配置时参考。

## 安装

1. clone 仓库到合适的本地目录
2. 进入仓库目录
3. 执行：

```bash
python3 install.py install-openclaw
```

如果用户指定了非默认 OpenClaw workspace，则改用：

```bash
python3 install.py install-openclaw --workspace /path/to/.openclaw/workspace
```

## 首次配置

安装成功后，直接进入首次配置，不需要用户再补额外口令。

先运行：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py setup-plan --json
```

然后围绕这些信息完成确认：

- 是否开启自动备份
- 自动节奏是手动、每周一次、两周一次，还是自定义
- 本机快照是否开启
- 本机快照目录
- 本机最多保留几份
- 是否开启离机归档
- 如果开启离机归档：确认一个稳定可写的 SMB 共享路径
- 容量提醒阈值
- 失败或告警是否回传最近一次交互渠道

离机归档目标不要求一定是 NAS。
任何稳定可写的 SMB 共享目录都可以作为归档仓库。

## 落盘

拿到足够信息后，只通过 `configure` 命令落盘。

例如：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py configure --auto on --cadence two-weeks --weekday 0 --time 03:30 --local-root "$HOME/OpenClawBackups/openclaw-snapshots" --local-keep 3 --nas off --alert-delivery recent-channel
```

如果用户开启离机归档，必须明确给出 `--nas-root`；
必要时再给 `--nas-staging-root`。

## 回显

配置完成后运行：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py status
```

然后把最终状态用简洁自然语言告诉用户。

## 后续重配置

当用户后续表达这些需求时，直接进入配置或重配置流程：

- 把备份开起来
- 帮我把备份配好
- 改成本机保留 5 份
- 改 SMB 路径
- 改成每周一次

恢复、删除这类破坏性操作，先确认快照 ID，再执行。
