#!/usr/bin/env python3
"""Apply all 13 patches to forwarder_patched.py"""
import re

PATH = "/root/.openclaw/forwarder/forwarder_patched.py"

with open(PATH, "r") as f:
    content = f.read()

# Track modifications
mods = 0

# ====================================================================
# 1. Fix State.get() default values (line ~125)
# The file already has the new fields but in a malformed way.
# We need to fix the broken JSON-like string.
# ====================================================================
# Find and fix the malformed get() method
old_get = '''    def get(self, uid):
        return self.users.get(uid, {"msgs_checked": 0, "approved": False, "spam_count": 0,
                                    "full_name": "", "username": "", "first_seen": "",
                                    "blocked": False, "auto_reply_used": 0,
                                    "abuse_count": 0, "has_forwarded": False,
                                    "last_fwd_msg_id": 0, "last_active": """abuse_count": 0, \"has_forwarded\": False,\n                                    \"last_fwd_msg_id\": 0, \"last_active\": \"\"\