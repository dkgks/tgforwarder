#!/usr/bin/env python3
"""
Weekly report script for the forwarder bot.
Reads stats, resets them, and sends a summary to the owner.

Run: python3 /root/.openclaw/forwarder/weekly_report.py
"""
import json, os, sys, logging
import httpx

NEW_BOT_TOKEN = "8948476556:AAHNbEYywIEg02bbKfS4c-RMaegjvcDLvDs"
OWNER_ID = 1092973953
STATS_FILE = "/root/.openclaw/forwarder/stats.json"
STATE_FILE = "/root/.openclaw/forwarder_state.json"
LOG_FILE = "/root/.openclaw/forwarder/forwarder.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default or {}


def main():
    # Load stats
    stats = load_json(STATS_FILE, {"ads_blocked": 0, "abuse_replies": 0})
    ads = stats.get("ads_blocked", 0)
    abuse = stats.get("abuse_replies", 0)

    # Count tracked users
    state = load_json(STATE_FILE, {})
    total_users = len(state)
    approved = sum(1 for u in state.values() if u.get("approved"))
    pending = total_users - approved

    # Build report
    lines = [
        "📊 **本周转发报告**",
        "",
        f"🛡️ 屏蔽广告：**{ads}** 条",
        f"🤬 回骂辱骂：**{abuse}** 人次",
        "",
        f"👥 累计用户：{total_users} 人",
        f"   ✅ 已批准：{approved} 人",
        f"   🔍 待审核：{pending} 人",
        "",
        "---",
        "⚙️ 由 @blacklight6bot 自动生成",
    ]
    text = "\n".join(lines)

    logger.info(f"Report: ads={ads}, abuse={abuse}, users={total_users}")

    # Send to owner
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{NEW_BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15
        )
        if r.status_code == 200 and r.json().get("ok"):
            logger.info("Report sent successfully")
        else:
            logger.error(f"Failed to send report: {r.status_code} {r.text[:200]}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Sending error: {e}")
        sys.exit(1)

    # Reset stats
    with open(STATS_FILE + ".tmp", "w") as f:
        json.dump({"ads_blocked": 0, "abuse_replies": 0}, f)
    os.replace(STATS_FILE + ".tmp", STATS_FILE)
    logger.info("Stats reset to zero")


if __name__ == "__main__":
    main()
