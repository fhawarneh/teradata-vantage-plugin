#!/usr/bin/env python3
"""Structural validator for the teradata-vantage plugin and its marketplace.

Runs every repository-specific check the generic ``claude plugin validate`` cannot know about, then the
repository-wide forbidden-token scan (check_no_leaks.py), then — unless ``--no-cli`` or the ``claude``
binary is absent — ``claude plugin validate <plugin> --strict`` and ``claude plugin validate <repo> --strict``.
Prints a report and exits 1 on any FAIL.

Checks (each finding carries a stable check id used by scripts/tests/test_validate_plugin.py):
  skill.*        SKILL.md frontmatter gate (name regex / length / no "--", directory == name, description
                 "Use when …" >= 40 chars, description + when_to_use <= 1536, body <= 20,000 bytes, no
                 `paths` inside metadata, allowed-/disallowed-tools resolve, references/*.md <= 30,000 bytes)
  agent.*        agents/*.md: name + description present, tools resolve against scripts/data/mcp_tools.yaml,
                 skills resolve to skills/<name>, no hooks/mcpServers/permissionMode keys
  hooks.*        hooks/hooks.json parses; every command hook is exec form (command + args) and its script exists
  workflow.*     workflows/*.js start with `export const meta`, no Date.now()/Math.random()/new Date()/import(;
                 every Workflow(teradata-vantage:<x>) grant resolves to a meta.name
  plugin.*       .claude-plugin/plugin.json: only known top-level keys, none of category/tags/component paths/
                 settings, slug regex, userConfig option shape; no settings.json and no bin/ at plugin root
  marketplace.*  entry has no version, source path exists with .claude-plugin/plugin.json, slug regex,
                 description 10-2000 chars without surrounding whitespace, no hidden Unicode
  unicode        hidden-Unicode scan over every .md/.json/.yaml/.yml/.js/.py/.sh/.cfg file
  profiles.run   config/profiles.yml has no `run:` key
  vendored.*     servers/VENDORED.sha256 matches the wheel
  leaks          check_no_leaks.py hits
  cli.*          claude plugin validate --strict (plugin and marketplace root)

Usage: validate_plugin.py [--repo PATH] [--no-cli] [--quiet]
Python 3 standard library + PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "validate_plugin.py needs PyYAML: python3 -m pip install pyyaml (or run it with an interpreter that has it)\n"
    )
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_no_leaks  # noqa: E402

PLUGIN_NAME = "teradata-vantage"
SERVER_KEY = "teradata"
TOOL_PREFIX_DEFAULT = f"mcp__plugin_{PLUGIN_NAME}_{SERVER_KEY}__"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
WORKFLOW_GRANT_RE = re.compile(r"Workflow\(\s*([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)\s*\)")
WORKFLOW_META_RE = re.compile(r"export\s+const\s+meta\s*=\s*\{")
WORKFLOW_BANNED = [
    (re.compile(r"\bDate\.now\s*\("), "Date.now()"),
    (re.compile(r"\bMath\.random\s*\("), "Math.random()"),
    (re.compile(r"\bnew\s+Date\s*\("), "new Date()"),
    (re.compile(r"\bimport\s*\("), "import("),
    (re.compile(r"^\s*import\s+", re.MULTILINE), "import statement"),
]
HIDDEN_UNICODE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u180e\u061c\ufff9-\ufffb]")
# ".txt" is here so the hidden-Unicode gate can see scripts/data/forbidden_tokens.txt — the one file whose
# whole job is to forbid bidi and zero-width characters, and which the leak scan exempts by basename.
SCAN_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".js", ".py", ".sh", ".cfg", ".txt"})
PLUGIN_JSON_ALLOWED = frozenset(
    {
        "$schema", "name", "displayName", "version", "description", "author", "homepage", "repository", "license",
        "keywords", "metadata", "defaultEnabled", "userConfig", "dependencies",
    }
)
PLUGIN_JSON_FORBIDDEN = {
    "category": "marketplace-entry field; --strict fails on it in plugin.json",
    "tags": "marketplace-entry field; --strict fails on it in plugin.json",
    "skills": "component path; declaring it replaces the default scan",
    "agents": "component path; declaring it replaces the default scan",
    "hooks": "component path; hooks live in hooks/hooks.json",
    "mcpServers": "component path; the server is declared in .mcp.json",
    "workflows": "component path; declaring it replaces the default scan",
    "settings": "a plugin settings.json would hijack every session",
    "commands": "component path; legacy flat commands are not shipped",
    "lspServers": "no Teradata language server is shipped",
    "outputStyles": "component path; default directory is scanned",
    "monitors": "component path; default directory is scanned",
    "themes": "component path; default directory is scanned",
}
AGENT_KNOWN_KEYS = frozenset(
    {"name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools", "skills", "memory",
     "background", "isolation", "color"}
)
AGENT_FORBIDDEN_KEYS = {"hooks", "mcpServers", "permissionMode"}
SKILL_KNOWN_KEYS = frozenset(
    {"name", "description", "when_to_use", "license", "metadata", "allowed-tools", "disallowed-tools",
     "user-invocable", "argument-hint", "paths", "compatibility", "model", "context", "agent", "hooks",
     "disable-model-invocation", "shell"}
)
HOOK_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "Notification", "UserPromptSubmit",
     "SessionStart", "SessionEnd", "Stop", "StopFailure", "SubagentStart", "SubagentStop", "PreCompact",
     "ConfigChange", "WorktreeCreate", "WorktreeRemove", "InstructionsLoaded", "CwdChanged", "FileChanged",
     "TaskCreated", "TaskCompleted", "Elicitation", "ElicitationResult", "TeammateIdle", "Setup"}
)
RESERVED_MARKETPLACE_NAMES = frozenset(
    {"claude-plugins-official", "claude-plugins-community", "claude-community", "anthropic-marketplace",
     "anthropic-plugins", "first-party-plugins"}
)
# NOTE: a 64-character ceiling on the namespaced tool name (mcp__plugin_<plugin>_<server>__<tool>)
# was enforced here and withdrawn on 2026-09-05. It had no empirical basis: on Claude Code 2.1.261 a
# live `ToolSearch select:` returns the full schemas for both 65- and 67-character names from this
# plugin, so nothing is dropped. Do not reintroduce a ceiling without citing a current, measured
# limit in this comment -- the rule reddened CI and would have forced a needless server-key rename.


@dataclass
class Finding:
    level: str  # FAIL | WARN | INFO
    check: str
    path: str
    message: str

    def format(self) -> str:
        return f"{self.level:<4} [{self.check}] {self.path}: {self.message}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)

    def fail(self, check: str, path: Any, message: str) -> None:
        self.findings.append(Finding("FAIL", check, str(path), message))

    def warn(self, check: str, path: Any, message: str) -> None:
        self.findings.append(Finding("WARN", check, str(path), message))

    def info(self, check: str, path: Any, message: str) -> None:
        self.findings.append(Finding("INFO", check, str(path), message))

    @property
    def failures(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "FAIL"]

    def failed_checks(self) -> set:
        return {f.check for f in self.failures}


# --------------------------------------------------------------------------- helpers
def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """Return (frontmatter_yaml, body) or (None, text) when there is no leading --- block."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return None, text


