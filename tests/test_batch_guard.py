#!/usr/bin/env python3
"""Batch-queue guard tests: filler-then-ad, cooldown, and AI-as-advisory.

Regression cover for this incident: a stranger sent filler ("哥"), the real ad
arrived 6 seconds later and was caught by the local keyword list — but the filler
was already in the debounce queue and got forwarded when the timer fired, because
(a) the queue never re-checked the cooldown and (b) the AI classifier was dead
(HTTP 404) and its failure was parsed as "everything is fine".

Run: python3 tests/test_batch_guard.py
"""
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Isolated instance dir — never point this at the live instance config, the module
# writes state/stats/reply_map next to whatever config.json it is given.
TMP = tempfile.mkdtemp(prefix="tgfwd-test-")
CFG = os.path.join(TMP, "config.json")
with open(CFG, "w") as f:
    json.dump({"bot_token": "0:test", "owner_id": 1, "utc_offset": 8,
               "ai": {"api_key": "sk-test-not-real", "base_url": "http://127.0.0.1:9/v1",
                      "platform": "openrouter", "classify_model": "test/model"},
               "suppress_sec": 600, "suppress_enabled": True}, f)

KW = {
    "_preset": {"abuse": ["傻逼"], "spam": ["对接", "加微"]},
    "_user_added": {"abuse": [], "spam": []},
    "_user_removed": {"abuse": [], "spam": []},
    "abuse": ["傻逼"],
    "spam": ["对接", "加微"],
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


sent, forwarded, purged = [], [], []


async def fake_send_msg(chat_id, text, reply_to_message_id=0):
    sent.append(text)
    return True


async def fake_forward_to_owner(uid, full_name, username, text, spam_c=0, abuse_c=0, notes=None):
    forwarded.append({"uid": uid, "text": text, "notes": list(notes or [])})
    return 900 + len(forwarded)


async def fake_schedule(uid, delay=None):
    return None


def install_stubs(ai_result=({}, "OK", False), ai_enabled=True, fail_tag=True, ai_labels=None):
    fw.state = FakeState()
    fw.send_msg = fake_send_msg
    fw.forward_to_owner = fake_forward_to_owner
    fw._schedule_batch_flush = fake_schedule
    fw.AI_ENABLED = ai_enabled
    fw.AI_FAIL_TAG_ENABLED = fail_tag
    fw._pending_queues.clear()
    sent.clear()
    forwarded.clear()
    purged.clear()

    async def fake_ai(messages, profile=""):
        return ai_result if ai_labels is None else (ai_labels, "OK", True)

    fw.ai_classify_batch = fake_ai


def make_update(uid, full_name, username, text, msg_id=1):
    from types import SimpleNamespace
    user = SimpleNamespace(id=uid, full_name=full_name, username=username)
    msg = SimpleNamespace(text=text, message_id=msg_id, from_user=user,
                          reply_to_message=None)
    return SimpleNamespace(message=msg)


def entry(text, local="OK", msg_id=1, name="小明", uname="xiaoming"):
    return {"text": text, "local": local, "matched": [], "msg_id": msg_id,
            "full_name": name, "username": uname, "arrived_at": "2026-01-01T00:00:00+00:00"}


async def run():
    fails = []

    # --- 1. Incident replay: filler queued, then the ad is caught locally ---
    # "哥" → queued. Ad "点位对接" → local SPAM → the queued filler must be purged.
    install_stubs()
    await fw.handle_stranger(make_update(601, "小明", "xiaoming", "哥", msg_id=1), None)
    if 601 not in fw._pending_queues:
        fails.append("1: filler did not enter the batch queue")
    await fw.handle_stranger(
        make_update(601, "小明", "xiaoming", "点位对接 加微信 abc", msg_id=2), None)
    if 601 in fw._pending_queues:
        fails.append("1: pending batch survived the spam hit (filler will leak)")
    await fw._flush_pending_queue(601)
    if forwarded:
        fails.append(f"1: filler was forwarded after the ad was blocked: {forwarded}")

    # --- 2. Cooldown re-check at flush time (queue not purged, e.g. restarted) ---
    install_stubs()
    fw._pending_queues[602] = {"messages": [entry("哥")], "first_ts": time.time(),
                               "timer_task": None}
    fw.state.users[602] = {"approved": False, "blocked": False, "spam_count": 1,
                           "suppress_until": time.time() + 300}
    await fw._flush_pending_queue(602)
    if forwarded:
        fails.append(f"2: batch forwarded during cooldown: {forwarded}")

    # --- 3. AI unavailable → forward, but tagged 未AI审查 ---
    install_stubs(ai_result=({}, "OK", False))
    fw._pending_queues[603] = {"messages": [entry("老师好，想学吉他")], "first_ts": time.time(),
                               "timer_task": None}
    await fw._flush_pending_queue(603)
    if len(forwarded) != 1:
        fails.append(f"3: expected 1 forward, got {forwarded}")
    elif "⚠️未AI审查" not in forwarded[0]["notes"]:
        fails.append(f"3: AI failure was not tagged: {forwarded[0]}")

    # --- 4. ai_fail_tag=False → forward untagged ---
    install_stubs(ai_result=({}, "OK", False), fail_tag=False)
    fw._pending_queues[604] = {"messages": [entry("老师好")], "first_ts": time.time(),
                               "timer_task": None}
    await fw._flush_pending_queue(604)
    if not forwarded or forwarded[0]["notes"]:
        fails.append(f"4: fail-tag switch ignored: {forwarded}")

    # --- 5. AI suspects spam → advisory tag, message still forwarded (no block) ---
    install_stubs(ai_labels={0: "SPAM"})
    fw._pending_queues[605] = {"messages": [entry("你好")], "first_ts": time.time(),
                               "timer_task": None}
    await fw._flush_pending_queue(605)
    if len(forwarded) != 1:
        fails.append(f"5: advisory suspect should not block: {forwarded}")
    elif not any("AI疑似广告" in n for n in forwarded[0]["notes"]):
        fails.append(f"5: missing advisory tag: {forwarded[0]}")
    us = fw.state.get(605)
    if us.get("blocked"):
        fails.append("5: AI alone blocked the user")

    # --- 6. One local hit in a batch → whole batch dropped, warning sent ---
    install_stubs(ai_enabled=False)
    fw._pending_queues[606] = {"messages": [entry("哥", msg_id=1),
                                            entry("点位对接加微", local="SPAM", msg_id=2)],
                               "first_ts": time.time(), "timer_task": None}
    await fw._flush_pending_queue(606)
    if forwarded:
        fails.append(f"6: batch with a local hit was forwarded: {forwarded}")
    if not sent:
        fails.append("6: no warning sent to the spammer")
    if fw.state.get(606).get("spam_count") != 1:
        fails.append(f"6: spam_count={fw.state.get(606).get('spam_count')} (want 1)")

    # --- 7. AI disabled entirely → clean batch forwarded with no notes ---
    install_stubs(ai_enabled=False)
    fw._pending_queues[607] = {"messages": [entry("想问问吉他课多少钱")], "first_ts": time.time(),
                               "timer_task": None}
    await fw._flush_pending_queue(607)
    if len(forwarded) != 1 or forwarded[0]["notes"]:
        fails.append(f"7: AI-off forward wrong: {forwarded}")

    # --- 8. Two local hits → block, nothing forwarded ---
    install_stubs(ai_enabled=False)
    fw._pending_queues[608] = {"messages": [entry("加微 对接", local="SPAM", msg_id=1),
                                            entry("点位对接", local="SPAM", msg_id=2)],
                               "first_ts": time.time(), "timer_task": None}
    await fw._flush_pending_queue(608)
    if forwarded:
        fails.append(f"8: blocked batch forwarded: {forwarded}")
    if not fw.state.get(608).get("blocked"):
        fails.append("8: user not blocked after 2 local hits")

    # --- 9. Nickname-ad path also purges a queued filler ---
    install_stubs()
    fw._pending_queues[609] = {"messages": [entry("在吗")], "first_ts": time.time(),
                               "timer_task": None}
    await fw.handle_stranger(make_update(609, "大成代收 阿强", "dcds77", "你好", msg_id=5), None)
    if 609 in fw._pending_queues:
        fails.append("9: nickname spam did not purge the queued filler")
    if fw.state.get(609).get("spam_count") != 1:
        fails.append(f"9: nickname hit not counted: {fw.state.get(609)}")

    print(f"batch guard checks, failures: {len(fails)}")
    for f in fails:
        print("  ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
