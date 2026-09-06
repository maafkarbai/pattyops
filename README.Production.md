# PattyOps kitchen edge + cloud deployment

PattyOps uses an edge-first architecture. Detection continues in the kitchen
when the internet is unavailable, while a separate process sends durable event
records and device heartbeats to the cloud when connectivity is available.

```text
USB webcam -> edge inference -> SQLite -> edge sync -> HTTPS cloud API -> PostgreSQL
```

Raw webcam video stays in the kitchen. The cloud API receives lifecycle events,
not a continuous camera stream.

## 1. Test the webcam on Windows

Use the native Python runner for the first camera and model test:

```powershell
python -m pip install -r requirements.txt
python pattyops.py run --model best.pt --source 0 --db Database/pattyops.db --show
```

Try source `1` or `2` if camera `0` is not the intended webcam. The production
edge container is designed for an Ubuntu host because Linux can pass
`/dev/video0` directly into Docker.

## 2. Start the cloud API locally

Copy the cloud environment example and replace both passwords/tokens. The
database password in the URL must match `POSTGRES_PASSWORD`.

```powershell
Copy-Item .env.cloud.example .env.cloud
docker compose -f compose.cloud.yaml config --quiet
docker compose -f compose.cloud.yaml up --build -d
```

Check the health endpoint:

```powershell
Invoke-RestMethod http://localhost:8000/healthz
```

For a public deployment, terminate TLS at a managed load balancer or reverse
proxy and expose only HTTPS. Do not expose PostgreSQL publicly.

## 3. Test event synchronization on Windows

Copy `.env.edge.example` to `.env.edge`, set the same device ID/token as the
cloud configuration, and use the local test URL:

```text
PATTYOPS_CLOUD_URL=http://localhost:8000
PATTYOPS_ALLOW_INSECURE_HTTP=true
PATTYOPS_DATABASE_PATH=Database/pattyops.db
```

Load the variables and perform one synchronization cycle:

```powershell
Get-Content .env.edge | ForEach-Object {
    if ($_ -match '^[^#][^=]*=') {
        $name, $value = $_ -split '=', 2
        Set-Item -Path "Env:$name" -Value $value
    }
}
python edge_sync.py --once
```

Re-running the command is safe. The cloud uses an idempotency key and the edge
stores a checkpoint only after the full batch is acknowledged.

## 4. Run the Ubuntu kitchen edge

Put the trained model at `models/best.pt`, connect the USB webcam, and copy the
edge environment configuration:

```bash
cp .env.edge.example .env.edge
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

## API endpoints

- `GET /healthz`: public container/load-balancer health check
- `POST /v1/devices/heartbeat`: authenticated device heartbeat
- `POST /v1/events/batch`: authenticated, idempotent event ingestion
- `GET /v1/events`: authenticated recent-event listing for the calling device

Authenticated endpoints require both `Authorization: Bearer <token>` and
`X-Device-ID: <device-id>` headers.
