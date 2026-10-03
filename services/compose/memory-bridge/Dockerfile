FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/memory-bridge/app/bridge.py /app/bridge.py
COPY services/compose/memory-bridge/app/memory_store.py /app/memory_store.py
COPY services/compose/memory-bridge/app/memory_scopes.py /app/memory_scopes.py
COPY services/compose/memory-bridge/app/memory_kinds.py /app/memory_kinds.py
COPY services/compose/memory-bridge/app/memory_history.py /app/memory_history.py
COPY services/compose/memory-bridge/app/memory_feedback.py /app/memory_feedback.py
COPY services/compose/memory-bridge/app/memory_activity.py /app/memory_activity.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py

# This ran as root until 2026-08-03 — the only service that did, and the one
# holding durable memory and the review gate. `validate` now fails on a
# Dockerfile without a non-root USER so it cannot recur.
USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
