#!/usr/bin/env python3
"""PreToolUse hook on ``Bash``: prompt before Teradata node-administration commands.

GATED on the project-relevance check: unless a Teradata connection is configured for this
session (``common.session_is_teradata()``), the hook returns immediately with no output and
never inspects the command. When it is a Teradata session, a command that matches

    \\b(dbscontrol|tpareset|tpa\\s+(stop|start)|vprocmanager)\\b   or   rm .*PanicLoopDetected

or that INVOKES ``ctl`` (see :func:`invokes_ctl` — a command POSITION only, so ``ctl.log``,
``kubectl`` and a commit message mentioning ctl do not prompt, while ``sudo ctl``,
``ssh node 'ctl'`` and ``printf ... | ctl`` do) raises the permission prompt, with a reason that
says what the tool changes and reminds the operator to record the current value before a modify
and keep the rollback value. These commands require OS access to the Teradata node; the hook
fires on the command text only.

Fail direction: an internal crash allows (silent exit 0) — this hook coaches, it does not
enforce.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# These four names are distinctive enough to match ANYWHERE in the command — including inside
# a quoted `ssh node '...'` payload — and measured zero false positives. Do NOT anchor them to a
# command position: the documented invocations use absolute paths and quoted remote commands.
HOST_OPS_RE = re.compile(r"\b(dbscontrol|tpareset|tpa\s+(?:stop|start)|vprocmanager)\b")

PANIC_RM_RE = re.compile(r"\brm\b.*PanicLoopDetected")

# ``ctl`` is a generic three-letter token: `ctl.log`, `grep ctl`, `kubectl`, a commit message
# mentioning ctl. Matched anywhere, it prompts on ordinary commands, and a guard that cries wolf
# gets clicked through. Matched with a regex anchored to "start of line or separator" it stops
# crying wolf but loses the shapes the recovery skill actually documents -- `ssh node \'ctl\'`,
# `nohup ctl`, `env FOO=1 ctl`, `printf ... | ctl`. So the command line is TOKENISED and ``ctl``
# is matched only where it is the command WORD: after any wrapper (sudo, env, nohup, time,
# exec, timeout, nice, ionice, chroot, a leading VAR=value), and inside the quoted payload of
# `bash -c`, `su -c`, `ssh host ...`, `docker exec ...`, `xargs` and `watch`. A host or container
# literally named ``ctl`` is an argument, not a command word, and does not prompt.
_SEP_TOKENS = frozenset({";", "&&", "||", "|", "&", "\n", "(", ")", "|&", ";;"})
_WRAPPERS = frozenset(
    {"sudo", "doas", "exec", "time", "nohup", "nice", "ionice", "env", "command", "builtin",
     "timeout", "stdbuf", "chroot"}
)
_SUDO_ARG_FLAGS = frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "-R", "-P"})
_SHELL_C = frozenset({"bash", "sh", "zsh", "ksh", "dash"})
_SSH_ARG_FLAGS = frozenset(
    {"-l", "-p", "-i", "-o", "-F", "-J", "-b", "-c", "-D", "-E", "-e", "-I", "-L", "-m", "-O",
     "-Q", "-R", "-S", "-W", "-w"}
)
_XARGS_ARG_FLAGS = frozenset(
    {"-I", "-i", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--arg-file", "--max-args",
     "--max-procs", "--delimiter", "--replace", "--max-chars"}
)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MAX_NESTING = 3


def _simple_commands(text: str):
    """Yield the token list of each simple command in ``text`` (POSIX shlex, quote-aware)."""
    lex = shlex.shlex(text, posix=True, punctuation_chars=";|&\n()")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    current: list = []
    try:
        for tok in lex:
            if tok in _SEP_TOKENS or set(tok) <= set(";|&\n()"):
                if current:
                    yield current
                current = []
            else:
                current.append(tok)
    except ValueError:
        # unbalanced quotes: fall back to a whitespace split so the guard still sees the words
        for piece in re.split(r"[;|&\n]+", text):
            words = piece.split()
            if words:
                yield words
        return
    if current:
        yield current


def _strip_wrappers(toks):
    """Drop leading VAR=value assignments, ``--`` and wrapper commands with their own options."""
    i = 0
    while i < len(toks):
        base = toks[i].rsplit("/", 1)[-1]
        if base in _WRAPPERS:
            i += 1
            while i < len(toks):
                arg = toks[i]
                if base in ("sudo", "doas") and arg in _SUDO_ARG_FLAGS:
                    i += 2
                elif arg.startswith("-"):
                    i += 1
                elif base == "env" and _ASSIGNMENT_RE.match(arg):
                    i += 1
                elif base == "timeout" and re.match(r"^[0-9.]+[smhd]?$", arg):
                    i += 1
                elif base == "nice" and arg.isdigit():
                    i += 1
                else:
                    break
            continue
        if toks[i] == "--" or _ASSIGNMENT_RE.match(toks[i]):
            i += 1
            continue
        break
    return toks[i:]


def _sub_command(toks, arg_flags):
    """The tokens from the first non-option word onward — the command a wrapper will run."""
    i = 0
    while i < len(toks):
        arg = toks[i]
        if arg == "--":
            return toks[i + 1:]
        if arg in arg_flags:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return toks[i:]
    return []


def _payload_after_target(toks, arg_flags):
    """For ``ssh [opts] host cmd...`` shapes: the tokens AFTER the first non-option argument."""
    i = 1
    while i < len(toks):
        arg = toks[i]
        if arg in arg_flags:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return toks[i + 1:]
    return []


def invokes_ctl(command, _depth: int = 0) -> bool:
    """True when ``command`` INVOKES the PDE ``ctl`` utility, rather than merely mentioning it."""
    if not isinstance(command, str) or not command.strip() or _depth > _MAX_NESTING:
        return False
    for toks in _simple_commands(command):
        toks = _strip_wrappers(toks)
        if not toks:
            continue
        head = toks[0].rsplit("/", 1)[-1]
        if head == "ctl":
            return True
        payload = None
        if head in _SHELL_C or head == "su":
            if "-c" in toks:
                j = toks.index("-c") + 1
                payload = toks[j:j + 1]
        elif head == "ssh":
            payload = _payload_after_target(toks, _SSH_ARG_FLAGS)
        elif head == "docker" and len(toks) > 2 and toks[1] in ("exec", "run"):
            payload = _payload_after_target(
                toks[1:], {"-u", "-w", "-e", "--env", "--user", "--workdir", "--name"}
            )
        elif head == "xargs":
            payload = _sub_command(toks[1:], _XARGS_ARG_FLAGS)
        elif head == "watch":
            payload = _sub_command(toks[1:], {"-n", "--interval"})
        if payload and invokes_ctl(" ".join(payload), _depth + 1):
            return True
    return False

_WHAT = {
    "dbscontrol": "edits DBS Control (GDO) fields that change database behaviour system-wide",
    "tpareset": "restarts the Teradata database (all sessions are lost)",
    "vprocmanager": "changes vproc state (online/offline/FATAL recovery)",
    "ctl": "edits PDE control settings (Start DBS, Start With Logons)",
    "tpa": "stops or starts the Teradata PDE/DBS on this node",
}


def matched_command(command: str) -> Optional[str]:
    """The administrative command matched in ``command``, or ``None``."""
    if not isinstance(command, str):
        return None
    if PANIC_RM_RE.search(command):
        return "rm PanicLoopDetected"
    hit = HOST_OPS_RE.search(command)
    if not hit:
        return "ctl" if invokes_ctl(command) else None
    word = hit.group(1)
    return "tpa" if word.split()[0] == "tpa" else word


def reason_for(matched: str) -> str:
    if matched == "rm PanicLoopDetected":
        what = (
            "removes the PanicLoopDetected marker; Teradata set it as a PROTECTIVE stop after "
            "repeated crashes, and clearing it lets the database restart into the same fault"
        )
    else:
        what = _WHAT.get(matched, "changes Teradata node state")
    return (
        f"Teradata host operation: `{matched}` {what}. Record the current value before modify; "
        "keep the rollback value. Requires OS access to the Teradata node — confirm you are on the "
        "intended system."
    )


def main() -> int:
    try:
        if not common.session_is_teradata():
            return 0  # not a Teradata session: never inspect the command
        payload = common.read_hook_input()
        if common.tool_suffix(payload.get("tool_name")) != "Bash":
            return 0
        tool_input = payload.get("tool_input") or {}
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        matched = matched_command(command)
        if matched:
            common.emit_ask(reason_for(matched), common.hook_event_name(payload, "PreToolUse"))
        return 0
    except Exception:  # noqa: BLE001 — coaching hook: fail to allow
        return 0


if __name__ == "__main__":
    sys.exit(main())
