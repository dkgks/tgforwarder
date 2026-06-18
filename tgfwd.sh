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
configure_ai() {
    echo ""
    echo -e "${YELLOW}┌──────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│  AI 智能识别功能说明                          │${NC}"
    echo -e "${YELLOW}│                                              │${NC}"
    echo -e "${YELLOW}│  • 自动判断广告/脏话，无需手动设关键词         │${NC}"
    echo -e "${YELLOW}│  • 对脏话自动回骂（AI 生成犀利反击）          │${NC}"
    echo -e "${YELLOW}│  • 对广告自动分级警告                         │${NC}"
    echo -e "${YELLOW}│  • 10 条消息审核期，通过后直接转发             │${NC}"
    echo -e "${YELLOW}│                                              │${NC}"
    echo -e "${YELLOW}│  ⚠️  不启用 AI：只有关键词屏蔽，无智能识别    │${NC}"
    echo -e "${YELLOW}│                                              │${NC}"
    echo -e "${YELLOW}│  推荐 AI 平台：OpenRouter (openrouter.ai)     │${NC}"
    echo -e "${YELLOW}│  大量免费模型，每天成本为 0                    │${NC}"
    echo -e "${YELLOW}│  也支持 SiliconFlow (siliconflow.cn)            │${NC}"
    echo -e "${YELLOW}│  国内优化，注册送额度                          │${NC}"
    echo -e "${YELLOW}│                                              │${NC}"
    echo -e "${YELLOW}│  每日费用：免费 ~ ¥0.5（视平台和消息量）        │${NC}"
    echo -e "${YELLOW}└──────────────────────────────────────────────┘${NC}"
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
    print(f'    {i:2d}.  {pid}  (上下文: {ctx})')
print('    提示：可以输入序号选择模型，也可以直接输入模型名')
" 2>/dev/null || echo "    ⚠️  获取模型列表失败，请手动输入模型名")

            echo ""
            echo "  $FREE_MODELS"
            echo ""
            echo -n "  分类模型 [默认 google/gemini-2.5-flash-lite]: "
            read -r CLASSIFY_INPUT
            if [ -z "$CLASSIFY_INPUT" ]; then
                CLASSIFY_MODEL="google/gemini-2.5-flash-lite"
            elif [[ "$CLASSIFY_INPUT" =~ ^[0-9]+$ ]]; then
                CLASSIFY_MODEL=$(echo "$FREE_MODELS" | sed -n "${CLASSIFY_INPUT}p" | sed 's/^[[:space:]]*[0-9]*\. *//;s/(.*//' | xargs)
                CLASSIFY_MODEL=${CLASSIFY_MODEL:-google/gemini-2.5-flash-lite}
            else
                CLASSIFY_MODEL="$CLASSIFY_INPUT"
            fi

            echo -n "  回骂模型 [默认 google/gemini-2.5-flash-lite]: "
            read -r INSULT_INPUT
            if [ -z "$INSULT_INPUT" ]; then
                INSULT_MODEL="google/gemini-2.5-flash-lite"
            elif [[ "$INSULT_INPUT" =~ ^[0-9]+$ ]]; then
                INSULT_MODEL=$(echo "$FREE_MODELS" | sed -n "${INSULT_INPUT}p" | sed 's/^[[:space:]]*[0-9]*\. *//;s/(.*//' | xargs)
                INSULT_MODEL=${INSULT_MODEL:-google/gemini-2.5-flash-lite}
            else
                INSULT_MODEL="$INSULT_INPUT"
            fi
        fi
    fi
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

    configure_ai

    INSTANCE_DIR="$INSTALL_DIR/instances/bot_${OWNER_ID}"
    mkdir -p "$INSTANCE_DIR"

    echo -e "${BLUE}[*] 生成配置文件...${NC}"
    python3 -c "
import json, os, shutil

cfg_path = '$INSTANCE_DIR/config.json'
base_cfg_path = '$INSTALL_DIR/config.example.json'
kwd_path = '$INSTANCE_DIR/keywords.json'
kwd_src = '$INSTALL_DIR/keywords.example.json'

if os.path.exists(base_cfg_path):
    with open(base_cfg_path) as f:
        cfg = json.load(f)
else:
    cfg = {}

cfg['bot_token'] = '$BOT_TOKEN'
cfg['owner_id'] = int('$OWNER_ID')

# AI settings
if '$AI_ENABLED' == 'true':
    cfg.setdefault('ai', {})
    cfg['ai']['enabled'] = True
    if '$AI_KEY':
        cfg['ai']['api_key'] = '$AI_KEY'
    if '$AI_BASE':
        cfg['ai']['base_url'] = '$AI_BASE'
    if '$AI_PLATFORM':
        cfg['ai']['platform'] = '$AI_PLATFORM'
    if '$CLASSIFY_MODEL':
        cfg['ai']['classify_model'] = '$CLASSIFY_MODEL'
    if '$INSULT_MODEL':
        cfg['ai']['insult_model'] = '$INSULT_MODEL'

with open(cfg_path, 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

# Copy keywords
if os.path.exists(kwd_src):
    shutil.copy(kwd_src, kwd_path)
elif os.path.exists('$INSTALL_DIR/keywords.json'):
    shutil.copy('$INSTALL_DIR/keywords.json', kwd_path)

print('Done')
"

    echo -e "${GREEN}✅ 机器人配置完成: $INSTANCE_DIR${NC}"
}

# ============================================================
start_bot() {
    local CONF="${1:-}"
    if [ -z "$CONF" ]; then
        echo -e "${RED}❌ 未指定配置文件${NC}"
        return 1
    fi

    local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$CONF').encode()).hexdigest()[:8])")
    local SVC_NAME="tg-forwarder@${TAG}"

    # Check if already running
    if systemctl --user is-active "$SVC_NAME" &>/dev/null 2>&1; then
        echo -e "${YELLOW}  机器人已在运行: $SVC_NAME${NC}"
        return 0
    fi

    # Detect service type (user or system)
    local SCOPE=""
    if systemctl --user status "$SVC_NAME" &>/dev/null 2>&1 || [ -d "$HOME/.config/systemd/user" ]; then
        SCOPE="user"
    elif [ -w "/etc/systemd/system" ]; then
        SCOPE="system"
    else
        SCOPE="user"
    fi

    if [ "$SCOPE" = "user" ]; then
        mkdir -p "$HOME/.config/systemd/user"
        cat > "$HOME/.config/systemd/user/${SVC_NAME}.service" << UNIT
[Unit]
Description=TG Forwarder Bot (${TAG})
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/forwarder.py $CONF
Restart=always
RestartSec=5
Environment=HOME=$HOME
StandardOutput=append:$INSTALL_DIR/logs/${TAG}.log
StandardError=append:$INSTALL_DIR/logs/${TAG}.log

[Install]
WantedBy=default.target
UNIT
        systemctl --user daemon-reload
        systemctl --user enable "$SVC_NAME" 2>/dev/null
        systemctl --user start "$SVC_NAME" 2>/dev/null || {
            echo -e "${YELLOW}  ⚠️  user systemd 启动失败，尝试后台运行${NC}"
            nohup python3 "$INSTALL_DIR/forwarder.py" "$CONF" >> "$INSTALL_DIR/logs/${TAG}.log" 2>&1 &
        }
    else
        cat > "/etc/systemd/system/${SVC_NAME}.service" << UNIT
[Unit]
Description=TG Forwarder Bot (${TAG})
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/forwarder.py $CONF
Restart=always
RestartSec=5
User=$USER
StandardOutput=append:$INSTALL_DIR/logs/${TAG}.log
StandardError=append:$INSTALL_DIR/logs/${TAG}.log

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload
        systemctl enable "$SVC_NAME" 2>/dev/null
        systemctl start "$SVC_NAME"
    fi

    sleep 2
    if systemctl --$SCOPE is-active "$SVC_NAME" &>/dev/null 2>&1; then
        echo -e "${GREEN}✅ 机器人已启动: $SVC_NAME${NC}"
    else
        echo -e "${YELLOW}  ⚠️  服务可能未正常启动，请检查日志: tail -f $INSTALL_DIR/logs/${TAG}.log${NC}"
    fi
}

# ============================================================
stop_bot() {
    local TAG="$1"
    if [ -z "$TAG" ]; then
        echo -e "${RED}❌ 未指定机器人${NC}"
        return 1
    fi
    local SVC_NAME="tg-forwarder@${TAG}"

    if systemctl --user is-active "$SVC_NAME" &>/dev/null 2>&1; then
        systemctl --user stop "$SVC_NAME" 2>/dev/null
        systemctl --user disable "$SVC_NAME" 2>/dev/null
        rm -f "$HOME/.config/systemd/user/${SVC_NAME}.service"
        systemctl --user daemon-reload
        echo -e "${GREEN}✅ 已停止用户服务: $SVC_NAME${NC}"
    elif systemctl is-active "$SVC_NAME" &>/dev/null 2>&1; then
        systemctl stop "$SVC_NAME"
        systemctl disable "$SVC_NAME" 2>/dev/null
        rm -f "/etc/systemd/system/${SVC_NAME}.service"
        systemctl daemon-reload
        echo -e "${GREEN}✅ 已停止系统服务: $SVC_NAME${NC}"
    else
        echo -e "${YELLOW}  服务未在运行: $SVC_NAME${NC}"
    fi
}

# ============================================================
list_bots() {
    echo ""
    echo -e "${BLUE}=== 已配置的机器人 ===${NC}"
    if [ -d "$INSTALL_DIR/instances" ]; then
        for d in "$INSTALL_DIR/instances"/bot_*; do
            if [ -f "$d/config.json" ]; then
                local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$d/config.json').encode()).hexdigest()[:8])")
                local SVC="tg-forwarder@${TAG}"
                local STATUS="已停止"
                if systemctl --user is-active "$SVC" &>/dev/null 2>&1; then
                    STATUS="运行中 (user)"
                elif systemctl is-active "$SVC" &>/dev/null 2>&1; then
                    STATUS="运行中 (system)"
                fi
                local OWNER=$(python3 -c "
import json
try:
    with open('$d/config.json') as f:
        d = json.load(f)
    print(d.get('owner_id', '?'))
except:
    print('?')
" 2>/dev/null)
                echo "  📱 bot_${OWNER}  [${TAG}]  ${STATUS}"
            fi
        done
    fi
    echo ""
}

# ============================================================
remove_bot() {
    list_bots
    echo -n "  请输入要删除的机器人 owner_id: "
    read -r OID
    if [ -z "$OID" ]; then
        echo -e "${RED}❌ 输入无效${NC}"
        return 1
    fi

    local DIR="$INSTALL_DIR/instances/bot_${OID}"
    if [ ! -d "$DIR" ]; then
        echo -e "${RED}❌ 未找到机器人 bot_${OID}${NC}"
        return 1
    fi

    local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$DIR/config.json').encode()).hexdigest()[:8])")
    stop_bot "$TAG"

    echo -n "  是否删除配置数据（含关键词、状态、日志）？[y/N]: "
    read -r DELDATA
    if [[ "$DELDATA" =~ ^[Yy]$ ]]; then
        rm -rf "$DIR"
        echo -e "${GREEN}✅ 已删除 bot_${OID} 全部数据${NC}"
    else
        echo -e "${YELLOW}  保留数据目录: $DIR${NC}"
    fi
}

# ============================================================
edit_ai() {
    echo ""
    echo -e "${BLUE}=== 修改 AI 配置 ===${NC}"
    echo "  选择要修改的机器人："
    echo "  0) 返回"
    local BOTS=()
    local i=1
    if [ -d "$INSTALL_DIR/instances" ]; then
        for d in "$INSTALL_DIR/instances"/bot_*; do
            if [ -f "$d/config.json" ]; then
                local OID=$(python3 -c "import json; print(json.load(open('$d/config.json')).get('owner_id','?'))" 2>/dev/null)
                echo "  $i) bot_${OID}"
                BOTS+=("$d/config.json")
                ((i++))
            fi
        done
    fi
    if [ ${#BOTS[@]} -eq 0 ]; then
        echo "  没有已配置的机器人"
        return
    fi
    echo -n "  请选择: "
    read -r CHOICE
    if [ "$CHOICE" = "0" ] || [ -z "$CHOICE" ]; then
        return
    fi
    local IDX=$((CHOICE - 1))
    if [ "$IDX" -ge 0 ] && [ "$IDX" -lt ${#BOTS[@]} ]; then
        local CONF="${BOTS[$IDX]}"
        echo ""
        echo -e "${YELLOW}当前 AI 配置：${NC}"
        python3 -c "
import json
with open('$CONF') as f:
    cfg = json.load(f)
ai = cfg.get('ai', {})
print(f'  平台: {ai.get(\"platform\", \"未设置\")}')
print(f'  API密钥: {\"已设置\" if ai.get(\"api_key\") else \"未设置（仅限免费模型）\"}')
print(f'  Base URL: {ai.get(\"base_url\", \"未设置\")}')
print(f'  分类模型: {ai.get(\"classify_model\", \"未设置\")}')
print(f'  回骂模型: {ai.get(\"insult_model\", \"未设置\")}')
print(f'  AI开关: {\"开启\" if ai.get(\"enabled\") else \"关闭\"}')
"

        echo ""
        echo "  要修改什么？"
        echo "    1) 更换 AI 平台/API密钥/模型"
        echo "    2) 开关 AI 功能"
        echo "    0) 返回"
        echo -n "  请选择: "
        read -r SUB
        if [ "$SUB" = "1" ]; then
            configure_ai
            echo -n "  是否保存并重启机器人？[Y/n]: "
            read -r SAVE
            if [[ ! "$SAVE" =~ ^[Nn]$ ]]; then
                local INST_DIR=$(dirname "$CONF")
                python3 -c "
import json
with open('$CONF') as f:
    cfg = json.load(f)
cfg.setdefault('ai', {})
if '$AI_ENABLED' == 'true':
    cfg['ai']['enabled'] = True
    if '$AI_KEY':
        cfg['ai']['api_key'] = '$AI_KEY'
    if '$AI_BASE':
        cfg['ai']['base_url'] = '$AI_BASE'
    if '$AI_PLATFORM':
        cfg['ai']['platform'] = '$AI_PLATFORM'
    if '$CLASSIFY_MODEL':
        cfg['ai']['classify_model'] = '$CLASSIFY_MODEL'
    if '$INSULT_MODEL':
        cfg['ai']['insult_model'] = '$INSULT_MODEL'
else:
    cfg['ai']['enabled'] = False
with open('$CONF', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('Saved')
"
                local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$CONF').encode()).hexdigest()[:8])")
                local SVC="tg-forwarder@${TAG}"
                if systemctl --user is-active "$SVC" &>/dev/null 2>&1; then
                    systemctl --user restart "$SVC"
                elif systemctl is-active "$SVC" &>/dev/null 2>&1; then
                    systemctl restart "$SVC"
                fi
                echo -e "${GREEN}✅ AI 配置已更新并重启${NC}"
            else
                echo -e "${YELLOW}  配置未保存${NC}"
            fi
        elif [ "$SUB" = "2" ]; then
            # Toggle AI on/off
            python3 -c "
import json
with open('$CONF') as f:
    cfg = json.load(f)
ai = cfg.setdefault('ai', {})
current = ai.get('enabled', False)
ai['enabled'] = not current
with open('$CONF', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('ON' if ai['enabled'] else 'OFF')
" > /tmp/ai_toggle_result
            local TOGGLE=$(cat /tmp/ai_toggle_result 2>/dev/null)
            local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$CONF').encode()).hexdigest()[:8])")
            local SVC="tg-forwarder@${TAG}"
            if systemctl --user is-active "$SVC" &>/dev/null 2>&1; then
                systemctl --user restart "$SVC"
            elif systemctl is-active "$SVC" &>/dev/null 2>&1; then
                systemctl restart "$SVC"
            fi
            if [ "$TOGGLE" = "ON" ]; then
                echo -e "${GREEN}✅ AI 功能已开启并重启${NC}"
            else
                echo -e "${GREEN}✅ AI 功能已关闭并重启${NC}"
            fi
            rm -f /tmp/ai_toggle_result
        fi
    fi
}

# ============================================================
toggle_features() {
    echo ""
    echo -e "${BLUE}=== 开关功能 ===${NC}"
    echo "  选择要操作机器人："
    echo "  0) 返回"
    local BOTS=()
    local i=1
    if [ -d "$INSTALL_DIR/instances" ]; then
        for d in "$INSTALL_DIR/instances"/bot_*; do
            if [ -f "$d/config.json" ]; then
                local OID=$(python3 -c "import json; print(json.load(open('$d/config.json')).get('owner_id','?'))" 2>/dev/null)
                echo "  $i) bot_${OID}"
                BOTS+=("$d/config.json")
                ((i++))
            fi
        done
    fi
    if [ ${#BOTS[@]} -eq 0 ]; then
        echo "  没有已配置的机器人"
        return
    fi
    echo -n "  请选择: "
    read -r CHOICE
    if [ "$CHOICE" = "0" ] || [ -z "$CHOICE" ]; then
        return
    fi
    local IDX=$((CHOICE - 1))
    if [ "$IDX" -lt 0 ] || [ "$IDX" -ge ${#BOTS[@]} ]; then
        return
    fi
    local CONF="${BOTS[$IDX]}"

    echo ""
    echo "  功能开关："
    echo "    1) AI 智能识别    (开关)"
    echo "    2) 关键词屏蔽     (开关)"
    echo "    3) 脏话回骂       (开关)"
    echo "    4) 广告警告       (开关)"
    echo "    0) 返回"
    echo -n "  请选择: "
    read -r SUB

    local KEY=""
    local LABEL=""
    case "$SUB" in
        1) KEY="ai.enabled"; LABEL="AI 智能识别" ;;
        2) KEY="keyword_enabled"; LABEL="关键词屏蔽" ;;
        3) KEY="insult_enabled"; LABEL="脏话回骂" ;;
        4) KEY="ad_warning_enabled"; LABEL="广告警告" ;;
        0) return ;;
        *) return ;;
    esac

    # Toggle
    local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$CONF').encode()).hexdigest()[:8])")
    local SVC="tg-forwarder@${TAG}"
    local MAIN_KEY=$(echo "$KEY" | cut -d. -f1)
    local SUB_KEY=$(echo "$KEY" | cut -d. -f2)

    python3 -c "
import json
with open('$CONF') as f:
    cfg = json.load(f)
section = cfg.setdefault('$MAIN_KEY', {})
if isinstance(section, dict):
    current = section.get('$SUB_KEY', True)
    section['$SUB_KEY'] = not current
    new_state = not current
else:
    new_state = not section
    cfg['$MAIN_KEY'] = new_state
with open('$CONF', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('ON' if new_state else 'OFF')
" > /tmp/feature_toggle_result
    local STATE=$(cat /tmp/feature_toggle_result 2>/dev/null)
    rm -f /tmp/feature_toggle_result

    # Restart
    if systemctl --user is-active "$SVC" &>/dev/null 2>&1; then
        systemctl --user restart "$SVC"
    elif systemctl is-active "$SVC" &>/dev/null 2>&1; then
        systemctl restart "$SVC"
    fi

    if [ "$STATE" = "ON" ]; then
        echo -e "${GREEN}✅ ${LABEL} 已开启并重启${NC}"
    else
        echo -e "${GREEN}✅ ${LABEL} 已关闭并重启${NC}"
    fi
}

# ============================================================
detect_install_method() {
    # Returns: docker | user | system | none
    for d in "$INSTALL_DIR/instances"/bot_*; do
        if [ -f "$d/config.json" ]; then
            local TAG=$(python3 -c "import hashlib, os; print(hashlib.md5(os.path.abspath('$d/config.json').encode()).hexdigest()[:8])" 2>/dev/null)
            if systemctl --user is-enabled "tg-forwarder@${TAG}" &>/dev/null 2>&1; then
                echo "user"
                return
            elif systemctl is-enabled "tg-forwarder@${TAG}" &>/dev/null 2>&1; then
                echo "system"
                return
            fi
        fi
    done
    if docker compose version &>/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'tgforwarder'; then
        echo "docker"
        return
    fi
    echo "none"
}

# ============================================================
# Main menu (for already installed users)
# ============================================================
main_menu() {
    while true; do
        echo ""
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}  管理菜单${NC}"
        echo -e "${BLUE}========================================${NC}"
        local METHOD=$(detect_install_method)
        echo "  安装方式: $METHOD"
        echo ""
        echo "  1) 查看已配置机器人列表"
        echo "  2) 添加新机器人"
        echo "  3) 删除机器人"
        echo "  4) 重启所有机器人"
        echo "  5) 修改 AI 配置"
        echo "  6) 开关功能"
        echo "  7) 更新程序 (git pull)"
        echo "  0) 退出"
        echo ""
        echo -n "  请选择 [0-7]: "
        read -r CHOICE

        case "$CHOICE" in
            1)
                list_bots
                ;;
            2)
                if [ "$METHOD" = "docker" ]; then
                    echo -e "${YELLOW}⚠️  Docker 环境请通过 docker compose 管理${NC}"
                else
                    add_bot
                    echo ""
                    echo -n "  是否立即启动? [Y/n]: "
                    read -r START_NOW
                    if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then
                        local conf="$INSTALL_DIR/instances/bot_${OWNER_ID}/config.json"
                        start_bot "$conf"
                    fi
                fi
                ;;
            3)
                remove_bot
                ;;
            4)
                echo -e "${BLUE}[*] 重启所有机器人...${NC}"
                for svc in $(systemctl list-units --all --no-legend "tg-forwarder@*" 2>/dev/null | awk '{print $1}'); do
                    systemctl restart "$svc" 2>/dev/null && echo "    ✅ $svc"
                done
                for svc in $(systemctl --user list-units --all --no-legend "tg-forwarder@*" 2>/dev/null | awk '{print $1}'); do
                    systemctl --user restart "$svc" 2>/dev/null && echo "    ✅ $svc"
                done
                echo -e "${GREEN}✅ 完成${NC}"
                ;;
            5)
                edit_ai
                ;;
            6)
                toggle_features
                ;;
            7)
                echo -e "${BLUE}[*] 更新代码...${NC}"
                cd "$INSTALL_DIR"
                git pull --ff-only origin main 2>/dev/null && echo -e "${GREEN}✅ 更新完成${NC}" || echo -e "${YELLOW}⚠️  更新失败${NC}"
                # Restart all
                for svc in $(systemctl list-units --all --no-legend "tg-forwarder@*" 2>/dev/null | awk '{print $1}'); do
                    systemctl restart "$svc" 2>/dev/null
                done
                for svc in $(systemctl --user list-units --all --no-legend "tg-forwarder@*" 2>/dev/null | awk '{print $1}'); do
                    systemctl --user restart "$svc" 2>/dev/null
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
# First-time setup
# ============================================================
first_setup() {
    print_banner

    echo "本脚本将完成以下步骤："
    echo "  1. 检查环境"
    echo "  2. 安装依赖"
    echo "  3. 下载代码"
    echo "  4. 配置机器人"
    echo "  5. 启动服务"
    echo ""
    echo "  选择安装方式："
    echo "    1) ★ 推荐：系统服务 (systemd，开机自启，崩溃自动重启)"
    echo "    2) Docker (需已安装 Docker + docker compose)"
    echo "    3) 后台进程 (简单，但重启后不会自动启动)"
    echo ""
    echo -n "  请选择 [1]: "
    read -r METHOD
    METHOD=${METHOD:-1}

    case "$METHOD" in
        2)
            # Docker install
            if ! command -v docker &>/dev/null; then
                echo -e "${RED}❌ 未检测到 Docker，请先安装${NC}"
                exit 1
            fi
            if ! docker compose version &>/dev/null 2>&1; then
                echo -e "${RED}❌ 未检测到 docker compose 插件${NC}"
                exit 1
            fi

            echo -n "  请输入 Bot Token（从 @BotFather 获取）: "
            read -r BOT_TOKEN
            echo -n "  请输入管理员 Telegram 用户ID: "
            read -r OWNER_ID

            if ! command -v git &>/dev/null; then
                echo -e "${RED}❌ 请先安装 git${NC}"
                exit 1
            fi

            if [ -d "tgforwarder" ]; then
                cd tgforwarder
                git pull origin main 2>/dev/null || true
                cd - >/dev/null
            else
                git clone "$GIT_REPO"
            fi

            cd tgforwarder
            mkdir -p data
            python3 -c "
