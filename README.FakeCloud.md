# PattyOps with local FakeCloud

The kitchen app sends HTTP events to the PattyOps API at `http://localhost:8000`.
The API runs on Windows and connects to a real PostgreSQL 16 container provisioned
through FakeCloud's RDS API at `http://localhost:4566`. This is a local RDS integration;
the API is not deployed to emulated ECS.

## Start

Start Docker Desktop, then run from this checkout:

```powershell
.\start-fakecloud.ps1
```

The script expects FakeCloud in the sibling `fakecloud` directory. Override with
`-FakeCloudPath` if needed. The project virtual environment requires
`requirements-cloud.txt` and `boto3` (installed during initial setup).

It starts FakeCloud, creates or reuses `pattyops-test`, discovers the database's
current port, verifies SQL connectivity, selects the kitchen's isolated test setup,
starts the API without a console window, and verifies an event upload and replay.
The test creates one synthetic event per run under `fakecloud-smoke-test`, separate
from kitchen records. Keep port 8000 available for this API.

Open or reopen PattyOps Kitchen after setup. Its saved cloud address and connection
key are already configured. The device is `kitchen-test-01`, and its local database
is `%LOCALAPPDATA%\PattyOps\fakecloud-test.db`. The existing model and camera/video
source are preserved; if no model was configured, setup selects `models\best.pt`.
Click **Start kitchen** to run detection and **Cloud event logs** to inspect uploads.

## Storage and credentials

FakeCloud's Compose configuration enables persistent mode and mounts its `data`
folder. RDS data uses a Docker named volume. Do not remove these to stop services.
Local secrets and API logs are in the Git-ignored `.fakecloud` directory. No real
AWS credentials are used. The original kitchen settings, when present, are copied
to `.fakecloud\kitchen-settings-before-fakecloud.json` before the first change.

## Stop and restart

Close the kitchen app, then stop the test API:

```powershell
.\stop-fakecloud-api.ps1
```

FakeCloud remains running for other projects. After a FakeCloud restart, stop and
start this API again: RDS recovery can assign a new host port, which startup discovers.
After a Windows reboot, start Docker Desktop and run `start-fakecloud.ps1` again.

To restore the former kitchen setup, close the kitchen app and copy the settings
backup over `%LOCALAPPDATA%\PattyOps\settings.json`, then reopen the app. The previous
local database is not changed by this setup.

## Production transition

Deploy the PattyOps API to a real host/container service and use production
PostgreSQL, an HTTPS endpoint, and new device credentials. This helper deliberately
hardcodes the local FakeCloud AWS endpoint and is not a production deployment tool.
Use a fresh production device/database or explicitly migrate history: edge upload
checkpoints are per device, not per cloud URL. Test actual AWS network access,
permissions, TLS, backups, and capacity separately.
