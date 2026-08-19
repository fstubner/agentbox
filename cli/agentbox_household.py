"""What the assistant is allowed to actuate in the house.

Separate from `agentbox_settings.py` for one reason, and it is a security
reason rather than a tidiness one: a *bridge* has to read this, and bridges
run in containers.

The settings file lives in the portal's state directory precisely because no
container mounts it — that is what stops the assistant making itself an admin
or redirecting a sign-in link. Putting device permissions in the same file
would mean mounting that directory into the Home Assistant bridge, which would
hand the assistant a route to every setting in it.

So this goes on the policy mount instead: the same read-only volume the grants
file uses, for the same reason. The portal (operator-owned) writes it; the
bridge reads it and cannot write it. Writing where the assistant can write
confers no authority anywhere in this system, and this keeps that true.

## What this does not do

It cannot widen anything the bridge refuses in code. Locks, alarms and covers
are `SECURITY_DOMAINS` and are rejected regardless of what appears here —
naming one is not an error, it simply has no effect. That belt is deliberate:
a settings page that could unlock a door would make every other control in
this system conditional on the portal never being compromised.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

# Refused by homeassistant-bridge whatever this file says. Listed here only so
# the page can tell somebody why their entry will not work, rather than
# accepting it and silently doing nothing.
NEVER_CONTROLLABLE = ("lock", "alarm_control_panel", "cover", "camera")


class InvalidEntity(ValueError):
    """A submitted entity id was rejected. Shown to the person."""


@dataclass
class HouseholdPolicy:
    """The entities the assistant may act on, beyond the allowed domains."""

    directory: Path

    @property
    def path(self) -> Path:
        return self.directory / "household.json"

    def controllable(self) -> list[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entities = data.get("controllable_entities", [])
        return [str(e) for e in entities] if isinstance(entities, list) else []

    def save(self, raw: str) -> list[str]:
        """Validate and store a comma or newline separated list.

        Rejects the whole submission on a single bad entry rather than storing
        the rest: a list that quietly loses a line leaves somebody believing
        they granted something they did not.
        """
        entities = []
        for chunk in raw.replace("\n", ",").split(","):
            entity = chunk.strip()
            if not entity:
                continue
            if not ENTITY_ID.match(entity):
                raise InvalidEntity(
                    f"'{entity}' is not an entity id — they look like "
                    f"light.kitchen or switch.washer")
            domain = entity.split(".", 1)[0]
            if domain in NEVER_CONTROLLABLE:
                raise InvalidEntity(
                    f"'{entity}' is in the {domain} domain, which the bridge "
                    f"refuses in code. Adding it here would have no effect, so "
                    f"it is rejected rather than accepted and ignored.")
            entities.append(entity)

        entities = sorted(dict.fromkeys(entities))
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"controllable_entities": entities}, indent=2),
                       encoding="utf-8")
        # Readable by the container that mounts this directory; not writable
        # by it, which the mount enforces.
        tmp.chmod(0o644)
        tmp.replace(self.path)
        return entities
