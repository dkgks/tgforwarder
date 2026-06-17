#!/bin/bash
# ============================================================
#  Telegram 消息转发机器人 - 一键搭建 + 多机器人管理脚本
#  GitHub: https://github.com/dkgks/tgforwarder
# ============================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTALL_DIR="$HOME/tg-forwarder"
GIT_REPO="https://github.com/dkgks/tgforwarder.git"

# ============================================================
print_banner() {
    echo -e "${GREEN}"
    echo "========================================"
    echo "  Telegram 消息转发机器人"
    echo "  一键搭建 + 多机器人管理"
    echo "========================================"
    echo -e "${NC}"
}

# ============================================================
check_python() {
    if ! command -v python3 &>/dev/null; then
        echo -e "${RED}❌ 未检测到 Python3，请先安装 Python 3.10+${NC}"
        echo "   Ubuntu/Debian: apt install python3 python3-pip"
        exit 1
    fi
    PY_VER=$(python3 -c 'import sys; print(sys.version_info.minor)')
    if [ "$PY_VER" -lt 10 ]; then
        echo -e "${YELLOW}⚠️  Python 版本过低（3.$PY_VER），建议升级到 3.10+${NC}"
    fi
    echo -e "${GREEN}✅ Python: $(python3 --version)${NC}"
}

# ============================================================
install_deps() {
    echo -e "${BLUE}[*] 安装依赖包...${NC}"
    pip3 install httpx python-telegram-bot --break-system-packages -q 2>/dev/null || \
    pip3 install httpx python-telegram-bot -q
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
}

# ============================================================
download_code() {
    echo -e "${BLUE}[*] 下载项目代码...${NC}"
    if [ -d "$INSTALL_DIR" ]; then
        echo "  目录已存在，执行 git pull 更新..."
        cd "$INSTALL_DIR"
        git pull --ff-only origin main 2>/dev/null || echo -e "${YELLOW}  ⚠️  git pull 失败，使用本地版本${NC}"
        cd - >/dev/null
    else
        if command -v git &>/dev/null; then
            git clone "$GIT_REPO" "$INSTALL_DIR" 2>/dev/null || {
                echo -e "${YELLOW}  ⚠️  git clone 失败，请手动下载${NC}"
                exit 1
            }
        else
            echo -e "${RED}❌ 未检测到 Git，请先安装 git${NC}"
            echo "   Ubuntu/Debian: apt install git"
            exit 1
        fi
    fi
    echo -e "${GREEN}✅ 代码已就绪: $INSTALL_DIR${NC}"
}

