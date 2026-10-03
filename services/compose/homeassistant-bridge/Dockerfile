FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/homeassistant-bridge/app/bridge.py /app/bridge.py
COPY services/compose/homeassistant-bridge/app/automation.py /app/automation.py
COPY services/compose/homeassistant-bridge/app/ha_api.py /app/ha_api.py
COPY services/compose/homeassistant-bridge/app/ha_access.py /app/ha_access.py
COPY services/compose/homeassistant-bridge/app/ha_cameras.py /app/ha_cameras.py
COPY services/compose/homeassistant-bridge/app/ha_screens.py /app/ha_screens.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py

USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
