# PattyOps

PattyOps is a computer vision application that tracks burger patties in camera feeds or recorded video and records changes in their visible cooking state. It converts detections into structured lifecycle events that can support operational reviews, process analysis, and future reporting integrations.

The repository includes local inference, a Roboflow video-processing workflow, and an edge-to-cloud synchronization service. The sections below provide a project overview for delivery teams and practical setup guidance for technical contributors.

## Project overview

### Purpose and current scope

PattyOps provides a timestamped record of when a patty is detected, changes state, appears to be flipped, or leaves tracking. It maintains local records in SQLite and can synchronize events to a central API backed by PostgreSQL.

Current capabilities include:

- Processing recorded video or a webcam feed with a trained Ultralytics model.
- Tracking individual patties and smoothing observations to reduce changes caused by noisy detections.
- Logging cooking sessions, patty records, and lifecycle events.
- Displaying live annotations and optionally saving annotated video.
- Processing video through a containerized Roboflow workflow.
- Sending local events and device heartbeats to an authenticated cloud API, with retries and duplicate protection.

The repository provides processing and data services. A project management dashboard, business reporting interface, and automated kitchen controls are outside the current implementation.

### How the workflow operates

1. A camera or video file supplies frames to the inference process.
2. The model identifies patties and their visible state.
3. Tracking associates observations with individual patties; smoothing determines when a state is stable enough to record.
4. Lifecycle events are written to the local database.
5. In an edge-to-cloud deployment, a separate service forwards those events when connectivity is available.

Local Ultralytics inference can continue during an internet outage once the required model and runtime are installed. The synchronization service retains its progress in SQLite and retries failed transfers. Its cloud payload contains event records and metadata; it does not upload a continuous camera stream.

### Interpreting the results

| Event | Meaning |
| --- | --- |
| `DETECTED` | A tracked patty has acquired a stable state, or a previously removed track has been reactivated. |
| `STATE_CHANGED` | A stable cooking-state transition has been accepted, other than the transition classified as a flip. |
| `FLIPPED` | The same track has changed from a stable `COOKED` state to a stable `RAW` state. |
| `REMOVED` | A track has been missing for the configured grace period, or its session has ended. |

The flip rule is an MVP heuristic based on visible state, rather than direct recognition of the physical flipping action. Likewise, `REMOVED` does not necessarily mean a patty was served: occlusion, tracking loss, and session completion can also produce this event. Event metadata distinguishes removal reasons.

### Delivery and acceptance considerations

Model accuracy and tracking continuity depend on the training data, camera position, lighting, occlusion, and the cooking surface. The `RAW` and `COOKED` labels describe model classifications; they do not measure internal temperature or establish food safety.

Before accepting a kitchen deployment, the project team should agree on representative footage, acceptable missed and false event rates, processing-speed requirements, and operational ownership. Suggested acceptance activities are:

- Compare generated events with manually reviewed footage from the intended kitchen.
- Check tracking and removal behavior when patties overlap or leave the camera view.
- Verify processing speed on the selected hardware.
- Disconnect and restore the network to confirm local logging and subsequent synchronization.
- Confirm responsibility for model updates, device credentials, backups, and data retention.

These are recommended validation activities, not claims of measured deployment performance.

## Architecture and operating modes

The edge-to-cloud path is:

```text
Camera / video
      |
      v
Ultralytics inference + BoT-SORT tracking
      |
      v
Lifecycle smoothing -> SQLite -> Edge synchronizer -> HTTPS API -> PostgreSQL
```

The Roboflow path uses a local inference-server container and its own detection stabilization, then writes lifecycle events through the shared core.

| Mode | Suitable use | Entry point | Requirements |
| --- | --- | --- | --- |
| Native Python | Camera trials, local development, and model evaluation | `pattyops.py` | Python, trained `.pt` checkpoint, camera or video |
| Roboflow with Docker | Repeatable recorded-video processing | `run-pattyops.ps1` / `compose.yaml` | Docker Compose, Roboflow credentials and workflow, input video |
| Edge + cloud | Kitchen inference with centralized event storage | `compose.edge.yaml` and `compose.cloud.yaml` | Linux camera host, trained model, device credentials, cloud API and PostgreSQL |

Use native Python for initial Windows webcam testing. The edge Compose configuration expects a Linux camera device at `/dev/video0`.

## Quick start: native Python

Run the following commands from the repository root in PowerShell. Python 3.12 is a practical starting point, matching the edge and cloud container images.

### 1. Prepare the environment

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Obtain a trained Ultralytics checkpoint for the patty classes and place it at `models/best.pt`, or use its existing location. Model weights, datasets, and input videos are excluded from version control and must be supplied separately.

### 2. Start a session

To use the interactive setup, which prompts for the source, model, and database:

```powershell
.\.venv\Scripts\python.exe pattyops.py
```

To run the default webcam directly:

```powershell
.\.venv\Scripts\python.exe pattyops.py run --model models/best.pt --source 0 --db Database/pattyops.db --show
```

To process a video and save an annotated copy:

```powershell
.\.venv\Scripts\python.exe pattyops.py run --model models/best.pt --source input/sample.mp4 --db Database/pattyops.db --save-video output/sample_annotated.mp4
```

Replace the example paths with your files. Press `q` in the live display to end a session cleanly.

### 3. Review the events

```powershell
.\.venv\Scripts\python.exe pattyops.py events --db Database/pattyops.db --limit 100
```

The default database is `pattyops.db` in the working directory when `--db` is omitted. Use the same database path when running inference and reviewing events.

## Alternative setup guides