import json
with open('config.example.json') as f:
    cfg = json.load(f)
cfg['bot_token'] = '$BOT_TOKEN'
cfg['owner_id'] = int('$OWNER_ID')
with open('data/config.json', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
"

            echo ""
            configure_ai

            if [ "$AI_ENABLED" = "true" ]; then
                python3 -c "
import json
with open('data/config.json') as f:
    cfg = json.load(f)
cfg.setdefault('ai', {})
cfg['ai']['enabled'] = True
if '$AI_KEY': cfg['ai']['api_key'] = '$AI_KEY'
if '$AI_BASE': cfg['ai']['base_url'] = '$AI_BASE'
if '$AI_PLATFORM': cfg['ai']['platform'] = '$AI_PLATFORM'
if '$CLASSIFY_MODEL': cfg['ai']['classify_model'] = '$CLASSIFY_MODEL'
if '$INSULT_MODEL': cfg['ai']['insult_model'] = '$INSULT_MODEL'
with open('data/config.json', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
"
            fi

            docker compose up -d
            echo -e "${GREEN}========================================${NC}"
            echo -e "${GREEN}  🎉 Docker 部署完成！${NC}"
            echo -e "${GREEN}========================================${NC}"
            echo "  📱 发送 /menu 给机器人打开管理面板"
            echo "  📋 docker compose logs -f"
            echo "  🔄 docker compose restart"
            echo "  🛑 docker compose down"
            cd - >/dev/null
            ;;

        3)
            # Background process install
            check_python
            install_deps
            download_code

            add_bot
            echo ""
            echo -n "  是否立即启动? [Y/n]: "
            read -r START_NOW
            if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then
                local conf="$INSTALL_DIR/instances/bot_${OWNER_ID}/config.json"
                nohup python3 "$INSTALL_DIR/forwarder.py" "$conf" >> "$INSTALL_DIR/logs/bg.log" 2>&1 &
                echo -e "${GREEN}✅ 后台已启动 (PID $!)${NC}"
                echo -e "${YELLOW}⚠️  关闭终端后服务会停止。下次启动使用管理脚本${NC}"
            fi
            echo ""
            echo -e "${GREEN}管理: bash $INSTALL_DIR/tgfwd.sh${NC}"
            ;;

        *)
            # Default: systemd service install
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
            echo -n "  是否立即启动? [Y/n]: "
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
            ;;

    esac
}

# ============================================================
# Entry point
# ============================================================
print_banner

if [ ! -d "$INSTALL_DIR" ]; then
    first_setup
else
    echo ""
    METHOD=$(detect_install_method)
    if [ "$METHOD" = "none" ]; then
        echo -e "${YELLOW}⚠️  检测到已安装但未找到运行中的服务。${NC}"
        echo "  请选择："
        echo "    1) 进入管理菜单"
        echo "    2) 重新配置"
        echo -n "  [1]: "
        read -r C
        if [ "$C" = "2" ]; then
            first_setup
        else
            main_menu
        fi
    else
        echo -e "${GREEN}检测到安装方式: $METHOD${NC}"
        main_menu
    fi
fi