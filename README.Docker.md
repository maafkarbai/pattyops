# PattyOps Docker deployment

This deployment runs two containers that share the cached Roboflow image
layers:

1. `inference-server`: the Roboflow CPU inference server.
2. `pattyops`: the video processor and SQLite lifecycle-event logger.

## Files and folders

Place the input video in `input/`. PattyOps writes the persistent SQLite file
to `Database/` and the annotated video to `output/`.

```text
PattyOps/
|-- Dockerfile
|-- compose.yaml
|-- .env
|-- input/Patty3.mp4
|-- Database/pattyops.db
`-- output/pattyops_output.mp4
```

## Configure

Copy `.env.example` to `.env`, then replace `replace_with_your_key` with the
Roboflow API key. Never commit or send the real `.env` file.

To process a different video, copy it into `input/` and update
`PATTYOPS_VIDEO_FILE` in `.env`. When no filenames are passed to the runner,
it automatically uses the video, database, and output names from `.env`.

## Run

From PowerShell in this folder:

```powershell
./run-pattyops.ps1
```

If PowerShell's execution policy blocks local scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-pattyops.ps1
```

Validate without starting containers:

```powershell
./run-pattyops.ps1 -ValidateOnly
```

Choose different filenames without editing Compose:

```powershell
./run-pattyops.ps1 `
    -VideoFile "Patty5.mp4" `
    -DatabaseFile "patty5.db" `
    -OutputFile "patty5_annotated.mp4"
```

The equivalent manual commands are:

```powershell
docker compose config
docker compose up --build --abort-on-container-exit --exit-code-from pattyops
```

The first build can take several minutes. When processing completes, inspect:

```text
Database\pattyops.db
output\pattyops_output.mp4
```

Stop and remove the containers without removing your bind-mounted results:

```powershell
docker compose down
```

SQLite is configured as a single-writer log. Do not scale the `pattyops`
service to multiple replicas that write to the same database file.
