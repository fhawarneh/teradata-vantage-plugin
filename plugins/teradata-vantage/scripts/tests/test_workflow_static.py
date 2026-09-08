"""Static checks over workflows/*.js.

Three classes of mistake are invisible until a workflow is actually run, and each one wastes a full
fan-out to discover:

* an ``agentType`` naming an agent that does not exist -- the run aborts on the first spawn;
* a schema whose ``required`` names a key its ``properties`` does not define -- ``agent()`` raises
  "unsatisfiable schema" at the call, after the earlier phases have already been paid for;
* a phase that is entered but never declared in ``meta.phases`` (or declared and never entered) --
  the progress display and the script disagree about what the run is doing.

The workflow scripts are JavaScript, so the schemas cannot simply be imported. Rather than shell out
to node -- which the Python CI leg does not install -- this module carries a small recursive-descent
parser for the literal subset the schemas actually use (identifier keys, single-quoted strings,
arrays, nested objects, numbers, booleans, null). It raises on anything outside that subset instead
of skipping, so a schema written in a form it cannot read fails the suite rather than passing
silently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((PLUGIN / "workflows").glob("*.js"))
AGENT_NAMES = {p.stem for p in (PLUGIN / "agents").glob("*.md")}


# --------------------------------------------------------------------------- literal parser


class LiteralError(ValueError):
    """The literal used a construct this parser does not cover. Fail loudly, never skip."""


class _Parser:
    """Parses the JS object-literal subset the workflow schemas use."""

    def __init__(self, text: str, pos: int = 0) -> None:
        self.s = text
        self.i = pos

    def _ws(self) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n,":
                self.i += 1
            elif self.s.startswith("//", self.i):
                nl = self.s.find("\n", self.i)
                self.i = len(self.s) if nl == -1 else nl + 1
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i)
                if end == -1:
                    raise LiteralError("unterminated block comment")
                self.i = end + 2
            else:
                return

    def value(self) -> Any:
        self._ws()
        if self.i >= len(self.s):
            raise LiteralError("unexpected end of literal")
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "'\"`":
            return self.concat_string()
        for lit, val in (("true", True), ("false", False), ("null", None)):
            if self.s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        m = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?").match(self.s, self.i)
        if m:
            self.i = m.end()
            return float(m.group()) if any(ch in m.group() for ch in ".eE") else int(m.group())
        raise LiteralError(f"unsupported token at {self.i}: {self.s[self.i:self.i + 40]!r}")

    def concat_string(self) -> str:
        """A string, plus any ``+ ...`` chain after it.

        The workflows build a few descriptions as ``'text ' + CONST + ')'``. Only key names matter
        to these checks, so an interpolated identifier is rendered as its own name rather than
        resolved -- but the chain must be consumed, or the parser stops mid-object.
        """
        parts = [self.string()]
        while True:
            save = self.i
            # _ws() also eats commas, which would let it walk past a value boundary; step manually.
            while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
                self.i += 1
            if self.i < len(self.s) and self.s[self.i] == "+":
                self.i += 1
                self._ws()
                if self.i < len(self.s) and self.s[self.i] in "'\"`":
                    parts.append(self.string())
                    continue
                m = re.compile(r"[A-Za-z_$][\w$.]*").match(self.s, self.i)
                if not m:
                    raise LiteralError(f"unsupported concatenation at {self.i}")
                parts.append(m.group())
                self.i = m.end()
                continue
            self.i = save
            return "".join(parts)

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out: List[str] = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                out.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise LiteralError("unterminated string")

    def arr(self) -> List[Any]:
        self.i += 1  # [
        out: List[Any] = []
        while True:
            self._ws()
            if self.i >= len(self.s):
                raise LiteralError("unterminated array")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())

    def obj(self) -> Dict[str, Any]:
        self.i += 1  # {
        out: Dict[str, Any] = {}
        while True:
            self._ws()
            if self.i >= len(self.s):
                raise LiteralError("unterminated object")
            if self.s[self.i] == "}":
                self.i += 1
                return out
            if self.s[self.i] in "'\"":
                key = self.string()
            else:
                m = re.compile(r"[A-Za-z_$][\w$]*").match(self.s, self.i)
                if not m:
                    raise LiteralError(f"bad key at {self.i}: {self.s[self.i:self.i + 40]!r}")
                key = m.group()
                self.i = m.end()
            self._ws()
            if self.i >= len(self.s) or self.s[self.i] != ":":
                raise LiteralError(f"expected ':' after key {key!r}")
            self.i += 1
            out[key] = self.value()


def top_level_consts(text: str) -> Dict[str, Any]:
    """Every ``const NAME = { ... }`` declared at column 0, parsed."""
    found: Dict[str, Any] = {}
    for m in re.finditer(r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?=\{)", text, re.M):
        found[m.group(1)] = _Parser(text, m.end()).value()
    return found


def walk_schemas(node: Any, path: str = "") -> List[Tuple[str, Dict[str, Any]]]:
    """Every dict that declares ``properties``, with the path that reaches it."""
    out: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            out.append((path or "<root>", node))
        for k, v in node.items():
            out.extend(walk_schemas(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for n, v in enumerate(node):
            out.extend(walk_schemas(v, f"{path}[{n}]"))
    return out


# --------------------------------------------------------------------------- the checks


def test_there_are_workflows_to_check() -> None:
    assert WORKFLOWS, "no workflows found - the rest of this module would vacuously pass"
    assert AGENT_NAMES, "no agents found - the agentType check would vacuously pass"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.stem)
def test_every_agent_type_resolves_to_a_real_agent(wf: Path) -> None:
    """A typo here aborts the whole run on the first spawn, after the plan is already committed."""
    text = wf.read_text(encoding="utf-8")
    consts = dict(re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=\s*'([^']+)'", text, re.M))

    referenced: List[str] = []
    for m in re.finditer(r"agentType:\s*(?:'([^']+)'|([A-Za-z_$][\w$]*))", text):
        literal, ident = m.groups()
        if literal:
            referenced.append(literal)
        else:
            assert ident in consts, f"{wf.name}: agentType uses {ident}, which is not a string const"
            referenced.append(consts[ident])

    assert referenced, f"{wf.name}: no agentType found - this workflow spawns nothing?"

    for ref in sorted(set(referenced)):
        name = ref.split(":", 1)[1] if ":" in ref else ref
        assert name in AGENT_NAMES, (
            f"{wf.name}: agentType {ref!r} names agent {name!r}, which has no "
            f"agents/{name}.md. Known agents: {sorted(AGENT_NAMES)}"
        )


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.stem)
def test_schema_required_entries_all_exist_in_properties(wf: Path) -> None:
    """agent() rejects an unsatisfiable schema at the call, wasting every phase before it."""
    consts = top_level_consts(wf.read_text(encoding="utf-8"))
    checked = 0
    for const_name, value in consts.items():
        for path, schema in walk_schemas(value, const_name):
            required = schema.get("required")
            if required is None:
                continue
            assert isinstance(required, list), f"{wf.name}: {path}.required is not a list"
            props = set(schema["properties"])
            missing = [r for r in required if r not in props]
            assert not missing, (
                f"{wf.name}: {path} requires {missing} which its properties do not define "
                f"(properties: {sorted(props)})"
            )
            checked += 1
    assert checked, f"{wf.name}: no schema with both required and properties was found to check"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.stem)
def test_phases_declared_and_entered_agree(wf: Path) -> None:
    """meta.phases drives the progress display; a mismatch means it describes a different run."""
    text = wf.read_text(encoding="utf-8")

    meta = top_level_consts(text).get("meta")
    assert isinstance(meta, dict), f"{wf.name}: no top-level `const meta` object literal"
    declared = {p["title"] for p in meta.get("phases", []) if isinstance(p, dict) and "title" in p}
    assert declared, f"{wf.name}: meta.phases declares no titles"

    # A phase is entered either by phase('X') or by passing phase: 'X' to an agent() call.
    entered = set(re.findall(r"\bphase\(\s*'([^']+)'\s*\)", text))
    entered |= set(re.findall(r"phase:\s*'([^']+)'", text))

    assert not (entered - declared), (
        f"{wf.name}: phases entered but not declared in meta.phases: {sorted(entered - declared)}"
    )
    assert not (declared - entered), (
        f"{wf.name}: phases declared in meta.phases but never entered: {sorted(declared - entered)}"
    )


def test_meta_name_matches_the_filename() -> None:
    """Workflow(name) resolves by meta.name; a drift makes the file unreachable by its own path."""
    for wf in WORKFLOWS:
        meta = top_level_consts(wf.read_text(encoding="utf-8")).get("meta")
        assert isinstance(meta, dict), f"{wf.name}: no top-level `const meta`"
        assert meta.get("name") == wf.stem, (
            f"{wf.name}: meta.name is {meta.get('name')!r} but the file is {wf.stem!r}"
        )


def test_the_parser_rejects_what_it_cannot_read() -> None:
    """The parser must fail loudly on an unsupported construct, never return a partial schema."""
    with pytest.raises(LiteralError):
        _Parser("{ a: someFunctionCall() }").value()
    with pytest.raises(LiteralError):
        _Parser("{ a: 1 ").value()
    # and it must actually read the shapes the workflows use
    got = _Parser("{ a: 'x', b: [1, 2], c: { d: true }, e: null }").value()
    assert got == {"a": "x", "b": [1, 2], "c": {"d": True}, "e": None}
