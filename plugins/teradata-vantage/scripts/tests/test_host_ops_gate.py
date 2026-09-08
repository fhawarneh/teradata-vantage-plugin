"""Tests for scripts/hooks/host_ops_gate.py — Bash gate on Teradata node-admin commands."""

from __future__ import annotations

import pytest

import host_ops_gate as gate
from conftest import decision_of, pre_tool_payload, reason_of, run_hook


def bash_payload(command: str) -> dict:
    return pre_tool_payload("Bash", {"command": command, "description": "test"})


# --------------------------------------------------------------------------- matching
@pytest.mark.parametrize(
    "command,expected",
    [
        ("dbscontrol", "dbscontrol"),
        ("sudo /usr/pde/bin/dbscontrol < changes.txt", "dbscontrol"),
        ("tpareset -f 'apply GDO change'", "tpareset"),
        ("/etc/init.d/tpa stop", "tpa"),
        ("/etc/init.d/tpa   start && pdestate -a", "tpa"),
        ("vprocmanager", "vprocmanager"),
        ("cd /usr/tdbms/bin && ./vprocmanager", "vprocmanager"),
        ("ssh node '/usr/pde/bin/tpareset -f x'", "tpareset"),
        ("printf 'modify nos 101=TRUE\nwrite\nquit\n' | dbscontrol", "dbscontrol"),
        # `ctl` only at a command position — every documented way it is actually invoked
        ("ctl", "ctl"),
        ("ctl < changes.txt", "ctl"),
        ("sudo ctl", "ctl"),
        ("sudo /usr/pde/bin/ctl", "ctl"),
        ("/usr/pde/bin/ctl", "ctl"),
        ("cd /usr/pde/bin && ./ctl", "ctl"),
        ("printf 'sc de\n0=on\nwr\nquit\n' | ctl", "ctl"),
        ("cd /tmp\nctl", "ctl"),
        ("sudo rm -f /var/opt/teradata/PanicLoopDetected", "rm PanicLoopDetected"),
        ("rm /tmp/x/PanicLoopDetected", "rm PanicLoopDetected"),
    ],
)
def test_admin_commands_are_matched(command, expected):
    assert gate.matched_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "pdestate -a",
        "systemctl status tdvs",
        "cat /var/log/messages | grep -i PanicLoopDetected",
        "python3 scripts/doctor.sh",
        "echo controls",
        "kubectl get pods",
        "",
    ],
)
def test_ordinary_commands_are_not_matched(command):
    assert gate.matched_command(command) is None


# REGRESSION 2026-09-05 — `ctl` used to be matched as a bare word ANYWHERE in the command, so
# a log path, a grep argument or a commit message raised a Teradata host-admin prompt in every
# Teradata-configured session. A guard that cries wolf gets clicked through, which is worse
# than no guard: these must be silent, and the invocation forms above must still prompt.
@pytest.mark.parametrize(
    "command",
    [
        "cat /var/log/ctl.log",
        "tail -f ctl.log",
        "git commit -m 'fix ctl parsing'",
        "grep ctl /etc/hosts",
        "ls /opt/ctl/bin",
        "find . -name ctl.json",
        "echo ctl",
        "echo 'see ctl docs'",
        "vim ctl.py",
        "npm run ctl-test",
        "docker logs my-ctl-container",
        "systemctl restart tdvs",
    ],
)
def test_ctl_as_a_substring_does_not_prompt(command):
    assert gate.matched_command(command) is None


@pytest.mark.parametrize(
    "command,expected",
    [
        ("ssh node 'tpareset -f x'", "tpareset"),
        ("ssh node 'dbscontrol'", "dbscontrol"),
        ("echo done; vprocmanager", "vprocmanager"),
    ],
)
def test_distinctive_utility_names_match_anywhere_including_quoted_remote_commands(command, expected):
    """Only `ctl` is anchored. NEVER anchor these four — they are invoked by absolute path and
    inside quoted ssh payloads, and a missed `tpareset` restarts the database."""
    assert gate.matched_command(command) == expected


def test_reason_carries_the_record_and_rollback_rule():
    reason = gate.reason_for("dbscontrol")
    assert "Record the current value before modify; keep the rollback value." in reason
    assert "Requires OS access to the Teradata node" in reason
    assert "PanicLoopDetected" in gate.reason_for("rm PanicLoopDetected")


# --------------------------------------------------------------------------- session gate
def test_gated_off_without_a_teradata_session():
    rc, out, err = run_hook("host_ops_gate.py", bash_payload("dbscontrol"))
    assert rc == 0
    assert out is None
    assert err == ""