- [Local inference and configuration](README.pattyops.md): interactive setup, source selection, class mappings, and smoothing.
- [Roboflow Docker processing](README.Docker.md): environment configuration, video processing, validation, and output locations.
- [Edge and cloud deployment](README.Production.md): cloud startup, device authentication, synchronization, and Ubuntu camera deployment.

The configuration templates serve separate purposes:

| Template | Local configuration file | Purpose |
| --- | --- | --- |
| `.env.example` | `.env` | Roboflow credentials, workflow identifiers, and processing filenames |
| `.env.edge.example` | `.env.edge` | SQLite path, cloud URL, device credentials, and synchronization settings |
| `.env.cloud.example` | `.env.cloud` | PostgreSQL connection settings and authorized device tokens |

Copy only the templates needed for your selected mode and replace their placeholders. Keep real credentials in the ignored local configuration files. For public cloud access, configure HTTPS through a reverse proxy or load balancer; the supplied cloud Compose file exposes the API on port `8000` without providing TLS termination.

## Technical reference

### State mapping and smoothing

The default lifecycle mapping is:

| Detector label | Lifecycle state |
| --- | --- |
| `Raw_Patty` | `RAW` |
| `CookedPatty` | `COOKED` |
| `CookedPatty_w-cheese` | `COOKED` |
| `SaltPepper` | Excluded from patty lifecycle tracking |

For different labels, use `class-map.example.json` as a starting point and pass the resulting file with `--class-map`.

The native runner defaults to a 12-observation history, at least 5 observations, a 65% majority, and a 45-frame removal grace period. Adjust these using `--history-size`, `--min-stable`, `--stable-ratio`, and `--removal-grace-frames`. These settings trade response time against resistance to detection noise; evaluate changes against representative footage.

### Storage and synchronization

| Local table | Purpose |
| --- | --- |
| `cooking_sessions` | Source, model path, timestamps, and completion status for each run |
| `patties` | Current lifecycle state and flip count for each track within a session |
| `patty_events` | Lifecycle history, state transitions, confidence, timestamps, and metadata |
| `cloud_sync_state` | Acknowledged synchronization position, created by the synchronizer |
| `edge_identity` | Persistent installation identity used by synchronization |

`occurred_at` records timezone-aware wall-clock time. For recorded footage, `video_seconds` separately identifies the position in the source video. Reprocessing an old recording therefore creates events with the current processing time.

The synchronizer advances its checkpoint only after the full batch is acknowledged. The cloud uses idempotency keys to prevent duplicate ingestion on retries. Preserve the SQLite database, including synchronization state, across device restarts. Run one inference writer per database; the provided edge setup shares that database with a separate synchronizer.

### Cloud API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Public health check |
| `POST` | `/v1/devices/heartbeat` | Update device activity |
| `POST` | `/v1/events/batch` | Ingest a batch of events with duplicate protection |
| `GET` | `/v1/events` | List recent events for the authenticated device |

All endpoints except `/healthz` require `Authorization: Bearer <token>` and `X-Device-ID: <device-id>`. Configure matching device credentials on the edge and cloud. The event listing is scoped to the calling device.

## Repository guide

| File or group | Responsibility |
| --- | --- |
| `pattyops.py` | Native CLI, interactive setup, inference, and video display/output |
| `pattyops_core.py` | Shared lifecycle logic, smoothing, and SQLite persistence |
| `pattyops_roboflow.py` | Roboflow workflow integration and annotated video processing |
| `edge_sync.py` | Event delivery, heartbeats, retries, and checkpoints |
| `cloud_api.py` | FastAPI application, authentication, and cloud persistence |
| `prepare_yolo_detection_dataset.py` | Prepare detection datasets, including polygon-to-box conversion |
| `run-pattyops.ps1` | Windows launcher and configuration validation for Roboflow Docker processing |
| `Dockerfile*`, `compose*.yaml` | Container images and service definitions for each operating mode |
| `requirements*.txt` | Dependencies for native, Roboflow Docker, edge, and cloud environments |
| `test_*.py` | Lifecycle, synchronization, API, and event-delivery tests |

## Verification and maintenance

The lifecycle and synchronization unit tests use Python's standard library:

```powershell
python -m unittest -v test_pattyops.py test_edge_sync.py
```

To run all four test modules in a virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-cloud.txt
.\.venv\Scripts\python.exe -m unittest -v test_pattyops.py test_edge_sync.py test_cloud_api.py test_production_flow.py
```

The API and event-delivery tests use temporary SQLite databases and an in-process API client. They do not require a running cloud deployment, camera, or trained model, and do not establish real-world detection accuracy or PostgreSQL deployment readiness.

When changing a model or class mapping, review annotated footage and event output together. When changing synchronization or the API, run the event-delivery tests and verify the deployed edge-to-cloud path. Keep model versions identifiable: sessions record the supplied model path, so overwriting a checkpoint at the same path does not provide full model version history.

## Troubleshooting

| Symptom | First checks |
| --- | --- |
| Model cannot be loaded | Confirm the checkpoint exists and is compatible with the selected inference mode. |
| Webcam does not open | Try camera index `1` or `2`; check whether another application is using it. For edge Docker, check `/dev/video0` access. |
| False flips or premature removals | Review detector output, tracking continuity, smoothing settings, and the removal grace period. |
| No events appear in the listing | Confirm the database path matches the inference run and the model labels map to supported lifecycle states. |
| Events do not reach the cloud | Check synchronizer logs, database path, API reachability, and matching device ID/token values. |
| Roboflow processing fails to start | Check Docker availability, input filenames, and `.env` values; run `./run-pattyops.ps1 -ValidateOnly`. |
