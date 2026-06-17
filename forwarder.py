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

__version__ = "1.0.0"

import asyncio, json, logging, os, signal, sys, time
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

ai_cfg = cfg.get("ai", {})
AI_AUTO_ENABLED = (ai_cfg.get("api_key", "").strip() != "")
AI_ABUSE_MANUAL_OFF = ai_cfg.get("abuse_manual_off", False)
AI_ENABLED = AI_AUTO_ENABLED and not AI_ABUSE_MANUAL_OFF
AI_API_KEY = ai_cfg.get("api_key", "")
AI_BASE_URL = cfg.get("ai", {}).get("base_url", "https://api.siliconflow.cn/v1")
AI_PLATFORM = cfg.get("ai", {}).get("platform", "openrouter")  # siliconflow or openrouter
CLASSIFY_MODEL = cfg.get("ai", {}).get("classify_model", "qwen/qwen3-next-80b-a3b-instruct:free")
INSULT_MODEL = cfg.get("ai", {}).get("insult_model", "google/gemma-4-31b-it:free")

# === Timezone: null = auto (server local), int = UTC offset ===
UTC_OFFSET = cfg.get("utc_offset")  # None (auto) or int like 8, -5
SERVER_LOCATION = cfg.get("server_location")  # None or dict with country, city, utc_offset, cached_at

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
    try:
        os.replace(STATS_FILE + ".tmp", STATS_FILE)
    except OSError:
        import shutil
        shutil.move(STATS_FILE + ".tmp", STATS_FILE)

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
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forwarder.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


from datetime import datetime, timezone, timedelta

def datetime_iso():
    return datetime.now(timezone.utc).isoformat()


def to_local_time(dt_str: str, offset: int | None, use_server_local: bool = False) -> str:
    """Convert UTC ISO datetime string to a human-readable local time.

    If dt_str is empty/falsy, returns "无记录".
    If offset is None AND use_server_local is False, returns server local time.
    If offset is an integer, applies that offset (hours).
    """
    if not dt_str:
        return "无记录"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if offset is not None:
            dt = dt + timedelta(hours=offset)
        else:
            dt = dt.astimezone(None)  # server local
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str[:16].replace("T", " ")


async def detect_server_location(timeout: int = 5) -> dict | None:
    """Detect server location via ip-api.com (free, no key).

    Returns dict with country, city, utc_offset (hours) on success, None on failure.
    Cached in config.json under server_location.
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://ip-api.com/json/", params={"fields": "country,city,timezone,offset"},
                                 timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                # ip-api with fields param returns data directly (no status field)
                if data.get("country"):
                    return {
                        "country": data.get("country", "未知"),
                        "city": data.get("city", "未知"),
                        "utc_offset": data.get("offset", 0) // 3600,
                        "cached_at": datetime.now(timezone.utc).isoformat()[:10],
                    }
    except Exception as e:
        logger.warning(f"Failed to detect server location: {e}")
    return None


class State:
    def __init__(self, path):
        self.path = path
        self.users: dict[int, dict] = {}
        self._lock = asyncio.Lock()
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                self.users = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"Loaded state: {len(self.users)} users")
        except (FileNotFoundError, json.JSONDecodeError):
            self.users = {}

    def _save_locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(k): v for k, v in self.users.items()}, f, indent=2)
        try:
            os.replace(tmp, self.path)
        except OSError:
            import shutil
            shutil.move(tmp, self.path)

    async def save(self):
        async with self._lock:
            self._save_locked()

    def _get_locked(self, uid):
        return self.users.get(uid, {"msgs_checked": 0, "approved": False, "spam_count": 0,
                                    "full_name": "", "username": "", "first_seen": "",
                                    "blocked": False, "auto_reply_used": 0,
                                    "abuse_count": 0, "has_forwarded": False,
                                    "last_fwd_msg_id": 0, "last_active": "",
                                    "ai_insult_count": 0, "last_msg_time": "",
                                    "abuse_history": []})

    def get(self, uid):
        # Read-only snapshot: safe without lock since dict reads are atomic in CPython
        return self.users.get(uid, self._get_locked(uid))

    async def update(self, uid, **kw):
        async with self._lock:
            entry = self._get_locked(uid)
            entry.update(kw)
            self.users[uid] = entry
            self._save_locked()

    async def ensure_user(self, uid, full_name="", username=""):
        """Ensure user exists with basic info; call on every message."""
        async with self._lock:
            entry = self._get_locked(uid)
            if not entry.get("first_seen"):
                entry["first_seen"] = datetime_iso()
            if full_name and not entry.get("full_name"):
                entry["full_name"] = full_name
            if username and not entry.get("username"):
                entry["username"] = username
            self.users[uid] = entry
            self._save_locked()
            return entry

    @property
    def user_copy(self):
        # Snapshot for admin panel reads (no lock needed)
        return self.users.copy()


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
    try:
        os.replace(AUTO_REPLY_FILE + ".tmp", AUTO_REPLY_FILE)
    except OSError:
        import shutil
        shutil.move(AUTO_REPLY_FILE + ".tmp", AUTO_REPLY_FILE)


reply_map: dict[int, int] = {}
REPLY_MAP_FILE = os.path.join(INSTANCE_DIR, "reply_map.json")

def _save_reply_map():
    try:
        tmp = REPLY_MAP_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(k): v for k, v in reply_map.items()}, f)
        os.replace(tmp, REPLY_MAP_FILE)
    except Exception as e:
        logger.error(f"Failed to save reply_map: {e}")

def _load_reply_map():
    try:
        with open(REPLY_MAP_FILE) as f:
            reply_map.update({int(k): v for k, v in json.load(f).items()})
        logger.info(f"Loaded reply_map: {len(reply_map)} entries")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Failed to load reply_map: {e}")

# === Keyword management (dynamic, persisted) ===
def load_keywords():
    try:
        with open(KEYWORDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Auto-copy from example on first run
        example_file = os.path.join(INSTANCE_DIR, "keywords.example.json")
        if os.path.exists(example_file):
            import shutil
            shutil.copy(example_file, KEYWORDS_FILE)
            logger.info("First run: copied keywords.example.json → keywords.json")
            try:
                with open(KEYWORDS_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"abuse": [], "spam": []}

def save_keywords(kw):
    with open(KEYWORDS_FILE + ".tmp", "w") as f:
        json.dump(kw, f, ensure_ascii=False, indent=2)
    try:
        os.replace(KEYWORDS_FILE + ".tmp", KEYWORDS_FILE)
    except OSError:
        import shutil
        shutil.move(KEYWORDS_FILE + ".tmp", KEYWORDS_FILE)

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

# Track active panel message ID so we can delete old panel before showing new one
ACTIVE_PANEL_MSG_ID: dict = {}

# Pending graceful restart flag (used by update flow instead of os.kill)
_pending_restart = False
_stop_event: asyncio.Event | None = None


def refresh_kw_sets():
    kw = load_keywords()
    ABUSE_KW.clear()
    ABUSE_KW.update(kw.get("abuse", []))
    SPAM_KW.clear()
    SPAM_KW.update(kw.get("spam", []))


async def record_blocked_content(user_id: int, text: str, kind: str, matched=None):
    """Record up to 3 blocked messages in user state for admin review."""
    us = await state.ensure_user(user_id, "", "")
    hist = list(us.get("abuse_history", []) or [])
    hist.append({"text": text[:200], "type": kind, "time": datetime_iso(), "matched": matched})
    if len(hist) > 3:
        hist = hist[-3:]
    await state.update(user_id, abuse_history=hist)


refresh_kw_sets()  # init on import


def local_check(text: str):
    """Returns (label, [matched_keywords]) - label: OK/ABUSE/SPAM/MAYBE."""
    if len(text.strip()) < 2:
        return ("OK", [])
    t = text.lower().replace(" ", "")
    matched = []
    for kw in ABUSE_KW:
        if kw in t:
            matched.append(kw)
    if matched:
        return ("ABUSE", matched)
    for kw in SPAM_KW:
        if kw in t:
            matched.append(kw)
    if matched:
        return ("SPAM", matched)
    return ("MAYBE", [])


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
                    "model": CLASSIFY_MODEL,
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
            elif r.status_code in (401, 403):
                logger.error(f"AI classify token invalid (HTTP {r.status_code}), disabling AI")
                await _disable_ai_due_to_auth()
    except Exception as e:
        logger.error(f"AI classify error: {e}")
    return "OK"


async def ai_generate_insult(target_text: str, context: str = None) -> str:
    """DeepSeek V4-Flash: fast, savage insult generation.
    If context provided (previous insults), AI maintains coherent style."""
    context_hint = ""
    if context:
        context_hint = f"\n\n你之前已经回骂了以下内容，请继续保持风格连贯且不重复：\n{context}"
    prompt = f"""对方发来了辱骂消息，请你用最恶毒、最有创意的话回骂对方。要犀利、扎心、让对方无地自容。用中文回复，控制在 80 字以内。{context_hint}

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
            elif r.status_code in (401, 403):
                logger.error(f"AI insult token invalid (HTTP {r.status_code}), disabling AI")
                await _disable_ai_due_to_auth()
    except Exception as e:
        logger.error(f"AI insult error: {e}")
    return "你这种人活着就是浪费空气。"


