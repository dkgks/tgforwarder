# tgforwarder — Telegram 消息转发机器人

自动将陌生人的消息转发到你的 Telegram，支持 AI 智能过滤广告/脏话、关键词屏蔽、分级处罚。

## 功能

- 🔄 陌生人消息自动转发给管理员
- 🛡️ 本地关键词过滤（7,111 广告词 + 212 脏话词）
- 🤖 AI 智能分类（可选，支持 SiliconFlow / OpenRouter）
- 🚫 广告三振拉黑、辱骂自动回怼
- 📝 屏蔽记录审查面板
- ⚙️ 全功能管理面板（Inline 按钮）
- 🕐 时区自动检测
- 🔄 一键 GitHub Release 在线升级
- 🔧 多机器人管理（一个脚本管理多个 bot 实例）

## 快速开始

### 一键部署（推荐）

```bash
bash <(curl -sSL https://raw.githubusercontent.com/dkgks/tgforwarder/main/tgfwd.sh)
```

首次运行会引导选择安装方式：
- **系统服务**（推荐）— systemd 开机自启，崩溃自动重启
- **Docker** — 需要已安装 Docker + docker compose
- **后台进程** — 简单快速，启动即用

已安装的用户再次运行 `bash tgfwd.sh` 会自动进入管理菜单。

### 手动部署

```bash
# 1. 克隆仓库
git clone https://github.com/dkgks/tgforwarder.git
cd tgforwarder

# 2. 安装 Python 依赖
pip install python-telegram-bot[job-queue] httpx

# 3. 启动管理脚本
bash tgfwd.sh
```

### 管理命令

| 操作 | 方式 |
|---|---|
| 管理面板入口 | `bash tgfwd.sh` |
| 添加机器人 | 菜单 → 添加新机器人 |
| 删除机器人 | 菜单 → 删除机器人 |
| 修改 AI 配置 | 菜单 → 修改 AI 配置 |
| 开关功能 | 菜单 → 开关功能 |
| 更新程序 | 菜单 → 更新程序 (git pull) |
| 在线升级 | Telegram 面板 → 🔄 检查更新 |

### 依赖要求

- Python 3.9+
- 操作系统：Linux（推荐 Debian/Ubuntu）
- systemd（用于服务管理，可选）
- Docker + docker compose（仅 Docker 部署需要）

### 创建 Bot Token

1. 在 Telegram 搜索 @BotFather
2. 发送 `/newbot`，按提示创建机器人
3. 获取 token（格式：`123456:ABC-DEF1234gh`）
4. 获取你的 User ID：搜索 @userinfobot，发送 `/start`

## 配置说明

编辑 `config.json`：

```json
{
  "bot_token": "你的Bot Token",
  "owner_id": 你的Telegram用户ID,
  "utc_offset": 8,
  "ai": {
    "api_key": "sk-xxx（可选，留空则只用关键词过滤）",
    "base_url": "https://api.siliconflow.cn/v1",
    "platform": "siliconflow"
  }
}
```

- `bot_token`：必填
- `owner_id`：必填，你的 Telegram 数字 ID
- `utc_offset`：时区，中国填 8，留空自动检测
- `ai`：可选，启用 AI 智能分类和自动回骂

## 管理面板

向机器人发送 `/menu` 打开管理面板，功能包括：

- 👥 用户列表 — 查看/搜索/批准/拉黑用户
- 📊 统计数据 — 消息/广告/辱骂计数
- 🔑 屏蔽词管理 — 添加/删除/查看关键词
- ⚙️ 设置 — 自动回复/欢迎词/AI/时区
- 🔄 检查更新 — 一键在线升级

## 升级

### 管理面板升级
在管理面板点「🔄 检查更新」→「升级到最新版」，自动下载 GitHub Release 并替换代码文件，备份旧版本，失败自动回滚。

### 脚本升级
已部署的用户可直接用 `tgfwd.sh` 管理菜单中的「更新程序」选项。

## 许可证

MIT