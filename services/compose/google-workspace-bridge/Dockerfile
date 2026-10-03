FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/google-workspace-bridge/app/bridge.py /app/bridge.py
COPY services/compose/google-workspace-bridge/app/google_api.py /app/google_api.py
COPY services/compose/google-workspace-bridge/app/google_gmail.py /app/google_gmail.py
COPY services/compose/google-workspace-bridge/app/google_calendar.py /app/google_calendar.py
COPY services/compose/google-workspace-bridge/app/google_drive.py /app/google_drive.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/bridge/app/pii.py /app/pii.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py

USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
