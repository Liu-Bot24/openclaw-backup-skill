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

然后把返回结果里的 `recommended_questions` 当作首次配置清单。

必须遵守：

- 默认先整理成一份“推荐配置摘要”，一次性发给用户确认，不要把首次配置拆成一问一答。
- 如果用户表示“直接采用推荐配置”，就直接落盘。
- 如果用户说要修改，再一次性接收改动项；只有缺少条件项时才补问。
- `core_confirm_together_ids` 里的项目应体现在推荐摘要里，但不需要逐条来回追问。
- `conditional_groups` 里的项目，只在条件成立时再追问。
- 特别注意：
  - 如果离机归档保持关闭，不要继续追问归档目录、warning 阈值、critical 阈值。
  - 如果离机归档开启，必须补齐 `--nas-root`；需要时再确认 warning / critical 阈值。

离机归档目标不要求一定是 NAS。
任何稳定可写的 SMB 共享目录都可以作为归档仓库。

## 落盘

拿到足够信息后，只通过 `configure` 命令落盘。

例如：

```bash
python3 ~/.openclaw/workspace/scripts/openclaw_backup_manage.py configure --auto on --cadence two-weeks --weekday 0 --time 03:30 --timezone Asia/Shanghai --local on --local-root "$HOME/OpenClawBackups/openclaw-snapshots" --local-keep 3 --nas off --alert-delivery recent-channel
```

如果用户开启离机归档，必须明确给出 `--nas-root`；
必要时再给 `--nas-staging-root`、`--warn-gib`、`--critical-gib`。

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
