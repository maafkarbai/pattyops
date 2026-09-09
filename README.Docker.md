# PattyOps local-model Docker processing

This Docker mode runs the trained Ultralytics checkpoint entirely on the local
machine. It does not call Roboflow or require an API key. Roboflow is used only
outside this runtime to annotate and export the dataset.

## Files and folders

Place the model and input video in the mounted folders. PattyOps writes its
SQLite log and annotated video to persistent host folders.

```text
PattyOps/
|-- models/best.pt
|-- input/Patty3.mp4
|-- Database/pattyops.db
`-- output/pattyops_output.mp4
```

## Configure

Copy `.env.example` to `.env` and select the filenames. No credentials belong
in this file.

```powershell
Copy-Item .env.example .env
```

The defaults select BoT-SORT with a confidence threshold of `0.35` and an IoU
threshold of `0.5`.

## Run

Recorded-video processing in Docker is headless:

```powershell
bun run docker:validate
bun run docker:video
bun run docker:down
```

When processing completes, inspect:

```text
Database\pattyops.db
output\pattyops_output.mp4
```

For a webcam on Windows, use the native runner because Docker Desktop does not
pass a Windows camera through as `/dev/video0`:

```powershell
bun run webcam
```

SQLite is a single-writer log. Do not run multiple inference containers against
the same database file.
