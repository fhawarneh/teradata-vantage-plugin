#!/usr/bin/env python3
"""UserPromptSubmit hook: warn when a connection string with a password is pasted into a prompt.

OFF by default. It runs only when BOTH hold:

* the plugin option ``enable_prompt_secret_guard`` is on (exported to hooks as
  ``CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD`` = 1/true/yes), and
* the session is a Teradata session (``common.session_is_teradata()``).

Otherwise it exits immediately without reading the prompt. When active, it looks for the
userinfo form of a URI — ``scheme://user:password@`` — anywhere in the prompt. It NEVER echoes
the match. Default action is a warning attached as ``additionalContext``;
``TERADATA_PROMPT_SECRET_GUARD=block`` blocks the prompt instead (exit 2 with one stderr line).
There is deliberately no broad ``PASSWORD=`` regex: that would fire on ordinary SQL and docs.

Fail direction: an internal crash allows.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# scheme://<userinfo-with-password>@  — the lookbehind keeps the scheme from starting mid-word.
URI_USERINFO_RE = re.compile(r"(?<![A-Za-z0-9+.\-])[a-zA-Z][a-zA-Z0-9+.\-]*://[^/@\s]*:[^/@\s]*@")

WARNING = (
    "A connection string of the form scheme://user:password@host appears in this prompt. It is now "
    "part of the transcript: rotate that password, and pass credentials through the plugin option "
    "database_uri, the DATABASE_URI environment variable, or scripts/teradata-vantage-connect (run in "
    "your own terminal) instead of the chat."
)


def enabled() -> bool:
    return common.env_flag("CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD", False)


def contains_secret_uri(prompt) -> bool:
    return isinstance(prompt, str) and URI_USERINFO_RE.search(prompt) is not None


def action() -> str:
    raw = (os.environ.get("TERADATA_PROMPT_SECRET_GUARD") or "").strip().lower()
    return "block" if raw == "block" else "warn"


def main() -> int:
    try:
        if not enabled() or not common.session_is_teradata():
            return 0
        payload = common.read_hook_input()
        if not contains_secret_uri(payload.get("prompt")):
            return 0
        if action() == "block":
            sys.stderr.write(
                "[teradata-vantage] prompt blocked: it contains a scheme://user:password@ connection "
                "string. Remove the credential and use the plugin option database_uri, DATABASE_URI, or "
                "scripts/teradata-vantage-connect instead.\n"
            )
            return 2
        common.emit_context(WARNING, common.hook_event_name(payload, "UserPromptSubmit"))
        return 0
    except Exception:  # noqa: BLE001 — fail to allow
        return 0


if __name__ == "__main__":
    sys.exit(main())
