# tgforwarder — Telegram 消息转发机器人

自动将陌生人的消息转发到你的 Telegram，支持 AI 智能过滤广告/脏话、关键词屏蔽、分级处罚、Docker 一键部署。

## 功能

- 🔄 陌生人消息自动转发给管理员
- 🛡️ 本地关键词过滤（7,111 广告词 + 212 脏话词）
- 🤖 AI 智能分类（可选，支持 SiliconFlow / OpenRouter）
- 🚫 广告三振拉黑、辱骂自动回怼
- 📝 屏蔽记录审查面板
- ⚙️ 全功能管理面板（Inline 按钮）
- 🕐 时区自动检测
- 🔄 一键 GitHub Release 在线升级
- 🐳 Docker 一键部署

## 快速开始

### Docker（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/dkgks/tgforwarder.git
cd tgforwarder

# 2. 准备配置
cp config.example.json data/config.json
# 编辑 data/config.json，填入 bot_token 和 owner_id

# 3. 启动
docker compose up -d

# 4. 查看日志
docker compose logs -f
```

### Linux 手动部署

```bash
# 1. 安装依赖
pip install python-telegram-bot[job-queue] httpx

# 2. 创建配置
cp config.example.json config.json
# 编辑 config.json，填入 bot_token 和 owner_id

# 3. 运行
python3 forwarder.py
```

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

在管理面板点「🔄 检查更新」→「升级到最新版」，自动下载 GitHub Release 并替换代码文件，备份旧版本，失败自动回滚。

## 许可证

MIT