async def _disable_ai_due_to_auth():
    """Notify owner about invalid AI token, keep fallback behavior."""
    err_msg = (
        "⚠️ **AI API 鉴权失败**\n\n"
        "AI token 返回了 401/403 错误，AI 分类和回骂功能暂时使用兜底逻辑。\n"
        "消息分类将默认为 OK，回骂将使用预设文本。\n"
        "请检查并更新 token。"
    )
    await send_msg(OWNER_ID, err_msg)
    logger.warning("AI auth failure, owner notified (keeping fallbacks)")


# Cached AI self-check result: None=unchecked, True=valid, False=invalid
_last_ai_check = None  # (valid, timestamp)
async def check_ai_token() -> str:
    """Lightweight AI token health check. Returns 'valid', 'invalid', or 'unchecked'."""
    global _last_ai_check
    if not AI_ENABLED:
        # If manually disabled, show appropriate status
        if not AI_AUTO_ENABLED:
            return "no_token"
        if AI_ABUSE_MANUAL_OFF:
            return "manually_off"
        return "disabled"
    # Use cached result if within 5 minutes
    if _last_ai_check:
        valid, ts = _last_ai_check
        if time.time() - ts < 300:
            return "valid" if valid else "invalid"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
                json={
                    "model": INSULT_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1
                }
            )
            if r.status_code == 200:
                _last_ai_check = (True, time.time())
                return "valid"
            if r.status_code in (401, 403):
                _last_ai_check = (False, time.time())
                return "invalid"
    except Exception:
        pass
    return "unchecked"


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


async def forward_to_owner(uid: int, name: str, username: str, text: str,
                              spam_count: int = 0, abuse_count: int = 0) -> int:
    """Forward legit message. Returns Telegram message_id or 0 on failure."""
    # Escape Markdown special chars in user-supplied text to avoid 400 errors
    md_escape = str.maketrans({c: f"\\{c}" for c in "_*[]()~`>#+-=|{}.!"})
    safe_text = text[:3500].translate(md_escape)
    uname = f" @{username}" if username else ""
    tags = []
    if spam_count > 0:
        tags.append(f"🛡️广告×{spam_count}")
    if abuse_count > 0:
        tags.append(f"🤬脏话×{abuse_count}")
    tag_line = " " + " ".join(tags) if tags else ""
    msg = f"📩 **{name}**{uname}\n🆔 `{uid}`{tag_line}\n\n{safe_text}"
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
                    _save_reply_map()
                    logger.info(f"Forwarded to owner msg#{fwd_id} ← user {uid}")
                    return fwd_id
    except Exception as e:
        logger.error(f"Forward to owner error: {e}")
    return 0


