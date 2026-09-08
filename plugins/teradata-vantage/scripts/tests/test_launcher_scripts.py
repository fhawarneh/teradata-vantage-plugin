"""Tests for the five shell entry points under ``scripts/``.

Pure unit tests: no Teradata, no network, no docker daemon. Each test runs the real script with
``bash`` against a fixture plugin tree, and every assertion pins behaviour that a prior version of
the script got wrong:

* ``monitor_health.sh`` resolved the plugin data directory itself and missed the
  ``<plugin>-<marketplace>`` suffix, so it never found the credential file and exited 0 in silence.
* ``doctor.sh`` used ``declare -A``, which needs bash 4 — macOS ships bash 3.2 as ``/bin/bash``.
* ``install-server.sh`` ran an unpinned ``pip install --upgrade pip`` and read a plugin option that
  cannot reach it.
* ``launch-mcp.sh`` started the container without the profile config directory, so docker mode died
  with ``ValueError: Profile 'tv_all' not found``.
* ``teradata-vantage-connect`` passed the password to ``python3`` in argv, where ``ps`` shows it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
PLUGIN_ROOT = os.path.dirname(SCRIPTS_DIR)

MONITOR = os.path.join(SCRIPTS_DIR, "monitor_health.sh")
DOCTOR = os.path.join(SCRIPTS_DIR, "doctor.sh")
LAUNCH = os.path.join(SCRIPTS_DIR, "launch-mcp.sh")
INSTALL = os.path.join(SCRIPTS_DIR, "install-server.sh")
CONNECT = os.path.join(SCRIPTS_DIR, "teradata-vantage-connect")
PLUGIN_ENV = os.path.join(SCRIPTS_DIR, "_plugin_env.sh")

SHELL_SCRIPTS = [MONITOR, DOCTOR, LAUNCH, INSTALL, CONNECT, PLUGIN_ENV]


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def run(argv, env, timeout=60):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def kv(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


@pytest.fixture
def cache_tree(tmp_path):
    """A plugin root shaped like a marketplace cache: .../plugins/cache/<marketplace>/<plugin>/<version>/."""
    root = tmp_path / "plugins" / "cache" / "acme-marketplace" / "teradata-vantage" / "0.1.0"
    (root / "scripts").mkdir(parents=True)
    for src in (MONITOR, PLUGIN_ENV):
        dst = root / "scripts" / os.path.basename(src)
        dst.write_text(read(src), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return root, home


def base_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN")}
    env["HOME"] = str(home)
    env.update(extra)
    return env


# --------------------------------------------------------------- monitor_health.sh path resolution
def test_monitor_resolves_the_plugin_marketplace_data_dir(cache_tree):
    root, home = cache_tree
    rc, out, err = run(["bash", str(root / "scripts" / "monitor_health.sh"), "--dry-run"], base_env(home))
    assert rc == 0, err
    data = kv(out)["data_dir"]
    # the writer (teradata-vantage-connect) uses <plugin>-<marketplace>; the reader must agree
    assert data == str(home / ".claude" / "plugins" / "data" / "teradata-vantage-acme-marketplace")
    assert data != str(home / ".claude" / "plugins" / "data" / "teradata-vantage")
    assert kv(out)["env_file"] == os.path.join(data, "teradata.env")


def test_monitor_treats_an_unexpanded_placeholder_argument_as_unset(cache_tree):
    root, home = cache_tree
    rc, out, _ = run(
        ["bash", str(root / "scripts" / "monitor_health.sh"), "--dry-run", "${CLAUDE_PLUGIN_DATA}"],
        base_env(home),
    )
    assert rc == 0
    assert kv(out)["data_dir"].endswith("teradata-vantage-acme-marketplace")


def test_monitor_honours_an_explicit_data_dir_argument(cache_tree, tmp_path):
    root, home = cache_tree
    explicit = tmp_path / "explicit-data"
    rc, out, _ = run(
        ["bash", str(root / "scripts" / "monitor_health.sh"), "--dry-run", str(explicit) + "/"],
        base_env(home),
    )
    assert rc == 0
    assert kv(out)["data_dir"] == str(explicit)


def test_monitor_uses_the_shared_resolver():
    code = "\n".join(ln for ln in read(MONITOR).splitlines() if not ln.strip().startswith("#"))
    assert "_plugin_env.sh" in code and "tv_resolve_paths" in code
    # no hand-rolled fallback: that is what dropped the <plugin>-<marketplace> suffix
    assert ".claude/plugins/data" not in code


def test_monitor_stays_silent_when_disabled(tmp_path):
    rc, out, _ = run(["bash", MONITOR, "--dry-run"], base_env(tmp_path, TERADATA_MONITORS="0"))
    assert rc == 0 and out == ""


# ------------------------------------------------------------------------ bash 3.2 compatibility
BASH4_ONLY = re.compile(r"declare\s+-A|\blocal\s+-A|\bmapfile\b|\breadarray\b|\$\{[A-Za-z_][A-Za-z_0-9]*(,,|\^\^)")


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: os.path.basename(p))
def test_scripts_use_no_bash4_only_constructs(script):
    # macOS ships bash 3.2 as /bin/bash and both doctor call sites resolve `bash` through PATH.
    hits = [ln for ln in read(script).splitlines() if BASH4_ONLY.search(ln) and not ln.strip().startswith("#")]
    assert hits == []


def test_doctor_short_reports_a_mode(tmp_path):
    env = base_env(tmp_path, CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    rc, out, err = run(["bash", DOCTOR, "--short"], env)
    assert rc == 0, err
    assert "server mode=" in out


# --------------------------------------------------------------------------- install-server.sh
def test_install_server_installs_no_unpinned_package():
    body = read(INSTALL)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "--upgrade pip" not in stripped, line
        if "install" in stripped and ("PIP[@]" in stripped or "-m pip" in stripped):
            assert "--require-hashes" in stripped, line


def test_install_server_does_not_read_an_option_that_cannot_reach_it():
    # CLAUDE_PLUGIN_OPTION_* / TERADATA_MCP_OPTION_* are delivered to hook and MCP server processes,
    # never to this installer, so reading one only pretends the plugin option works.
    body = read(INSTALL)
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert "CLAUDE_PLUGIN_OPTION_" not in code
    assert "TERADATA_MCP_OPTION_" not in code
    assert 'EXTRAS="${TERADATA_MCP_EXTRAS:-}"' in code


def test_install_server_records_the_extras_it_installed():
    assert "INSTALLED_EXTRAS" in read(INSTALL)


# ------------------------------------------------------------------------------- launch-mcp.sh
@pytest.fixture
def docker_stub(tmp_path):
    """A no-op `docker` on PATH, so the launcher's docker branch is reachable without a daemon."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(bindir)


