"""agentbox_scaffold — builder sandbox bridge generator.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 200 LOC)
- Preserves exact Dockerfile/compose/README generation, port allocation, branch creation, output strings, and exit codes
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

SCAFFOLD_PORT_RANGE = range(3480, 3500)


def _rep(report_fn: Callable[[str, str], None] | None, level: str, msg: str) -> None:
    if report_fn:
        report_fn(level, msg)
    else:
        print(f"[{level.lower()}] {msg}")


def used_ports(repo_path: Path) -> set[int]:
    ports = set()
    for compose in (repo_path / "services").rglob("compose.y*ml"):
        for match in re.finditer(r":(\d{4}):8080", compose.read_text(encoding="utf-8")):
            ports.add(int(match.group(1)))
    return ports


def scaffold(name: str, port: int | None, upstream_env: str | None,
             repo_path: Path | None = None,
             validate_fn: Callable[[], int] | None = None,
             report_fn: Callable[[str, str], None] | None = None) -> int:
    repo = repo_path or Path(__file__).resolve().parents[1]

    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", name):
        _rep(report_fn, FAIL, "name must be lowercase letters, digits and hyphens (e.g. todoist)")
        return 1
    service = f"{name}-bridge"
    target = repo / "services" / "compose" / service
    if target.exists():
        _rep(report_fn, FAIL, f"{service} already exists at {target.relative_to(repo)}")
        return 1

    taken = used_ports(repo)
    if port is None:
        port = next((p for p in SCAFFOLD_PORT_RANGE if p not in taken), None)
        if port is None:
            _rep(report_fn, FAIL, f"no free port in {SCAFFOLD_PORT_RANGE.start}-{SCAFFOLD_PORT_RANGE.stop - 1}")
            return 1
    elif port in taken:
        _rep(report_fn, FAIL, f"port {port} is already mapped by another service")
        return 1

    upper = name.upper().replace("-", "_")
    upstream_env = upstream_env or f"{upper}_API_TOKEN"
    bridge_env = f"{upper}_BRIDGE_TOKEN"

    template = repo / "services" / "templates" / "bridge"
    target.mkdir(parents=True)
    (target / "app").mkdir()

    # No vendoring: the Dockerfile copies the shared base from the repo root at
    # build time, so there is never a second copy to drift.
    (target / "Dockerfile").write_text(
        "FROM python:3.13-alpine\n\n"
        "ENV PYTHONDONTWRITEBYTECODE=1\n"
        "ENV PYTHONUNBUFFERED=1\n\n"
        "WORKDIR /app\n"
        f"COPY services/compose/{service}/app/bridge.py /app/bridge.py\n"
        "COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py\n\n"
        "USER 65532:65532\n\n"
        "EXPOSE 8080\n"
        'CMD ["python", "/app/bridge.py"]\n', encoding="utf-8")

    def rewrite(text: str) -> str:
        return (text.replace("EXAMPLE_UPSTREAM_TOKEN", upstream_env)
                    .replace("EXAMPLE_BRIDGE_TOKEN", bridge_env)
                    .replace("example-bridge", service)
                    .replace("ExampleBridge", f"{name.replace('-', ' ').title().replace(' ', '')}Bridge")
                    .replace("agentbox-bridge-example", service)
                    .replace(":3480:8080", f":{port}:8080"))

    (target / "app" / "bridge.py").write_text(
        rewrite((template / "app" / "bridge.py").read_text(encoding="utf-8")), encoding="utf-8")
    compose = rewrite((template / "compose.yaml").read_text(encoding="utf-8"))
    compose = compose.replace("    build:\n      context: .\n",
                              f"    build:\n      context: ../../..\n"
                              f"      dockerfile: services/compose/{service}/Dockerfile\n")
    compose = compose.replace("    build: .\n",
                              f"    build:\n      context: ../../..\n"
                              f"      dockerfile: services/compose/{service}/Dockerfile\n")
    (target / "compose.yaml").write_text(compose, encoding="utf-8")
    (target / f"{service}.op.env.example").write_text(
        f"{upstream_env}=op://Agentbox/{service}/api_token\n"
        f"{bridge_env}=op://Agentbox/{service}/bridge_token\n"
        "LAN_BIND_IP=127.0.0.1\n", encoding="utf-8")
    (target / "README.md").write_text(
        f"# {service}\n\n"
        f"Scaffolded by `agentbox scaffold`. Not deployed, not merged.\n\n"
        f"- Host port: `127.0.0.1:{port}`\n"
        f"- Upstream credential: `{upstream_env}` (never seen by the assistant)\n"
        f"- Bridge token: `{bridge_env}`\n\n"
        f"## Before this is worth merging\n\n"
        f"1. Replace the echo route in `app/bridge.py` with real calls.\n"
        f"2. Implement `upstream_status()` if this fronts a service you run.\n"
        f"3. Follow `skills/adding-a-bridge/SKILL.md` — bounded lists, idempotent\n"
        f"   creates, symmetric write constraints, no policy stated only in prose.\n"
        f"4. Map any new MCP tools to a capability in `policies/approval-policy.yaml`;\n"
        f"   unmapped tools fail closed and `validate` will flag them.\n"
        f"5. Create the 1Password item, then `cli/agentbox deploy {service}`.\n",
        encoding="utf-8")

    test_path = repo / "tests" / f"test_{service.replace('-', '_')}.py"
    test_path.write_text(
        f'"""Scaffolded checks for {service}. Extend as routes are added."""\n'
        "from __future__ import annotations\n\n"
        "import importlib.util\nimport sys\nfrom pathlib import Path\n\n"
        "REPO = Path(__file__).resolve().parent.parent\n"
        f'BRIDGE = REPO / "services" / "compose" / "{service}" / "app" / "bridge.py"\n'
        'sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))\n'
        f'spec = importlib.util.spec_from_file_location("{service.replace("-", "_")}", BRIDGE)\n'
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n\n\n"
        "def test_bridge_token_is_read_from_env_not_hardcoded():\n"
        '    """Fail-closed auth depends on this being empty when unset."""\n'
        f'    assert mod.BRIDGE_TOKEN == ""\n\n\n'
        "def test_upstream_credential_is_separate_from_the_bridge_token():\n"
        '    """One credential guards the door; the other is what the door protects."""\n'
        "    assert mod.UPSTREAM_TOKEN is not mod.BRIDGE_TOKEN or mod.UPSTREAM_TOKEN == \"\"\n",
        encoding="utf-8")

    _rep(report_fn, OK, f"scaffolded {service} on 127.0.0.1:{port}")
    if validate_fn and validate_fn() != 0:
        _rep(report_fn, FAIL, "generated service does not pass validate; leaving it in place to inspect")
        return 1

    branch = f"scaffold/{service}"
    proc = subprocess.run(["git", "checkout", "-b", branch], cwd=repo,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        _rep(report_fn, WARN, f"could not create branch {branch}: {proc.stderr.strip()}")
        _rep(report_fn, WARN, "files are written; commit them yourself")
        return 0
    subprocess.run(["git", "add", str(target), str(test_path)], cwd=repo, check=False)
    subprocess.run(["git", "commit", "-q", "-m",
                    f"scaffold: {service} bridge skeleton\n\n"
                    f"Generated by `agentbox scaffold {name}`. Echo route is a placeholder.\n"
                    f"Not deployed and not merged: review against\n"
                    f"skills/adding-a-bridge/SKILL.md before either."],
                   cwd=repo, check=False)
    _rep(report_fn, OK, f"committed on branch {branch} — not merged, not deployed")
    print(f"\nNext: edit services/compose/{service}/app/bridge.py, then open a PR.\n"
          f"Nothing runs this service until someone reviews it and runs "
          f"`cli/agentbox deploy {service}`.")
    return 0