async def tag_forwarded_message(fwd_msg_id: int, tag: str, original_text: str = ""):
    """Edit a forwarded message to append a tag at the end.
    If original_text is provided, appends tag to it. Otherwise sends a separate notification."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            if original_text:
                new_text = original_text + "\n\n" + tag
            else:
                # No original text available - send a separate notification
                r = await c.post(
                    f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/sendMessage",
                    json={"chat_id": OWNER_ID, "text": tag,
                          "reply_to_message_id": fwd_msg_id}
                )
                if r.status_code == 200:
                    logger.info(f"Tagged msg#{fwd_msg_id} via reply with '{tag}'")
                else:
                    logger.warning(f"Failed to tag msg#{fwd_msg_id}")
                return
            r = await c.post(
                f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/editMessageText",
                json={
                    "chat_id": OWNER_ID,
                    "message_id": fwd_msg_id,
                    "text": new_text[:4000]
                }
            )
            if r.status_code != 200:
                logger.warning(f"Failed to edit msg#{fwd_msg_id}, sent separate message")
            else:
                logger.info(f"Tagged msg#{fwd_msg_id} with '{tag}'")
    except Exception as e:
        logger.error(f"tag_forwarded_message error: {e}")


async def handle_stranger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    user = msg.from_user
    if user.id == OWNER_ID:
        reply = msg.reply_to_message
        if reply and reply.message_id in reply_map:
            sid = reply_map[reply.message_id]
            await send_msg(sid, msg.text)
            logger.info(f"Owner replied to stranger {sid}")
            if sid in state.users:
                await state.update(sid, approved=True)
                logger.info(f"Auto-approved user {sid} (owner replied)")
        return

    text = msg.text
    now = datetime_iso()
    us = await state.ensure_user(user.id, user.full_name or "", user.username or "")

    if us.get("blocked"):
        await send_msg(user.id, "你已被拉黑")
        logger.info(f"Blocked user {user.id} attempted message")
        return

    spam_c = us.get("spam_count", 0)

    # === 1. Local ABUSE check ===
    local, matched_kws = local_check(text)
    if local == "ABUSE":
        await record_blocked_content(user.id, text, "abuse", matched_kws)
        abuse_c = us.get("abuse_count", 0) + 1
        checked = 0
        update_kw = {"msgs_checked": checked, "spam_count": spam_c, "abuse_count": abuse_c,
                     "last_active": now, "last_msg_time": now}
        if abuse_c >= 2:
            if AI_ENABLED:
                insult = await ai_generate_insult(text)
                stats_add("abuse_replies")
                update_kw["ai_insult_count"] = us.get("ai_insult_count", 0) + 1
                if us.get("has_forwarded") and us.get("last_fwd_msg_id"):
                    await tag_forwarded_message(us["last_fwd_msg_id"], "⚠️ 已启动AI自动回骂")
            else:
                insult = "请文明用语，再有一次将被拉黑。"
        else:
            insult = "请文明用语，此条消息不会转发给管理员。"
        await send_msg(user.id, insult)
        await state.update(user.id, **update_kw)
        logger.info(f"ABUSE from {user.id} (local hit, abuse #{abuse_c}) → replied")
        return

    # === 2. Local SPAM check ===
    if local == "SPAM":
        await record_blocked_content(user.id, text, "spam", matched_kws)
        spam_c += 1
        checked = 0
        update_kw = {"msgs_checked": checked, "spam_count": spam_c,
                     "last_active": now, "last_msg_time": now}
        if spam_c == 1:
            stats_add("ads_blocked")
            await send_msg(user.id, "请勿发送广告。")
        elif spam_c == 2:
            stats_add("ads_blocked")
            await send_msg(user.id, "请注意言辞，再发广告将被拉黑。")
        else:
            stats_add("ads_blocked")
            update_kw["blocked"] = True
            await send_msg(user.id, "你已被拉黑，滚吧。")
        await state.update(user.id, **update_kw)
        logger.info(f"SPAM from {user.id} (local hit, spam #{spam_c}) → auto-replied")
        return

    # === 3. Approved user → forward ===
    if us.get("approved"):
        checked = us.get("msgs_checked", 0) + 1
        old_abuse = us.get("abuse_count", 0)
        update_kw = {"msgs_checked": checked, "spam_count": spam_c,
                     "last_active": now, "last_msg_time": now}
        fwd_id = await forward_to_owner(user.id, user.full_name, user.username, text,
                                        spam_c, old_abuse)
        if fwd_id:
            update_kw["has_forwarded"] = True
            update_kw["last_fwd_msg_id"] = fwd_id
        logger.info(f"Approved user {user.id} msg → forwarded")
        auto_cfg = load_auto_reply()
        if auto_cfg.get("enabled"):
            max_cnt = auto_cfg.get("max_count", 1)
            used = us.get("auto_reply_used", 0)
            if used < max_cnt:
                await send_msg(user.id, auto_cfg["text"])
                update_kw["auto_reply_used"] = used + 1
        await state.update(user.id, **update_kw)
        return

    # === 4. Not approved → AI classify ===
    checked_us = us.get("msgs_checked", 0)
    if AI_ENABLED:
        label = await ai_classify(text)
    else:
        label = "OK"
    checked = checked_us + 1
    logger.info(f"User {user.id} msg#{checked} → {label}: {text[:100]}")

    if "ABUSE" in label:
        await record_blocked_content(user.id, text, "abuse", ["AI"])
        abuse_c = us.get("abuse_count", 0) + 1
        checked = 0
        update_kw = {"msgs_checked": checked, "spam_count": spam_c, "abuse_count": abuse_c,
                     "last_active": now, "last_msg_time": now}
        if abuse_c >= 2:
            if AI_ENABLED:
                insult = await ai_generate_insult(text)
                stats_add("abuse_replies")
                update_kw["ai_insult_count"] = us.get("ai_insult_count", 0) + 1
                if us.get("has_forwarded") and us.get("last_fwd_msg_id"):
                    await tag_forwarded_message(us["last_fwd_msg_id"], "⚠️ 已启动AI自动回骂")
            else:
                insult = "请文明用语，再有一次将被拉黑。"
        else:
            insult = "请文明用语，此条消息不会转发给管理员。"
        await send_msg(user.id, insult)
        await state.update(user.id, **update_kw)
        logger.info(f"ABUSE from {user.id} (AI, abuse #{abuse_c}) → replied")
        return

    if label == "SPAM":
        await record_blocked_content(user.id, text, "spam", ["AI"])
        spam_c += 1
        checked = 0
        update_kw = {"msgs_checked": checked, "spam_count": spam_c,
                     "last_active": now, "last_msg_time": now}
        if spam_c == 1:
            stats_add("ads_blocked")
            await send_msg(user.id, "请勿发送广告。")
        elif spam_c == 2:
            stats_add("ads_blocked")
            await send_msg(user.id, "请注意言辞，再发广告将被拉黑。")
        else:
            stats_add("ads_blocked")
            update_kw["blocked"] = True
            await send_msg(user.id, "你已被拉黑，滚吧。")
        await state.update(user.id, **update_kw)
        logger.info(f"SPAM from {user.id} (AI, spam #{spam_c}) → auto-replied")
        return

    # === 5. OK → forward ===
    old_abuse = us.get("abuse_count", 0)
    update_kw = {"spam_count": spam_c, "last_active": now, "last_msg_time": now}
    if checked >= 10:
        update_kw["approved"] = True
        update_kw["msgs_checked"] = checked
        logger.info(f"User {user.id} APPROVED (continuous {checked} OK)")
    else:
        update_kw["msgs_checked"] = checked
    fwd_id = await forward_to_owner(user.id, user.full_name, user.username, text,
                                    spam_c, old_abuse)
    if fwd_id:
        update_kw["has_forwarded"] = True
        update_kw["last_fwd_msg_id"] = fwd_id
    logger.info(f"User {user.id} OK → forwarded")

    auto_cfg = load_auto_reply()
    if auto_cfg.get("enabled"):
        max_cnt = auto_cfg.get("max_count", 1)
        used = us.get("auto_reply_used", 0)
        if used < max_cnt:
            await send_msg(user.id, auto_cfg["text"])
            update_kw["auto_reply_used"] = used + 1
    await state.update(user.id, **update_kw)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id == OWNER_ID:
        await update.message.reply_text(
            "你好主人！转发服务已启动。\n\n"
            "别人的消息会自动转发给你。\n"
            "你直接回复转发的消息即可回复给对方。"
        )
    else:
        await update.message.reply_text(WELCOME_MSG)
    try:
        await update.message.delete()
    except Exception:
        pass


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        await msg.reply_text("用法: /reply <用户ID> <消息>")
        try:
            await msg.delete()
        except Exception:
            pass
        return
    try:
        parts = msg.text.split(None, 2)
        if len(parts) < 3:
            await msg.reply_text("用法: /reply <用户ID> <消息>")
            try:
                await msg.delete()
            except Exception:
                pass
            return
        uid = int(parts[1])
        text = parts[2]
        ok = await send_msg(uid, text)
        if ok:
            await msg.reply_text(f"✅ 已回复用户 {uid}")
        else:
            await msg.reply_text(f"❌ 回复失败（用户可能已拉黑机器人）")
        try:
            await msg.delete()
        except Exception:
            pass
    except ValueError:
        await msg.reply_text("用户ID必须是数字")
        try:
            await msg.delete()
        except Exception:
            pass


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: /status to see all tracked users."""
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    if not state.users:
        await msg.reply_text("暂无追踪用户。")
        try:
            await msg.delete()
        except Exception:
            pass
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
            status = f"🔍 审核中({u.get('msgs_checked',0)}/10)"
        spam = f" 🛡️×{u.get('spam_count',0)}" if u.get('spam_count', 0) > 0 else ""
        abuse = f" 🤬×{u.get('abuse_count',0)}" if u.get('abuse_count', 0) > 0 else ""
        lines.append(f"`{uid}` {name}{uname} — {status}{spam}{abuse}")
    await msg.reply_text(f"📊 追踪用户 ({len(state.users)})：\n" + "\n".join(lines[:30]), parse_mode="Markdown")
    try:
        await msg.delete()
    except Exception:
        pass


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
    """Two-tier user list: each user is a button to open detail panel."""
    users = list(state.users.items())
    # Sort by last_active descending (newly active first), empty strings last
    users.sort(key=lambda x: x[1].get("last_active", "") or "0000", reverse=True)
    total_pages = max(1, (len(users) + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * USERS_PER_PAGE
    end = start + USERS_PER_PAGE

    text_lines = [f"👥 **用户列表** ({len(users)}人) 第{page+1}/{total_pages}页\n"]
    text_lines.append("🔍 点击用户名查看详情和操作：\n")

    idx = start + 1
    for uid, u in users[start:end]:
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        uname = f" (@{u['username']})" if u.get("username") else ""
        if u.get("blocked"):
            st = "🚫 已拉黑"
        elif u.get("approved"):
            st = "✅ 已批准"
        else:
            st = f"🔍 审核中({u.get('msgs_checked',0)}/10连续)"
        spam_s = u.get("spam_count", 0)
        abuse_s = u.get("abuse_count", 0)
        has_fwd = u.get("has_forwarded", False)
        tags = []
        if spam_s > 0:
            tags.append(f"🛡️广告×{spam_s}")
        if abuse_s > 0:
            tags.append(f"🤬脏话×{abuse_s}")
        if u.get("ai_insult_count", 0) > 0:
            tags.append(f"⚡AI回骂×{u.get('ai_insult_count', 0)}")
        tag_part = " | " + " ".join(tags) if tags else ""
        t = to_local_time(u.get("last_msg_time", ""), UTC_OFFSET)
        text_lines.append(f"{idx}. {name}{uname}\n   🆔 `{uid}` | {st}{tag_part}\n   🕐 {t}\n")
        idx += 1

    btn_rows = []
    for uid, u in users[start:end]:
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        btn_rows.append([InlineKeyboardButton(f"{name} (ID:{uid})", callback_data=f"user_detail_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"users_p{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"users_p{page+1}"))
    if nav:
        btn_rows.append(nav)

    btn_rows.append([InlineKeyboardButton("🔍 搜索用户", callback_data="users_search")])
    btn_rows.append([InlineKeyboardButton("↩ 返回菜单", callback_data="menu")])

    text = "\n".join(text_lines)
    markup = InlineKeyboardMarkup(btn_rows)
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
        [InlineKeyboardButton("🤖 AI 设置", callback_data="ai_settings")],
        [InlineKeyboardButton("🕐 时区设置", callback_data="timezone")],
        [InlineKeyboardButton("🔄 检查更新", callback_data="check_update")],
        [InlineKeyboardButton("🗑 一键清空所有用户", callback_data="clearall_confirm")],
        [InlineKeyboardButton("↩ 返回菜单", callback_data="menu")],
    ])
    return text, markup



def build_update_panel(latest_info: dict | None = None, checking: bool = False, error: str = ""):
    """Build update check / upgrade panel.

    latest_info: None = not checked yet, dict with tag_name, body, html_url on success
    checking: True = API call in progress
    error: non-empty on failure
    """
    if checking:
        text = (
            "🔄 **检查更新**\n\n"
            f"当前版本：v{__version__}\n"
            "正在检查最新版本..."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
        ])
        return text, markup

    if error:
        text = (
            "🔄 **检查更新**\n\n"
            f"当前版本：v{__version__}\n"
            f"❌ 检查失败：{error}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 重试", callback_data="check_update")],
            [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
        ])
        return text, markup

    if latest_info is None:
        text = (
            "🔄 **检查更新**\n\n"
            f"当前版本：v{__version__}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 检查最新版本", callback_data="check_update")],
            [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
        ])
        return text, markup

    tag = latest_info.get("tag_name", "")
    latest_ver = tag.lstrip("v")
    body = (latest_info.get("body") or "（无更新说明）")[:800]
    is_newer = latest_ver != __version__

    if is_newer:
        text = (
            "🔄 **检查更新**\n\n"
            f"当前版本：v{__version__}\n"
            f"🆕 最新版本：{tag}\n\n"
            f"📋 **更新内容：**\n{body}\n\n"
            "⚠️ 更新仅覆盖代码文件，不影响配置和数据"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬆️ 升级到最新版", callback_data="update_confirm")],
            [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
        ])
    else:
        text = (
            "🔄 **检查更新**\n\n"
            f"当前版本：v{__version__}\n"
            f"✅ 已是最新版本（{tag}）"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
        ])
    return text, markup