def docker_dry_run(tmp_path, docker_stub, **extra):
    env = base_env(
        tmp_path,
        PATH=docker_stub + os.pathsep + os.environ.get("PATH", ""),
        CLAUDE_PLUGIN_DATA=str(tmp_path / "data"),
        DATABASE_URI="teradata://u:p@example.invalid:1025/u",
        TERADATA_MCP_MODE="docker",
        TERADATA_MCP_DOCKER_IMAGE="example/teradata-mcp-server:0.2.6",
        **extra,
    )
    rc, out, err = run(["bash", LAUNCH, "--dry-run"], env)
    assert rc == 0, err
    return kv(out)


def test_docker_mode_mounts_the_profile_config_dir(tmp_path, docker_stub):
    fields = docker_dry_run(tmp_path, docker_stub)
    assert fields["mode"] == "docker"
    argv = fields["docker_argv"]
    # tv_all lives only in the plugin's config/profiles.yml; without the mount AND the name the server
    # exits at startup with "Profile 'tv_all' not found".
    assert "-e CONFIG_DIR=/opt/tv-config" in argv
    assert f"-v {fields['config_dir']}:/opt/tv-config:ro" in argv


def test_docker_mode_appends_nothing_after_the_image(tmp_path, docker_stub):
    fields = docker_dry_run(tmp_path, docker_stub)
    argv = fields["docker_argv"].split()
    image = "example/teradata-mcp-server:0.2.6"
    assert argv[-1] == image, argv
    # the upstream image has a CMD and no ENTRYPOINT: an appended flag would REPLACE the server command
    assert "--profile" not in argv and "--config_dir" not in argv and "--mcp_transport" not in argv


def test_docker_mode_honours_a_custom_config_dir(tmp_path, docker_stub):
    custom = tmp_path / "my-config"
    custom.mkdir()
    fields = docker_dry_run(tmp_path, docker_stub, TERADATA_MCP_CONFIG_DIR=str(custom))
    assert f"-v {custom}:/opt/tv-config:ro" in fields["docker_argv"]


def test_docker_mode_passes_the_optional_settings_it_documents(tmp_path, docker_stub):
    argv = docker_dry_run(tmp_path, docker_stub)["docker_argv"]
    for name in ("TD_PEM", "PROGRESSIVE_DISCLOSURE", "TD_PAT", "TD_BASE_URL"):
        assert f"-e {name}" in argv, name


def test_launcher_reports_unknown_extras_for_a_venv_built_before_the_marker(tmp_path, docker_stub):
    data = tmp_path / "data"
    (data / "venv" / "bin").mkdir(parents=True)
    server = data / "venv" / "bin" / "teradata-mcp-server"
    server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    server.chmod(0o755)
    fields = docker_dry_run(tmp_path, docker_stub, TERADATA_MCP_EXTRAS="tdvs")
    # "none" would be a claim we cannot make about a venv installed by an older plugin version
    assert fields["extras_installed"] == "unknown"