def test_asks_when_a_session_is_configured_by_env():
    rc, out, _ = run_hook("host_ops_gate.py", bash_payload("tpareset -f x"), {"DATABASE_URI": "set-by-test"})
    assert rc == 0
    assert decision_of(out) == "ask"
    assert "tpareset" in reason_of(out)
    assert "Record the current value before modify" in reason_of(out)


def test_asks_when_a_session_is_configured_by_credential_file(isolated_env):
    (isolated_env / "teradata.env").write_text("DATABASE_URI=placeholder\n")
    rc, out, _ = run_hook("host_ops_gate.py", bash_payload("sudo rm /var/opt/teradata/PanicLoopDetected"))
    assert decision_of(out) == "ask"


def test_ordinary_command_in_a_teradata_session_is_silent():
    rc, out, _ = run_hook("host_ops_gate.py", bash_payload("pdestate -a"), {"TERADATA_MCP_URL": "set-by-test"})
    assert rc == 0 and out is None


def test_non_bash_tool_is_ignored():
    payload = pre_tool_payload("Write", {"file_path": "x.sh", "content": "dbscontrol"})
    rc, out, _ = run_hook("host_ops_gate.py", payload, {"DATABASE_URI": "set-by-test"})
    assert rc == 0 and out is None


def test_internal_crash_allows(monkeypatch, capsys):
    def boom():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(gate.common, "session_is_teradata", boom)
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- ctl through a wrapper
# CLOSED 2026-09-08. The command-position regex added on 2026-09-05 stopped the cry-wolf
# matches (`ctl.log`, a commit message) but silently lost the shapes the recovery skill
# actually documents: an ssh payload, nohup, a leading VAR=value, sudo with its own flags.
# The matcher now TOKENISES the command line and matches `ctl` only as a command WORD, so
# both properties hold at once. These vectors pin that; do not replace the tokeniser with a
# regex without re-running them.

CTL_INVOCATIONS = [
    "sudo -u root ctl",
    "doas ctl",
    "nohup ctl",
    "env FOO=1 ctl",
    "FOO=1 BAR=2 ctl",
    "time ctl",
    "exec ctl",
    "timeout 30 ctl",
    "nice -n 10 ctl",
    "ionice -c2 ctl",
    "ssh node 'ctl'",
    'ssh -t node "sudo ctl"',
    "ssh node ctl",
    "ssh -l root -p 22 node ctl",
    "ssh -i /tmp/k node 'sudo /usr/pde/bin/ctl'",
    "ssh node -- ctl",
    "ssh node 'cd /usr/pde/bin; ./ctl'",
    "bash -c 'ctl < f'",
    "/bin/sh -c 'ctl'",
    "su - root -c 'ctl'",
    "docker exec td ctl",
    "docker exec -u root td ctl < f",
    "watch -n 5 ctl",
    "xargs -I{} ctl",
    "(cd /usr/pde/bin && ./ctl)",
    "ctl <<EOF\nsc de\n2=all\nwr\nquit\nEOF",
]

CTL_MENTIONS = [
    "docker logs ctl",
    "ssh ctl",                      # a HOST named ctl is an argument, not a command
    "ssh ctl uptime",
    "docker exec ctl uptime",       # a CONTAINER named ctl
    "kubectl exec ctl -- ls",
    "watch tail ctl.log",
    "xargs grep ctl",
    "sudo -u root systemctl status ctl",
    "git log --grep ctl",
    "rsync ctl host:",
    "test -f ctl",
    "ls -la /usr/pde/bin/ctl",
    "stat ctl",
    "file ctl",
    "which ctl",
    "md5sum ctl",
    "vim ctl.txt",
    "./ctl.sh",
    "ctlx",
    "xctl",
]


@pytest.mark.parametrize("command", CTL_INVOCATIONS)
def test_ctl_through_a_wrapper_is_an_invocation(command):
    assert gate.invokes_ctl(command), command
    assert gate.matched_command(command) == "ctl"


@pytest.mark.parametrize("command", CTL_MENTIONS)
def test_ctl_merely_mentioned_is_not_an_invocation(command):
    assert not gate.invokes_ctl(command), command


def test_ctl_matcher_survives_hostile_input():
    """Unbalanced quotes, empties and non-strings must not raise — the hook fails open."""
    for bad in ["", "   ", "ctl 'unclosed", 'ssh node "unclosed', None, 123, ["ctl"]]:
        gate.invokes_ctl(bad)          # must not raise
    assert gate.invokes_ctl("ctl 'unclosed") is True
    assert gate.matched_command(None) is None


def test_ctl_nesting_is_bounded():
    """A deeply nested payload stops at the recursion cap rather than running away."""
    deep = "ctl"
    for _ in range(gate._MAX_NESTING + 3):
        deep = f"ssh node {deep!r}"
    gate.invokes_ctl(deep)             # must return, not recurse forever
