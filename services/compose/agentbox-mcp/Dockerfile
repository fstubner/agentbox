FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/agentbox-mcp/app/server.py /app/server.py
COPY services/compose/agentbox-mcp/app/integrations /app/integrations
COPY services/templates/mcp/mcp_base.py /app/mcp_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py
COPY services/templates/mcp/outcome_log.py /app/outcome_log.py
COPY policies/approval-policy.yaml /app/approval-policy.yaml

USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/server.py"]