def build_update_confirm_panel(latest_info: dict | None):
    """Secondary confirmation before actual upgrade."""
    tag = latest_info.get("tag_name", "未知") if latest_info else "未知"
    text = (
        "⚠️ **确认升级**\n\n"
        f"将从 v{__version__} 升级到 {tag}。\n\n"
        "升级仅替换代码文件（.py, .sh, .example.json），\n"
        "不会影响你的配置、词库、用户数据。\n"
        "升级完成后机器人会自动重启。\n\n"
        "确认升级？"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认升级", callback_data="update_do")],
        [InlineKeyboardButton("❌ 取消", callback_data="settings")],
    ])
    return text, markup


def build_timezone_settings():
    """Timezone settings panel."""
    # Current setting
    if UTC_OFFSET is not None:
        current = f"UTC{UTC_OFFSET:+d}"
    else:
        current = "自动（服务器本地时间）"

    # Server location info
    loc_lines = ""
    if SERVER_LOCATION:
        loc = SERVER_LOCATION
        sl_offset = loc.get("utc_offset", "?")
        sl_offset_str = f"UTC{sl_offset:+d}" if isinstance(sl_offset, int) else f"UTC{sl_offset}"
        loc_lines = (
            f"服务器位于：{loc['country']} {loc['city']}\n"
            f"预计 {sl_offset_str}"
        )
    else:
        loc_lines = "服务器位置：未检测"

    text = (
        "🕐 **时区设置**\n\n"
        f"当前：{current}\n"
        f"{loc_lines}\n\n"
        "设置正确的时区偏移后，用户列表中的\n"
        "时间才会显示为你所在地区的时刻。\n"
        "如果服务器和你在同一时区，无需设置。\n\n"
        "请选择你的时区偏移："
    )

    # Build buttons: 4 per row for -12 to +14, plus auto
    offsets = list(range(-12, 15))  # -12..+14 inclusive
    rows = []
    for i in range(0, len(offsets), 4):
        chunk = offsets[i:i + 4]
        row = []
        for v in chunk:
            label = f"UTC{v:+d}"
            callback = f"tz_{v}"
            if v == UTC_OFFSET:
                label = f"{label} ✅"
            row.append(InlineKeyboardButton(label, callback_data=callback))
        rows.append(row)

    # Auto button
    auto_label = "🔄 自动（服务器时间）"
    if UTC_OFFSET is None:
        auto_label = f"{auto_label} ✅"
    rows.append([InlineKeyboardButton(auto_label, callback_data="tz_auto")])

    # Refresh location
    rows.append([InlineKeyboardButton("🔄 刷新服务器位置", callback_data="tz_refresh")])

    rows.append([InlineKeyboardButton("↩ 返回设置", callback_data="settings")])

    markup = InlineKeyboardMarkup(rows)
    return text, markup


def build_ai_settings():
    """AI settings panel - status, toggle, API key, models."""
    auto_abuse = not AI_ABUSE_MANUAL_OFF
    # Status line: read cached check result
    status_line = "🤖 AI 服务：⚠️ 未验证（请点击测试）"
    if AI_AUTO_ENABLED:
        if _last_ai_check:
            valid, _ = _last_ai_check
            status_line = "🤖 AI 服务：✅ 正常" if valid else "🤖 AI 服务：❌ 密钥无效"
    else:
        status_line = "🤖 AI 服务：❌ 未配置密钥"
    text = (
        "🤖 **AI 设置**\n\n"
        f"{status_line}\n"
        f"🤬 自动回骂：{'✅ 已开启' if auto_abuse else '❌ 已关闭'}\n\n"
        "请选择操作："
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 测试 AI 连接", callback_data="check_ai_status")],
        [InlineKeyboardButton(
            f"🤬 自动回骂：{'关闭' if auto_abuse else '开启'}",
            callback_data="toggle_abuse"
        )],
        [InlineKeyboardButton("🔑 API 密钥管理", callback_data="ai_apikey_panel")],
        [InlineKeyboardButton("🧠 自定义模型", callback_data="ai_model_panel")],
        [InlineKeyboardButton("↩ 返回菜单", callback_data="menu")],
    ])
    return text, markup


def build_ai_apikey_panel():
    """API key management panel - shows masked key, allows editing."""
    api_key = cfg.get("ai", {}).get("api_key", "")
    masked = "***" + api_key[-4:] if len(api_key) > 4 else "（未设置）"
    platform = cfg.get("ai", {}).get("platform", "unknown")
    text = (
        "🔑 API 密钥管理\n\n"
        f"平台：{platform}\n"
        f"当前密钥：{masked}\n\n"
        "修改密钥请先选择平台："
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 硅基流动 (SiliconFlow)", callback_data="ai_setkey_platform_siliconflow")],
        [InlineKeyboardButton("🟠 OpenRouter", callback_data="ai_setkey_platform_openrouter")],
        [InlineKeyboardButton("↩ 返回 AI 设置", callback_data="ai_settings")],
    ])
    return text, markup


def build_ai_model_panel():
    """Model customization panel."""
    classify_model = cfg.get("ai", {}).get("classify_model", "未设置")
    insult_model = cfg.get("ai", {}).get("insult_model", "未设置")
    text = (
        "🧠 **自定义模型**\n\n"
        f"分类模型：{classify_model}\n"
        f"回骂模型：{insult_model}\n\n"
        "修改模型请用命令行：\n"
        "`/ai_classify_model <模型名>`\n"
        "`/ai_insult_model <模型名>`"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ 返回 AI 设置", callback_data="ai_settings")],
    ])
    return text, markup


def build_clearall_confirm():
    """Clear all users confirmation panel."""
    total = len(state.users)
    text = (
        f"🗑 **确认清空所有用户记录？**\n\n"
        f"将删除全部 **{total}** 个用户的所有数据。\n"
        f"确认后不可恢复！"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认清空", callback_data="clearall_do"),
            InlineKeyboardButton("❌ 取消", callback_data="settings"),
        ]
    ])
    return text, markup
def build_menu_msg(extra=""):
    lines = [
        "📋 **管理面板**",
        "",
    ]
    if extra:
        lines.append(extra)
    lines.append("请点击下方按钮选择操作：")
    text = "\n".join(lines)
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


