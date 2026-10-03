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


# Every tool schema is sent to the model on every turn, used or not, and
# together they are the largest fixed cost per turn. A documented figure once
# drifted to three times its stated value without anyone noticing, so this
# measures it. It is a ceiling with room to spare, so adding a tool needs no
# edit here but tripling the cost needs a deliberate decision.
TOOL_SCHEMA_TOKEN_BUDGET = int(os.environ.get(
    "AGENTBOX_TOOL_SCHEMA_BUDGET", "9000"))


def tool_schema_cost() -> tuple[int, int, list[tuple[str, int]]]:
    """Tool count, approximate tokens, and the largest schemas."""
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
        # If the measurement cannot run, skip it rather than fail validate.
        # scaffold() runs validate in a temporary repo where the server code
        # is not importable.
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
        # Host networking skips every port check above, because there is no
        # ports section. Home Assistant needs it for device discovery, so it
        # is allowed, but only with an `agentbox.exposure` label. Then
        # `grep agentbox.exposure` lists everything the network can reach.
        if re.search(r"^\s*network_mode:\s*[\"']?host", text, re.M):
            if not re.search(r"agentbox\.exposure:\s*(lan|public)", text):
                error(f"network_mode: host reaches every interface; declare "
                      f"`agentbox.exposure: lan` and say why: {rel}")

        # A bridge holds a real credential, so it publishes no host port, and
        # a stolen bridge token is useless from the host. agentbox-mcp reaches
        # bridges over their compose networks. `agentbox.exposure: operator`
        # marks the exception for memory review, which the operator's CLI
        # reaches directly.
        if "/compose/" in str(rel) and rel.parent.name.endswith("-bridge"):
            if re.search(r"^\s*ports:", text, re.M) and \
                    "agentbox.exposure: operator" not in text:
                error(f"a bridge must not publish host ports (or declare "
                      f"`agentbox.exposure: operator` and say why): {rel}")

        if not re.search(r"^\s*(cpus|mem_limit|pids_limit):", text, re.M):
            error(f"compose service should define resource limits: {rel}")
        if "no-new-privileges:true" not in text:
            error(f"compose service should set no-new-privileges: {rel}")

    # A container running as root is one `docker exec` from being root on its
    # bind mounts. Forgetting to drop it is easy and silent, so it is checked.
    for dockerfile in sorted(REPO.glob("services/compose/*/Dockerfile")):
        text = dockerfile.read_text(encoding="utf-8")
        if not re.search(r"^USER\s+\S+", text, re.M):
            error(f"Dockerfile must drop to a non-root USER: "
                  f"{dockerfile.relative_to(REPO)}")
        elif re.search(r"^USER\s+(0|root)\b", text, re.M):
            error(f"Dockerfile must not run as root: "
                  f"{dockerfile.relative_to(REPO)}")

    # Docker's compose parser varies by version, and older versions on CI
    # rejected valid files. So this reports rather than fails. A broken file
    # still stops `deploy`, and the rules above are checked by code here.
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

    # Shared sources are copied from the repository at build time rather than
    # duplicated per service, so there are no copies to compare.

    # An unmapped tool fails closed at runtime without saying why, so catch it
    # here.
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

    # Lint blocks a deploy. A NameError that ruff had already reported once
    # shipped and crash-looped the bridge holding the Google credential.
    #
    # Missing ruff is a warning, so a machine without dev tools can still
    # deploy, but it never reads as a pass. Lint only runs where this
    # project's pyproject applies, which excludes scaffold's temporary repo.
    ruff_configured = any((REPO / name).is_file()
                          for name in ("pyproject.toml", "ruff.toml", ".ruff.toml"))
    if shutil.which("ruff") and ruff_configured:
        # `ruff check .` skips files without a .py suffix, which includes the
        # CLI entry points. They are globbed so a new one is covered without
        # anyone adding it here.
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

# Files already over the limit. Each number is a ceiling. A listed file may
# shrink but not grow, and an unlisted file may not cross the limit. Raising an
# entry is allowed, as a visible line in a diff. Extensionless scripts are
# measured too, since generic size checkers only look at *.py.
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
    """Tracked Python files, including extensionless scripts that are Python."""
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
    """Files newly over the limit, and recorded files that grew."""
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
