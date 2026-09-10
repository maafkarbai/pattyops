# PattyOps kitchen edge + cloud deployment

PattyOps uses an edge-first architecture. Detection continues in the kitchen
when the internet is unavailable, while a separate process sends durable event
records and device heartbeats to the cloud when connectivity is available.

```text
USB webcam -> edge inference -> SQLite -> edge sync -> HTTPS cloud API -> PostgreSQL
```

Raw webcam video stays in the kitchen. The cloud API receives lifecycle events,
not a continuous camera stream.

## Windows kitchen installation

For nontechnical kitchen staff, use [the graphical desktop installer and guide](README.Kitchen.md). The instructions below are for deployment owners provisioning the cloud and alternative container deployments.

## 1. Test the webcam on Windows

Use the native Python runner for the first camera and model test:

```powershell
python -m pip install -r requirements.txt
.\run-pattyops.ps1 -Source 0
```

Try source `1` or `2` if camera `0` is not the intended webcam. The production
edge container is designed for an Ubuntu host because Linux can pass
`/dev/video0` directly into Docker.

## 2. Start the cloud API locally

Generate the private environment files, install the trained checkpoint at
`models/best.pt`, and start the deployment:

```powershell
bun run deploy:configure
bun run deploy
```

The configuration script creates one database password and one device token
using the operating system cryptographic random-number generator. It does not
print either secret, and Git ignores the generated files. To rotate both
credentials later, stop the deployment and run:

```powershell
.\configure-deployment.ps1 -Force
bun run deploy
```

The local API is available only from this computer at
`http://localhost:8000`; PostgreSQL has no host port. For a public deployment,
terminate TLS at a managed load balancer or reverse proxy, set the edge URL to
the real `https://` endpoint, and expose neither PostgreSQL nor plain HTTP.

## 3. Test event synchronization on Windows

Perform one authenticated synchronization cycle from the existing Windows
SQLite event log:

```powershell
bun run deploy:sync
```

Re-running the command is safe. The cloud uses an idempotency key and the edge
stores a checkpoint only after the full batch is acknowledged.

## 4. Run the Ubuntu kitchen edge

Copy the repository and the already-generated `.env.edge` securely to the
Ubuntu edge host, connect the USB webcam, and start both edge services. If the
cloud API is on another host, change `PATTYOPS_CLOUD_URL` to its HTTPS URL and
set `PATTYOPS_ALLOW_INSECURE_HTTP=false` first.

```bash
mkdir -p Database models
docker compose -f compose.edge.yaml config --quiet
docker compose -f compose.edge.yaml up --build -d
```

Inspect the services:

```bash
docker compose -f compose.edge.yaml ps
docker compose -f compose.edge.yaml logs -f --tail=100
```

Both services restart after a reboot when Docker is enabled. The inference and
sync services share only the persistent SQLite directory; the synchronizer
backs off and retries without deleting unsent events when the network is down.

When the cloud and edge Compose projects run on the same computer, include the
local network override:

```powershell
docker compose -f compose.edge.yaml -f compose.edge.local.yaml up --build -d
```

## Stop the local cloud

```powershell
bun run deploy:stop
```

This removes the containers and private network but preserves the named
PostgreSQL data volume. Add `docker compose -f compose.cloud.yaml down -v` only
when you intentionally want to delete all cloud data.

## API endpoints

- `GET /healthz`: public container/load-balancer health check
- `POST /v1/devices/heartbeat`: authenticated device heartbeat
- `POST /v1/events/batch`: authenticated, idempotent event ingestion
- `GET /v1/events`: authenticated recent-event listing for the calling device

Authenticated endpoints require both `Authorization: Bearer <token>` and
`X-Device-ID: <device-id>` headers.