def build_user_detail(uid: int):
    """Build user detail panel with action buttons."""
    u = state.get(uid)
    name = u.get("full_name", "") or u.get("username", "") or "未知"
    uname = f" (@{u['username']})" if u.get("username") else ""
    blocked = u.get("blocked", False)
    approved = u.get("approved", False)
    spam_c = u.get("spam_count", 0)
    abuse_c = u.get("abuse_count", 0)
    checked = u.get("msgs_checked", 0)
    first = u.get("first_seen", "未知")[:10]
    last = to_local_time(u.get("last_active", ""), UTC_OFFSET)
    last_msg_str = to_local_time(u.get("last_msg_time", ""), UTC_OFFSET)
    has_fwd = u.get("has_forwarded", False)
    ai_insult_c = u.get("ai_insult_count", 0)

    if blocked:
        status = "🚫 已拉黑"
    elif approved:
        status = "✅ 已批准"
    else:
        status = f"🔍 审核中({checked}/10连续)"

    lines = [
        f"👤 **{name}**{uname}\n",
        f"🆔 `{uid}`",
        f"状态：{status}",
        f"📩 连续正常：{checked} 条",
        f"🛡️ 广告拦截：{spam_c} 条",
        f"🤬 脏话拦截：{abuse_c} 条",
        f"📤 已转发：{'是' if has_fwd else '否'}",
        f"⚡ AI回骂次数：{ai_insult_c} 次",
        f"📅 首次出现：{first}",
        f"🕐 最后活跃：{last}",
        f"💬 最后消息：{last_msg_str}",
        f"\n操作：",
    ]

    rows = []
    if not blocked:
        if not approved:
            rows.append([InlineKeyboardButton("✅ 批准", callback_data=f"approve_{uid}")])
        rows.append([InlineKeyboardButton("🚫 拉黑", callback_data=f"block_{uid}")])
    else:
        rows.append([InlineKeyboardButton("🔓 解封", callback_data=f"unblock_{uid}")])
    rows.append([
        InlineKeyboardButton("🔄 重置", callback_data=f"reset_{uid}"),
        InlineKeyboardButton("🤬 一键回骂", callback_data=f"insult_confirm_{uid}"),
    ])
    rows.append([
        InlineKeyboardButton("🗑 删除用户", callback_data=f"delete_confirm_{uid}"),
        InlineKeyboardButton("📝 屏蔽记录", callback_data=f"abuse_history_{uid}"),
    ])
    rows.append([InlineKeyboardButton("↩ 返回用户列表", callback_data="users_p0")])

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(rows)
    return text, markup


def build_delete_confirm(uid: int):
    """Delete user confirmation panel."""
    u = state.get(uid)
    name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"

    text = (
        f"🗑 **确认删除 {name}（{uid}）？**\n\n"
        f"将删除该用户的所有记录数据。\n"
        f"确认后不可恢复。"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认删除", callback_data=f"delete_do_{uid}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"user_detail_{uid}"),
        ]
    ])
    return text, markup


def build_abuse_history(uid: int):
    """Build abuse history panel for admin review."""
    u = state.get(uid)
    name = u.get("full_name", "") or u.get("username", "") or f"{uid}"
    hist = u.get("abuse_history", []) or []

    if not hist:
        text = f"📝 **{name}** ({uid}) 屏蔽记录\n\n暂无屏蔽记录"
    else:
        lines = [f"📝 **{name}** ({uid}) 屏蔽记录\n"]
        for i, entry in enumerate(reversed(hist), 1):
            kind = entry.get("type", "??")
            icon = "🚫" if kind == "abuse" else "🟡"
            label_text = "脏话" if kind == "abuse" else "广告"
            t = to_local_time(entry.get("time", ""), UTC_OFFSET)
            content = entry.get("text", "")[:150]
            matched = entry.get("matched", [])
            if matched:
                matched_str = ", ".join(matched)
                lines.append(f"{i}. {icon} **{label_text}** · {t}\n   命中: {matched_str}\n   > {content}\n")
            else:
                lines.append(f"{i}. {icon} **{label_text}** · {t}\n   > {content}\n")
        text = "\n".join(lines)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ 返回用户详情", callback_data=f"user_detail_{uid}")],
    ])
    return text, markup


def build_insult_confirm(uid: int):
    """Insult confirmation panel with count selection and AI check."""
    u = state.get(uid)
    name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"

    if not AI_ENABLED:
        text = (
            "❌ **需要先配置 AI**\n\n"
            "回骂功能需要开启 AI 并配置 API 密钥。\n"
            "请前往设置 → AI 设置中配置。"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩ 返回", callback_data=f"user_detail_{uid}")]
        ])
        return text, markup

    text = (
        f"🤬 **一键回骂 {name}（{uid}）**\n\n"
        f"选择回骂条数（1-5条），AI 将逐条生成并发送。"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1条", callback_data=f"insult_confirm2_{uid}_1"),
            InlineKeyboardButton("2条", callback_data=f"insult_confirm2_{uid}_2"),
            InlineKeyboardButton("3条", callback_data=f"insult_confirm2_{uid}_3"),
        ],
        [
            InlineKeyboardButton("4条", callback_data=f"insult_confirm2_{uid}_4"),
            InlineKeyboardButton("5条", callback_data=f"insult_confirm2_{uid}_5"),
            InlineKeyboardButton("❌ 取消", callback_data=f"user_detail_{uid}"),
        ]
    ])
    return text, markup


def build_insult_confirm2(uid: int, count: int):
    """Second-layer confirmation before actually sending insults."""
    u = state.get(uid)
    name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
    text = (
        f"🤬 **确认回骂 {name}（{uid}） ×{count}条？**\n\n"
        f"AI 将逐条生成并发送，确认后不可撤回。"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ 确认发送 {count}条", callback_data=f"insult_send_{uid}_{count}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"user_detail_{uid}"),
        ]
    ])
    return text, markup


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: /menu to show management panel with inline buttons."""
    msg = update.message
    if msg.from_user.id != OWNER_ID:
        return
    prev_id = ACTIVE_PANEL_MSG_ID.get(OWNER_ID)
    # Step 1: Send new panel first (so chat doesn't flash empty)
    reply = await msg.reply_text(build_menu_msg(), reply_markup=MENU_INLINE_BOARD, parse_mode="Markdown")
    ACTIVE_PANEL_MSG_ID[OWNER_ID] = reply.message_id
    # Step 2: Delete the /menu command message
    try:
        await msg.delete()
    except Exception:
        pass
    # Step 3: Delete previous panel after new one is visible
    if prev_id:
        try:
            await context.bot.delete_message(chat_id=OWNER_ID, message_id=prev_id)
        except Exception:
            pass


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner text: either keyword-adding mode or reply forwarding."""
    global AI_AUTO_ENABLED, AI_ENABLED, AI_ABUSE_MANUAL_OFF, cfg
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.from_user.id != OWNER_ID:
        return

    uid = msg.from_user.id

    # === User search mode ===
    if uid in search_active:
        search_active.discard(uid)
        query_text = msg.text.strip()
        if query_text in ("/cancel", "取消"):
            await msg.reply_text("✅ 已取消搜索")
            return
        # Search users
        matches = []
        q = query_text.lower()
        for suid, u in state.users.items():
            name = (u.get("full_name", "") or u.get("username", "") or "").lower()
            uname = (u.get("username", "") or "").lower()
            if q in name or q in uname or str(suid) == query_text:
                matches.append(suid)
        if not matches:
            await msg.reply_text(f"❌ 未找到匹配 '**{query_text}**' 的用户", parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([
                                   [InlineKeyboardButton("↩ 返回用户列表", callback_data="users_p0")]
                               ]))
            return
        if len(matches) == 1:
            content, markup = build_user_detail(matches[0])
            await msg.reply_text(content, reply_markup=markup, parse_mode="Markdown")
            return
        # Multiple matches
        lines = [f"🔍 搜索 '**{query_text}**' — 找到 {len(matches)} 人\n\n请选择："]
        btns = []
        for suid in matches[:10]:
            u = state.get(suid)
            name = u.get("full_name", "") or u.get("username", "") or f"ID:{suid}"
            btns.append([InlineKeyboardButton(f"{name} (ID:{suid})", callback_data=f"user_detail_{suid}")])
        btns.append([InlineKeyboardButton("↩ 返回用户列表", callback_data="users_p0")])
        await msg.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        return

    # === AI API key input mode ===
    if uid in ai_setkey_waiting:
        ai_setkey_waiting.discard(uid)
        platform = ai_setkey_platform.pop(uid, "siliconflow")
        new_key = msg.text.strip()
        if new_key in ("/cancel", "取消"):
            await msg.reply_text("✅ 已取消密钥修改")
            return
        if not new_key or len(new_key) < 3:
            await msg.reply_text("❌ 密钥无效（太短），请重试")
            return
        cfg["ai"]["api_key"] = new_key
        cfg["ai"]["platform"] = platform
        save_config(cfg)
        AI_AUTO_ENABLED = (new_key.strip() != "")
        AI_ENABLED = AI_AUTO_ENABLED and not AI_ABUSE_MANUAL_OFF
        await msg.reply_text(
            f"✅ {platform} API 密钥已更新\n\nAI 自动启用：{'✅' if AI_ENABLED else '❌'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩ 返回 AI 设置", callback_data="ai_settings")]
            ])
        )
        logger.info(f"Owner updated AI API key for {platform}")
        return

    # === Auto-reply text editing mode ===
    if uid in auto_reply_editing:
        auto_reply_editing.discard(uid)
        new_text = msg.text.strip()
        if len(new_text) > 500:
            await msg.reply_text("❌ 文案过长（最多500字）")
            return
        cfg_ar = load_auto_reply()
        cfg_ar["text"] = new_text
        save_auto_reply(cfg_ar)
        content_ar, markup_ar = build_auto_reply_panel()
        await msg.reply_text(f"✅ 自动回复文案已更新\n\n" + content_ar, reply_markup=markup_ar, parse_mode="Markdown")
        logger.info(f"Owner updated auto-reply text: {new_text[:50]}...")
        return

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

