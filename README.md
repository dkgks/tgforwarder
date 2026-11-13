# tgforwarder — Telegram 陌生人消息转发机器人

陌生人给你 Bot 发消息 → 自动过滤垃圾 → 转发到你的私聊。你直接在 Telegram 里回复那条转发消息，就能回给对方，不用暴露自己的账号。

三道过滤（本地词库 / 昵称检测 / AI 分类）叠加分级处置：第 1 次广告命中只警告并静默计数，第 2 次自动拉黑。正常访客基本察觉不到过滤器的存在。

[![CI](https://github.com/dkgks/tgforwarder/actions/workflows/ci.yml/badge.svg)](https://github.com/dkgks/tgforwarder/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/dkgks/tgforwarder)](https://github.com/dkgks/tgforwarder/releases/latest)
[![License](https://img.shields.io/github/license/dkgks/tgforwarder)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/dkgks/tgforwarder)](https://hub.docker.com/r/dkgks/tgforwarder)

## 目录

- [它怎么工作](#它怎么工作)
- [功能一览](#功能一览)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [管理面板](#管理面板)
- [机器人命令](#机器人命令)
- [在线升级](#在线升级)
- [数据与日志](#数据与日志)
- [常见问题](#常见问题)
- [开发与测试](#开发与测试)
- [隐私说明](#隐私说明)

## 它怎么工作

```
陌生人发消息
   │
   ├─ 已在拉黑列表 ──────────────────────→ 丢弃
   ├─ 冷却期内（刚被判过广告/辱骂） ────→ 丢弃（提示语每个窗口只发一次）
   │
   ├─ 昵称/用户名含广告词 ──────────────→ 拦截 + 计数
   ├─ 正文命中本地词库 ─────────────────→ 拦截 + 计数 + 清空该用户待转发队列
   │
   └─ 干净消息 → 进批次队列（8 秒窗口，最长等 30 秒）
                     │
                     ├─ AI 判 OK ─────→ 转发给你
                     ├─ AI 判 SPAM ───→ 拦截 + 静默计数（不警告对方）
                     └─ AI 不可用 ────→ 暂扣，发带「放行 / 丢弃」按钮的通知等你拍板
```

几个刻意的设计：

- **AI 只是建议，不是唯一依据**。本地词库先跑，AI 只处理本地漏掉的（例如外文广告）。免费模型会把「我电话 186…」这类正常询价误判成广告，所以 AI 判 SPAM 时**不警告发送者**，只计数。
- **AI 调用失败不猜也不放过**。超时、非 200、空正文都算失败，消息暂扣待你人工放行，绝不静默放行。
- **第几次拉黑是可预期的**。广告第 2 次命中自动拉黑，辱骂第 2 次起 AI 自动回怼；你只在「拉黑」时收到一条提醒，不会被每条命中刷屏。
- **10 条连续干净消息自动批准**。新访客从「审核中」转「已批准」，之后不再做昵称检查。

## 功能一览

**转发与回复**

- 陌生人文本消息自动转发，附带昵称、@username、ID、时间、命中记录
- 直接回复转发消息即可回给对方（也支持 `/reply <用户ID> <内容>`）
- 多条消息按批次聚合转发，不刷屏
- 多实例：一台机器跑多个 Bot（不同 config + 独立 pidfile，互不干扰）

**三层过滤**

- 本地词库：约 7,100 条广告词 + 210 条脏话词，预置词库可一键从云端同步更新
- 昵称/用户名检测：只发「你好」、把广告塞进昵称的也会被拦
- AI 分类：可选，支持 OpenRouter 与硅基流动，模型名可填模型链（主 + 备），一个失败自动换下一个

**处置与风控**

- 广告：第 1 次警告，第 2 次拉黑（阈值见代码 `SPAM_BLOCK_AT`）
- 辱骂：不转发，第 2 次起 AI 生成回怼；也可在面板手动「一键回骂」（1–5 条，两次确认）
- 冷却抑制：命中后该用户一段时间内的后续消息一律不转发
- 待审暂扣：AI 不可用时给主人发「✅放行 / 🫠丢弃」按钮
- 屏蔽记录：每条被拦内容都留档，可按用户翻查命中词

**运营**

- 全功能 Inline 按钮管理面板：用户列表 / 搜索 / 批准 / 拉黑 / 解封 / 删除 / 重置
- 统计：本周屏蔽广告数、回骂次数、用户状态分布
- 欢迎词、自动回复（次数上限可调）、时区自动检测
- 在线升级：面板一键拉取 GitHub Release，失败自动回退
- Docker 镜像双源发布（Docker Hub + ghcr.io），跟随 Release 自动构建

## 快速开始

### 一键部署（推荐）

```bash
bash <(curl -sSL https://raw.githubusercontent.com/dkgks/tgforwarder/main/tgfwd.sh)
```

首次运行可选安装方式：**系统服务**（systemd，开机自启 / 崩溃重启，推荐）、**Docker**、**后台进程**。装完再执行 `bash tgfwd.sh` 进入管理菜单。

### 手动部署（systemd）

```bash
git clone https://github.com/dkgks/tgforwarder.git
cd tgforwarder
pip install -r requirements.txt          # python-telegram-bot[job-queue] + httpx

cp config.example.json config.json
nano config.json                          # 填 bot_token、owner_id

python3 forwarder.py config.json          # 前台试跑，确认能连上
```

确认无误后写成服务：

```ini
# /etc/systemd/system/tg-forwarder@.service
[Unit]
Description=Telegram Forwarder (%i)
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/tgforwarder/forwarder.py %h/tgforwarder/instances/%i/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now tg-forwarder@main
```

一个实例 = 一个目录（`instances/<名字>/`），里面放 `config.json`、`state.json`、`keywords.json` 等。多开 Bot 就多建一个目录。

### Docker

无需本地构建，直接拉预构建镜像（每次 Release 自动推送到两个仓库）：

```bash
docker pull dkgks/tgforwarder:latest            # Docker Hub，免登录
docker pull ghcr.io/dkgks/tgforwarder:latest    # GitHub Container Registry
```

```bash
mkdir -p data && cp config.example.json data/config.json
nano data/config.json                            # 填 bot_token、owner_id

docker run -d --name tgforwarder --restart unless-stopped \
  -v $(pwd)/data:/app/data \
  dkgks/tgforwarder:latest
```

或用仓库里的 compose 自己构建：

```bash
cp config.example.json data/config.json
docker compose up -d --build
```

`/app/data` 是唯一需要持久化的目录（配置、状态、词库、日志都在里面）。

### 依赖要求

- Python **3.10+**（代码使用了 `X | None` 类型注解；镜像基于 3.11）
- Linux（Debian / Ubuntu 已验证）；systemd 可选
- Docker + docker compose（仅 Docker 部署需要）

### 创建 Bot 与拿到自己的 ID

1. 和 [@BotFather](https://t.me/BotFather) 对话，`/newbot`，拿到 `123456:ABC-DEF…` 形式的 token
2. 和 [@userinfobot](https://t.me/userinfobot) 对话，拿到自己的数字 User ID
3. 把两者填进 `config.json`，启动后给你的 Bot 发 `/menu`，能看到面板就通了

## 配置说明

配置文件路径由启动参数决定（`python3 forwarder.py <config.json>`），所有数据文件都放在同一目录下。

```json
{
  "bot_token": "YOUR_BOT_TOKEN_HERE",
  "owner_id": 123456789,
  "utc_offset": null,
  "welcome_msg": "👋 你好，这是消息转发助手。\n\n你的留言将会被转发给管理员，他会通过这个机器人回复你。\n⚠️ 请勿发送广告或骚扰信息。",
  "name_check_enabled": true,
  "suppress_enabled": true,
  "suppress_sec": 300,
  "batch_window_sec": 8,
  "batch_max_wait_sec": 30,
  "suppress_notice_enabled": true,
  "suppress_notice_text": "⏳ 你已被暂时限制，冷却期内的消息不会被转发给管理员。",
  "ai": {
    "platform": "openrouter",
    "api_key": "",
    "base_url": "https://openrouter.ai/api/v1",
    "classify_model": "nvidia/nemotron-3.5-super-vl:free",
    "classify_models": ["nvidia/nemotron-3.5-super-vl:free", "cohere/north-mini-code:free"],
    "classify_max_tokens": 3000,
    "insult_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "abuse_manual_off": false
  }
}
```

**必填**

| 键 | 说明 |
|---|---|
| `bot_token` | BotFather 给的 token |
| `owner_id` | 你的数字 User ID。只有这个 ID 能用面板和 `/reply` |

**转发与过滤**

| 键 | 默认 | 说明 |
|---|---|---|
| `welcome_msg` | 见示例 | 陌生人发 `/start` 时的欢迎词（面板也可改） |
| `name_check_enabled` | `true` | 昵称/用户名查词库 |
| `name_min_kw_len` | `2` | 昵称匹配时忽略过短的词，减少误杀 |
| `name_notice_text` | 内置文案 | 昵称命中时发给对方的提示 |
| `suppress_enabled` | `true` | 冷却抑制开关 |
| `suppress_sec` | `300` | 冷却时长（秒） |
| `suppress_notice_enabled` | `true` | 是否给被冷却的人回一条提示（每个窗口一次） |
| `suppress_notice_text` | 内置文案 | 上条提示的内容 |
| `batch_window_sec` | `8` | 批次窗口：最后一条消息后等多久再转发 |
| `batch_max_wait_sec` | `30` | 批次上限：从第一条算起最多等多久 |

**AI（可选）**

| 键 | 说明 |
|---|---|
| `platform` | `openrouter` 或 `siliconflow`，决定默认 `base_url` |
| `api_key` | 留空 = 完全不启用 AI，只用本地词库。**`enabled` 字段无效**，是按 `api_key` 是否为空判断的 |
| `base_url` | 兼容 OpenAI 的接口地址，自定义网关也可用 |
| `classify_model` | 分类模型；面板里改成别的模型会自动覆盖 `classify_models` |
| `classify_models` | 模型链，按顺序尝试，全失败才算 AI 不可用 |
| `classify_max_tokens` | 单次分类的 `max_tokens`，默认 3000。推理型模型给小了会返回空正文，别调低 |
| `insult_model` | 自动回怼用的模型 |
| `abuse_manual_off` | `true` = 只保留分类，关掉自动回怼 |

其他文件：

| 文件 | 说明 |
|---|---|
| `keywords.json` | 屏蔽词库（`abuse` 脏话 / `spam` 广告 + 预设与自定义的差集），可从面板同步云端更新 |
| `auto_reply.json` | 自动回复：`enabled`、`text`、`max_count`（每人最多回几次） |

## 管理面板

给自己的 Bot 发 `/menu`：

```
📋 管理面板
├── 👥 用户列表       搜索 / 详情 / 批准 / 拉黑 / 解封 / 重置 / 删除 / 屏蔽记录 / 一键回骂
├── 🚫 拉黑列表       一键解封
├── 📊 统计数据       本周屏蔽广告、回骂人次、用户状态分布
└── ⚙️ 设置
    ├── 🔑 屏蔽词管理   查看 / 添加 / 删除 / 同步云端词库
    ├── 💬 欢迎词设置
    ├── 🤖 自动回复设置  开关、次数上限、改文案
    ├── 🛡️ 反垃圾设置    昵称检测、冷却抑制、冷却时长、批次窗口、最长等待、提示语
    ├── 🤖 AI 设置      测试连接、自动回怼开关、API 密钥管理、自定义模型、OpenRouter 免费模型列表
    ├── 🕐 时区设置      UTC-12 ~ UTC+14 或自动检测
    ├── 🔄 检查更新      在线升级
    └── 🗑 一键清空所有用户
```

面板是单条消息原地编辑的：点哪个按钮就更新同一条消息，不会在聊天里堆一堆历史。

## 机器人命令

| 命令 | 谁可用 | 作用 |
|---|---|---|
| `/start` | 所有人 | 陌生人看欢迎词；主人看服务状态 |
| `/menu` | 主人 | 打开管理面板（也是 Bot 菜单里唯一注册的命令） |
| `/status` | 主人 | 列出所有追踪用户及状态 |
| `/reply <用户ID> <内容>` | 主人 | 不用回复转发消息也能主动发给某人 |
| `/cancel` | 主人 | 取消当前的输入态（改模型 / 改密钥 / 搜索 / 编辑中） |
| `/cancel_edit` | 主人 | 取消文案与屏蔽词编辑 |

## 在线升级

两个入口，都只替换代码文件（`.py`、`.sh`、`*.example.json`），不动配置、词库和用户数据：

- **面板**：设置 → 🔄 检查更新 → 升级到最新版
- **脚本**：`bash tgfwd.sh` → 更新程序

升级后机器人自动重启，并起一个 15 秒的 watchdog：`getMe` 通了就通知「升级成功」并清掉备份；起不来就自动回退到上一版本再重启。版本比较按数字（major.minor.patch）而不是字符串，所以本地开发版不会收到「降级」提示。

## 数据与日志

一个实例目录里的文件：

| 文件 | 内容 |
|---|---|
| `config.json` | 配置（含 token，注意权限） |
| `state.json` | 用户档案：批准/拉黑状态、连续干净条数、命中计数、屏蔽记录、最近消息时间 |
| `stats.json` | 累计统计（面板里「本周」的数字，由 `weekly_report.py` 清零） |
| `keywords.json` | 词库 |
| `reply_map.json` | 转发的消息 ID → 用户 ID 映射，决定「回复哪条就发给谁」 |
| `auto_reply.json` | 自动回复配置 |
| `forwarder.<hash>.pid` | 单实例锁（同一 config 不会被跑两遍） |
| `upgrade_flag.json` / `backup/` | 升级回退用，成功后自动清理 |
| `logs/<名字>.log` | 运行日志（用 logrotate 或 systemd 自带的重定向自行切割） |

`weekly_report.py <config.json>` 可以发一份周报给主人并清零统计。

## 常见问题

**某条消息为什么没被转发？**
按顺序查：用户是否被拉黑 → 是否在冷却期 → 昵称是否命中词库 → 正文是否命中词库 → AI 是否判了 SPAM → AI 是否失败被暂扣。日志里有明确的分支记录（`Batch for user …`、`apply_spam_hit`、`hold_for_owner_review`），`state.json` 里该用户的 `abuse_history` 也留着原文和命中词。

**AI 会不会误伤真实客户？**
会，所以 AI 判 SPAM 时不给对方发任何警告，只静默计数并留档；你翻「屏蔽记录」就能恢复。真想让它完全不参与，把 `ai.api_key` 留空即可。

**把 AI 关了为什么还在调用？**
`ai.enabled` 是历史遗留字段，代码只看 `api_key` 是否为空。清空 `api_key`（或置 `abuse_manual_off: true` 只关回怼）。

**免费模型时好时坏？**
免费模型有并发和额度限制，`content` 空正文也常见。所以分类走模型链（`classify_models`，主 + 备），全失败才判定不可用并暂扣消息。可以把面板里「测试 AI 连接」当健康检查。

**词库会覆盖我手动加的词吗？**
不会。云端同步只替换预置部分，你自己增删的词单独记着。

**能同时跑几个 Bot 吗？**
可以。每个实例一份 config 目录；`tgfwd.sh` 或 systemd 模板服务都支持按实例名起停。

## 开发与测试

```bash
python3 -m py_compile forwarder.py
python3 tests/test_name_check.py
python3 tests/test_handler_name.py
python3 tests/test_batch_guard.py
```

测试是自包含脚本：用一个临时的实例目录导入 `forwarder.py`，自己写 `keywords.json`，把 `send_msg` / `forward_to_owner` 打桩，不会碰线上数据。CI（`.github/workflows/ci.yml`）只做语法检查，Release 时构建并推送双源镜像。

改代码前建议看这几个约定：

- 屏蔽词库不要断言线上内容（云端会变）
- 命中/拉黑的门槛与文案集中在 `SPAM_BLOCK_AT`、`SPAM_ESCALATION_TEXT`、`NAME_ESCALATION_TEXT`
- 每次命中广告都要经过 `apply_spam_hit()`，不要复制那段升级逻辑

## 隐私说明

- Bot token、用户档案、屏蔽记录全部只存在你机器上的实例目录里，仓库的 `.gitignore` 已排除它们
- 消息**转发**不经过任何第三方；只有启用 AI 时，待判定的文本会发给你自己配置的 AI 服务商（OpenRouter / 硅基流动 / 自建网关）
- 不使用 AI 就是纯本地运行，没有外部请求（除了 Telegram API 和可选的云端词库同步）

## 许可证

MIT
