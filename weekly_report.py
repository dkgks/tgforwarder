#!/usr/bin/env python3
"""
Weekly report script for the forwarder bot.
Reads stats, resets them, and sends a summary to the owner.

Usage: python3 weekly_report.py <config.json>
  Reads bot_token and owner_id from the config file.
"""
import json, os, sys, logging
import httpx

def load_config(config_path):
    with open(config_path) as f:
        return json.load(f)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 weekly_report.py <config.json>")
        sys.exit(1)

    config_path = sys.argv[1]
    cfg = load_config(config_path)
    bot_token = cfg["bot_token"]
    owner_id = cfg["owner_id"]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    stats_file = os.path.join(script_dir, "stats.json")
    state_file = os.path.join(script_dir, "state.json")
    log_file = os.path.join(script_dir, "forwarder.log")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)

    # Load stats
    stats = {}
    try:
        if os.path.exists(stats_file):
            with open(stats_file) as f:
                stats = json.load(f)
    except Exception:
        pass

    ads = stats.get("ads_blocked", 0)
    abuse = stats.get("abuse_replies", 0)
    total = ads + abuse

    # Count tracked users
    state = {}
    try:
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
    except Exception:
        pass

    total_users = len(state)
    approved = sum(1 for u in state.values() if u.get("approved"))
    pending = total_users - approved

    # Build report
    lines = [
        "📊 **本周转发报告**",
        "",
        f"🛡️ 屏蔽广告：**{ads}** 条",
        f"🤬 回骂辱骂：**{abuse}** 人次",
        f"📦 本周总计：**{total}**",
        "",
        f"👥 累计用户：{total_users} 人",
        f"   ✅ 已批准：{approved} 人",
        f"   🔍 待审核：{pending} 人",
        "",
        "---",
        "⚙️ 由 TGForwarder 自动生成",
    ]
    text = "\n".join(lines)

    logger.info("Report: ads=%s, abuse=%s, users=%s", ads, abuse, total_users)

    # Send to owner
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": owner_id, "text": text, "parse_mode": "Markdown"},
            timeout=15
        )
        if r.is_success and r.json().get("ok"):
            logger.info("Report sent successfully")
        else:
            logger.error("Failed to send report: %s %s", r.status_code, r.text[:200])
            sys.exit(1)
    except Exception as e:
        logger.error("Sending error: %s", e)
        sys.exit(1)

    # Reset stats
    tmp_file = stats_file + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump({"ads_blocked": 0, "abuse_replies": 0}, f)
    os.replace(tmp_file, stats_file)
    logger.info("Stats reset to zero")

if __name__ == "__main__":
    main()