# Track which owner is in search mode
search_active: set[int] = set()
# Track which owner is waiting for API key input
ai_setkey_waiting: set[int] = set()
ai_setkey_platform: dict[int, str] = {}


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks from owner."""
    global AI_ABUSE_MANUAL_OFF, AI_ENABLED, cfg, _last_ai_check, ai_setkey_waiting, ai_setkey_platform
    query = update.callback_query
    logger.info(f"Callback query received: data={query.data}, from={query.from_user.id}")

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("❌ 仅主人可操作")
        return

    data = query.data
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    # Helper to edit current message
    async def edit(text, markup=None):
        try:
            has_md = any(c in text for c in ('*', '_', '`', '~'))
            await context.bot.edit_message_text(
                text, chat_id=chat_id, message_id=msg_id,
                reply_markup=markup,
                parse_mode="Markdown" if has_md else None
            )
        except Exception as e:
            logger.error(f"Edit message error: {e}")
            # Fallback: try without parse_mode
            try:
                await context.bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id,
                    reply_markup=markup,
                    parse_mode=None
                )
            except Exception as e2:
                logger.error(f"Edit message fallback error: {e2}")

    # --- Menu navigation ---
    if data == "menu":
        prev_id = ACTIVE_PANEL_MSG_ID.get(OWNER_ID)
        # Step 1: Update current message to menu panel first
        await edit(build_menu_msg(), MENU_INLINE_BOARD)
        ACTIVE_PANEL_MSG_ID[OWNER_ID] = msg_id
        # Step 2: Delete old panel after new one is displayed
        if prev_id and prev_id != msg_id:
            try:
                await context.bot.delete_message(chat_id=OWNER_ID, message_id=prev_id)
            except Exception:
                pass
        return

    if data == "close_menu":
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.error(f"Delete message error: {e}")
        if ACTIVE_PANEL_MSG_ID.get(OWNER_ID) == msg_id:
            ACTIVE_PANEL_MSG_ID.pop(OWNER_ID, None)
        return

    # --- Settings sub-menu ===
    # --- AI settings ---
    if data == "ai_settings":
        # Auto-check AI connection on first visit (cache empty)
        if AI_AUTO_ENABLED and _last_ai_check is None:
            try:
                await check_ai_token()
            except Exception as e:
                logger.error(f"check_ai_token error in ai_settings: {e}")
        content, markup = build_ai_settings()
        await edit(content, markup)
        return

    if data == "settings":
        content, markup = build_settings_menu()
        await edit(content, markup)
        return

    # --- Timezone settings ---
    if data == "timezone":
        content, markup = build_timezone_settings()
        await edit(content, markup)
        return

    if data == "tz_refresh":
        global SERVER_LOCATION
        loc = await detect_server_location()
        if loc:
            SERVER_LOCATION = loc
            cfg_server = load_config()
            cfg_server["server_location"] = loc
            save_config(cfg_server)
            await query.answer(
                f"已刷新：{loc['country']} {loc['city']} UTC{loc['utc_offset']:+d}",
                show_alert=True
            )
        else:
            await query.answer("检测失败，请检查网络后重试", show_alert=True)
        content, markup = build_timezone_settings()
        await edit(content, markup)
        return

    if data.startswith("tz_"):
        global UTC_OFFSET
        tz_val = data[3:]
        if tz_val == "auto":
            # Set to auto (server local time)
            UTC_OFFSET = None
            cfg["utc_offset"] = None
            save_config(cfg)
            await query.answer("已切换为自动（服务器本地时间）", show_alert=True)
        else:
            try:
                v = int(tz_val)
                UTC_OFFSET = v
                cfg["utc_offset"] = v
                save_config(cfg)
                await query.answer(f"已设置时区为 UTC{v:+d}", show_alert=True)
            except ValueError:
                await query.answer("无效的时区值", show_alert=True)
                return
        content, markup = build_timezone_settings()
        await edit(content, markup)
        return

    # --- Update check ---
    if data == "check_update":
        # Show checking state first
        content, markup = build_update_panel(checking=True)
        await edit(content, markup)
        # Fetch latest release from GitHub API
        latest = None
        err = ""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    "https://api.github.com/repos/dkgks/tgforwarder/releases/latest",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "tgforwarder-bot"}
                )
                if r.status_code == 200:
                    d = r.json()
                    latest = {
                        "tag_name": d.get("tag_name", ""),
                        "body": d.get("body", ""),
                        "html_url": d.get("html_url", ""),
                    }
                elif r.status_code == 404:
                    err = "暂无发布版本"
                elif r.status_code >= 500:
                    err = "GitHub 暂时不可达，请稍后重试"
                else:
                    err = f"检查失败 (HTTP {r.status_code})"
        except Exception as e:
            logger.error(f"check_update error: {e}")
            err = "网络请求失败，请稍后重试"
        content, markup = build_update_panel(latest_info=latest, error=err)
        await edit(content, markup)
        return

    if data == "update_confirm":
        # Re-fetch latest release info for confirmation
        latest = None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    "https://api.github.com/repos/dkgks/tgforwarder/releases/latest",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "tgforwarder-bot"}
                )
                if r.status_code == 200:
                    d = r.json()
                    latest = {
                        "tag_name": d.get("tag_name", ""),
                        "body": d.get("body", ""),
                        "html_url": d.get("html_url", ""),
                    }
        except Exception as e:
            logger.error(f"update_confirm fetch error: {e}")
        if latest is None:
            await query.answer("无法获取版本信息，请重试", show_alert=True)
            return
        content, markup = build_update_confirm_panel(latest)
        await edit(content, markup)
        return

    if data == "update_do":
        # Step 1: Fetch latest tag
        tag = ""
        step_text = "🔄 正在获取最新版本信息..."
        await edit(step_text, InlineKeyboardMarkup([]))

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    "https://api.github.com/repos/dkgks/tgforwarder/releases/latest",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "tgforwarder-bot"}
                )
                if r.status_code == 200:
                    d = r.json()
                    tag = d.get("tag_name", "")
        except Exception as e:
            logger.error(f"update_do fetch tag error: {e}")
        if not tag:
            text = "❌ GitHub 暂时不可达或暂无发布版本"
            await edit(text, InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 重试", callback_data="check_update")],
                [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
            ]))
            return

        try:
            import subprocess, shutil, tempfile

            # Step 2: Backup current version
            step2 = (
                "🔄 **正在升级...**\n\n"
                f"📦 ① 正在备份当前版本 (v{__version__})..."
            )
            await edit(step2, InlineKeyboardMarkup([]))

            dest = INSTANCE_DIR
            back_dir = os.path.join(INSTANCE_DIR, "backup", f"v{__version__}")
            os.makedirs(back_dir, exist_ok=True)
            backup_files = ["forwarder.py", "weekly_report.py", "tgfwd.sh",
                           "keywords.example.json", "config.example.json", ".gitignore"]
            backed = []
            for fn in backup_files:
                fp = os.path.join(dest, fn)
                if os.path.exists(fp):
                    shutil.copy(fp, os.path.join(back_dir, fn))
                    backed.append(fn)
            logger.info(f"Backed up {len(backed)} files to {back_dir}")

            # Step 3: Download
            step3 = (
                "🔄 **正在升级...**\n\n"
                f"📦 ① 已备份 v{__version__} ({len(backed)} 文件)\n"
                f"⬇️ ② 正在下载 {tag}..."
            )
            await edit(step3, InlineKeyboardMarkup([]))

            url = f"https://github.com/dkgks/tgforwarder/archive/refs/tags/{tag}.tar.gz"
            tmpdir = tempfile.mkdtemp()
            tarball = os.path.join(tmpdir, "release.tar.gz")
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                r2 = await client.get(url)
                if r2.status_code != 200:
                    raise Exception(f"下载失败：HTTP {r2.status_code}")
                with open(tarball, "wb") as f:
                    f.write(r2.content)

            # Step 4: Extract
            step4 = (
                "🔄 **正在升级...**\n\n"
                f"📦 ① 已备份 v{__version__} ({len(backed)} 文件)\n"
                f"⬇️ ② 已下载 {tag}\n"
                f"📦 ③ 正在解压..."
            )
            await edit(step4, InlineKeyboardMarkup([]))

            subprocess.run(["tar", "xzf", tarball, "-C", tmpdir], check=True)

            # Find source dir
            src = None
            for item in os.listdir(tmpdir):
                full = os.path.join(tmpdir, item)
                if os.path.isdir(full) and item.startswith("tgforwarder-"):
                    src = full
                    break
            if not src:
                raise Exception("解包失败：找不到源码目录")

            # Step 5: Install
            step5 = (
                "🔄 **正在升级...**\n\n"
                f"📦 ① 已备份 v{__version__} ({len(backed)} 文件)\n"
                f"⬇️ ② 已下载 {tag}\n"
                f"📦 ③ 已解压\n"
                f"📋 ④ 正在安装代码文件..."
            )
            await edit(step5, InlineKeyboardMarkup([]))

            copied = []
            for fn in ["forwarder.py", "weekly_report.py", "tgfwd.sh",
                       "keywords.example.json", "config.example.json", ".gitignore"]:
                s = os.path.join(src, fn)
                d = os.path.join(dest, fn)
                if os.path.exists(s):
                    shutil.copy(s, d)
                    copied.append(fn)

            shutil.rmtree(tmpdir)

            # Write upgrade flag for rollback watchdog
            flag_path = os.path.join(INSTANCE_DIR, "upgrade_flag.json")
            with open(flag_path, "w") as ff:
                json.dump({
                    "from_version": __version__,
                    "to_version": tag.lstrip("v"),
                    "backup_dir": f"backup/v{__version__}",
                    "created_at": datetime_iso(),
                }, ff)

            # Step 6: Final
            step6 = (
                "✅ **升级完成！**\n\n"
                f"v{__version__} → {tag}\n"
                f"已更新：{', '.join(copied)}\n"
                f"备份：{back_dir}\n\n"
                "机器人正在重启，升级验证中...\n"
                "如有问题将自动回退到旧版本。"
            )
            await edit(step6, InlineKeyboardMarkup([
                [InlineKeyboardButton("↩ 返回菜单（请稍后）", callback_data="menu")],
            ]))
            logger.info(f"Update to {tag} complete, restarting...")
            global _pending_restart, _stop_event
            _pending_restart = True
            if _stop_event:
                _stop_event.set()

        except Exception as e:
            logger.error(f"update_do error: {e}")
            text = f"❌ 升级失败：{e}"
            await edit(text, InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 重试", callback_data="check_update")],
                [InlineKeyboardButton("↩ 返回设置", callback_data="settings")],
            ]))
        return

    # --- Toggle AI auto-abuse ---
    if data == "toggle_abuse":
        if not AI_AUTO_ENABLED:
            await query.answer("❌ 未配置 AI API 密钥，无法开启", show_alert=True)
            return
        AI_ABUSE_MANUAL_OFF = not AI_ABUSE_MANUAL_OFF
        AI_ENABLED = AI_AUTO_ENABLED and not AI_ABUSE_MANUAL_OFF
        cfg["ai"]["abuse_manual_off"] = AI_ABUSE_MANUAL_OFF
        save_config(cfg)
        await query.answer(
            f"🤬 自动回骂已{'关闭' if AI_ABUSE_MANUAL_OFF else '开启'}",
            show_alert=True
        )
        content, markup = build_ai_settings()
        await edit(content, markup)
        logger.info(f"AI auto-abuse toggled: manual_off={AI_ABUSE_MANUAL_OFF}, effective={AI_ENABLED}")
        return

    # --- Check AI connection status ---
    if data == "check_ai_status":
        if not AI_AUTO_ENABLED:
            await query.answer("❌ 未配置 AI API 密钥", show_alert=True)
            return
        # Direct API check - bypass check_ai_token's complex logic
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{AI_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {AI_API_KEY}"},
                    json={
                        "model": INSULT_MODEL,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1
                    }
                )
                if r.status_code == 200:
                    _last_ai_check = (True, time.time())
                    await query.answer("✅ AI 连接正常", show_alert=True)
                elif r.status_code in (401, 403):
                    _last_ai_check = (False, time.time())
                    await query.answer("❌ AI API 密钥无效（401/403）", show_alert=True)
                else:
                    await query.answer(f"⚠️ 未知状态码: {r.status_code}", show_alert=True)
        except Exception as e:
            logger.error(f"check_ai_status direct API error: {e}")
            await query.answer("⚠️ 无法验证（网络错误）", show_alert=True)
        content, markup = build_ai_settings()
        await edit(content, markup)
        return

    # --- AI API key panel ---
    if data == "ai_apikey_panel":
        content, markup = build_ai_apikey_panel()
        await edit(content, markup)
        return

    # --- AI set key platform selection ---
    if data.startswith("ai_setkey_platform_"):
        platform = data.split("_")[3]  # siliconflow or openrouter
        await query.answer(f"💬 请在聊天框输入 {platform} 的新 API 密钥，或发送 /cancel 取消", show_alert=True)
        ai_setkey_waiting.add(chat_id)
        ai_setkey_platform[chat_id] = platform
        return

    # --- AI model panel ---
    if data == "ai_model_panel":
        content, markup = build_ai_model_panel()
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

    # --- User detail panel ---
    if data.startswith("user_detail_"):
        uid = int(data.split("_")[2])
        content, markup = build_user_detail(uid)
        await edit(content, markup)
        return

    # --- Abuse history ---
    if data.startswith("abuse_history_"):
        uid = int(data.split("_")[2])
        content, markup = build_abuse_history(uid)
        await edit(content, markup)
        return

    # --- Insult confirm panel (first layer - count selection) ---
    if data.startswith("insult_confirm_"):
        uid = int(data.split("_")[2])
        content, markup = build_insult_confirm(uid)
        await edit(content, markup)
        return

    # --- Insult confirm panel (second layer - final confirm) ---
    if data.startswith("insult_confirm2_"):
        parts = data.split("_")
        uid = int(parts[2])
        count = int(parts[3])
        content, markup = build_insult_confirm2(uid, count)
        await edit(content, markup)
        return

    # --- Insult execute (after two confirmations) ---
    if data.startswith("insult_send_"):
        parts = data.split("_")
        uid = int(parts[2])
        count = int(parts[3]) if len(parts) >= 4 else 1
        count = min(max(count, 1), 5)
        if not AI_ENABLED:
            await edit("❌ AI 未配置，无法回骂", InlineKeyboardMarkup([
                [InlineKeyboardButton("↩ 返回", callback_data=f"user_detail_{uid}")]
            ]))
            return
        # Immediately show loading state (no buttons) to prevent double-tap
        await edit(f"⏳ 正在生成回骂内容… (0/{count})")
        insults = []
        for i in range(count):
            ctx = "\n".join(insults) if insults else None
            insult = await ai_generate_insult("辱骂", context=ctx)
            if insult:
                insults.append(insult)
                await send_msg(uid, insult)
                # Update progress with sent content previews
                progress_lines = [f"⏳ 正在回骂… ({i+1}/{count})"]
                for j, ins in enumerate(insults, 1):
                    preview = ins[:40] + "..." if len(ins) > 40 else ins
                    progress_lines.append(f"✅ {j}. {preview}")
                await edit("\n".join(progress_lines))
                await asyncio.sleep(0.5)
        stats_add("abuse_replies", count)
        old_ai = state.get(uid).get("ai_insult_count", 0)
        await state.update(uid, ai_insult_count=old_ai + count, has_forwarded=True)
        logger.info(f"Owner manually insulted user {uid} x{count}")
        u = state.get(uid)
        name = u.get("full_name", "") or u.get("username", "") or f"ID:{uid}"
        result_lines = [f"✅ 已回骂 **{name}** ×{count}条\n"]
        for j, ins in enumerate(insults, 1):
            preview = ins[:60] + "..." if len(ins) > 60 else ins
            result_lines.append(f"{j}. {preview}")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩ 返回用户详情", callback_data=f"user_detail_{uid}")]
        ])
        await edit("\n".join(result_lines), markup)
        return

    # --- Delete user confirm ---
    if data.startswith("delete_confirm_"):
        uid = int(data.split("_")[2])
        content, markup = build_delete_confirm(uid)
        await edit(content, markup)
        return

    # --- Delete user execute ---
    if data.startswith("delete_do_"):
        uid = int(data.split("_")[2])
        if uid in state.users:
            del state.users[uid]
            await state.save()
            logger.info(f"Owner deleted user {uid} from records")
        content, markup = build_user_list(0)
        await edit(f"🗑 已删除用户 {uid}\n\n" + content, markup)
        return

    # --- Clear all confirm ---
    if data == "clearall_confirm":
        content, markup = build_clearall_confirm()
        await edit(content, markup)
        return

    # --- Clear all execute ---
    if data == "clearall_do":
        count = len(state.users)
        state.users.clear()
        await state.save()
        logger.info(f"Owner cleared all {count} user records")
        await edit(f"🗑 已清空全部 {count} 个用户记录",
                   InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回菜单", callback_data="menu")]]))
        return

    # --- User search ---
    if data == "users_search":
        # Enter search mode
        search_active.add(query.from_user.id)
        await edit(
            "🔍 **搜索用户**\n\n发送用户名、用户ID或 @username 来搜索。\n支持模糊匹配。发送 `/cancel_search` 取消。",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩ 返回用户列表", callback_data="users_p0")]])
        )
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
                await state.update(uid, approved=True)
                logger.info(f"Owner approved user {uid}")
            elif prefix == "block_":
                await state.update(uid, blocked=True)
                logger.info(f"Owner blocked user {uid}")
            elif prefix == "unblock_":
                await state.update(uid, blocked=False)
                logger.info(f"Owner unblocked user {uid}")
            elif prefix == "reset_":
                await state.update(uid, msgs_checked=0, approved=False, spam_count=0, abuse_count=0,
                             auto_reply_used=0, has_forwarded=False, last_fwd_msg_id=0,
                             ai_insult_count=0, abuse_history=[])
                logger.info(f"Owner reset user {uid}")

            content, markup = build_user_detail(uid)
            await edit(content, markup)
            return

    # Fallback: acknowledge any unhandled callback
    await query.answer()


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
    try:
        await msg.delete()
    except Exception:
        pass


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


async def rollback_watchdog():
    """After an upgrade, wait for bot to stabilize, then verify getMe.

    If getMe fails → restore backed-up code files → kill self.
    If getMe succeeds → cleanup upgrade_flag.json and backup dir.
    """
    import shutil as _shutil
    flag_path = os.path.join(INSTANCE_DIR, "upgrade_flag.json")

    # Wait for bot to connect to Telegram
    logger.info("Upgrade watchdog: waiting 15s for bot to stabilize...")
    await asyncio.sleep(15)

    # Verify bot is alive
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/getMe"
            )
            if r.status_code == 200 and r.json().get("ok"):
                logger.info("Upgrade watchdog: getMe OK, upgrade successful")
                # Cleanup
                if os.path.exists(flag_path):
                    with open(flag_path) as f:
                        flag_data = json.load(f)
                    os.remove(flag_path)
                    # Remove old backup
                    back_dir = os.path.join(INSTANCE_DIR, flag_data.get("backup_dir", ""))
                    if back_dir and os.path.isdir(back_dir):
                        _shutil.rmtree(back_dir)
                        logger.info(f"Upgrade watchdog: cleaned up {back_dir}")
                return
            else:
                raise Exception(f"getMe failed: HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Upgrade watchdog: getMe verification failed: {e}")

    # Rollback
    logger.info("Upgrade watchdog: starting rollback...")
    if not os.path.exists(flag_path):
        logger.error("Upgrade watchdog: upgrade_flag.json not found, cannot rollback")
        return

    try:
        with open(flag_path) as f:
            flag_data = json.load(f)
        back_dir = os.path.join(INSTANCE_DIR, flag_data.get("backup_dir", ""))
        from_ver = flag_data.get("from_version", "unknown")

        if not back_dir or not os.path.isdir(back_dir):
            logger.error(f"Upgrade watchdog: backup dir not found: {back_dir}")
            os.remove(flag_path)
            return

        # Restore backup files
        dest = INSTANCE_DIR
        restored = []
        for fn in os.listdir(back_dir):
            src = os.path.join(back_dir, fn)
            dst = os.path.join(dest, fn)
            if os.path.isfile(src):
                _shutil.copy(src, dst)
                restored.append(fn)

        logger.info(f"Upgrade watchdog: restored {len(restored)} files from {back_dir}")

        # Remove flag to prevent infinite rollback loop
        os.remove(flag_path)
        # Keep backup dir for debugging
        logger.info(f"Upgrade watchdog: rollback to v{from_ver} complete, restarting...")
        global _pending_restart, _stop_event
        _pending_restart = True
        if _stop_event:
            _stop_event.set()

    except Exception as e:
        logger.error(f"Upgrade watchdog: rollback failed: {e}")
        # Remove flag anyway to prevent loop
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
            except Exception:
                pass


async def main():
    global SERVER_LOCATION, UTC_OFFSET, _stop_event

    # --- PID-based mutual exclusion (per-config, supports multi-bot on one host) ---
    import hashlib
    config_tag = hashlib.md5(CONFIG_PATH.encode()).hexdigest()[:8]
    pidfile = os.path.join(INSTANCE_DIR, f"forwarder.{config_tag}.pid")
    try:
        fd = os.open(pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        try:
            with open(pidfile) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            logger.error(f"Another instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except (OSError, ProcessLookupError, ValueError):
            # Stale — remove and retry atomically
            os.remove(pidfile)
            fd = os.open(pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
    logger.info("Starting Forwarder...")

    # Auto-detect server location on first run (cached in config.json)
    global SERVER_LOCATION, UTC_OFFSET
    if SERVER_LOCATION is None:
        loc = await detect_server_location()
        if loc:
            cfg_server = load_config()
            cfg_server["server_location"] = loc
            save_config(cfg_server)
            SERVER_LOCATION = loc
            logger.info(f"Server location detected: {loc['country']} {loc['city']} UTC{loc['utc_offset']:+d}")

    # Check for pending upgrade verification / rollback
    flag_path = os.path.join(INSTANCE_DIR, "upgrade_flag.json")
    if os.path.exists(flag_path):
        task = asyncio.create_task(rollback_watchdog())
        task.add_done_callback(lambda t: logger.error(f"Rollback watchdog crashed: {t.exception()}") if t.exception() else None)

    _load_reply_map()
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
                             ],
                             "scope": {"type": "chat", "chat_id": OWNER_ID}
                         })
        logger.info(f"Registered owner commands: {r.json()}")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reply", cmd_reply))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("cancel_edit", cmd_cancel_edit))
    # Owner text: process editing modes first, then reply forwarding
    # Single handler with explicit priority — editing modes consume, otherwise fall through to reply forwarding
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(user_id=OWNER_ID),
        handle_menu_text
    ), group=0)
    # Other strangers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.User(user_id=OWNER_ID), handle_stranger), group=1)
    app.add_handler(CallbackQueryHandler(handle_callback))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        logger.info("Forwarder running!")
        stop = asyncio.Event()
        _stop_event = stop
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()
        _stop_event = None

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

        if _pending_restart:
            logger.info("Pending restart detected, exiting cleanly.")

    logger.info("Forwarder stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        pidfile = os.path.join(INSTANCE_DIR, "forwarder.pid")
        try:
            os.remove(pidfile)
        except OSError:
            pass
