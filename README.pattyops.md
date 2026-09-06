# PattyOps SQLite event logger

This logger uses your trained Ultralytics checkpoint with BoT-SORT and stores
meaningful patty lifecycle events in SQLite. It is configured for the classes in
this dataset:

- `Raw_Patty` -> `RAW`
- `CookedPatty` and `CookedPatty_w-cheese` -> `COOKED`
- `SaltPepper` -> ignored by the patty lifecycle logger

The MVP flip rule is a smoothed `COOKED -> RAW` transition on the same BoT-SORT
track ID. A single noisy detector frame cannot create a flip event.

## Set up

Open PowerShell in this folder and create or activate your Python environment.
Then install the runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

Put your trained checkpoint somewhere accessible. The dataset itself does not
contain `best.pt`; training normally creates it under a path similar to:

```text
runs\detect\train\weights\best.pt
```

## Start the interactive live logger

Run PattyOps without arguments:

```powershell
python pattyops.py
```

It asks you to choose either:

1. a video file, or
2. a webcam/USB camera (camera `0` is the usual default).

It then asks for the current Ultralytics `.pt` model and SQLite log location.
The live window shows the camera/video feed with detections and the local date,
time, and UTC offset. Press `q` to finish the session cleanly.

Each run is stored in `cooking_sessions`. Patty lifecycle events are stored in
`patty_events` with a timezone-aware `occurred_at` value such as
`2026-08-28T14:32:10.123+05:00`. This is real wall-clock time; `video_seconds`
separately records the position within an uploaded video.

## Run directly with command-line options

```powershell
python pattyops.py run --model "C:\path\to\best.pt" --source "C:\path\to\video.mp4" --show
```

Press `q` to stop the display. To save annotated output as well:

```powershell
python pattyops.py run --model "C:\path\to\best.pt" --source "C:\path\to\video.mp4" --save-video "annotated.mp4"
```

For the default camera:

```powershell
python pattyops.py run --model "C:\path\to\best.pt" --source 0 --show
```

`--source camera:0` is also accepted and is stored in the database as
`webcam:0` so camera sessions are easy to distinguish from video sessions.

By default, the database is created as `pattyops.db` in the current folder.
Override it with `--db "C:\path\to\pattyops.db"`.

## Inspect events

```powershell
python pattyops.py events --db pattyops.db --limit 100
```

The schema contains:

- `cooking_sessions`: one row per video/camera run
- `patties`: one current lifecycle record per track ID and session
- `patty_events`: deduplicated `DETECTED`, `STATE_CHANGED`, `FLIPPED`, and
  `REMOVED` events

## Tune smoothing

The conservative defaults use a 12-observation window, at least 5 observations,
and a 65% majority before accepting a state. They can be adjusted:

```powershell
python pattyops.py run --model best.pt --source video.mp4 --history-size 12 --min-stable 5 --stable-ratio 0.65 --removal-grace-frames 45
```

Increase the stability requirements if false flips appear. Increase
`--removal-grace-frames` if occlusions cause premature `REMOVED` events.

## Update the model or dataset labels

After training on an expanded dataset, point the wizard or `--model` option at
the newly exported `best.pt`. The database history remains intact because each
session records the exact model path used.

If the updated dataset introduces different detector class names, copy
`class-map.example.json`, edit the labels, and pass it without changing code:

```powershell
python pattyops.py run `
    --model "runs\detect\train2\weights\best.pt" `
    --source 0 `
    --class-map "class-map.json" `
    --show
```

The JSON keys are detector labels and each value must be either `RAW` or
`COOKED`. Matching ignores capitalization, spaces, underscores, and hyphens.

## Run the core tests

These tests need only Python's standard library:

```powershell
python -m unittest -v test_pattyops.py
```
