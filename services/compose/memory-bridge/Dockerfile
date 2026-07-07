FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/bridge.py /app/bridge.py

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