# ============================================================
add_bot() {
    echo ""
    echo -n "  请输入 Bot Token（从 @BotFather 获取）: "
    read -r BOT_TOKEN
    if [ -z "$BOT_TOKEN" ]; then
        echo -e "${RED}❌ Token 不能为空${NC}"
        return 1
    fi

    echo -n "  请输入管理员 Telegram 用户ID（纯数字）: "
    read -r OWNER_ID
    if ! [[ "$OWNER_ID" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}❌ 用户ID 必须是纯数字${NC}"
        return 1
    fi

    echo ""
    echo "  ┌──────────────────────────────────────────────┐"
    echo "  │  AI 智能识别功能说明                          │"
    echo "  │                                              │"
    echo "  │  • 自动判断广告/脏话，无需手动设关键词         │"
    echo "  │  • 对脏话自动回骂（AI 生成犀利反击）          │"
    echo "  │  • 对广告自动分级警告                         │"
    echo "  │  • 10 条消息审核期，通过后直接转发             │"
    echo "  │                                              │"
    echo "  │  ⚠️  不启用 AI：只有关键词屏蔽，无智能识别    │"
    echo "  │                                              │"
    echo "  │  推荐 AI 平台：OpenRouter (openrouter.ai)     │"
    echo "  │  大量免费模型，每天成本为 0                    │"
    echo "  │  也支持 SiliconFlow (siliconflow.cn)            │"
    echo "  │  国内优化，注册送额度                          │"
    echo "  │                                              │"
    echo "  │  每日费用：免费 ~ ¥0.5（视平台和消息量）        │"
    echo "  └──────────────────────────────────────────────┘"
    echo ""
    echo -n "  是否启用 AI 智能识别 [y/N]: "
    read -r ENABLE_AI
    AI_ENABLED="false"
    AI_KEY=""
    AI_BASE=""
    AI_PLATFORM=""
    CLASSIFY_MODEL=""
    INSULT_MODEL=""

    if [[ "$ENABLE_AI" =~ ^[Yy]$ ]]; then
        AI_ENABLED="true"
        echo ""
        echo "  选择 AI 平台："
        echo "    1) OpenRouter ★推荐 (全球节点，免费模型多)"
        echo "    2) SiliconFlow (硅基流动，国内优化)"
        echo -n "  请选择 [1]: "
        read -r PLATFORM
        PLATFORM=${PLATFORM:-1}

        if [ "$PLATFORM" = "2" ]; then
            # === SiliconFlow ===
            AI_PLATFORM="siliconflow"
            AI_BASE="https://api.siliconflow.cn/v1"
            echo ""
            echo "  📋 获取 SiliconFlow API 密钥："
            echo "     访问 https://cloud.siliconflow.cn/account/ak"
            echo -n "  请输入 SiliconFlow API 密钥: "
            read -r AI_KEY
            echo -n "  分类模型 [默认 deepseek-ai/DeepSeek-V4-Flash]: "
            read -r CLASSIFY_MODEL
            CLASSIFY_MODEL=${CLASSIFY_MODEL:-deepseek-ai/DeepSeek-V4-Flash}
            echo -n "  回骂模型 [默认 deepseek-ai/DeepSeek-V4-Flash]: "
            read -r INSULT_MODEL
            INSULT_MODEL=${INSULT_MODEL:-deepseek-ai/DeepSeek-V4-Flash}
        else
            # === OpenRouter (默认) ===
            AI_PLATFORM="openrouter"
            AI_BASE="https://openrouter.ai/api/v1"
            echo ""
            echo "  📋 获取 OpenRouter API 密钥："
            echo "     访问 https://openrouter.ai/keys"
            echo "     （留空则只能使用免费模型，无需注册）"
            echo -n "  请输入 OpenRouter API 密钥 [可留空]: "
            read -r AI_KEY

            echo ""
            echo "  ⏳ 正在从 OpenRouter 获取免费模型列表..."
            FREE_MODELS=$(curl -s https://openrouter.ai/api/v1/models | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('data', [])
free = []
for m in models:
    pid = m.get('id', '')
    price = m.get('pricing', {}).get('prompt', '999')
    try:
        p = float(price)
    except:
        p = 999
    if p == 0:
        ctx = str(m.get('context_length', '?'))[:7]
        free.append((pid, ctx))
for i, (pid, ctx) in enumerate(free[:25], 1):
    print(f'{i:>2}. {pid} (上下文:{ctx})')
" 2>/dev/null)

            if [ -n "$FREE_MODELS" ]; then
                echo "$FREE_MODELS"
            fi

            echo ""
            echo "  ┌─────────────────────────────────────────────┐"
            echo "  │  🌟 推荐免费模型：                           │"
            echo "  │                                             │"
            echo "  │  分类模型 (轻量快速):                        │"
            echo "  │    qwen/qwen3-next-80b-a3b-instruct:free      │"
            echo "  │                                             │"
            echo "  │  回骂模型 (犀利反击):                        │"
            echo "  │    google/gemma-4-31b-it:free                │"
            echo "  │                                             │"
            echo "  │  你也可以输入上面列表中任意模型ID            │"
            echo "  │  或任何 OpenRouter 支持的付费模型             │"
            echo "  └─────────────────────────────────────────────┘"
            echo ""
            echo -n "  分类模型 [默认 qwen/qwen3-next-80b-a3b-instruct:free]: "
            read -r CLASSIFY_MODEL
            CLASSIFY_MODEL=${CLASSIFY_MODEL:-qwen/qwen3-next-80b-a3b-instruct:free}
            echo -n "  回骂模型 [默认 google/gemma-4-31b-it:free]: "
            read -r INSULT_MODEL
            INSULT_MODEL=${INSULT_MODEL:-google/gemma-4-31b-it:free}
        fi
    fi

    # 生成实例目录
    INSTANCE_NAME="bot_${OWNER_ID}"
    INSTANCE_PATH="$INSTALL_DIR/instances/$INSTANCE_NAME"
    mkdir -p "$INSTANCE_PATH"

    CONFIG_FILE="$INSTANCE_PATH/config.json"
    cat > "$CONFIG_FILE" <<EOF
{
  "bot_token": "$BOT_TOKEN",
  "owner_id": $OWNER_ID,
  "ai": {
    "enabled": $AI_ENABLED,
    "platform": "$AI_PLATFORM",
    "api_key": "$AI_KEY",
    "base_url": "$AI_BASE",
    "classify_model": "$CLASSIFY_MODEL",
    "insult_model": "$INSULT_MODEL"
  }
}
EOF

    echo -e "${GREEN}✅ 机器人已配置: $INSTANCE_NAME${NC}"
    echo "  配置文件: $CONFIG_FILE"

    return 0
}

# ============================================================
start_bot() {
    local conf="$1"
    local name="$(basename $(dirname $conf))"
    echo -e "${BLUE}[*] 启动机器人: $name${NC}"

    # 检查是否已在运行
    if pgrep -f "forwarder.py $conf" >/dev/null; then
        echo -e "${YELLOW}  ⚠️  已在运行，跳过${NC}"
        return 0
    fi

    nohup python3 "$INSTALL_DIR/forwarder.py" "$conf" > "$INSTALL_DIR/logs/${name}.out" 2>&1 &
    sleep 1
    if pgrep -f "forwarder.py $conf" >/dev/null; then
        echo -e "${GREEN}  ✅ 启动成功${NC}"
    else
        echo -e "${RED}  ❌ 启动失败，查看日志: $INSTALL_DIR/logs/${name}.out${NC}"
    fi
}

# ============================================================
stop_bot() {
    local conf="$1"
    local pids=$(pgrep -f "forwarder.py $conf")
    if [ -n "$pids" ]; then
        kill $pids 2>/dev/null
        echo -e "${GREEN}✅ 已停止${NC}"
    else
        echo -e "${YELLOW}⚠️  未在运行${NC}"
    fi
}

# ============================================================
# 主菜单
# ============================================================
main_menu() {
    while true; do
        echo ""
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}  Telegram 消息转发机器人 - 管理菜单${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo ""
        echo "  1) 添加新机器人"
        echo "  2) 查看所有机器人"
        echo "  3) 启动单个机器人"
        echo "  4) 停止单个机器人"
        echo "  5) 启动全部机器人"
        echo "  6) 停止全部机器人"
        echo "  7) 删除机器人配置"
        echo "  8) 更新项目代码"
        echo "  0) 退出"
        echo ""
        echo -n "  请选择 [0-8]: "
        read -r CHOICE

        case "$CHOICE" in
            1)
                add_bot || true
                ;;
            2)
                echo ""
                echo -e "${BLUE}已配置的机器人:${NC}"
                if [ -d "$INSTALL_DIR/instances" ]; then
                    for d in "$INSTALL_DIR/instances"/bot_*/; do
                        if [ -f "${d}config.json" ]; then
                            name=$(basename "$d")
                            conf="${d}config.json"
                            if pgrep -f "forwarder.py $conf" >/dev/null; then
                                status="${GREEN}运行中${NC}"
                            else
                                status="${RED}已停止${NC}"
                            fi
                            echo -e "  $name → $status"
                        fi
                    done
                else
                    echo "  (暂无)"
                fi
                ;;
            3)
                echo -n "  输入要启动的机器人名称 (如 bot_123456789): "
                read -r name
                conf="$INSTALL_DIR/instances/$name/config.json"
                if [ -f "$conf" ]; then
                    start_bot "$conf"
                else
                    echo -e "${RED}❌ 未找到配置: $conf${NC}"
                fi
                ;;
            4)
                echo -n "  输入要停止的机器人名称 (如 bot_123456789): "
                read -r name
                conf="$INSTALL_DIR/instances/$name/config.json"
                if [ -f "$conf" ]; then
                    stop_bot "$conf"
                else
                    echo -e "${RED}❌ 未找到配置: $conf${NC}"
                fi
                ;;
            5)
                if [ -d "$INSTALL_DIR/instances" ]; then
                    for conf in "$INSTALL_DIR/instances"/bot_*/config.json; do
                        [ -f "$conf" ] && start_bot "$conf"
                    done
                fi
                echo -e "${GREEN}✅ 全部启动完成${NC}"
                ;;
            6)
                pids=$(pgrep -f "forwarder.py" 2>/dev/null)
                if [ -n "$pids" ]; then
                    echo "$pids" | xargs kill 2>/dev/null
                    echo -e "${GREEN}✅ 已停止全部机器人${NC}"
                else
                    echo -e "${YELLOW}⚠️  没有运行中的机器人${NC}"
                fi
                ;;
            7)
                echo -n "  输入要删除的机器人名称 (如 bot_123456789): "
                read -r name
                if [ -d "$INSTALL_DIR/instances/$name" ]; then
                    # 先停掉
                    conf="$INSTALL_DIR/instances/$name/config.json"
                    [ -f "$conf" ] && stop_bot "$conf"
                    # 移动备份
                    mv "$INSTALL_DIR/instances/$name" "$INSTALL_DIR/instances/${name}.deleted.$(date +%Y%m%d%H%M%S)"
                    echo -e "${GREEN}✅ 已删除（配置文件已备份）${NC}"
                else
                    echo -e "${RED}❌ 未找到机器人: $name${NC}"
                fi
                ;;
            8)
                echo -e "${BLUE}[*] 检查最新正式发布版本...${NC}"
                cd "$INSTALL_DIR"
                # Fetch latest release tag from GitHub API
                LATEST=$(curl -sL https://api.github.com/repos/dkgks/tgforwarder/releases/latest 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tag_name',''))" 2>/dev/null)
                if [ -z "$LATEST" ]; then
                    echo -e "${RED}❌ 无法获取最新版本信息，请检查网络后重试${NC}"
                    cd - >/dev/null
                    continue
                else
                    CURRENT=$(git describe --tags --abbrev=0 2>/dev/null || echo "(未知)")
                    echo "  当前版本: $CURRENT"
                    echo "  最新版本: $LATEST"
                    if [ "$CURRENT" = "$LATEST" ]; then
                        echo -e "${GREEN}  ✅ 已是最新版本，无需更新${NC}"
                    else
                        echo -e "${BLUE}[*] 从 GitHub Releases 下载 $LATEST ...${NC}"
                        TMPDIR=$(mktemp -d)
                        # Download zipball, extract, and move code files only
                        curl -sL "https://github.com/dkgks/tgforwarder/archive/refs/tags/$LATEST.tar.gz" -o "$TMPDIR/release.tar.gz"
                        tar xzf "$TMPDIR/release.tar.gz" -C "$TMPDIR"
                        # Find the extracted directory (prefix varies)
                        SRC=$(find "$TMPDIR" -maxdepth 1 -type d -name "tgforwarder-*" | head -1)
                        if [ -d "$SRC" ]; then
                            # Only copy code files (.py .sh .example.json), never data files
                            echo "  正在更新代码文件..."
                            for f in forwarder.py weekly_report.py tgfwd.sh keywords.example.json config.example.json; do
                                if [ -f "$SRC/$f" ]; then
                                    cp "$SRC/$f" "$INSTALL_DIR/$f"
                                    echo "    ✅ $f"
                                fi
                            done
                            # Update .gitignore too
                            [ -f "$SRC/.gitignore" ] && cp "$SRC/.gitignore" "$INSTALL_DIR/.gitignore" && echo "    ✅ .gitignore"
                            # Sync git tag to local repo for version tracking
                            git fetch origin --tags 2>/dev/null || true
                            echo -e "${GREEN}✅ 更新到 $LATEST 完成${NC}"
                            echo -e "${YELLOW}  💡 你的 config.json、keywords.json、state.json 等数据文件未被修改${NC}"
                        else
                            echo -e "${RED}❌ 解包失败，更新终止${NC}"
                        fi
                        rm -rf "$TMPDIR"
                    fi
                fi
                # 重启所有运行中的机器人
                echo -e "${BLUE}[*] 重启运行中的机器人...${NC}"
                for conf in "$INSTALL_DIR/instances"/bot_*/config.json; do
                    [ -f "$conf" ] && stop_bot "$conf" && start_bot "$conf"
                done
                cd - >/dev/null
                ;;
            0)
                echo "再见！"
                exit 0
                ;;
            *)
                echo -e "${RED}❌ 无效选择${NC}"
                ;;
        esac
    done
}

