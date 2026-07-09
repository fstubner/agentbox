FROM python:3.12-slim

WORKDIR /app
COPY app/ /app/

# stdlib-only; no pip install needed. Run as a non-root, non-login user.
USER 65532:65532

EXPOSE 8080
CMD ["python3", "bridge.py"]
