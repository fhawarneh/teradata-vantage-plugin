"""Tests for scripts/validate_plugin.py using a synthetic mini plugin built in tmp_path.

`good_repo` passes every check (with --no-cli); each mutation test breaks exactly one thing and asserts the
stable check id that must fail. Fixtures use placeholder names only (sales_fact, <db>.<table>).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import validate_plugin as vp  # noqa: E402

PREFIX = vp.TOOL_PREFIX_DEFAULT


def w(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def build_good_repo(root: Path) -> Path:
    plugin = root / "plugins" / "teradata-vantage"
    w(root, ".claude-plugin/marketplace.json", json.dumps({
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": "teradata-plugins",
        "description": "Community plugins for Teradata Vantage. Not affiliated with Teradata Corporation.",
        "owner": {"name": "Community Maintainers"},
        "plugins": [{
            "name": "teradata-vantage",
            "source": "./plugins/teradata-vantage",
            "description": "Work with Teradata Vantage from Claude Code. Community plugin.",
            "category": "database",
            "tags": ["teradata"],
        }],
    }, indent=2))
    w(plugin, ".claude-plugin/plugin.json", json.dumps({
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "name": "teradata-vantage",
        "displayName": "Teradata Vantage Toolkit (Community)",
        "version": "0.1.0",
        "description": "Teradata Vantage toolkit. Community plugin; not affiliated with Teradata Corporation.",
        "author": {"name": "Community Maintainers"},
        "license": "MIT",
        "keywords": ["teradata"],
        "userConfig": {
            "database_uri": {"type": "string", "title": "DATABASE_URI", "description": "Connection string.", "sensitive": True, "required": False, "default": ""},
        },
    }, indent=2))
    w(plugin, ".mcp.json", json.dumps({"mcpServers": {"teradata": {"command": "bash", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh"]}}}))
    w(plugin, "README.md", "# Teradata Vantage Toolkit (Community)\n")
    w(plugin, "LICENSE", "MIT License\n")
    w(plugin, "scripts/launch-mcp.sh", "#!/usr/bin/env bash\nexit 0\n")
    w(plugin, "scripts/hooks/sql_read_guard.py", "import sys\nsys.exit(0)\n")
    w(plugin, "scripts/data/mcp_tools.yaml", "\n".join([
        "package: teradata-mcp-server",
        'verified_version: "0.2.6"',
        'wheel_sha256: "WHEEL_SHA"',
        f"tool_prefix: {PREFIX}",
        "groups:",
        "  base:",
        "    tools: [base_readQuery, base_databaseList, base_tableList, base_tableDDL]",
        "  dba:",
        "    tools: [dba_databaseSpace, dba_sessionInfo]",
        "  tdvs:",
        "    tools: [tdvs_list, tdvs_destroy]",
        "guarded_read_tools: [base_readQuery]",
        "destructive_tools: [base_writeQuery, tdvs_destroy]",
        "",
    ]))
    w(plugin, "config/profiles.yml", "tv_all:\n  tool: ['.*']\n  prompt: ['^(?!_?test).*']\n  resource: ['.*']\n")
    wheel = plugin / "servers" / "teradata_mcp_server-0.2.6-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_bytes(b"PK\x03\x04 synthetic wheel bytes")
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    w(plugin, "servers/VENDORED.sha256", f"{sha}  {wheel.name}\n")
    w(plugin, "servers/requirements-0.2.6.txt", "# empty\n")
    w(plugin, "servers/VENDORED.md", "# Vendored\n")
    w(plugin, "servers/NOTICE.md", "# Notice\n")
    # fix the inventory's wheel sha to the synthetic wheel
    inv = plugin / "scripts/data/mcp_tools.yaml"
    inv.write_text(inv.read_text().replace("WHEEL_SHA", sha))
    w(plugin, "hooks/hooks.json", json.dumps({"hooks": {"PreToolUse": [{
        "matcher": "mcp__.*__(base_readQuery|execute_tool)$",
        "hooks": [{"type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks/sql_read_guard.py"], "timeout": 10}],
    }]}}, indent=2))
    w(plugin, "workflows/health-audit.js", "\n".join([
        "// health audit",
        "export const meta = {",
        '  name: "health-audit",',
        '  description: "Audit database health.",',
        '  whenToUse: "Launched by the health skill for several databases.",',
        "  phases: [],",
        "};",
        "export default async function run(ctx) { return ctx.args; }",
        "",
    ]))
    w(plugin, "skills/health/SKILL.md", "\n".join([
        "---",
        "name: health",
        "description: Use when checking whether a Teradata system is healthy — space, sessions, flow control.",
        "when_to_use: '\"is teradata healthy\", \"check space\"'",
        "license: MIT",
        "metadata:",
        "  skill_type: workflow",
        "  category: teradata",
        '  version: "1.0.0"',
        "allowed-tools:",
        f"  - {PREFIX}dba_databaseSpace",
        "  - Workflow",
        "  - Workflow(teradata-vantage:health-audit)",
        "---",
        "# Health",
        "Probe with `dba_databaseSpace`; report a verdict line first.",
        "",
    ]))
    w(plugin, "skills/health/references/space.md", "# Space management — parent/child PERM and error 3541\n\ntext\n")
    w(plugin, "skills/teradata-sql/SKILL.md", "\n".join([
        "---",
        "name: teradata-sql",
        "description: Use when writing or repairing Teradata SQL — TOP not LIMIT, IS NULL, reserved words.",
        "license: MIT",
        "user-invocable: false",
        "metadata:",
        "  skill_type: documentation",
        "  category: teradata",
        '  version: "1.0.0"',
        "---",
        "# Teradata SQL",
        "NEVER write LIMIT; ALWAYS write `SELECT TOP n`.",
        "",
    ]))
    w(plugin, "agents/dba.md", "\n".join([
        "---",
        "name: dba",
        "description: Teradata DBA agent for health and space questions.",
        "model: inherit",
        "maxTurns: 40",
        f"tools: {PREFIX}base_readQuery, {PREFIX}dba_databaseSpace, {PREFIX}base_writeQuery, Bash",
        "skills:",
        "  - teradata-vantage:health",
        "  - teradata-vantage:teradata-sql",
        "---",
        "You are the DBA agent.",
        "",
    ]))
    return plugin


@pytest.fixture
def good_repo(tmp_path):
    build_good_repo(tmp_path)
    return tmp_path


def failed(repo: Path) -> set:
    return vp.run_checks(repo, use_cli=False).failed_checks()


def test_good_repo_passes(good_repo):
    rep = vp.run_checks(good_repo, use_cli=False)
    assert rep.failures == [], [f.format() for f in rep.failures]


def test_main_exit_codes(good_repo, capsys):
    assert vp.main(["--repo", str(good_repo), "--no-cli"]) == 0
    assert "PASSED" in capsys.readouterr().out
    (good_repo / "plugins/teradata-vantage/settings.json").write_text("{}")
    assert vp.main(["--repo", str(good_repo), "--no-cli", "--quiet"]) == 1


@pytest.mark.parametrize(
    "rel, old, new, check",
    [
        ("plugins/teradata-vantage/skills/health/SKILL.md", "name: health", "name: Health", "skill.name"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "name: health", "name: health--x", "skill.name"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "name: health", "name: teradata-vantage:health", "skill.name"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "description: Use when checking", "description: Checks", "skill.description"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "  category: teradata", "  category: teradata\n  paths: ['**/*.sql']", "skill.metadata"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "dba_databaseSpace\n", "dba_nope\n", "skill.tools"),
        ("plugins/teradata-vantage/skills/health/SKILL.md", "health-audit)", "profile-database)", "workflow.grant"),
        ("plugins/teradata-vantage/agents/dba.md", "dba_databaseSpace", "dba_unknownTool", "agent.tools"),
        ("plugins/teradata-vantage/agents/dba.md", "  - teradata-vantage:teradata-sql", "  - teradata-vantage:tune", "agent.skills"),
        ("plugins/teradata-vantage/agents/dba.md", "maxTurns: 40", "maxTurns: 40\npermissionMode: bypassPermissions", "agent.forbidden-key"),
        ("plugins/teradata-vantage/agents/dba.md", "description: Teradata DBA agent for health and space questions.\n", "", "agent.description"),
        ("plugins/teradata-vantage/workflows/health-audit.js", "// health audit", "const t = Date.now();", "workflow.meta"),
        ("plugins/teradata-vantage/workflows/health-audit.js", "return ctx.args;", "return Math.random();", "workflow.banned"),
        ("plugins/teradata-vantage/workflows/health-audit.js", "// health audit", "import fs from 'fs';", "workflow.banned"),
        ("plugins/teradata-vantage/config/profiles.yml", "  resource: ['.*']", "  resource: ['.*']\n  run:\n    mcp_port: 8001", "profiles.run"),
        ("plugins/teradata-vantage/servers/VENDORED.sha256", "  teradata_mcp_server", "  other_", "vendored.sha256"),
    ],
)
def test_mutations_fail_expected_check(good_repo, rel, old, new, check):
    p = good_repo / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"fixture drift: {old!r} not in {rel}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    assert check in failed(good_repo)


def test_skill_dir_name_mismatch(good_repo):
    (good_repo / "plugins/teradata-vantage/skills/health").rename(good_repo / "plugins/teradata-vantage/skills/healthy")
    assert "skill.name" in failed(good_repo)


def test_skill_too_large_and_combined_length(good_repo):
    p = good_repo / "plugins/teradata-vantage/skills/health/SKILL.md"
    p.write_text(p.read_text() + "x" * 20001)
    f = failed(good_repo)
    assert "skill.size" in f
    p2 = good_repo / "plugins/teradata-vantage/skills/teradata-sql/SKILL.md"
    p2.write_text(p2.read_text().replace("license: MIT", "when_to_use: " + "trigger " * 220 + "\nlicense: MIT"))
    assert "skill.combined" in failed(good_repo)


def test_hooks_shell_form_and_missing_script(good_repo):
    hj = good_repo / "plugins/teradata-vantage/hooks/hooks.json"
    data = json.loads(hj.read_text())
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    hook_shell = dict(hook)
    hook_shell["command"] = "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hooks/sql_read_guard.py"
    del hook_shell["args"]
    data["hooks"]["PreToolUse"][0]["hooks"] = [hook_shell]
    hj.write_text(json.dumps(data))
    assert "hooks.exec-form" in failed(good_repo)
    hook_missing = dict(hook)
    hook_missing["args"] = ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks/does_not_exist.py"]
    data["hooks"]["PreToolUse"][0]["hooks"] = [hook_missing]
    hj.write_text(json.dumps(data))
    assert "hooks.script" in failed(good_repo)
    hj.write_text("{not json")
    assert "hooks.json" in failed(good_repo)
    hj.unlink()
    assert "hooks.missing" in failed(good_repo)


def test_plugin_json_forbidden_keys_and_slug(good_repo):
    pj = good_repo / "plugins/teradata-vantage/.claude-plugin/plugin.json"
    data = json.loads(pj.read_text())
    data["category"] = "database"
    data["skills"] = "./skills"
    pj.write_text(json.dumps(data))
    assert "plugin.keys" in failed(good_repo)
    data = json.loads(pj.read_text())
    del data["category"], data["skills"]
    data["userConfig"]["database_uri"].pop("title")
    pj.write_text(json.dumps(data))
    assert "plugin.userConfig" in failed(good_repo)


def test_plugin_root_forbidden_files(good_repo):
    plugin = good_repo / "plugins/teradata-vantage"
    (plugin / "settings.json").write_text("{}")
    (plugin / "bin").mkdir()
    (plugin / "bin" / "tool").write_text("#!/bin/sh\n")
    f = failed(good_repo)
    assert {"plugin.settings-json", "plugin.bin"} <= f


def test_marketplace_checks(good_repo):
    mp = good_repo / ".claude-plugin/marketplace.json"
    data = json.loads(mp.read_text())
    entry = data["plugins"][0]
    entry["version"] = "0.1.0"
    entry["description"] = " padded "
    mp.write_text(json.dumps(data))
    f = failed(good_repo)
    assert {"marketplace.version", "marketplace.description"} <= f
    data = json.loads(mp.read_text())
    data["plugins"][0]["source"] = "./plugins/missing"
    data["plugins"][0]["description"] = "Fine description without padding."
    del data["plugins"][0]["version"]
    data["name"] = "anthropic-plugins-v2"
    mp.write_text(json.dumps(data))
    f = failed(good_repo)
    assert {"marketplace.source", "marketplace.slug"} <= f


def test_hidden_unicode_is_a_failure(good_repo):
    p = good_repo / "plugins/teradata-vantage/README.md"
    p.write_text("# Title\u200b\n", encoding="utf-8")
    assert "unicode" in failed(good_repo)


def test_leaks_are_failures(good_repo):
    p = good_repo / "plugins/teradata-vantage/README.md"
    p.write_text("# Title\nconnect to " + "host" + ".docker.internal\n", encoding="utf-8")
    assert "leaks" in failed(good_repo)


def test_long_tool_names_are_not_a_finding(good_repo):
    """A namespaced tool name over 64 characters is NOT a defect.

    The validator enforced a 64-char ceiling until 2026-09-05. It had no empirical basis:
    on Claude Code 2.1.261 a live `ToolSearch select:` returns the full schemas for both a
    65- and a 67-character name from this plugin, so nothing is dropped. The rule reddened
    CI and would have forced a needless rename of the MCP server key. This test pins the
    withdrawal so it cannot creep back without someone deleting the test on purpose.
    """
    long_tool = "dba_aVeryLongToolNameThatOverflowsSixtyFourCharacters"
    inv = good_repo / "plugins/teradata-vantage/scripts/data/mcp_tools.yaml"
    inv.write_text(inv.read_text().replace("dba_sessionInfo]", f"dba_sessionInfo, {long_tool}]"))
    agent = good_repo / "plugins/teradata-vantage/agents/dba.md"
    agent.write_text(agent.read_text().replace(f"{PREFIX}dba_databaseSpace", f"{PREFIX}{long_tool}"))
    assert len(PREFIX + long_tool) > 64, "the fixture must actually exceed the old ceiling"

    rep = vp.run_checks(good_repo, use_cli=False)
    assert "toolname.length" not in rep.failed_checks()
    assert not any(f.check == "toolname.length" for f in rep.findings), (
        "the withdrawn 64-char rule must emit neither a FAIL nor a WARN"
    )


def test_a_tool_not_in_the_inventory_is_still_a_failure(good_repo):
    """Withdrawing the length rule must not weaken the check that DOES matter."""
    agent = good_repo / "plugins/teradata-vantage/agents/dba.md"
    agent.write_text(agent.read_text().replace(f"{PREFIX}dba_databaseSpace", f"{PREFIX}dba_toolThatDoesNotExist"))
    assert any(c.startswith("agent") and c.endswith(".tools") for c in failed(good_repo))


def test_as_list_handles_strings_and_lists():
    assert vp.as_list("Read, Grep Bash(git *), Workflow(teradata-vantage:health-audit)") == [
        "Read", "Grep", "Bash(git *)", "Workflow(teradata-vantage:health-audit)"]
    assert vp.as_list(["a", "b, c"]) == ["a", "b", "c"]
    assert vp.as_list(None) == []


def test_workflow_meta_name_parsing():
    src = "/* c */\nexport const meta = {\n  name: 'drop-impact',\n  description: 'x' };"
    assert vp.parse_meta_name(src) == "drop-impact"
    assert vp.strip_leading_comments(src).startswith("export const meta")
    assert vp.parse_meta_name("export default 1") is None


# ---------------------------------------------------------------- profile module-gate rule
# REGRESSION 2026-09-08. `^sec_userDbPermissions$` in tv_readonly and
# `^tdvs_(list|get_details|...)$` in tv_analyst named tools whose MODULE the server never loaded,
# because upstream decides module loading by matching each pattern against `<prefix>_test` (and, for
# the optional modules, `<prefix>_*`). Both profiles silently exposed zero tools from that module.
# Measured: tv_readonly 28 -> 29 tools once the sec_ pattern became prefix-shaped.

def _write_profiles(repo: Path, body: str) -> None:
    p = repo / "plugins" / "teradata-vantage" / "config" / "profiles.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_exact_name_tool_pattern_is_a_failure(good_repo):
    _write_profiles(
        good_repo,
        "tv_x:\n  tool:\n    - ^sec_userDbPermissions$\n  prompt:\n    - .*\n  resource:\n    - .*\n",
    )
    assert "profiles.module_gate" in failed(good_repo)


def test_prefix_shaped_tool_pattern_passes(good_repo):
    _write_profiles(
        good_repo,
        "tv_x:\n  tool:\n    - ^sec_(?!(rolePermissions|userRoles)$).*\n  prompt:\n    - .*\n  resource:\n    - .*\n",
    )
    assert "profiles.module_gate" not in failed(good_repo)


def test_optional_module_needs_the_second_gate_too(good_repo):
    """tdvs/bar/chat/fs pass module_loader on `<prefix>_test` AND app.py on `<prefix>_*`."""
    _write_profiles(
        good_repo,
        "tv_x:\n  tool:\n    - ^tdvs_(list|get_details)$\n  prompt:\n    - .*\n  resource:\n    - .*\n",
    )
    assert "profiles.module_gate" in failed(good_repo)
    _write_profiles(
        good_repo,
        "tv_x:\n  tool:\n    - ^tdvs_(?!(create|destroy)$).*\n  prompt:\n    - .*\n  resource:\n    - .*\n",
    )
    assert "profiles.module_gate" not in failed(good_repo)


def test_a_profile_naming_no_module_prefix_is_unaffected(good_repo):
    _write_profiles(good_repo, "tv_x:\n  tool:\n    - .*\n  prompt:\n    - .*\n  resource:\n    - .*\n")
    assert "profiles.module_gate" not in failed(good_repo)


# ---------------------------------------------------------------- agent skills must be qualified
# REGRESSION 2026-09-08. A bare `skills:` entry resolves against every skill source and a same-named
# project or user skill WINS, silently. Proven: a decoy `.claude/skills/health/SKILL.md` in the working
# directory was preloaded into the plugin's own subagent instead of the plugin's `health`, with no
# warning and nothing in the debug log. The generic names this plugin ships — health, explore, profile,
# tune, archive — are exactly the ones a project is likely to define too.

def _agent_with_skills(repo: Path, entries: str) -> None:
    p = repo / "plugins" / "teradata-vantage" / "agents" / "probe.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\nname: probe\ndescription: A probe agent used by the test suite.\n"
        f"skills:\n{entries}---\n\nBody.\n",
        encoding="utf-8",
    )


def test_bare_skill_entry_warns(good_repo):
    _agent_with_skills(good_repo, "  - health\n")
    rep = vp.run_checks(good_repo, use_cli=False)
    warnings = [f for f in rep.findings if f.level == "WARN"]
    assert any(
        w.check == "agent.skills" and "bare name" in w.message and "probe.md" in w.path
        for w in warnings
    ), [
        w.format() for w in warnings
    ]


def test_qualified_skill_entry_does_not_warn(good_repo):
    _agent_with_skills(good_repo, "  - teradata-vantage:health\n")
    rep = vp.run_checks(good_repo, use_cli=False)
    warnings = [
        f
        for f in rep.findings
        if f.level == "WARN" and f.check == "agent.skills" and "probe.md" in f.path
    ]
    assert not warnings, [w.format() for w in warnings]


def test_qualified_entry_pointing_at_a_missing_skill_still_fails(good_repo):
    _agent_with_skills(good_repo, "  - teradata-vantage:no-such-skill\n")
    assert "agent.skills" in failed(good_repo)


def test_every_shipped_agent_qualifies_its_skills():
    """The real agents, not a fixture: a bare entry here is a live shadowing risk."""
    import yaml as _yaml

    agents_dir = Path(vp.__file__).resolve().parent.parent / "agents"
    for md in sorted(agents_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        fm = _yaml.safe_load(text.split("---", 2)[1])
        for entry in fm.get("skills") or []:
            assert entry.startswith("teradata-vantage:"), f"{md.name}: bare skills entry {entry!r}"
