# 🤖 TGForwarder

> You talk shit, it talks back. **With AI.**

一个带 AI 智能识别的 Telegram 消息转发机器人。帮你在 Telegram 上自动过滤骚扰信息，对喷子重拳出击，对正经人友好转发。

---

## ✨ 功能亮点

| 功能 | 说明 |
|------|------|
| 🛡️ **智能过滤** | AI 自动识别广告和脏话，无需手动设规则 |
| 🥊 **自动反击** | 对喷子 AI 生成犀利回骂，绝不憋屈 |
| 📩 **消息转发** | 正常用户的 10 条消息内审核通过，之后直接转发 |
| 🔑 **关键词屏蔽** | 本地关键词库兜底，零 API 消耗 |
| 🧠 **双 AI 平台** | OpenRouter（免费模型）/ SiliconFlow，灵活切换 |
| 🤖 **多实例** | 一台服务器跑多个机器人，各自独立配置 |
| 📊 **管理面板** | Inline 按钮菜单，用户管理/拉黑/关键词/自动回复一站式 |

---

## 🚀 一键安装

```bash
bash <(curl -sSL https://raw.githubusercontent.com/dkgks/tgforwarder/main/tgfwd.sh)
```

脚本会自动完成：环境检查 → 安装依赖 → 克隆项目 → 交互式配置 → 启动运行。

之后运行 `bash tgfwd.sh` 可进入管理菜单（添加/管理/更新多个机器人）。

---

## 📋 前置准备

1. **Bot Token** — 在 [@BotFather](https://t.me/BotFather) 创建机器人获取
2. **用户 ID** — 通过 [@userinfobot](https://t.me/userinfobot) 获取你的数字 ID
3. **AI 平台**（可选）：
   - 不启用 AI：只用关键词屏蔽，零成本
   - 启用 AI：
     - ⭐ **OpenRouter**（推荐）— [openrouter.ai/keys](https://openrouter.ai/keys) 获取 Key，免费模型零成本
     - **SiliconFlow** — [siliconflow.cn](https://siliconflow.cn) 注册送额度

---

## 🎮 使用方式

在机器人聊天中：

- 输入框左侧菜单按钮 → 选择命令
- `/menu` — 打开管理面板
- `/status` — 查看用户状态
- `/reply <用户ID> <消息>` — 主动回复陌生人

陌生人给你发消息时，机器人会自动审核并转发到你这里。你直接在聊天里回复转发的消息，就会传回给陌生人。

---

## 🏗️ 架构

```
陌生人 → [关键词屏蔽] → [AI 审核(可选)] → 转发给你
你回复 → 传回给陌生人

管理面板 ←→ 机器人菜单（仅你可见）
```

---

## 🛠️ 手动配置

如果不想用一键脚本，也可以手动：

```bash
git clone https://github.com/dkgks/tgforwarder.git
cd tgforwarder
pip3 install httpx python-telegram-bot --break-system-packages

# 创建配置文件 config.json：
cat > config.json << 'EOF'
{
  "bot_token": "你的BotToken",
  "owner_id": 你的用户ID,
  "ai": {
    "enabled": false
  }
}
