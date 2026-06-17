#!/usr/bin/env bash
set -euo pipefail

# tgforwarder 一键部署脚本
# 用法: bash setup.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  tgforwarder 一键部署脚本${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 检查 Docker
if ! command -v docker &>/dev/null; then
    echo -e "${RED}❌ 未检测到 Docker，请先安装 Docker：${NC}"
    echo "   https://docs.docker.com/engine/install/"
    exit 1
fi

if ! docker compose version &>/dev/null 2>&1; then
    echo -e "${RED}❌ 未检测到 docker compose 插件，请先安装：${NC}"
    echo "   https://docs.docker.com/compose/install/"
    exit 1
fi

# 交互式配置
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

# 创建配置
mkdir -p data
echo -e "${YELLOW}⚙️  生成配置文件...${NC}"
python3 -c "
import json
with open('config.example.json') as f:
    cfg = json.load(f)
cfg['bot_token'] = '${BOT_TOKEN}'
cfg['owner_id'] = int('${OWNER_ID}')
with open('data/config.json', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('✅ 配置已保存到 data/config.json')
"

# 启动
echo ""
echo -e "${YELLOW}🚀 启动服务...${NC}"
docker compose up -d

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  🎉 部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "📱 现在可以在 Telegram 给你的机器人发送 /menu 打开管理面板"
echo "📋 查看日志: docker compose logs -f"
echo "🔄 重启服务: docker compose restart"
echo "🛑 停止服务: docker compose down"
