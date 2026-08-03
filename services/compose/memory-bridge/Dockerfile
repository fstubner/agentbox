FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/memory-bridge/app/bridge.py /app/bridge.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py
COPY policies/approval-policy.yaml /app/approval-policy.yaml

# This ran as root until 2026-08-03 — the only service that did, and the one
# holding durable memory and the review gate. `validate` now fails on a
# Dockerfile without a non-root USER so it cannot recur.
USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