def load_frontmatter(path: Path, rep: Report, check: str, root: Path) -> Optional[Dict[str, Any]]:
    fm_text, _ = split_frontmatter(read_text(path))
    if fm_text is None:
        rep.fail(check, rel(path, root), "missing YAML frontmatter (--- block)")
        return None
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        rep.fail(check, rel(path, root), f"frontmatter is not valid YAML: {exc}")
        return None
    if not isinstance(data, dict):
        rep.fail(check, rel(path, root), "frontmatter must be a mapping")
        return None
    return data


def as_list(value: Any) -> List[str]:
    """Normalise a frontmatter list that may be a YAML list or a comma/space separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        # split on commas and whitespace, but never inside parentheses so "Bash(git *)" and
        # "Workflow(teradata-vantage:x)" stay whole
        items: List[str] = []
        buf: List[str] = []
        depth = 0
        for ch in value:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            if depth == 0 and (ch == "," or ch.isspace()):
                if buf:
                    items.append("".join(buf))
                    buf = []
                continue
            buf.append(ch)
        if buf:
            items.append("".join(buf))
        return items
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for v in value:
            if isinstance(v, str):
                out.extend(as_list(v) if "," in v else [v.strip()])
        return out
    return [str(value)]


def load_yaml_file(path: Path, rep: Report, check: str, root: Path) -> Optional[Any]:
    try:
        return yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        rep.fail(check, rel(path, root), f"invalid YAML: {exc}")
        return None


def load_json_file(path: Path, rep: Report, check: str, root: Path) -> Optional[Any]:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        rep.fail(check, rel(path, root), f"invalid JSON: {exc}")
        return None


def find_hidden_unicode(text: str) -> List[Tuple[int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in HIDDEN_UNICODE_RE.finditer(line):
            ch = m.group(0)
            out.append((i, f"U+{ord(ch):04X} {unicodedata.name(ch, 'UNKNOWN')}"))
    return out


# --------------------------------------------------------------------------- inventory
@dataclass
class Inventory:
    tools: set
    prefix: str
    destructive: set
    wheel_sha256: Optional[str]


def load_inventory(plugin: Path, rep: Report, root: Path) -> Inventory:
    path = plugin / "scripts" / "data" / "mcp_tools.yaml"
    inv = Inventory(tools=set(), prefix=TOOL_PREFIX_DEFAULT, destructive=set(), wheel_sha256=None)
    if not path.is_file():
        rep.fail("inventory.missing", rel(path, root), "scripts/data/mcp_tools.yaml not found")
        return inv
    data = load_yaml_file(path, rep, "inventory.yaml", root)
    if not isinstance(data, dict):
        rep.fail("inventory.yaml", rel(path, root), "top level must be a mapping")
        return inv
    inv.prefix = str(data.get("tool_prefix") or TOOL_PREFIX_DEFAULT)
    if inv.prefix != TOOL_PREFIX_DEFAULT:
        rep.warn("inventory.prefix", rel(path, root), f"tool_prefix {inv.prefix!r} differs from {TOOL_PREFIX_DEFAULT!r}")
    for gname, group in (data.get("groups") or {}).items():
        if isinstance(group, dict):
            for t in group.get("tools") or []:
                inv.tools.add(str(t))
    for t in data.get("progressive_disclosure_tools") or []:
        inv.tools.add(str(t))
    inv.destructive = {str(t) for t in data.get("destructive_tools") or []}
    unknown_destructive = inv.destructive - inv.tools
    if unknown_destructive:
        rep.warn(
            "inventory.destructive", rel(path, root),
            f"destructive_tools not in any group (a live capture under a profile that hides them?): {sorted(unknown_destructive)}",
        )
    # the guarded / destructive lists are part of the inventory contract: hooks and agents may reference them
    inv.tools |= inv.destructive
    inv.tools |= {str(t) for t in data.get("guarded_read_tools") or []}
    inv.wheel_sha256 = data.get("wheel_sha256")
    if not inv.tools:
        rep.fail("inventory.empty", rel(path, root), "no tools listed under groups")
    return inv


def check_tool_ref(ref: str, inv: Inventory, rep: Report, where: str, check_prefix: str) -> None:
    """Validate one tool reference from an agent tools: line or a skill allowed-tools entry."""
    if not ref.startswith(inv.prefix):
        if ref.startswith("mcp__") and PLUGIN_NAME in ref:
            rep.fail(f"{check_prefix}.tools", where, f"tool {ref!r} uses a non-canonical prefix; expected {inv.prefix}<tool>")
        return
    tool = ref[len(inv.prefix):]
    if tool not in inv.tools:
        rep.fail(f"{check_prefix}.tools", where, f"tool {tool!r} is not in scripts/data/mcp_tools.yaml")


# --------------------------------------------------------------------------- workflows
def strip_leading_comments(src: str) -> str:
    i = 0
    n = len(src)
    while i < n:
        # skip whitespace
        while i < n and src[i].isspace():
            i += 1
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        break
    return src[i:]


def parse_meta_name(src: str) -> Optional[str]:
    m = WORKFLOW_META_RE.search(src)
    if not m:
        return None
    tail = src[m.end():]
    nm = re.search(r"\bname\s*:\s*(['\"`])([^'\"`]+)\1", tail)
    return nm.group(2) if nm else None


def check_workflows(plugin: Path, rep: Report, root: Path) -> Dict[str, Path]:
    names: Dict[str, Path] = {}
    wdir = plugin / "workflows"
    if not wdir.is_dir():
        rep.warn("workflow.missing", rel(wdir, root), "no workflows/ directory")
        return names
    for js in sorted(wdir.glob("*.js")):
        where = rel(js, root)
        src = read_text(js)
        body = strip_leading_comments(src)
        if not body.startswith("export const meta"):
            rep.fail("workflow.meta", where, "file must start with `export const meta = {...}` (only comments may precede it)")
        name = parse_meta_name(src)
        if not name:
            rep.fail("workflow.meta", where, "could not read meta.name (needs a literal string)")
        else:
            if name.startswith(f"{PLUGIN_NAME}:"):
                rep.fail("workflow.meta", where, f"meta.name must not carry the plugin prefix: {name!r}")
            elif not SKILL_NAME_RE.match(name):
                rep.fail("workflow.meta", where, f"meta.name {name!r} must be kebab-case")
            if name in names:
                rep.fail("workflow.meta", where, f"duplicate meta.name {name!r} (also in {rel(names[name], root)})")
            names[name] = js
            if name != js.stem:
                rep.warn("workflow.meta", where, f"meta.name {name!r} differs from file stem {js.stem!r}")
        for rx, label in WORKFLOW_BANNED:
            if rx.search(src):
                rep.fail("workflow.banned", where, f"uses {label}; pass timestamps/randomness via args and never import")
        for key in ("description", "whenToUse", "phases"):
            if not re.search(r"\b" + key + r"\s*:", src):
                rep.warn("workflow.meta", where, f"meta has no `{key}` field")
    return names


# --------------------------------------------------------------------------- skills
def check_skills(plugin: Path, inv: Inventory, workflows: Dict[str, Path], rep: Report, root: Path) -> set:
    skills_dir = plugin / "skills"
    names: set = set()
    if not skills_dir.is_dir():
        rep.fail("skill.missing", rel(skills_dir, root), "no skills/ directory")
        return names
    skill_dirs = sorted(p for p in skills_dir.iterdir() if p.is_dir())
    if not skill_dirs:
        rep.fail("skill.missing", rel(skills_dir, root), "skills/ is empty")
    for sdir in skill_dirs:
        skill_md = sdir / "SKILL.md"
        where = rel(skill_md, root)
        if not skill_md.is_file():
            rep.fail("skill.missing", rel(sdir, root), "directory has no SKILL.md")
            continue
        size = skill_md.stat().st_size
        if size > 20000:
            rep.fail("skill.size", where, f"SKILL.md is {size} bytes (> 20000); move depth into references/*.md")
        fm = load_frontmatter(skill_md, rep, "skill.frontmatter", root)
        if fm is None:
            continue
        # name (port of validate_frontmatter)
        name = fm.get("name")
        if not isinstance(name, str) or not name:
            rep.fail("skill.name", where, "frontmatter.name is required (str)")
        else:
            if name.startswith(f"{PLUGIN_NAME}:"):
                rep.fail("skill.name", where, f"name must be bare, not {name!r}")
            if len(name) > 64:
                rep.fail("skill.name", where, f"name too long ({len(name)} > 64)")
            if not SKILL_NAME_RE.match(name):
                rep.fail("skill.name", where, f"name {name!r} must be lowercase alphanumeric + hyphens, no leading/trailing hyphen")
            if "--" in name:
                rep.fail("skill.name", where, f"name {name!r} must not contain '--'")
            if name != sdir.name:
                rep.fail("skill.name", where, f"name {name!r} != directory {sdir.name!r}")
            names.add(name)
        # description
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            rep.fail("skill.description", where, "frontmatter.description is required (non-empty str)")
            desc = ""
        else:
            if len(desc) > 1024:
                rep.fail("skill.description", where, f"description too long ({len(desc)} > 1024)")
            elif len(desc) > 400:
                rep.warn("skill.description", where, f"description is {len(desc)} chars (> 400 recommended)")
            if not desc.lstrip().startswith("Use when"):
                rep.fail("skill.description", where, "description must start with 'Use when'")
            if len(desc.strip()) < 40:
                rep.fail("skill.description", where, f"description too short ({len(desc.strip())} < 40)")
        wtu = fm.get("when_to_use")
        if wtu is not None and not isinstance(wtu, str):
            rep.fail("skill.when_to_use", where, "when_to_use must be a string")
            wtu = ""
        wtu = wtu or ""
        if len(wtu) > 400:
            rep.warn("skill.when_to_use", where, f"when_to_use is {len(wtu)} chars (> 400 recommended)")
        if len(desc) + len(wtu) > 1536:
            rep.fail("skill.combined", where, f"description + when_to_use = {len(desc) + len(wtu)} chars (> 1536)")
        # license / metadata
        if fm.get("license") != "MIT":
            rep.warn("skill.license", where, f"license should be MIT (got {fm.get('license')!r})")
        meta = fm.get("metadata", {})
        if meta is None:
            meta = {}
        if not isinstance(meta, dict):
            rep.fail("skill.metadata", where, "frontmatter.metadata must be a mapping")
            meta = {}
        for k, v in meta.items():
            if isinstance(v, (dict, list)):
                rep.fail("skill.metadata", where, f"metadata.{k} must be a scalar (got {type(v).__name__})")
        if "paths" in meta:
            rep.fail("skill.metadata", where, "`paths` inside metadata is inert; put it at the top level (and only on the file-scoped skill)")
        st = meta.get("skill_type")
        if st not in ("documentation", "workflow"):
            rep.warn("skill.metadata", where, f"metadata.skill_type should be documentation|workflow (got {st!r})")
        if meta.get("category") != "teradata":
            rep.warn("skill.metadata", where, f"metadata.category should be 'teradata' (got {meta.get('category')!r})")
        if not isinstance(meta.get("version"), str):
            rep.warn("skill.metadata", where, "metadata.version should be a quoted string like \"1.0.0\"")
        if "paths" in fm and sdir.name != "sql-files":
            rep.warn("skill.paths", where, "`paths` LIMITS activation; only the file-scoped skill (sql-files) should carry it")
        for key in fm:
            if key not in SKILL_KNOWN_KEYS:
                rep.warn("skill.frontmatter", where, f"unknown frontmatter key {key!r}")
        # tools + grants
        for key in ("allowed-tools", "disallowed-tools"):
            for ref in as_list(fm.get(key)):
                m = WORKFLOW_GRANT_RE.fullmatch(ref)
                if m:
                    pfx, wname = m.group(1), m.group(2)
                    if pfx != PLUGIN_NAME:
                        rep.fail("workflow.grant", where, f"{ref}: plugin prefix must be {PLUGIN_NAME!r}")
                    elif wname not in workflows:
                        rep.fail("workflow.grant", where, f"{ref}: no workflows/*.js declares meta.name {wname!r}")
                    continue
                check_tool_ref(ref, inv, rep, where, "skill")
        # references
        refs = sdir / "references"
        if refs.is_dir():
            for md in sorted(refs.glob("*.md")):
                rsize = md.stat().st_size
                if rsize > 30000:
                    rep.fail("skill.reference-size", rel(md, root), f"{rsize} bytes (> 30000)")
                first = read_text(md).lstrip().splitlines()[:1]
                if not first or not first[0].strip():
                    rep.warn("skill.reference-header", rel(md, root), "reference has no one-line purpose header")
    return names


# --------------------------------------------------------------------------- agents
def check_agents(plugin: Path, inv: Inventory, skill_names: set, rep: Report, root: Path) -> None:
    adir = plugin / "agents"
    if not adir.is_dir():
        rep.warn("agent.missing", rel(adir, root), "no agents/ directory")
        return
    for md in sorted(adir.glob("*.md")):
        where = rel(md, root)
        fm = load_frontmatter(md, rep, "agent.frontmatter", root)
        if fm is None:
            continue
        name = fm.get("name")
        if not isinstance(name, str) or not name.strip():
            rep.fail("agent.name", where, "frontmatter.name is required")
        elif name != md.stem:
            rep.warn("agent.name", where, f"name {name!r} differs from file stem {md.stem!r}")
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            rep.fail("agent.description", where, "frontmatter.description is required")
        for key in AGENT_FORBIDDEN_KEYS:
            if key in fm:
                rep.fail("agent.forbidden-key", where, f"`{key}` is ignored in plugin agents; remove it")
        for key in fm:
            if key not in AGENT_KNOWN_KEYS and key not in AGENT_FORBIDDEN_KEYS:
                rep.warn("agent.frontmatter", where, f"unknown frontmatter key {key!r}")
        for key in ("tools", "disallowedTools"):
            for ref in as_list(fm.get(key)):
                check_tool_ref(ref, inv, rep, where, "agent")
        for s in as_list(fm.get("skills")):
            s_bare = s.split(":", 1)[1] if s.startswith(f"{PLUGIN_NAME}:") else s
            if not s.startswith(f"{PLUGIN_NAME}:"):
                # A BARE name resolves against every skill source, and a project or user skill of the same
                # name WINS. Proven 2026-09-08: a decoy `.claude/skills/health/SKILL.md` in the working
                # directory was preloaded into the plugin's own subagent instead of the plugin's `health`,
                # silently — no warning, nothing in the debug log. The generic names this plugin ships
                # (health, explore, profile, tune, archive) are exactly the ones a project is likely to
                # reuse, so qualify them.
                rep.warn(
                    "agent.skills",
                    where,
                    f"skills entry {s!r} is a bare name; a same-named project or user skill would shadow it "
                    f"silently. Write it as {PLUGIN_NAME}:{s}.",
                )
            if s_bare not in skill_names and not (plugin / "skills" / s_bare / "SKILL.md").is_file():
                rep.fail("agent.skills", where, f"skills entry {s!r} does not resolve to skills/{s_bare}/SKILL.md")
        mt = fm.get("maxTurns")
        if mt is not None and not isinstance(mt, int):
            rep.fail("agent.frontmatter", where, "maxTurns must be an integer")


# --------------------------------------------------------------------------- hooks
def check_hooks(plugin: Path, rep: Report, root: Path) -> None:
    hooks_json = plugin / "hooks" / "hooks.json"
    where = rel(hooks_json, root)
    if not hooks_json.is_file():
        rep.fail("hooks.missing", where, "hooks/hooks.json not found")
        return
    data = load_json_file(hooks_json, rep, "hooks.json", root)
    if data is None:
        return
    if not isinstance(data, dict):
        rep.fail("hooks.json", where, "top level must be an object")
        return
    events = data.get("hooks", data)
    if not isinstance(events, dict):
        rep.fail("hooks.json", where, "`hooks` must be an object keyed by event name")
        return
    for event, matchers in events.items():
        if event == "description":
            continue
        if event not in HOOK_EVENTS:
            rep.warn("hooks.event", where, f"unknown hook event {event!r}")
        if not isinstance(matchers, list):
            rep.fail("hooks.json", where, f"{event}: must be a list of matcher groups")
            continue
        for gi, group in enumerate(matchers):
            if not isinstance(group, dict):
                rep.fail("hooks.json", where, f"{event}[{gi}]: matcher group must be an object")
                continue
            matcher = group.get("matcher")
            if matcher is not None:
                try:
                    re.compile(str(matcher))
                except re.error as exc:
                    rep.fail("hooks.matcher", where, f"{event}[{gi}]: matcher does not compile: {exc}")
            for hi, hook in enumerate(group.get("hooks") or []):
                loc = f"{event}[{gi}].hooks[{hi}]"
                if not isinstance(hook, dict):
                    rep.fail("hooks.json", where, f"{loc}: hook must be an object")
                    continue
                htype = hook.get("type")
                if htype != "command":
                    rep.fail("hooks.type", where, f"{loc}: only command hooks are shipped (got {htype!r})")
                    continue
                cmd = hook.get("command")
                args = hook.get("args")
                if not isinstance(cmd, str) or not cmd:
                    rep.fail("hooks.exec-form", where, f"{loc}: `command` must be a non-empty string")
                    continue
                if not isinstance(args, list):
                    rep.fail("hooks.exec-form", where, f"{loc}: exec form requires `args` as a list (shell strings are not allowed)")
                    continue
                if any(ch in cmd for ch in " \t|&;$<>`"):
                    rep.fail("hooks.exec-form", where, f"{loc}: `command` must be a bare program name, not a shell string: {cmd!r}")
                script_refs = [a for a in args if isinstance(a, str) and "${CLAUDE_PLUGIN_ROOT}" in a]
                if not script_refs:
                    rep.warn("hooks.script", where, f"{loc}: no argument references ${{CLAUDE_PLUGIN_ROOT}}")
                for a in script_refs:
                    resolved = Path(a.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin)))
                    if not resolved.is_file():
                        rep.fail("hooks.script", where, f"{loc}: script does not exist: {a}")
                for a in args:
                    if isinstance(a, str) and "${CLAUDE_PLUGIN_DATA}" not in a and re.search(r"\$\{user_config\.", a):
                        rep.warn("hooks.script", where, f"{loc}: ${{user_config.*}} is not substituted in hooks")


# --------------------------------------------------------------------------- manifests
def check_plugin_json(plugin: Path, rep: Report, root: Path) -> Optional[Dict[str, Any]]:
    pj = plugin / ".claude-plugin" / "plugin.json"
    where = rel(pj, root)
    if not pj.is_file():
        rep.fail("plugin.missing", where, "plugin.json not found")
        return None
    data = load_json_file(pj, rep, "plugin.json", root)
    if not isinstance(data, dict):
        rep.fail("plugin.json", where, "plugin.json must be an object")
        return None
    for key in data:
        if key in PLUGIN_JSON_FORBIDDEN:
            rep.fail("plugin.keys", where, f"forbidden key `{key}`: {PLUGIN_JSON_FORBIDDEN[key]}")
        elif key not in PLUGIN_JSON_ALLOWED:
            rep.fail("plugin.keys", where, f"unknown top-level key `{key}` (fails claude plugin validate --strict)")
    name = data.get("name")
    if not isinstance(name, str) or not SLUG_RE.match(name):
        rep.fail("plugin.slug", where, f"name {name!r} must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
    elif name != PLUGIN_NAME:
        rep.warn("plugin.slug", where, f"name {name!r} != expected {PLUGIN_NAME!r}")
    version = data.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        rep.fail("plugin.version", where, f"version {version!r} must be semver (x.y.z)")
    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        rep.fail("plugin.description", where, "description is required")
    for k in ("name", "displayName", "description"):
        v = data.get(k)
        if isinstance(v, str) and HIDDEN_UNICODE_RE.search(v):
            rep.fail("unicode", where, f"hidden Unicode in {k}")
    if data.get("$schema") not in (None, "https://json.schemastore.org/claude-code-plugin-manifest.json"):
        rep.warn("plugin.schema", where, f"unexpected $schema {data.get('$schema')!r}")
    if data.get("license") != "MIT":
        rep.warn("plugin.license", where, f"license should be MIT (got {data.get('license')!r})")
    uc = data.get("userConfig")
    if uc is not None:
        if not isinstance(uc, dict):
            rep.fail("plugin.userConfig", where, "userConfig must be an object")
        else:
            for opt, spec in uc.items():
                if not isinstance(spec, dict):
                    rep.fail("plugin.userConfig", where, f"userConfig.{opt} must be an object")
                    continue
                for req in ("type", "title", "description"):
                    if not isinstance(spec.get(req), str) or not spec.get(req):
                        rep.fail("plugin.userConfig", where, f"userConfig.{opt}.{req} is required")
                if spec.get("type") not in ("string", "boolean", "number", "integer", None):
                    rep.fail("plugin.userConfig", where, f"userConfig.{opt}.type {spec.get('type')!r} is not a known type")
                if not re.match(r"^[a-z][a-z0-9_]*$", opt):
                    rep.warn("plugin.userConfig", where, f"option key {opt!r} should be lower_snake_case")
    # forbidden files at plugin root
    if (plugin / "settings.json").exists():
        rep.fail("plugin.settings-json", rel(plugin / "settings.json", root), "a plugin settings.json is not shipped (it would apply to every session)")
    if (plugin / "bin").exists():
        rep.fail("plugin.bin", rel(plugin / "bin", root), "top-level bin/ blocks organisation distribution; keep executables in scripts/")
    if (plugin / "CLAUDE.md").exists():
        rep.warn("plugin.claude-md", rel(plugin / "CLAUDE.md", root), "plugin CLAUDE.md is never loaded")
    mcp = plugin / ".mcp.json"
    if mcp.is_file():
        mdata = load_json_file(mcp, rep, "plugin.mcp-json", root)
        if isinstance(mdata, dict):
            servers = mdata.get("mcpServers", mdata)
            if not isinstance(servers, dict) or SERVER_KEY not in servers:
                rep.fail("plugin.mcp-json", rel(mcp, root), f"expected an mcpServers.{SERVER_KEY} entry")
    else:
        rep.fail("plugin.mcp-json", rel(mcp, root), ".mcp.json not found")
    for p in (plugin / "README.md", plugin / "LICENSE"):
        if not p.is_file():
            rep.warn("plugin.docs", rel(p, root), "missing")
    return data


def check_marketplace(repo: Path, plugin: Path, rep: Report) -> None:
    mp = repo / ".claude-plugin" / "marketplace.json"
    where = rel(mp, repo)
    if not mp.is_file():
        rep.fail("marketplace.missing", where, "marketplace.json not found at the repository root")
        return
    data = load_json_file(mp, rep, "marketplace.json", repo)
    if not isinstance(data, dict):
        return
    mname = data.get("name")
    if not isinstance(mname, str) or not SLUG_RE.match(mname):
        rep.fail("marketplace.slug", where, f"marketplace name {mname!r} must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
    elif mname in RESERVED_MARKETPLACE_NAMES or re.search(r"anthropic|official|claude-plugins", mname):
        rep.fail("marketplace.slug", where, f"marketplace name {mname!r} is reserved or impersonates an official marketplace")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        rep.fail("marketplace.entry", where, "plugins[] is missing or empty")
        return
    names = [p.get("name") for p in plugins if isinstance(p, dict)]
    if names != sorted(names):
        rep.warn("marketplace.sorted", where, "plugins[] is not sorted alphabetically by name")
    if len(set(names)) != len(names):
        rep.fail("marketplace.entry", where, "duplicate plugin names")
    entry = next((p for p in plugins if isinstance(p, dict) and p.get("name") == PLUGIN_NAME), None)
    if entry is None:
        rep.fail("marketplace.entry", where, f"no entry named {PLUGIN_NAME!r}")
        return
    if "version" in entry:
        rep.fail("marketplace.version", where, "entry must not carry `version` (plugin.json is the single source)")
    for key in ("skills", "agents", "hooks", "mcpServers", "workflows", "commands"):
        if key in entry:
            rep.fail("marketplace.entry", where, f"entry must not carry component field `{key}`")
    ename = entry.get("name")
    if not isinstance(ename, str) or not SLUG_RE.match(ename):
        rep.fail("marketplace.slug", where, f"entry name {ename!r} must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
    desc = entry.get("description")
    if not isinstance(desc, str):
        rep.fail("marketplace.description", where, "entry description is required")
    else:
        if desc != desc.strip():
            rep.fail("marketplace.description", where, "description has leading/trailing whitespace")
        if not (10 <= len(desc) <= 2000):
            rep.fail("marketplace.description", where, f"description length {len(desc)} not in 10..2000")
        if HIDDEN_UNICODE_RE.search(desc):
            rep.fail("unicode", where, "hidden Unicode in entry description")
    if isinstance(ename, str) and HIDDEN_UNICODE_RE.search(ename):
        rep.fail("unicode", where, "hidden Unicode in entry name")
    source = entry.get("source")
    if isinstance(source, str):
        if not source.startswith("./"):
            rep.fail("marketplace.source", where, f"relative source must start with ./ (got {source!r})")
        src_dir = (repo / source).resolve()
        if not src_dir.is_dir():
            rep.fail("marketplace.source", where, f"source {source!r} does not exist")
        elif not (src_dir / ".claude-plugin" / "plugin.json").is_file():
            rep.fail("marketplace.source", where, f"source {source!r} has no .claude-plugin/plugin.json")
        elif src_dir != plugin.resolve():
            rep.warn("marketplace.source", where, f"source {source!r} is not the validated plugin directory")
    elif isinstance(source, dict):
        rep.warn("marketplace.source", where, "entry uses an object source; the in-repo layout expects ./plugins/<name>")
    else:
        rep.fail("marketplace.source", where, "entry source is required")
    mdesc = data.get("description")
    if isinstance(mdesc, str) and (mdesc != mdesc.strip() or not (10 <= len(mdesc) <= 2000)):
        rep.warn("marketplace.description", where, "marketplace description should be 10..2000 chars without surrounding whitespace")


# --------------------------------------------------------------------------- misc checks
def check_hidden_unicode(repo: Path, rep: Report) -> None:
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if any(part in check_no_leaks.SKIP_DIRS for part in path.relative_to(repo).parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, what in find_hidden_unicode(text):
            rep.fail("unicode", f"{rel(path, repo)}:{line_no}", f"hidden Unicode {what}")


#: Tool-module prefixes the bundled server can load. Two upstream gates decide whether a module loads at
#: all, and both are satisfied only by a PREFIX-shaped pattern — see the rule inside check_profiles.
MODULE_PREFIXES = ("base", "dba", "sec", "qlty", "plot", "rag", "sql", "graph", "tdvs", "bar", "chat", "fs")
#: These additionally pass through app.py's `re.match(pattern, "<prefix>_*")` feature gate.
OPTIONAL_MODULE_PREFIXES = frozenset({"tdvs", "bar", "chat", "fs"})


def _matches(pattern: str, name: str) -> bool:
    """`re.match` the way the server does it, tolerating a pattern that does not compile."""
    try:
        return re.match(pattern, name) is not None
    except re.error:
        return False


def check_profiles(plugin: Path, rep: Report, root: Path) -> None:
    prof = plugin / "config" / "profiles.yml"
    where = rel(prof, root)
    if not prof.is_file():
        rep.fail("profiles.missing", where, "config/profiles.yml not found")
        return
    data = load_yaml_file(prof, rep, "profiles.yaml", root)
    if not isinstance(data, dict):
        rep.fail("profiles.yaml", where, "top level must be a mapping of profiles")
        return
    for pname, profile in data.items():
        if not isinstance(profile, dict):
            rep.fail("profiles.yaml", where, f"profile {pname!r} must be a mapping")
            continue
        if "run" in profile:
            rep.fail("profiles.run", where, f"profile {pname!r} carries a `run:` block (it would inject database_uri/transport into the environment)")
        if "resource" not in profile:
            rep.warn("profiles.resource", where, f"profile {pname!r} has no `resource:` key (exposes zero MCP resources)")
        for key in ("tool", "prompt", "resource"):
            for pat in profile.get(key) or []:
                try:
                    re.compile(str(pat))
                except re.error as exc:
                    rep.fail("profiles.yaml", where, f"profile {pname!r} {key} pattern {pat!r} does not compile: {exc}")

        # MODULE-GATE RULE. The bundled server loads a tool module only when some `tool:` pattern is
        # PREFIX-shaped for it: tools/module_loader.py matches each pattern against "<prefix>_test", and
        # app.py additionally matches the optional modules against "<prefix>_*". A pattern pinned to one
        # exact tool name (`^sec_userDbPermissions$`) satisfies neither, so the module never loads and the
        # tool it names never appears — a silently empty surface, not an error. Measured 2026-09-08:
        # tv_readonly went 28 -> 29 tools when its sec_ pattern was made prefix-shaped, and tv_analyst was
        # exposing no tdvs_* tool at all. Write single-tool selections as `^<prefix>_(?!(a|b)$).*`.
        patterns = [str(pat) for pat in (profile.get("tool") or [])]
        for prefix in MODULE_PREFIXES:
            named = [pat for pat in patterns if pat.startswith("^" + prefix + "_")]
            if not named:
                continue
            loads = any(_matches(pat, f"{prefix}_test") for pat in patterns)
            gated = prefix in OPTIONAL_MODULE_PREFIXES
            enabled = (not gated) or any(_matches(pat, f"{prefix}_*") for pat in patterns)
            if not loads or not enabled:
                which = "module_loader" if not loads else "the optional-module gate"
                rep.fail(
                    "profiles.module_gate",
                    where,
                    f"profile {pname!r} names {prefix}_ tools ({named[0]!r}) but no pattern is prefix-shaped, "
                    f"so {which} never enables the module and the profile exposes NO {prefix}_ tool. "
                    f"Use ^{prefix}_(?!(...)$).* instead of an exact-name anchor.",
                )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_vendored(plugin: Path, inv: Inventory, rep: Report, root: Path) -> None:
    servers = plugin / "servers"
    sha_file = servers / "VENDORED.sha256"
    where = rel(sha_file, root)
    if not sha_file.is_file():
        rep.fail("vendored.missing", where, "servers/VENDORED.sha256 not found")
        return
    entries = []
    for line in read_text(sha_file).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-f]{64})\s+\*?(\S+)$", line)
        if not m:
            rep.fail("vendored.sha256", where, f"malformed line: {line!r}")
            continue
        entries.append((m.group(1), m.group(2)))
    if not entries:
        rep.fail("vendored.sha256", where, "no checksum lines")
        return
    wheels = sorted(servers.glob("*.whl"))
    if not wheels:
        rep.fail("vendored.wheel", rel(servers, root), "no vendored wheel in servers/")
    for digest, fname in entries:
        target = servers / fname
        if not target.is_file():
            rep.fail("vendored.sha256", where, f"{fname} listed but missing from servers/")
            continue
        actual = sha256_file(target)
        if actual != digest:
            rep.fail("vendored.sha256", where, f"{fname}: sha256 {actual} != recorded {digest}")
        elif fname.endswith(".whl") and inv.wheel_sha256 and str(inv.wheel_sha256) != actual:
            rep.fail("vendored.inventory", rel(plugin / "scripts" / "data" / "mcp_tools.yaml", root), f"wheel_sha256 {inv.wheel_sha256} != wheel {actual}")
    listed = {f for _, f in entries}
    for w in wheels:
        if w.name not in listed:
            rep.fail("vendored.sha256", where, f"wheel {w.name} is not listed in VENDORED.sha256")
    for req in ("requirements-", ):
        if not list(servers.glob(f"{req}*.txt")):
            rep.warn("vendored.requirements", rel(servers, root), "no hash-pinned requirements-<version>.txt")
    for doc in ("VENDORED.md", "NOTICE.md"):
        if not (servers / doc).is_file():
            rep.warn("vendored.docs", rel(servers / doc, root), "missing")


def check_leaks(repo: Path, plugin: Path, rep: Report) -> None:
    try:
        tokens_path = check_no_leaks.default_tokens_path(repo, SCRIPT_DIR)
        tokens = check_no_leaks.load_tokens(tokens_path)
    except (FileNotFoundError, check_no_leaks.TokenFileError) as exc:
        rep.fail("leaks", str(repo), f"cannot load forbidden tokens: {exc}")
        return
    hits = check_no_leaks.scan_repo(repo, tokens)
    for h in hits:
        rep.fail("leaks", f"{rel(h.path, repo)}:{h.line}", f"forbidden token {h.token!r} -> {h.matched!r}")
    if not hits:
        rep.info("leaks", str(repo), f"clean ({len(tokens)} patterns)")


def run_cli_validate(repo: Path, plugin: Path, rep: Report) -> None:
    claude = shutil.which("claude")
    if not claude:
        rep.warn("cli.missing", "claude", "claude binary not on PATH; skipped `claude plugin validate --strict`")
        return
    for label, target in (("plugin", plugin), ("marketplace", repo)):
        cmd = [claude, "plugin", "validate", str(target), "--strict"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            rep.fail(f"cli.{label}", str(target), f"could not run {' '.join(cmd)}: {exc}")
            continue
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
            rep.fail(f"cli.{label}", str(target), "claude plugin validate --strict failed:\n      " + "\n      ".join(tail))
        else:
            rep.info(f"cli.{label}", str(target), "claude plugin validate --strict passed")


# --------------------------------------------------------------------------- driver
def infer_repo() -> Path:
    return SCRIPT_DIR.parent.parent.parent


def locate_plugin(repo: Path) -> Path:
    candidate = repo / "plugins" / PLUGIN_NAME
    if (candidate / ".claude-plugin" / "plugin.json").is_file():
        return candidate
    if (repo / ".claude-plugin" / "plugin.json").is_file() and (repo / ".mcp.json").is_file():
        return repo
    return candidate


def run_checks(repo: Path, use_cli: bool = True) -> Report:
    repo = repo.resolve()
    plugin = locate_plugin(repo)
    rep = Report()
    if not plugin.is_dir():
        rep.fail("plugin.missing", str(plugin), "plugin directory not found")
        return rep
    check_plugin_json(plugin, rep, repo)
    check_marketplace(repo, plugin, rep)
    inv = load_inventory(plugin, rep, repo)
    workflows = check_workflows(plugin, rep, repo)
    skill_names = check_skills(plugin, inv, workflows, rep, repo)
    check_agents(plugin, inv, skill_names, rep, repo)
    check_hooks(plugin, rep, repo)
    check_profiles(plugin, rep, repo)
    check_vendored(plugin, inv, rep, repo)
    check_hidden_unicode(repo, rep)
    check_leaks(repo, plugin, rep)
    if use_cli:
        run_cli_validate(repo, plugin, rep)
    return rep


def print_report(rep: Report, quiet: bool = False) -> None:
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    for f in sorted(rep.findings, key=lambda x: (order[x.level], x.check, x.path)):
        if quiet and f.level != "FAIL":
            continue
        print(f.format())
    n_fail = len(rep.failures)
    n_warn = sum(1 for f in rep.findings if f.level == "WARN")
    verdict = "FAILED" if n_fail else "PASSED"
    print(f"\nvalidate_plugin: {verdict} — {n_fail} failure(s), {n_warn} warning(s)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the teradata-vantage plugin repository.")
    ap.add_argument("--repo", type=Path, default=None, help="marketplace/repository root (default: inferred from this script)")
    ap.add_argument("--no-cli", action="store_true", help="skip `claude plugin validate --strict`")
    ap.add_argument("--quiet", action="store_true", help="print failures only")
    args = ap.parse_args(argv)
    repo = (args.repo or infer_repo()).resolve()
    if not repo.is_dir():
        print(f"validate_plugin: not a directory: {repo}", file=sys.stderr)
        return 2
    rep = run_checks(repo, use_cli=not args.no_cli)
    print_report(rep, quiet=args.quiet)
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
