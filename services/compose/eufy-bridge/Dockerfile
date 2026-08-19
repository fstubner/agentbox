FROM python:3.12-slim
WORKDIR /app
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/bridge/app/pii.py /app/pii.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py
COPY services/compose/eufy-bridge/app/ /app/
USER nobody
CMD ["python", "/app/bridge.py"]
