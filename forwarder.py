#!/usr/bin/env python3
"""
Telegram Message Forwarder with AI Spam/Abuse Filter

Multi-instance ready: each instance points to its own config file.
Usage: python3 forwarder.py [config.json]
  Default config: ./config.json

Features:
- Keyword-based abuse/spam filtering (always active)
- Optional AI classification and auto-insult (requires API key)
- Owner-only management panel via inline buttons
- User approval workflow (10-message filtering window)
- Blocked user management, auto-reply, keyword management
"""

import asyncio, json, logging, os, signal, sys
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ============================================================
# Config loading
DEFAULT_CONFIG = "./config.json"
CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ 无法加载配置文件 {CONFIG_PATH}: {e}")
        sys.exit(1)

def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)

cfg = load_config()

# === Global constants from config ===
NEW_BOT_TOKEN = cfg["bot_token"]
OWNER_ID = cfg["owner_id"]

AI_ENABLED = cfg.get("ai", {}).get("enabled", False)
AI_API_KEY = cfg.get("ai", {}).get("api_key", "")
AI_BASE_URL = cfg.get("ai", {}).get("base_url", "https://api.siliconflow.cn/v1")
AI_PLATFORM = cfg.get("ai", {}).get("platform", "openrouter")  # siliconflow or openrouter
CLASSIFY_MODEL = cfg.get("ai", {}).get("classify_model", "qwen/qwen3-next-80b-a3b-instruct:free")
INSULT_MODEL = cfg.get("ai", {}).get("insult_model", "google/gemma-4-31b-it:free")

# Per-instance data files (derived from config path)
INSTANCE_DIR = os.path.dirname(os.path.abspath(CONFIG_PATH))
STATE_FILE = os.path.join(INSTANCE_DIR, "state.json")
STATS_FILE = os.path.join(INSTANCE_DIR, "stats.json")
AUTO_REPLY_FILE = os.path.join(INSTANCE_DIR, "auto_reply.json")
WELCOME_MSG = cfg.get("welcome_msg", "👋 你好，这是消息转发助手。\n\n你的留言将会被转发给管理员，他会通过这个机器人回复你。\n⚠️ 请勿发送广告或骚扰信息。\n\n有什么想说的，直接发过来吧~")
KEYWORDS_FILE = os.path.join(INSTANCE_DIR, "keywords.json")

# === Weekly stats (ads blocked + abuse replies) ===
def load_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ads_blocked": 0, "abuse_replies": 0}

def save_stats(s):
    with open(STATS_FILE + ".tmp", "w") as f:
        json.dump(s, f)
    os.replace(STATS_FILE + ".tmp", STATS_FILE)

def stats_add(key, n=1):
    s = load_stats()
    s[key] = s.get(key, 0) + n
    save_stats(s)
    logger.info(f"Stats: {key} += {n} (now {s[key]})")

def stats_get_and_reset():
    """Return current stats and reset to zero."""
    s = load_stats()
    saved = {"ads_blocked": 0, "abuse_replies": 0}
    save_stats(saved)
    logger.info("Stats reset - was: ads_blocked=%r, abuse_replies=%r", s.get("ads_blocked", 0), s.get("abuse_replies", 0))
    return s
LOG_FILE = "/root/.openclaw/forwarder/forwarder.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


from datetime import datetime, timezone

def datetime_iso():
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, path):
        self.path = path
        self.users: dict[int, dict] = {}
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                self.users = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"Loaded state: {len(self.users)} users")
        except (FileNotFoundError, json.JSONDecodeError):
            self.users = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(k): v for k, v in self.users.items()}, f, indent=2)
        os.replace(tmp, self.path)

    def get(self, uid):
        return self.users.get(uid, {"msgs_checked": 0, "approved": False, "spam_count": 0,
                                    "full_name": "", "username": "", "first_seen": "",
                                    "blocked": False, "auto_reply_used": 0})

    def update(self, uid, **kw):
        entry = self.get(uid)
        entry.update(kw)
        self.users[uid] = entry
        self.save()

    def ensure_user(self, uid, full_name="", username=""):
        """Ensure user exists with basic info; call on every message."""
        entry = self.get(uid)
        if not entry.get("first_seen"):
            entry["first_seen"] = datetime_iso()
        if full_name and not entry.get("full_name"):
            entry["full_name"] = full_name
        if username and not entry.get("username"):
            entry["username"] = username
        self.users[uid] = entry
        self.save()
        return entry


state = State(STATE_FILE)

# === Auto-reply config ===
def load_auto_reply():
    try:
        with open(AUTO_REPLY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "text": "收到你的消息了，管理员看到后会回复你", "max_count": 1}

