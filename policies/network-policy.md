# Network Policy

All services are private by default.

## Allowed Exposure

- `127.0.0.1` for local-only services.
- LAN IP for local network services.
- Tailscale IP for Tailnet services.
- mDNS hostnames such as `assistant.local` for LAN access.
- Tailscale MagicDNS hostnames for remote private access.

## Denied Exposure

- Public internet exposure.
- Tailscale Funnel unless explicitly approved for a specific service.
- `0.0.0.0` host binding.
- Unqualified Docker Compose ports such as `8080:80`.

## Compose Port Rule

Use explicit host binding:

```yaml
ports:
  - "${LAN_BIND_IP:-127.0.0.1}:8080:80"
```

Do not use:

```yaml
ports:
  - "8080:80"
```

## mDNS

mDNS names resolve hosts, not Docker services. The simple setup is:

- one host mDNS name, e.g. `assistant.local`
- services exposed by port or through a local reverse proxy

Per-service names like `tasks.local` require either Avahi aliases, local DNS, or a reverse proxy plus DNS/mDNS aliasing.

