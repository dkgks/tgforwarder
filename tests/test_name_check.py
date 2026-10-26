#!/usr/bin/env python3
"""Ad-in-nickname detection tests.

Spammers send harmless text ("你好") and put the ad in their display name,
so forwarder.name_check() screens 昵称 + @username.

Run: python3 tests/test_name_check.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Isolated instance dir: the module writes state/stats/keywords next to its config,
# so never point tests at the live instance config.
TMP = tempfile.mkdtemp(prefix="tgfwd-test-")
CFG = os.path.join(TMP, "config.json")
with open(CFG, "w") as f:
    json.dump({"bot_token": "0:test", "owner_id": 1, "ai": {}, "utc_offset": 8}, f)

# Hermetic keyword list: the live keywords.json is cloud-synced and changes over
# time, so tests ship the words they rely on (plus "工作室" for the ignore list).
# NOTE: both _preset and the runtime "abuse"/"spam" fields are needed —
# refresh_kw_sets() reads the runtime fields, not _preset.
KW = {
    "_preset": {
        "abuse": ["傻逼", "下贱"],
        "spam": ["加v", "加微", "兼职", "日赚", "投资", "理财", "推广", "开发票",
                 "代开发票", "包邮", "weixin", "工作室"],
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

# (full_name, username) that MUST be flagged as SPAM
SPAM_CASES = [
    ("🎁福利群 @xxx", None),
    ("加V：wx8888", None),
    ("兼职日赚300", "abc"),
    ("USDT 兑换", None),
    ("投资理财顾问 王先生", None),
    ("推·广 加❤️微: abc123", None),
    ("代开发票 t.me/xxx", None),
    ("招代理 日结 包邮", None),
    ("出U 收U 承兑商", None),
    ("空投白名单 撸毛", None),
    ("看我资料入群", "abc"),
    ("13812345678", None),
    ("+86 138-1234-5678", None),
    ("合约带单老师", None),
    ("低价货源一件代发", None),
    ("薇信：abc12345", None),
    ("微信：kf8888", None),
    ("Telegram群 t.me/joinchat", None),
    ("女装批发", None),
    ("天天秒杀", None),
    ("招代 私我", None),
    ("私信我领资料", None),
]

# Real students / teachers — MUST NOT be flagged
OK_CASES = [
    ("小明", "xiaoming"),
    ("Zoe @zoe", "zoe"),
    ("阿伟 体育老师", "pe_teacher"),
    ("阳光设计工作室", "sunshine_design"),
    ("张伟", None),
    ("李老师", "liteacher"),
    ("小美 ❤️", "meimei1990"),
    ("Alex", "alex2010"),
    ("城南学员小王", None),
    ("usbking", None),
    ("微信支付测试", None),
    ("陈奕迅粉丝", None),
    ("学生家长 刘女士", None),
    ("摄影爱好者", "photo_lover"),
    ("2026", None),
    ("王小明 186", "wxm186"),
    ("家电维修 老陈", None),
    ("爱钓鱼的小张", "zhang_fishing"),
    ("南山琴行 王老师", None),
    ("英语培训 小刘", None),
]


def main() -> int:
    fails = []
    for name, uname in SPAM_CASES:
        label, hit = fw.name_check(name or "", uname or "")
        if label != "SPAM":
            fails.append(f"MISS  {name!r} @{uname} -> {label} {hit}")
    for name, uname in OK_CASES:
        label, hit = fw.name_check(name or "", uname or "")
        if label != "OK":
            fails.append(f"FALSE+ {name!r} @{uname} -> {label} {hit[:3]}")

    print(f"spam cases: {len(SPAM_CASES)}, ok cases: {len(OK_CASES)}, failures: {len(fails)}")
    for f in fails:
        print(" ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
