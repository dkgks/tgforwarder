#!/usr/bin/env python3
"""End-to-end wiring test for the nickname-ad check inside handle_stranger().

Simulates incoming messages with fake Telegram objects and asserts on the
outgoing sends / state, without touching the real bot or network.

Run: python3 tests/test_handler_name.py
"""
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="tgfwd-test-")
CFG = os.path.join(TMP, "config.json")
with open(CFG, "w") as f:
    json.dump({"bot_token": "0:test", "owner_id": 1, "ai": {}, "utc_offset": 8}, f)

KW = {
    "_preset": {
        "abuse": ["傻逼", "下贱"],
        "spam": ["加v", "加微", "兼职", "日赚", "投资", "理财", "推广", "开发票",
                 "代开发票", "包邮", "weixin", "工作室", "批发"],
    },
    "_user_added": {"abuse": [], "spam": []},
    "_user_removed": {"abuse": [], "spam": []},
    "abuse": ["傻逼", "下贱"],
    "spam": ["加v", "加微", "兼职", "日赚", "投资", "理财", "推广", "开发票",
             "代开发票", "包邮", "weixin", "工作室", "批发"],
}
with open(os.path.join(TMP, "keywords.json"), "w") as f:
    json.dump(KW, f, ensure_ascii=False)

sys.argv = ["forwarder.py", CFG]
spec = importlib.util.spec_from_file_location("forwarder", os.path.join(ROOT, "forwarder.py"))
if spec is None or spec.loader is None:  # pragma: no cover
    raise SystemExit("cannot load forwarder.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


class FakeState:
    """Minimal stand-in for the real State class."""

    def __init__(self):
        self.users = {}

    def _entry(self, uid):
        return self.users.setdefault(uid, {
            "blocked": False, "approved": False, "spam_count": 0, "abuse_count": 0,
            "suppress_until": 0, "suppress_notified_until": 0,
            "has_forwarded": False, "last_fwd_msg_id": 0, "abuse_history": [],
            "msgs_checked": 0, "full_name": "", "username": "",
        })

    async def ensure_user(self, uid, full_name="", username=""):
        u = self._entry(uid)
        if full_name and not u.get("full_name"):
            u["full_name"] = full_name
        return dict(u)

    async def update(self, uid, **kw):
        self._entry(uid).update(kw)

    def get(self, uid):
        return dict(self.users.get(uid, {}))


sent = []
forwarded = []


def make_update(uid, full_name, username, text="你好", msg_id=1):
    user = SimpleNamespace(id=uid, full_name=full_name, username=username)
    msg = SimpleNamespace(text=text, message_id=msg_id, from_user=user,
                          reply_to_message=None)
    return SimpleNamespace(message=msg)


async def fake_send_msg(chat_id, text, reply_to_message_id=0):
    sent.append({"chat_id": chat_id, "text": text})
    return True


async def fake_forward_to_owner(uid, full_name, username, text, spam_c=0, abuse_c=0, notes=None):
    forwarded.append({"uid": uid, "name": full_name, "text": text, "notes": list(notes or [])})
    return 999


async def fake_schedule(uid):
    return None


def install_stubs():
    fw.state = FakeState()
    fw.send_msg = fake_send_msg
    fw.forward_to_owner = fake_forward_to_owner
    fw._schedule_batch_flush = fake_schedule
    fw._pending_queues.clear()
    sent.clear()
    forwarded.clear()


async def run():
    fails = []

    # --- 1. Ad in nickname + harmless text → blocked, not forwarded ---
    install_stubs()
    await fw.handle_stranger(make_update(111, "🎁福利群 @xxx", None), None)
    us = fw.state.get(111)
    if not sent or fw.NAME_NOTICE_TEXT not in sent[-1]["text"]:
        fails.append(f"1: expected name notice, got {sent}")
    if us.get("spam_count") != 1:
        fails.append(f"1: spam_count={us.get('spam_count')} (want 1)")
    if not us.get("suppress_until", 0) > time.time():
        fails.append("1: suppress_until not set")
    if forwarded:
        fails.append(f"1: message was forwarded ({forwarded})")
    if 111 in fw._pending_queues:
        fails.append("1: ad user entered the batch queue")

    # --- 2. Same user again inside cooldown → cooldown notice, no new hit ---
    sent.clear()
    await fw.handle_stranger(make_update(111, "🎁福利群 @xxx", None, msg_id=2), None)
    us = fw.state.get(111)
    if not sent or fw.SUPPRESS_NOTICE_TEXT not in sent[-1]["text"]:
        fails.append(f"2: expected cooldown notice, got {sent}")
    if us.get("spam_count") != 1:
        fails.append(f"2: spam_count changed to {us.get('spam_count')}")

    # --- 3. Third hit (cooldown expired) → auto-block ---
    install_stubs()
    fw.state.users[222] = {"spam_count": 2, "full_name": "USDT 兑换", "suppress_until": 0,
                           "approved": False, "blocked": False}
    await fw.handle_stranger(make_update(222, "USDT 兑换", None), None)
    us = fw.state.get(222)
    if not us.get("blocked"):
        fails.append(f"3: expected blocked, state={us}")
    if not sent or "拉黑" not in sent[-1]["text"]:
        fails.append(f"3: expected block reply, got {sent}")

    # --- 4. Normal student → queued for the batch window, nothing sent ---
    install_stubs()
    await fw.handle_stranger(make_update(333, "小明", "xiaoming"), None)
    if sent:
        fails.append(f"4: legit user got a reply: {sent}")
    if 333 not in fw._pending_queues:
        fails.append("4: legit user did not enter the batch queue")

    # --- 5. Approved user with ad-ish nickname → still forwarded ---
    install_stubs()
    fw.state.users[444] = {"approved": True, "full_name": "服装批发 老王",
                           "spam_count": 0, "blocked": False, "suppress_until": 0}
    await fw.handle_stranger(make_update(444, "服装批发 老王", None), None)
    if not forwarded:
        fails.append("5: approved user's message was not forwarded")
    if sent:
        fails.append(f"5: approved user got an antispam reply: {sent}")

    # --- 6. Ad in @username only → blocked ---
    install_stubs()
    await fw.handle_stranger(make_update(555, "Tom", "usdt_exchange88"), None)
    us = fw.state.get(555)
    if us.get("spam_count") != 1 or forwarded:
        fails.append(f"6: username ad not caught (spam_count={us.get('spam_count')}, fwd={forwarded})")

    # --- 7. 第一次广告只警告，不拉黑 ---
    install_stubs()
    await fw.handle_stranger(make_update(666, "小明", "xiaoming", "加微 abc 推广"), None)
    us = fw.state.get(666)
    if us.get("spam_count") != 1 or us.get("blocked"):
        fails.append(f"7: first hit should warn only, state={us}")
    if not sent or sent[-1]["text"] != fw.SPAM_ESCALATION_TEXT[0]:
        fails.append(f"7: first hit should send the plain warning, got {sent}")

    # --- 8. 第二次广告直接拉黑（SPAM_BLOCK_AT=2） ---
    install_stubs()
    fw.state.users[777] = {"spam_count": 1, "approved": False, "blocked": False,
                           "suppress_until": 0, "abuse_count": 0}
    await fw.handle_stranger(make_update(777, "小明", "xiaoming", "加微 abc 推广"), None)
    us = fw.state.get(777)
    if not us.get("blocked"):
        fails.append(f"8: second hit did not block, state={us}")
    if not sent or "拉黑" not in sent[-1]["text"]:
        fails.append(f"8: second hit should send the block notice, got {sent}")

    print(f"handler wiring checks, failures: {len(fails)}")
    for f in fails:
        print("  ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
