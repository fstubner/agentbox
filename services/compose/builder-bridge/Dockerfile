FROM python:3.13-alpine

# git is the point of this service: proposals are branches, so the trail is
# whatever `git log` says. No compilers or package managers beyond it, this
# container reads and writes text and pushes refs, nothing more.
RUN apk add --no-cache git

# The clone is owned by the operator with group 65532 and setgid, so this
# container writes it by group. Git refuses a repo it does not own unless told
# otherwise, and it deliberately ignores `-c safe.directory` from the command
# line, so an attacker-controlled argv cannot disable the check. A system
# config baked into the image is the supported route, and it names exactly one
# path.
RUN git config --system --add safe.directory /repo \
 && git config --system --add safe.directory /origin

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY services/compose/builder-bridge/app/bridge.py /app/bridge.py
COPY services/templates/bridge/app/bridge_base.py /app/bridge_base.py
COPY services/templates/mcp/policy_gate.py /app/policy_gate.py

# Same non-root uid as every other service. This one writes files, so
# running as root would be the worst place in the platform to forget it.
USER 65532:65532

EXPOSE 8080
CMD ["python", "/app/bridge.py"]
