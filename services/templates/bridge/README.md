# Bridge template

A **bridge** is a small HTTP service that holds a credential (an upstream API
token, an OAuth refresh token, …) and exposes a narrow, allowlisted API. The
assistant authenticates to the bridge with a *bridge token* and never sees the
upstream credential.

Copy this directory to `services/compose/<your-bridge>/` and edit only the
marked parts. See `skills/adding-a-bridge/SKILL.md` for the full walkthrough.

## Contract

| Concern | Where it lives | Rule |
|---|---|---|
| Shared HTTP/auth/error machinery | `app/bridge_base.py` | **Do not edit per-bridge.** Copy verbatim. |
| Your routes + upstream calls | `app/bridge.py` | The only file you write. |
| Two tokens | env | `*_BRIDGE_TOKEN` (assistant→bridge) and the upstream credential are distinct. |
| Auth | `bridge_base` | Fail-closed: unset bridge token ⇒ 503; wrong token ⇒ 401; constant-time compare. |
| Errors | `bridge_base` | `BridgeError(status, msg)` for expected failures; anything else ⇒ JSON 500, never a traceback. |
| Health | `bridge_base` | `GET /health` is unauthenticated. |
| Network | `compose.yaml` | No host port once agentbox-mcp has an integration for it. During review it may publish on `127.0.0.1`, never `0.0.0.0`. |
| Container | `Dockerfile` | Non-root (`USER 65532`), `read_only`, `cap_drop: ALL`, `no-new-privileges`. |

## Route handler shape

```python
def my_route(handler, body):          # body is the parsed JSON dict (or None)
    if not UPSTREAM_TOKEN:
        raise BridgeError(503, "UPSTREAM token not configured")
    ...                               # call the upstream API with the credential
    return 200, {"result": safe_data} # return only allowlisted fields
```

Register it on your handler subclass:

```python
class MyBridge(BridgeHandler):
    bridge_token = os.environ.get("MY_BRIDGE_TOKEN", "")
    routes = {("POST", "/v1/do_thing"): my_route}
```

## Deploy

```
cli/agentbox deploy <your-bridge>
```

## Verify

While the review port is published:

```
curl -s localhost:<port>/health                       # 200, no auth
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  localhost:<port>/v1/do_thing                         # 401 without token
```