def save_auto_reply(cfg):
    with open(AUTO_REPLY_FILE + ".tmp", "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(AUTO_REPLY_FILE + ".tmp", AUTO_REPLY_FILE)


reply_map: dict[int, int] = {}

# === Keyword management (dynamic, persisted) ===
def load_keywords():
    try:
        with open(KEYWORDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"abuse": [], "spam": []}

def save_keywords(kw):
    with open(KEYWORDS_FILE + ".tmp", "w") as f:
        json.dump(kw, f, ensure_ascii=False, indent=2)
    os.replace(KEYWORDS_FILE + ".tmp", KEYWORDS_FILE)

def add_keyword(kind: str, word: str):
    kw = load_keywords()
    if word not in kw[kind]:
        kw[kind].append(word)
        save_keywords(kw)
        refresh_kw_sets()

def remove_keyword(kind: str, word: str):
    kw = load_keywords()
    if word in kw[kind]:
        kw[kind].remove(word)
        save_keywords(kw)
        refresh_kw_sets()

ABUSE_KW: set = set()
SPAM_KW: set = set()

def refresh_kw_sets():
    kw = load_keywords()
    ABUSE_KW.clear()
    ABUSE_KW.update(kw.get("abuse", []))
    SPAM_KW.clear()
    SPAM_KW.update(kw.get("spam", []))

refresh_kw_sets()  # init on import


def local_check(text: str) -> str:
    """Fast local pre-check. Returns 'ABUSE', 'SPAM', 'MAYBE', or 'OK'."""
    if len(text.strip()) < 2:
        return "OK"
    t = text.lower().replace(" ", "")
    # Check abuse first (higher priority)
    for kw in ABUSE_KW:
        if kw in t:
            return "ABUSE"
    # Check spam
    for kw in SPAM_KW:
        if kw in t:
            return "SPAM"
    return "MAYBE"  # need AI to decide


async def ai_classify(text: str) -> str:
    """Cheap model (Qwen2.5-7B) to classify message. Returns SPAM/ABUSE/SPAM+ABUSE/OK."""
    prompt = f"""判断以下消息类别：
ABUSE = 辱骂/脏话/人身攻击
SPAM = 广告/推广/拉群/可疑链接
SPAM+ABUSE = 同时包含广告和辱骂
OK = 正常消息

只回答一个词：SPAM / ABUSE / SPAM+ABUSE / OK

消息：{text}"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
                json={
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "messages": [
                        {"role": "system", "content": "你是一个消息分类器。只回答一个标签词。"},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 10,
                    "temperature": 0
                }
            )
            if r.status_code == 200:
                ans = r.json()["choices"][0]["message"]["content"].strip().upper()
                for lbl in ["SPAM+ABUSE", "SPAM", "ABUSE", "OK"]:
                    if lbl in ans:
                        return lbl
                return "OK"
    except Exception as e:
        logger.error(f"AI classify error: {e}")
    return "OK"


async def ai_generate_insult(target_text: str) -> str:
    """DeepSeek V4-Flash: fast, savage insult generation."""
    prompt = f"""对方发来了辱骂消息，请你用最恶毒、最有创意的话回骂对方。要犀利、扎心、让对方无地自容。用中文回复，控制在 80 字以内。

对方的辱骂内容：{target_text}

你的回骂（直接回复）："""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
                json={
                    "model": "deepseek-ai/DeepSeek-V4-Flash",
                    "messages": [
                        {"role": "system", "content": "你是一个嘴毒心狠的吵架高手，擅长用最扎心的中文回骂。回复简洁有力，不超过 80 字。"},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 200,
                    "temperature": 0.9
                }
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"AI insult error: {e}")
    return "你这种人活着就是浪费空气。"


async def send_msg(chat_id: int, text: str) -> bool:
    """Send a message via the new bot."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text}
            )
            return r.status_code == 200 and r.json().get("ok")
    except Exception as e:
        logger.error(f"send_msg error: {e}")
    return False


async def forward_to_owner(uid: int, name: str, username: str, text: str):
    """Forward legit message to owner."""
    uname = f" @{username}" if username else ""
    msg = f"📩 **{name}**{uname}\n🆔 `{uid}`\n\n{text[:3500]}"

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/sendMessage",
                json={"chat_id": OWNER_ID, "text": msg, "parse_mode": "Markdown"}
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    fwd_id = data["result"]["message_id"]
                    reply_map[fwd_id] = uid
                    logger.info(f"Forwarded to owner msg#{fwd_id} ← user {uid}")
    except Exception as e:
        logger.error(f"Forward to owner error: {e}")


