FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/server.py /app/server.py

USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/server.py"]