def test_launcher_reports_requested_and_installed_extras(tmp_path, docker_stub):
    data = tmp_path / "data"
    data.mkdir()
    (data / "INSTALLED_EXTRAS").write_text("tdvs\n", encoding="utf-8")
    fields = docker_dry_run(tmp_path, docker_stub, TERADATA_MCP_EXTRAS="bar")
    assert fields["extras"] == "bar"
    assert fields["extras_installed"] == "tdvs"


# --------------------------------------------------------------------- teradata-vantage-connect
def test_connect_never_passes_the_password_in_argv():
    body = read(CONNECT)
    # /proc/<pid>/cmdline and `ps` are world-readable; /proc/<pid>/environ is not.
    assert "sys.argv" not in body
    for line in body.splitlines():
        if "python3 -c" in line and not line.strip().startswith("#"):
            # the secret may only appear BEFORE `python3`, as an environment assignment — never after it,
            # where it would become an argument in /proc/<pid>/cmdline
            assert "$PASS" not in line.split("python3", 1)[1], line
    assert 'TV_P="$PASS" python3' in body


def test_connect_clears_every_scratch_copy_of_the_secret():
    line = [ln for ln in read(CONNECT).splitlines() if ln.startswith("unset ")]
    assert line, "the helper must unset its scratch variables"
    for name in ("PASS", "ENC", "ENC_USER", "ENC_PASS", "URI"):
        assert name in line[0].split(), line[0]


