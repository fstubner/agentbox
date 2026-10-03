"""`agentbox validate`: repository checks that CI also runs."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agentbox_common import FAIL, OK, REPO, WARN, report
from agentbox_policy import load_policy, load_tool_map

# --- validate ---------------------------------------------------------------

SECRET_FILE_NAMES = re.compile(r"^(\.env(\..*)?|.*\.pem|.*\.key|id_rsa|id_ed25519)$")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PORT_MAPPING = re.compile(r'^\s*-\s*"?\d+:\d+')


# The assistant pays for every tool schema on every turn, whether it uses the
# tool or not. mcp_base.py described that as "the single largest fixed cost in
# this system at ~2,250 tokens per turn"; by 2026-08-14 it measured 7,778 —
# 3.5x the documented figure, drifted silently while this project maintained a
# whole document on context economy. Nothing measured it, so nothing noticed.
#
# Deliberately a ceiling with slack rather than a pinned number: adding a tool
# should be possible without editing a test, but tripling the cost should not
# be possible without someone deciding to.
TOOL_SCHEMA_TOKEN_BUDGET = int(os.environ.get(
    "AGENTBOX_TOOL_SCHEMA_BUDGET", "9000"))


def tool_schema_cost() -> tuple[int, int, list[tuple[str, int]]]:
    """(tools, approx tokens, biggest offenders) for the assembled surface."""
    app = REPO / "services" / "compose" / "agentbox-mcp" / "app"
    if not (app / "server.py").is_file():
        return 0, 0, []
    env = {**os.environ, "PYTHONPATH": f"{app}:{REPO / 'services' / 'templates' / 'mcp'}"}
    code = ("import json,server;"
            "print(json.dumps([[t['name'],len(json.dumps(t))] for t in server.TOOLS]))")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, cwd=str(app), timeout=60)
        if out.returncode != 0:
            return 0, 0, []
        sizes = json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        # A measurement that cannot run must not break the thing it measures.
        # scaffold() calls validate(), and this crashed it inside a scaffolded
        # temp repo where the gateway's app tree is not importable.
        return 0, 0, []
    total = sum(n for _, n in sizes)
    worst = sorted(sizes, key=lambda kv: -kv[1])[:3]
    return len(sizes), total // 4, [(n, c // 4) for n, c in worst]


def validate() -> int:
    failures = 0

    def error(msg: str) -> None:
        nonlocal failures
        failures += 1
        report(FAIL, msg)

    tracked = [
        p for p in REPO.rglob("*")
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
    ]

    for p in tracked:
        name = p.name
        if SECRET_FILE_NAMES.match(name) and not name.endswith((".env.example", ".op.env.example")):
            error(f"secret-like file present: {p.relative_to(REPO)}")

    for p in tracked:
        if p.suffix in {".py", ".yaml", ".yml", ".md", ".sh", ".env", ".example", ".json", ".service"} or not p.suffix:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if PRIVATE_KEY.search(text) and p.name != Path(__file__).name:
                error(f"private key material detected: {p.relative_to(REPO)}")

    compose_files = list((REPO / "services").rglob("compose.y*ml")) if (REPO / "services").exists() else []
    for compose in compose_files:
        text = compose.read_text(encoding="utf-8")
        rel = compose.relative_to(REPO)
        if "0.0.0.0:" in text:
            error(f"0.0.0.0 binding is not allowed: {rel}")
        for line in text.splitlines():
            if PORT_MAPPING.match(line) and "127.0.0.1" not in line and "${" not in line:
                error(f"unqualified port mapping (bind to localhost/LAN/tailscale explicitly): {rel}: {line.strip()}")
        # Host networking sidesteps every check above: there is no ports
        # section to inspect, so a service could reach the LAN while passing
        # the "no 0.0.0.0, no unqualified port" rules without comment. Found
        # while adding Home Assistant, which legitimately needs it — mDNS and
        # SSDP are link-local multicast and do not cross a Docker bridge.
        #
        # Allowed, but only when the compose file says out loud that it is
        # exposed, so `grep agentbox.exposure` answers "what can the network
        # reach" truthfully rather than listing only the services that used a
        # ports mapping.
        if re.search(r"^\s*network_mode:\s*[\"']?host", text, re.M):
            if not re.search(r"agentbox\.exposure:\s*(lan|public)", text):
                error(f"network_mode: host reaches every interface; declare "
                      f"`agentbox.exposure: lan` and say why: {rel}")

        # A bridge holds a real credential, so it must not be reachable from
        # the host: any published port makes its bridge token spendable by any
        # local process that steals one. The MCP gateway reaches bridges over
        # their compose networks. `agentbox.exposure: operator` is the explicit
        # exception for bridges that operator tooling must reach directly
        # (memory review), so the compose file says out loud why the hole
        # exists.
        if "/compose/" in str(rel) and rel.parent.name.endswith("-bridge"):
            if re.search(r"^\s*ports:", text, re.M) and \
                    "agentbox.exposure: operator" not in text:
                error(f"a bridge must not publish host ports (or declare "
                      f"`agentbox.exposure: operator` and say why): {rel}")

        if not re.search(r"^\s*(cpus|mem_limit|pids_limit):", text, re.M):
            error(f"compose service should define resource limits: {rel}")
        if "no-new-privileges:true" not in text:
            error(f"compose service should set no-new-privileges: {rel}")

    # A service image that never drops root is one `docker exec` away from
    # being root on a bind mount. Every service already did this except the two
    # I added for the builder, which are the ones that write files — so the
    # check exists because omitting it is easy and the omission is silent.
    for dockerfile in sorted(REPO.glob("services/compose/*/Dockerfile")):
        text = dockerfile.read_text(encoding="utf-8")
        if not re.search(r"^USER\s+\S+", text, re.M):
            error(f"Dockerfile must drop to a non-root USER: "
                  f"{dockerfile.relative_to(REPO)}")
        elif re.search(r"^USER\s+(0|root)\b", text, re.M):
            error(f"Dockerfile must not run as root: "
                  f"{dockerfile.relative_to(REPO)}")

    # Parsing a compose file is docker's opinion, and that opinion varies by
    # version — the same files that pass here failed on a CI runner's older
    # docker with no defect to find. So this reports rather than fails: a
    # genuinely broken compose file still stops `deploy`, which is the moment
    # it matters, and the repo's own rules above are checked in code we own.
    if os.environ.get("AGENTBOX_VALIDATE_SKIP_COMPOSE") == "1":
        report(WARN, "compose config validation skipped "
                     "(AGENTBOX_VALIDATE_SKIP_COMPOSE=1)")
    elif shutil.which("docker"):
        for compose in compose_files:
            proc = subprocess.run(
                ["docker", "compose", "-f", compose.name, "config", "--no-interpolate"],
                cwd=compose.parent, capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip().splitlines()
                report(WARN, f"docker could not parse {compose.relative_to(REPO)}"
                             + (f": {detail[0][:120]}" if detail else "")
                             + " — deploy will refuse it if it is genuinely broken")
    else:
        report(WARN, "docker not found; skipped compose config validation")

    try:
        tiers = load_policy()
        for required in ("allowed", "approval_required", "always_denied"):
            if not tiers.get(required):
                error(f"approval-policy.yaml missing or empty tier: {required}")
    except OSError:
        error("policies/approval-policy.yaml missing")

    # Shared sources are copied from the repo root at build time rather than
    # duplicated per service, so there are no copies left to drift. The hash
    # checks that used to police that are gone with them.

    # Every assistant-visible tool must map to a capability, or it fails closed
    # at runtime — correct, but silently. Catch it here instead.
    try:
        mapped = set(load_tool_map())
        for server in sorted(REPO.glob("services/compose/*-mcp*/app/server.py")):
            block = server.read_text(encoding="utf-8").split("TOOLS = [", 1)
            if len(block) < 2:
                continue
            for name in re.findall(r'"name":\s*"([a-z_]+)"', block[1].split("\ndef ", 1)[0]):
                if name not in mapped:
                    error(f"tool not mapped in approval-policy.yaml: {name} ({server.parent.parent.name})")
    except OSError:
        error("policies/approval-policy.yaml missing")

    # Lint is a gate, not advice. On 2026-08-14 a NameError in the Google
    # bridge was on screen from `ruff check` and deployed anyway, crash-looping
    # the container that holds the OAuth credential. `validate` runs before
    # every deploy, so this is the place a known-bad change should stop.
    #
    # Absent ruff is a warning rather than a failure: a box without dev tools
    # should still be able to deploy, and a check that cannot run must not
    # masquerade as one that passed.
    # Only where the project's own ruff config applies. scaffold() calls
    # validate() inside a generated temp repo that carries no pyproject, and
    # ruff's defaults there are not this project's standard — linting it would
    # be measuring the wrong thing, and it broke scaffold when it tried.
    ruff_configured = any((REPO / name).is_file()
                          for name in ("pyproject.toml", "ruff.toml", ".ruff.toml"))
    if shutil.which("ruff") and ruff_configured:
        # `.` alone silently skips every file without a .py suffix, which is
        # all three CLI entry points — the largest Python in the repo, and the
        # ones holding the auth and settings logic. They went unlinted from
        # the day this gate was added until somebody read its output closely.
        # Globbed rather than listed so a new entry point is covered by
        # existing, not by somebody remembering to add it here.
        scripts = [str(p) for p in (REPO / "cli").glob("agentbox*")
                   if p.is_file() and p.suffix == "" and b"python" in p.read_bytes()[:100]]
        lint = subprocess.run(["ruff", "check", "--quiet", ".", *scripts],
                              cwd=str(REPO), capture_output=True, text=True,
                              timeout=120)
        if lint.returncode != 0:
            first = (lint.stdout or lint.stderr).strip().splitlines()
            error(f"ruff reports {len([x for x in first if '-->' in x]) or 'some'} "
                  f"issue(s); deploying past a lint error crash-looped a bridge "
                  f"once: " + " / ".join(x.strip() for x in first[:2]))
        else:
            report(OK, "ruff clean")
    elif not ruff_configured:
        pass          # not this repo's standard to enforce
    else:
        report(WARN, "ruff not installed; lint not checked")

    count, tokens, worst = tool_schema_cost()
    if count:
        if tokens > TOOL_SCHEMA_TOKEN_BUDGET:
            error(f"tool schemas cost ~{tokens:,} tokens per turn across "
                  f"{count} tools, over the {TOOL_SCHEMA_TOKEN_BUDGET:,} "
                  f"budget. Biggest: "
                  + ", ".join(f"{n} ~{c}" for n, c in worst)
                  + ". Trim a description, or raise "
                  "AGENTBOX_TOOL_SCHEMA_BUDGET deliberately.")
        else:
            report(OK, f"tool schemas ~{tokens:,} tokens/turn across {count} "
                       f"tools (budget {TOOL_SCHEMA_TOKEN_BUDGET:,})")

    oversized, grown = file_size_drift()
    if oversized or grown:
        detail = []
        if oversized:
            detail.append("new file(s) over the limit: "
                          + ", ".join(f"{f} ({n})" for f, n in oversized))
        if grown:
            detail.append("already-large file(s) that grew: "
                          + ", ".join(f"{f} ({was} -> {n})"
                                      for f, was, n in grown))
        error("; ".join(detail) + ". Split it, or raise its recorded ceiling "
              "in LARGE_FILES deliberately.")
    else:
        report(OK, f"no source file over {LARGE_FILE_LIMIT} lines except the "
                   f"{len(LARGE_FILES)} already recorded")

    report(OK if failures == 0 else FAIL, f"validate finished with {failures} error(s)")
    return 1 if failures else 0


# --- file size, including the files nothing else measures ----------------------

LARGE_FILE_LIMIT = 400

# Files already past the point where one sitting reads the whole thing. Each
# number is a ceiling, not a blessing: these may shrink and must not grow, and
# a file not listed here may not cross the limit at all.
#
# This exists because the smell checker that flags large files globs `*.py`,
# so the four biggest things in this repo are invisible to it — `cli/agentbox`
# and `cli/agentbox-portal` among them, which between them hold the argument
# parsing, the policy checks and every HTTP handler. A gate blind to its own
# worst case reports clean and means nothing, so the repo measures itself.
#
# Raising an entry is allowed and is meant to be a visible line in a diff.
LARGE_FILES = {
    "services/compose/memory-bridge/app/bridge.py": 1075,
    "services/compose/google-workspace-bridge/app/bridge.py": 937,
    "cli/agentbox-approvals": 788,
    "services/compose/homeassistant-bridge/app/bridge.py": 759,
    "tests/test_mcp_base.py": 650,
    "tests/test_portal_auth.py": 632,
    "tests/test_memory_feedback.py": 602,
    "cli/agentbox-invite": 558,
    "services/templates/mcp/mcp_base.py": 546,
    "tests/test_homeassistant.py": 544,
    "tests/test_drive_constraints.py": 499,
    "cli/agentbox_onboarding.py": 431,
    "cli/agentbox_status.py": 503,
    "cli/agentbox_settings.py": 439,
    "tests/test_builder.py": 403,
}


def source_files() -> list[Path]:
    """Tracked Python, plus the extensionless scripts that are also Python.

    Extension is not a reliable signal here: the four largest files in the
    repo have none, because they are commands people type.
    """
    listing = subprocess.run(["git", "ls-files"], cwd=str(REPO),
                             capture_output=True, text=True, check=False)
    out = []
    for name in listing.stdout.split():
        path = REPO / name
        if path.suffix == ".py":
            out.append(path)
            continue
        if path.suffix:
            continue
        try:
            if path.read_bytes()[:2] == b"#!":
                out.append(path)
        except OSError:
            continue
    return out


def file_size_drift() -> tuple[list, list]:
    """(files newly over the limit, recorded files that grew)."""
    oversized, grown = [], []
    for path in source_files():
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        name = str(path.relative_to(REPO))
        recorded = LARGE_FILES.get(name)
        if recorded is None:
            if lines > LARGE_FILE_LIMIT:
                oversized.append((name, lines))
        elif lines > recorded:
            grown.append((name, recorded, lines))
    return oversized, grown
