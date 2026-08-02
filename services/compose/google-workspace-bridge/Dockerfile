FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/google-workspace-bridge/app/bridge.py /app/bridge.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py

USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