async def handle_stranger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    user = msg.from_user
    if user.id == OWNER_ID:
        # Owner reply check
        reply = msg.reply_to_message
        if reply and reply.message_id in reply_map:
            sid = reply_map[reply.message_id]
            await send_msg(sid, msg.text)
            logger.info(f"Owner replied to stranger {sid}")
        return

    text = msg.text

    # Ensure user record exists with name info
    us = state.ensure_user(user.id, user.full_name or "", user.username or "")

    # === 0. Blocked user check ===
    if us.get("blocked"):
        await send_msg(user.id, "你已被拉黑")
        logger.info(f"Blocked user {user.id} attempted message → replied '你已被拉黑'")
        return

    checked = us.get("msgs_checked", 0)
    spam_c = us.get("spam_count", 0)

    # === 1. Fast local pre-check for abuse keywords ===
    local = local_check(text)
    if local == "ABUSE":
        # Clear abuse → insult if AI enabled, otherwise generic reply
        checked += 1
        if AI_ENABLED:
            insult = await ai_generate_insult(text)
        else:
            insult = "请注意言辞，辱骂信息已被屏蔽。"
        await send_msg(user.id, insult)
        stats_add("abuse_replies")
        state.update(user.id, msgs_checked=checked, spam_count=spam_c)
        logger.info(f"ABUSE from {user.id} (local hit) → replied")
        return

    # === 2. Fast local pre-check for spam keywords ===
    if local == "SPAM":
        spam_c += 1
        if spam_c == 1:
            stats_add("ads_blocked")
            await send_msg(user.id, "请勿发送广告。")
        elif spam_c == 2:
            stats_add("ads_blocked")
            await send_msg(user.id, "请注意言辞，再发广告将被拉黑。")
        else:
            stats_add("ads_blocked")
            await send_msg(user.id, "傻逼")
        checked += 1
        state.update(user.id, msgs_checked=checked, spam_count=spam_c)
        logger.info(f"SPAM from {user.id} (local hit, spam #{spam_c}) → auto-replied")
        return

    # === 3. Approved user, no local hit → forward directly ===
    if us.get("approved"):
        checked += 1
        logger.info(f"User {user.id} msg#{checked}/10 → OK (approved): {text[:100]}")
        state.update(user.id, msgs_checked=checked, spam_count=spam_c)
        await forward_to_owner(user.id, user.full_name, user.username, text)

        # Auto-reply for approved users (if enabled and count not exhausted)
        auto_cfg = load_auto_reply()
        if auto_cfg.get("enabled"):
            max_cnt = auto_cfg.get("max_count", 1)
            used = us.get("auto_reply_used", 0)
            if used < max_cnt:
                await send_msg(user.id, auto_cfg["text"])
                state.update(user.id, auto_reply_used=used + 1)
                logger.info(f"Auto-reply #{used+1}/{max_cnt} to user {user.id}")
        return

    # === 4. Not approved, no local hit → classify ===
    if AI_ENABLED:
        label = await ai_classify(text)
    else:
        label = "OK"  # Without AI, treat unclassified messages as legitimate
    checked += 1
    logger.info(f"User {user.id} msg#{checked}/10 → {label}: {text[:100]}")

    if "ABUSE" in label:
        # AI found abuse → insult
        if AI_ENABLED:
            insult = await ai_generate_insult(text)
        else:
            insult = "请注意言辞，辱骂信息已被屏蔽。"
        await send_msg(user.id, insult)
        stats_add("abuse_replies")
        state.update(user.id, msgs_checked=checked, spam_count=spam_c)
        logger.info(f"ABUSE from {user.id} (classified) → replied")
        return

    if label == "SPAM":
        spam_c += 1
        if spam_c == 1:
            stats_add("ads_blocked")
            await send_msg(user.id, "请勿发送广告。")
        elif spam_c == 2:
            stats_add("ads_blocked")
            await send_msg(user.id, "请注意言辞，再发广告将被拉黑。")
        else:
            stats_add("ads_blocked")
            await send_msg(user.id, "傻逼")
        state.update(user.id, msgs_checked=checked, spam_count=spam_c)
        logger.info(f"SPAM from {user.id} (AI detected, spam #{spam_c}) → auto-replied")
        return

    # === 5. OK → approve and forward ===
    state.update(user.id, msgs_checked=checked, approved=True, spam_count=spam_c)
    logger.info(f"User {user.id} APPROVED")
    await forward_to_owner(user.id, user.full_name, user.username, text)

    # Auto-reply for newly approved user
    auto_cfg = load_auto_reply()
    if auto_cfg.get("enabled"):
        max_cnt = auto_cfg.get("max_count", 1)
        used = us.get("auto_reply_used", 0)
        if used < max_cnt:
            await send_msg(user.id, auto_cfg["text"])
            state.update(user.id, auto_reply_used=used + 1)
            logger.info(f"Auto-reply #{used+1}/{max_cnt} to new user {user.id}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id == OWNER_ID:
        await update.message.reply_text(
            "你好主人！转发服务已启动。\n\n"
            "别人的消息会自动转发给你。\n"
            "你直接回复转发的消息即可回复给对方。"
        )
    else:
        await update.message.reply_text(
            "👋 你好，这是黑光的消息转发助手。\n\n"
            "你的留言将会被转发给黑光，他会通过这个机器人回复你。\n"
            "⚠️ 请勿发送广告或骚扰信息。\n\n"
            "有什么想说的，直接发过来吧~"
        )


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    try:
        parts = msg.text.split(None, 2)
        if len(parts) < 3:
            await msg.reply_text("用法: /reply <用户ID> <消息>")
            return
        uid = int(parts[1])
        text = parts[2]
        ok = await send_msg(uid, text)
        if ok:
            await msg.reply_text(f"✅ 已回复用户 {uid}")
        else:
            await msg.reply_text(f"❌ 回复失败（用户可能已拉黑机器人）")
    except ValueError:
        await msg.reply_text("用户ID必须是数字")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: /status to see all tracked users."""
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    if not state.users:
        await msg.reply_text("暂无追踪用户。")
        return
    lines = []
    for uid, u in state.users.items():
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        uname = f" (@{u['username']})" if u.get("username") else ""
        if u.get("blocked"):
            status = "🚫 已拉黑"
        elif u.get("approved"):
            status = "✅ 已批准"
        else:
            status = f"🔍 过滤中({u.get('msgs_checked',0)}/10)"
        spam = f" 🛡️×{u.get('spam_count',0)}" if u.get('spam_count', 0) > 0 else ""
        lines.append(f"`{uid}` {name}{uname} — {status}{spam}")
    await msg.reply_text(f"📊 追踪用户 ({len(state.users)})：\n" + "\n".join(lines[:30]), parse_mode="Markdown")


# ============================================================
#  MENU SYSTEM
# ============================================================

USERS_PER_PAGE = 5

MENU_INLINE_BOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("👥 用户列表", callback_data="users_p0"),
     InlineKeyboardButton("📊 统计数据", callback_data="stats")],
    [InlineKeyboardButton("🚫 拉黑列表", callback_data="blocked_p0"),
     InlineKeyboardButton("⚙️ 设置", callback_data="settings")],
    [InlineKeyboardButton("❌ 关闭菜单", callback_data="close_menu")],
])


def build_user_list(page=0):
    """Build user list with inline buttons for owner."""
    users = list(state.users.items())
    total_pages = max(1, (len(users) + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * USERS_PER_PAGE
    end = start + USERS_PER_PAGE

    text_lines = [f"👥 **用户列表** ({len(users)}人) 第{page+1}/{total_pages}页\n"]
    keyboard_rows = []

    for uid, u in users[start:end]:
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        uname = f" (@{u['username']})" if u.get("username") else ""
        if u.get("blocked"):
            st = "🚫 已拉黑"
        elif u.get("approved"):
            st = "✅ 已批准"
        else:
            st = f"🔍 审核中({u.get('msgs_checked',0)}/10)"
        spam = f" 🛡️×{u.get('spam_count',0)}" if u.get('spam_count', 0) > 0 else ""
        text_lines.append(f"• {name}{uname} — `{uid}` — {st}{spam}")

        # Action buttons per user
        btns = []
        if u.get("blocked"):
            btns.append(InlineKeyboardButton(f"🔓 解封 {uid}", callback_data=f"unblock_{uid}"))
        else:
            if not u.get("approved"):
                btns.append(InlineKeyboardButton(f"✅ 批准 {uid}", callback_data=f"approve_{uid}"))
            btns.append(InlineKeyboardButton(f"🚫 拉黑 {uid}", callback_data=f"block_{uid}"))
        btns.append(InlineKeyboardButton(f"🔄 重置 {uid}", callback_data=f"reset_{uid}"))
        keyboard_rows.append(btns)

    # Pagination row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"users_p{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"users_p{page+1}"))
    if nav:
        keyboard_rows.append(nav)

    # Back to menu
    keyboard_rows.append([InlineKeyboardButton("↩ 返回菜单", callback_data="menu")])

    text = "\n".join(text_lines)
    markup = InlineKeyboardMarkup(keyboard_rows)
    return text, markup


def build_blocked_list(page=0):
    """Build blocked users list."""
    blocked = [(uid, u) for uid, u in state.users.items() if u.get("blocked")]
    total_pages = max(1, (len(blocked) + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * USERS_PER_PAGE
    end = start + USERS_PER_PAGE

    if not blocked:
        text = "🚫 拉黑列表为空"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回菜单", callback_data="menu")]])
        return text, markup

    text_lines = [f"🚫 **拉黑列表** ({len(blocked)}人) 第{page+1}/{total_pages}页\n"]
    keyboard_rows = []

    for uid, u in blocked[start:end]:
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        uname = f" (@{u['username']})" if u.get("username") else ""
        text_lines.append(f"• {name}{uname} — `{uid}`")
        keyboard_rows.append([InlineKeyboardButton(f"🔓 解封 {uid}", callback_data=f"unblock_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"blocked_p{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"blocked_p{page+1}"))
    if nav:
        keyboard_rows.append(nav)
    keyboard_rows.append([InlineKeyboardButton("↩ 返回菜单", callback_data="menu")])

    text = "\n".join(text_lines)
    markup = InlineKeyboardMarkup(keyboard_rows)
    return text, markup


def build_stats_panel():
    """Build stats panel."""
    s = load_stats()
    ads = s.get("ads_blocked", 0)
    abuse = s.get("abuse_replies", 0)
    total = len(state.users)
    approved = sum(1 for u in state.users.values() if u.get("approved") and not u.get("blocked"))
    blocked = sum(1 for u in state.users.values() if u.get("blocked"))
    pending = total - approved - blocked

    text = (
        f"📊 **统计数据**\n\n"
        f"🛡️ 本周屏蔽广告：**{ads}** 条\n"
        f"🤬 本周回骂辱骂：**{abuse}** 人次\n\n"
        f"👥 累计用户：{total}\n"
        f"  ✅ 已批准：{approved}\n"
        f"  🚫 已拉黑：{blocked}\n"
        f"  🔍 待审核：{pending}"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回菜单", callback_data="menu")]])
    return text, markup


def build_auto_reply_panel():
    """Build auto-reply settings panel."""
    cfg = load_auto_reply()
    enabled = cfg.get("enabled", False)
    max_cnt = cfg.get("max_count", 1)
    text_val = cfg.get("text", "")

    status = "✅ 开启" if enabled else "❌ 关闭"
    text_lines = [
        f"🤖 **自动回复设置**\n",
        f"当前状态：{status}",
        f"回复次数上限：{max_cnt} 次/人",
        f"\n📝 当前文案：\n{text_val}",
    ]

    row1 = [
        InlineKeyboardButton("✅ 开启" if not enabled else "❌ 关闭", callback_data="autoreply_toggle"),
    ]
    row2 = [
        InlineKeyboardButton("➖ 次数", callback_data="autoreply_cnt_dec"),
        InlineKeyboardButton(f"{max_cnt}", callback_data="autoreply_cnt_show"),
        InlineKeyboardButton("次数 ➕", callback_data="autoreply_cnt_inc"),
    ]
    row3 = [
        InlineKeyboardButton("✏️ 改文案(发消息)", callback_data="autoreply_edit"),
    ]
    row4 = [InlineKeyboardButton("↩ 返回菜单", callback_data="menu")]

    text = "\n".join(text_lines)
    markup = InlineKeyboardMarkup([row1, row2, row3, row4])
    return text, markup


def build_welcome_panel():
    """Build welcome message settings panel."""
    text_lines = [
        "💬 **欢迎词设置**\n",
        f"当前欢迎词：\n{WELCOME_MSG}",
    ]
    row = [
        InlineKeyboardButton("✏️ 修改欢迎词(发消息)", callback_data="welcome_edit"),
    ]
    row2 = [InlineKeyboardButton("↩ 返回设置", callback_data="settings")]
    text = "\n".join(text_lines)
    markup = InlineKeyboardMarkup([row, row2])
    return text, markup


def build_settings_menu():
    """Settings sub-menu."""
    kw = load_keywords()
    na = len(kw.get("abuse", []))
    ns = len(kw.get("spam", []))
    text = (
        "⚙️ **设置**\n\n"
        "请选择要配置的项："
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔑 屏蔽词管理 ({na}+{ns})", callback_data="kw_menu")],
        [InlineKeyboardButton("💬 欢迎词设置", callback_data="welcome_panel")],
        [InlineKeyboardButton("🤖 自动回复设置", callback_data="autoreply")],
        [InlineKeyboardButton("↩ 返回菜单", callback_data="menu")],
    ])
    return text, markup
def build_menu_msg():
    text = (
        "📋 **管理面板**\n\n"
        "请点击下方按钮选择操作："
    )
    return text


KW_PER_PAGE = 8


def build_kw_menu():
    """Keyword management main panel."""
    kw = load_keywords()
    na = len(kw.get("abuse", []))
    ns = len(kw.get("spam", []))
    text = (
        f"🔑 **屏蔽词管理**\n\n"
        f"😠 脏话屏蔽词：{na} 个\n"
        f"📋 广告屏蔽词：{ns} 个\n\n"
        f"屏蔽词命中后直接本地拦截，不消耗 AI 额度。"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"😠 查看脏话词 ({na})", callback_data="kw_view_abuse_0"),
         InlineKeyboardButton(f"📋 查看广告词 ({ns})", callback_data="kw_view_spam_0")],
        [InlineKeyboardButton("➕ 添加屏蔽词", callback_data="kw_add_pick")],
        [InlineKeyboardButton("➖ 删除屏蔽词", callback_data="kw_del_pick")],
        [InlineKeyboardButton("↩ 返回菜单", callback_data="menu")],
    ])
    return text, markup


def build_kw_view(kind: str, page: int):
    """Paginated view of keywords."""
    kw = load_keywords()
    words = kw.get(kind, [])
    label = "脏话" if kind == "abuse" else "广告"
    emoji = "😠" if kind == "abuse" else "📋"
    total_pages = max(1, (len(words) + KW_PER_PAGE - 1) // KW_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * KW_PER_PAGE
    end = start + KW_PER_PAGE

    if not words:
        text = f"{emoji} **{label}屏蔽词** 列表为空"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ 添加屏蔽词", callback_data="kw_add_" + kind)],
            [InlineKeyboardButton("↩ 返回屏蔽词菜单", callback_data="kw_menu")],
        ])
        return text, markup

    lines = [f"{emoji} **{label}屏蔽词** ({len(words)}个) 第{page+1}/{total_pages}页\n"]
    for i, w in enumerate(words[start:end], start + 1):
        lines.append(f"{i}. `{w}`")

    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"kw_view_{kind}_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"kw_view_{kind}_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("↩ 返回屏蔽词菜单", callback_data="kw_menu")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


def build_kw_del_view(kind: str, page: int):
    """Paginated view with delete buttons per keyword."""
    kw = load_keywords()
    words = kw.get(kind, [])
    label = "脏话" if kind == "abuse" else "广告"
    emoji = "😠" if kind == "abuse" else "📋"
    total_pages = max(1, (len(words) + KW_PER_PAGE - 1) // KW_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * KW_PER_PAGE
    end = start + KW_PER_PAGE

    if not words:
        text = f"{emoji} **{label}屏蔽词** 列表为空，无需删除"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩ 返回屏蔽词菜单", callback_data="kw_menu")],
        ])
        return text, markup

    lines = [f"{emoji} **删除{label}屏蔽词** ({len(words)}个) 第{page+1}/{total_pages}页\n"]
    lines[-1] += "点击按钮旁边 ❌ 删除对应词"

    rows = []
    for i, w in enumerate(words[start:end], start + 1):
        rows.append([
            InlineKeyboardButton(f"{i}. {w}", callback_data="kw_noop"),
            InlineKeyboardButton("❌", callback_data=f"kw_del_{kind}_{w}"),
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"kw_del_v_{kind}_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"kw_del_v_{kind}_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("↩ 返回屏蔽词菜单", callback_data="kw_menu")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


# Track owner entering keyword-add mode
kw_adding: dict[int, str] = {}


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: /menu to show management panel with inline buttons."""
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    await msg.reply_text(build_menu_msg(), reply_markup=MENU_INLINE_BOARD, parse_mode="Markdown")


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner text: either keyword-adding mode or reply forwarding."""
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.from_user.id != OWNER_ID:
        return

    uid = msg.from_user.id

    # === Keyword-adding / Welcome-editing mode ===
    if uid in kw_adding:
        mode = kw_adding.pop(uid)
        if mode == "welcome":
            global WELCOME_MSG
            new_text = msg.text.strip()
            if len(new_text) < 2:
                await msg.reply_text("❌ 欢迎词太短")
                return
            WELCOME_MSG = new_text
            cfg["welcome_msg"] = new_text
            save_config(cfg)
            await msg.reply_text(f"✅ 欢迎词已更新")
            logger.info(f"Owner updated welcome message")
            return
        else:
            kind = mode
            label = "脏话" if kind == "abuse" else "广告"
            word = msg.text.strip()
            if len(word) < 2:
                await msg.reply_text("❌ 屏蔽词太短（至少2个字）")
                return
            if len(word) > 50:
                await msg.reply_text("❌ 屏蔽词过长（最多50字）")
                return
            add_keyword(kind, word)
            await msg.reply_text(f"✅ 已添加{label}屏蔽词：`{word}`", parse_mode="Markdown")
            logger.info(f"Owner added {kind} keyword: {word}")
            return

    # Otherwise: try replying to stranger
    return await handle_stranger(update, context)


# Track which owner is editing auto-reply text
auto_reply_editing: set[int] = set()


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks from owner."""
    query = update.callback_query
    await query.answer()  # Acknowledge button press

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("❌ 仅主人可操作")
        return

    data = query.data
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    # Helper to edit current message
    async def edit(text, markup=None):
        try:
            await context.bot.edit_message_text(
                text, chat_id=chat_id, message_id=msg_id,
                reply_markup=markup, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Edit message error: {e}")

    # --- Menu navigation ---
    if data == "menu":
        # Edit current message back to main inline menu
        await edit(build_menu_msg(), MENU_INLINE_BOARD)
        return

    if data == "close_menu":
        # Delete the menu message to clean up the chat
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.error(f"Delete message error: {e}")
        return

    # --- Settings sub-menu ===
    if data == "settings":
        content, markup = build_settings_menu()
        await edit(content, markup)
        return

    # --- Welcome message ===
    global WELCOME_MSG
    if data == "welcome_panel":
        content, markup = build_welcome_panel()
        await edit(content, markup)
        return

    if data == "welcome_edit":
        kw_adding[query.from_user.id] = "welcome"
        await edit(
            "💬 **修改欢迎词**\n\n请在聊天框发送新的欢迎词\n当前欢迎词仅供预览，发送新内容即可替换\n发送 `/cancel_edit` 取消",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回设置", callback_data="settings")]])
        )
        return

    # --- Stats and auto-reply from menu ===
    if data == "stats":
        content, markup = build_stats_panel()
        await edit(content, markup)
        return

    if data == "autoreply":
        content, markup = build_auto_reply_panel()
        await edit(content, markup)
        return

    # --- Auto-reply controls ---
    if data == "autoreply_toggle":
        cfg = load_auto_reply()
        cfg["enabled"] = not cfg.get("enabled", False)
        save_auto_reply(cfg)
        content, markup = build_auto_reply_panel()
        await edit(content, markup)
        return

    if data == "autoreply_cnt_inc":
        cfg = load_auto_reply()
        cfg["max_count"] = min(cfg.get("max_count", 1) + 1, 10)
        save_auto_reply(cfg)
        content, markup = build_auto_reply_panel()
        await edit(content, markup)
        return

    if data == "autoreply_cnt_dec":
        cfg = load_auto_reply()
        cfg["max_count"] = max(cfg.get("max_count", 1) - 1, 1)
        save_auto_reply(cfg)
        content, markup = build_auto_reply_panel()
        await edit(content, markup)
        return

    if data == "autoreply_edit":
        auto_reply_editing.add(query.from_user.id)
        await edit(
            "✏️ 请在聊天框发送新的自动回复文案\n30秒内有效，发送 `/cancel_edit` 取消",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回", callback_data="menu")]])
        )
        return

    # --- Keyword management ---
    if data == "kw_menu":
        content, markup = build_kw_menu()
        await edit(content, markup)
        return

    if data.startswith("kw_view_"):
        # kw_view_abuse_0 / kw_view_spam_2
        parts = data.split("_")
        kind = parts[2]
        page = int(parts[3])
        content, markup = build_kw_view(kind, page)
        await edit(content, markup)
        return

    if data == "kw_add_pick":
        await edit(
            "➕ **添加屏蔽词**\n\n选择类型：",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("😠 脏话屏蔽词", callback_data="kw_add_abuse"),
                 InlineKeyboardButton("📋 广告屏蔽词", callback_data="kw_add_spam")],
                [InlineKeyboardButton("↩ 返回", callback_data="kw_menu")],
            ])
        )
        return

    if data.startswith("kw_add_"):
        kind = data.split("_")[2]
        label = "脏话" if kind == "abuse" else "广告"
        kw_adding[query.from_user.id] = kind
        await edit(
            f"➕ **添加{label}屏蔽词**\n\n请在聊天框发送要添加的屏蔽词\n30秒内有效，发送 `/cancel_edit` 取消\n\n⚠️ 一次发一个词",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回", callback_data="kw_menu")]])
        )
        return

    if data == "kw_del_pick":
        await edit(
            "➖ **删除屏蔽词**\n\n选择类型：",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("😠 脏话屏蔽词", callback_data="kw_del_v_abuse_0"),
                 InlineKeyboardButton("📋 广告屏蔽词", callback_data="kw_del_v_spam_0")],
                [InlineKeyboardButton("↩ 返回", callback_data="kw_menu")],
            ])
        )
        return

    if data.startswith("kw_del_v_"):
        # kw_del_v_abuse_0
        parts = data.split("_")
        kind = parts[3]
        page = int(parts[4])
        content, markup = build_kw_del_view(kind, page)
        await edit(content, markup)
        return

    if data.startswith("kw_del_"):
        # kw_del_abuse_操你妈 — single delete, then refresh the same view
        parts = data.split("_")
        kind = parts[2]
        word = "_".join(parts[3:])  # word may contain underscores re-joined
        remove_keyword(kind, word)
        content, markup = build_kw_del_view(kind, 0)
        await edit(content, markup)
        return

    if data == "kw_noop":
        return  # do nothing, just acknowledges button

    # --- User list pagination ---
    if data.startswith("users_p"):
        page = int(data.split("p")[1])
        content, markup = build_user_list(page)
        await edit(content, markup)
        return

    # --- Blocked list pagination ---
    if data.startswith("blocked_p"):
        page = int(data.split("p")[1])
        content, markup = build_blocked_list(page)
        await edit(content, markup)
        return

    # --- User actions: approve, block, unblock, reset ---
    for prefix in ("approve_", "block_", "unblock_", "reset_"):
        if data.startswith(prefix):
            uid = int(data[len(prefix):])
            if prefix == "approve_":
                state.update(uid, approved=True)
                logger.info(f"Owner approved user {uid}")
            elif prefix == "block_":
                state.update(uid, blocked=True)
                logger.info(f"Owner blocked user {uid}")
            elif prefix == "unblock_":
                state.update(uid, blocked=False)
                logger.info(f"Owner unblocked user {uid}")
            elif prefix == "reset_":
                state.update(uid, msgs_checked=0, approved=False, spam_count=0, auto_reply_used=0)
                logger.info(f"Owner reset user {uid}")

            # Refresh current view
            if "blocked_p" in data or prefix == "unblock_":
                # Came from blocked list? Rebuild blocked list (page 0)
                content, markup = build_blocked_list(0)
            else:
                content, markup = build_user_list(0)
            await edit(content, markup)
            return


