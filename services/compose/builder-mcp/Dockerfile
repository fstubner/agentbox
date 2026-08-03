FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/builder-mcp/app/server.py /app/server.py
COPY services/templates/mcp/mcp_base.py /app/mcp_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py
COPY services/templates/mcp/outcome_log.py /app/outcome_log.py
COPY policies/approval-policy.yaml /app/approval-policy.yaml

# Same non-root uid as every other service. This one writes files, so
# running as root would be the worst place in the platform to forget it.
USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/server.py"]