# ============================================================
# 首次安装入口
# ============================================================
first_setup() {
    print_banner

    echo "本脚本将完成以下步骤："
    echo "  1. 检查 Python 环境"
    echo "  2. 安装依赖包"
    echo "  3. 下载项目代码"
    echo "  4. 配置第一个机器人"
    echo "  5. 启动机器人"
    echo ""
    echo -n "是否继续? [Y/n]: "
    read -r CONFIRM
    if [[ "$CONFIRM" =~ ^[Nn]$ ]]; then
        echo "已取消"
        exit 0
    fi

    check_python
    install_deps
    download_code

    mkdir -p "$INSTALL_DIR/logs"
    mkdir -p "$INSTALL_DIR/instances"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  配置第一个机器人${NC}"
    echo -e "${GREEN}========================================${NC}"
    add_bot

    echo ""
    echo -n "是否立即启动? [Y/n]: "
    read -r START_NOW
    if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then
        conf="$INSTALL_DIR/instances/bot_${OWNER_ID}/config.json"
        start_bot "$conf"
    fi

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  安装完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "  管理菜单: bash $INSTALL_DIR/tgfwd.sh"
    echo "  手动运行: python3 $INSTALL_DIR/forwarder.py 实例路径/config.json"
    echo "  Bot Token 获取: https://t.me/BotFather"
    echo "  用户ID 获取: https://t.me/userinfobot"
    echo "  OpenRouter API 密钥: https://openrouter.ai"
    echo "  SiliconFlow API 密钥: https://siliconflow.cn"
    echo ""
}

# ============================================================
# 入口判断
# ============================================================
if [ ! -d "$INSTALL_DIR" ]; then
    first_setup
else
    print_banner
    main_menu
fi