async def cmd_cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel auto-reply text editing or keyword adding."""
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    uid = msg.from_user.id
    auto_reply_editing.discard(uid)
    if uid in kw_adding:
        del kw_adding[uid]
    await msg.reply_text("✅ 已取消编辑")


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When owner is editing auto-reply text, capture next message."""
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.from_user.id not in auto_reply_editing:
        return

    new_text = msg.text.strip()
    if len(new_text) > 500:
        await msg.reply_text("❌ 文案过长（最多500字）")
        return

    cfg = load_auto_reply()
    cfg["text"] = new_text
    save_auto_reply(cfg)
    auto_reply_editing.discard(msg.from_user.id)

    content, markup = build_auto_reply_panel()
    await msg.reply_text(f"✅ 文案已更新\n\n" + content, reply_markup=markup, parse_mode="Markdown")
    logger.info(f"Owner updated auto-reply text: {new_text[:50]}...")


async def main():
    logger.info("Starting Forwarder...")

    app = Application.builder().token(NEW_BOT_TOKEN).build()

    # === Register owner-only commands (appears in menu button next to input) ===
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/deleteMyCommands",
                         json={"scope": {"type": "chat", "chat_id": OWNER_ID}})
        logger.info(f"Cleared old commands: {r.json()}")
        r = await c.post(f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/setMyCommands",
                         json={
                             "commands": [
                                 {"command": "menu", "description": "打开管理面板"},
                                 {"command": "status", "description": "查看所有用户状态"},
                                 {"command": "reply", "description": "回复陌生人"},
                                 {"command": "start", "description": "查看帮助"},
                             ],
                             "scope": {"type": "chat", "chat_id": OWNER_ID}
                         })
        logger.info(f"Registered owner commands: {r.json()}")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reply", cmd_reply))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("cancel_edit", cmd_cancel_edit))
    # Remove ReplyKeyboardMarkup menu; owner text is now either a command or a reply to stranger
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(user_id=OWNER_ID),
        handle_stranger  # owner non-command text → check if replying to a forwarded msg
    ), group=0)
    # Other strangers (group=1, lower priority)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_stranger), group=1)
    app.add_handler(CallbackQueryHandler(handle_callback))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        logger.info("Forwarder running!")
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    logger.info("Forwarder stopped.")


if __name__ == "__main__":
    asyncio.run(main())
