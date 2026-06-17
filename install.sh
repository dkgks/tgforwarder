#!/usr/bin/env bash
set -euo pipefail

# tgforwarder 非 Docker 一键部署脚本
# 用法: bash install.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  tgforwarder 一键部署（非 Docker）${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ 未检测到 python3，请先安装 Python 3.9+：${NC}"
    echo "   Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "   CentOS/RHEL: sudo yum install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "(0,0)")
echo -e "   Python 版本: ${PYTHON_VERSION}"

# 检查 pip
if ! command -v pip3 &>/dev/null; then
    echo -e "${RED}❌ 未检测到 pip3，请先安装：${NC}"
    echo "   Ubuntu/Debian: sudo apt install python3-pip"
    exit 1
fi

# 交互式配置
echo ""
echo -e "${YELLOW}📝 配置你的机器人：${NC}"
echo ""

read -rp "请输入 Bot Token（从 @BotFather 获取）: " BOT_TOKEN
if [ -z "$BOT_TOKEN" ]; then
    echo -e "${RED}❌ Bot Token 不能为空${NC}"
    exit 1
fi

read -rp "请输入你的 Telegram 用户 ID（从 @userinfobot 获取）: " OWNER_ID
if [ -z "$OWNER_ID" ]; then
    echo -e "${RED}❌ Owner ID 不能为空${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}📦 拉取项目代码...${NC}"

if [ -d "tgforwarder" ]; then
    echo "目录 tgforwarder 已存在，更新代码..."
    cd tgforwarder
    git pull origin main 2>/dev/null || true
else
    git clone https://github.com/dkgks/tgforwarder.git
    cd tgforwarder
fi

echo -e "${YELLOW}📦 安装 Python 依赖...${NC}"
pip3 install python-telegram-bot[job-queue] httpx

echo -e "${YELLOW}⚙️  生成配置文件...${NC}"
python3 << PYEOF
import json
with open('config.example.json') as f:
    cfg = json.load(f)
cfg['bot_token'] = '${BOT_TOKEN}'
cfg['owner_id'] = int('${OWNER_ID}')
with open('config.json', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('✅ 配置已保存到 config.json')
PYEOF

echo ""
echo -e "${YELLOW}🚀 启动服务...${NC}"
python3 forwarder.py &
sleep 3

# 检查是否跑起来了
if ps -p $! > /dev/null 2>&1; then
    echo -e "${GREEN}✅ 服务已在后台运行 (PID $!)${NC}"
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  🎉 部署完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "📱 现在可以在 Telegram 给你的机器人发送 /menu 打开管理面板"
    echo "📋 查看日志: tail -f forwarder.log"
    echo ""
    echo -e "${YELLOW}⚠️  当前为前台进程，关闭终端后服务会停止${NC}"
    echo ""
    echo "💡 推荐使用 tgfwd.sh 安装为系统服务（开机自启 + 崩溃重启）："
    echo "   bash tgfwd.sh"
    echo "   → 选 1 启动"
else
    echo -e "${RED}❌ 服务启动失败，请检查 forwarder.log${NC}"
    exit 1
fi