def test_plugin_env_falls_back_to_the_inline_data_dir(tmp_path):
    """_plugin_env.sh mirrors common.plugin_data_dir(): with no CLAUDE_PLUGIN_DATA and a plugin
    root that is NOT a marketplace cache path, prefer <plugin>-inline when it exists."""
    home = tmp_path / "home"
    (home / ".claude" / "plugins" / "data" / "teradata-vantage-inline").mkdir(parents=True)
    root = PLUGIN_ROOT
    script = (
        'set -u; source "$1/scripts/_plugin_env.sh"; tv_resolve_paths "$1/scripts";'
        ' echo "$CLAUDE_PLUGIN_DATA"'
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(root)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    out = subprocess.run(
        ["bash", "-c", script, "_", str(root)], capture_output=True, text=True, env=env, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("teradata-vantage-inline"), out.stdout


# ---------------------------------------------------------------- tdvs extra Python floor
# The core server needs Python >= 3.11, but the tdvs closure pins numpy 2.5.2, which requires
# >= 3.12 and publishes no cp311 wheel (PyPI, checked 2026-09-08). Without this guard a 3.11
# user asking for the vector-store extra gets an opaque hash-pinned resolve failure instead of
# the real reason.

def _fake_interpreter(tmp_path, version: str):
    """A stub that answers install-server.sh's version probes as `version`."""
    major, minor = (int(p) for p in version.split(".")[:2])
    py = tmp_path / "fakepython"
    py.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-c" ]; then\n'
        '  case "$2" in\n'
        f'    *"sys.version_info >= (3, 11)"*) exit {0 if (major, minor) >= (3, 11) else 1} ;;\n'
        f'    *"sys.version_info >= (3, 12)"*) exit {0 if (major, minor) >= (3, 12) else 1} ;;\n'
        f'    *"print(sys.version.split()[0])"*) echo "{version}"; exit 0 ;;\n'
        f'    *"print(sys.executable, sys.version.split()[0])"*) echo "/usr/bin/python {version}"; exit 0 ;;\n'
        "  esac\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    return py


def _install_with(tmp_path, version: str, extras: str):
    env = dict(os.environ)
    env.update(
        {
            "TERADATA_MCP_PYTHON": str(_fake_interpreter(tmp_path, version)),
            "TERADATA_MCP_EXTRAS": extras,
            "CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        }
    )
    return subprocess.run(
        ["bash", INSTALL], capture_output=True, text=True, env=env, timeout=120
    )


def test_tdvs_extra_is_refused_on_python_311(tmp_path):
    out = _install_with(tmp_path, "3.11.9", "tdvs")
    combined = out.stdout + out.stderr
    assert out.returncode != 0, combined
    assert "tdvs" in combined and "3.12" in combined, combined


def test_tdvs_extra_is_accepted_on_python_312(tmp_path):
    out = _install_with(tmp_path, "3.12.13", "tdvs")
    combined = out.stdout + out.stderr
    assert "needs Python >= 3.12" not in combined, combined


def test_bar_extra_is_unaffected_by_the_tdvs_floor(tmp_path):
    out = _install_with(tmp_path, "3.11.9", "bar")
    combined = out.stdout + out.stderr
    assert "needs Python >= 3.12" not in combined, combined


# ---------------------------------------------------------------- bridge URL must never be echoed
# REGRESSION 2026-09-08. URL_HOST was extracted with `sed -E 's#^[a-zA-Z]+://…#\2#'`, which prints the
# pattern space UNCHANGED when it does not match. A bridge URL without a scheme therefore reached
# last-launch.txt, the dry-run, the bridge log and — through the SessionStart hook — the conversation
# transcript, userinfo and all. The launcher, doctor.sh and SECURITY.md all promise it never echoes a
# connection string, so this is the test that keeps the promise true.

SECRET = "hunt" + "er2"          # assembled: this file is scanned by the leak scanner


def _dry_run_url(tmp_path, url: str) -> str:
    env = dict(os.environ)
    env.update(
        {
            "CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
            "TERADATA_MCP_URL": url,
        }
    )
    out = subprocess.run(
        ["bash", LAUNCH, "--dry-run"], capture_output=True, text=True, env=env, timeout=60
    )
    return out.stdout + out.stderr


@pytest.mark.parametrize(
    "url",
    [
        "127.0.0.1:8001/mcp/?u=admin:" + SECRET + "@x",     # no scheme -> the old sed fell through
        "admin:" + SECRET + "@127.0.0.1:8001/mcp/",
        "ftp://admin:" + SECRET + "@host/mcp/",             # a scheme mcp-remote does not speak
        "not a url " + SECRET,
    ],
)
def test_unparsable_bridge_url_is_never_echoed(tmp_path, url):
    text = _dry_run_url(tmp_path, url)
    assert SECRET not in text, text
    assert "url_host=(unparsed)" in text, text


def test_credentialed_https_url_reports_only_the_host(tmp_path):
    text = _dry_run_url(tmp_path, f"https://admin:{SECRET}@td.example.com:8001/mcp/")
    assert SECRET not in text, text
    assert "url_host=td.example.com" in text, text


@pytest.mark.parametrize(
    "url,host",
    [
        ("http://127.0.0.1:8011/mcp/", "127.0.0.1"),
        ("https://td.example.com/mcp/", "td.example.com"),
        ("http://[2001:db8::1]:8001/mcp/", "[2001:db8::1]"),
    ],
)
def test_ordinary_bridge_urls_still_report_their_host(tmp_path, url, host):
    assert f"url_host={host}" in _dry_run_url(tmp_path, url)


def test_last_launch_record_never_carries_the_userinfo(tmp_path):
    data = tmp_path / "data"
    env = dict(os.environ)
    env.update(
        {
            "CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(data),
            "TERADATA_MCP_URL": "127.0.0.1:8001/mcp/?u=admin:" + SECRET + "@x",
        }
    )
    subprocess.run(["bash", LAUNCH, "--dry-run"], capture_output=True, text=True, env=env, timeout=60)
    record = data / "last-launch.txt"
    if record.is_file():
        assert SECRET not in record.read_text(encoding="utf-8")


def test_mcp_url_option_is_marked_sensitive():
    """A bridge URL can carry credentials in its userinfo, so Claude Code must mask it like database_uri."""
    with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["userConfig"]["mcp_url"].get("sensitive") is True


def test_launch_record_and_data_dir_are_owner_only(tmp_path):
    """The data directory holds the 0600 credential file; the launch record names the host and
    interpreter path. Neither should be world-readable on a shared machine."""
    data = tmp_path / "data"
    env = dict(os.environ)
    env.update(
        {
            "CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(data),
            "TERADATA_MCP_URL": "http://127.0.0.1:8011/mcp/",
        }
    )
    subprocess.run(
        ["bash", LAUNCH], capture_output=True, text=True, env=env, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    record = data / "last-launch.txt"
    assert record.is_file(), "the launcher should have written a launch record"
    assert oct(record.stat().st_mode & 0o777) == "0o600", oct(record.stat().st_mode & 0o777)
    assert oct(data.stat().st_mode & 0o777) == "0o700", oct(data.stat().st_mode & 0o777)


def test_no_bridge_url_reports_an_empty_host_not_a_placeholder(tmp_path):
    """`(unparsed)` must mean 'a URL was configured and could not be parsed', never 'no URL'."""
    env = dict(os.environ)
    env.update({"CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT, "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")})
    env.pop("TERADATA_MCP_URL", None)
    out = subprocess.run(
        ["bash", LAUNCH, "--dry-run"], capture_output=True, text=True, env=env, timeout=60
    )
    assert "url_host=\n" in out.stdout or out.stdout.rstrip().endswith("url_host="), out.stdout
    assert "(unparsed)" not in out.stdout, out.stdout
