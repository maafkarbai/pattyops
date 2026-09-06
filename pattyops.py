from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from pattyops_core import Observation, PattyOpsDatabase, PattyStateManager


def parse_source(value: str) -> str | int:
    cleaned = value.strip()
    if cleaned.casefold().startswith("camera:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    return int(cleaned) if cleaned.isdigit() else cleaned


def source_label(source: str | int) -> str:
    return f"webcam:{source}" if isinstance(source, int) else source


def event_line(event) -> str:
    transition = ""
    if event.previous_state or event.new_state:
        transition = f" {event.previous_state or '-'} -> {event.new_state or '-'}"
    confidence = "" if event.confidence is None else f" conf={event.confidence:.3f}"
    timestamp = event.occurred_at or datetime.now().astimezone().isoformat(
        timespec="milliseconds"
    )
    return (
        f"[{timestamp}] track={event.track_id} "
        f"{event.event_type}{transition}{confidence}"
    )


def probe_source(cv2, source: str | int, fallback_fps: float) -> float:
    """Fail early for a bad video/camera and return its declared FPS."""
    capture = cv2.VideoCapture(source)
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Could not open {source_label(source)}")
        if isinstance(source, int):
            # USB cameras sometimes need a few reads before their first frame.
            for _ in range(10):
                success, frame = capture.read()
                if success and frame is not None:
                    break
            else:
                raise RuntimeError(
                    f"Connected to webcam {source}, but could not read a frame"
                )
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        return fps if fps > 0 else fallback_fps
    finally:
        capture.release()


def load_class_map(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    map_path = Path(path).expanduser().resolve()
    if not map_path.is_file():
        raise ValueError(f"Class map not found: {map_path}")
    try:
        contents = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read class map {map_path}: {exc}") from exc
    if not isinstance(contents, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in contents.items()
    ):
        raise ValueError(
            "Class map must be a JSON object mapping detector labels to RAW or COOKED"
        )
    invalid_states = sorted(
        {value for value in contents.values() if value.strip().upper() not in {"RAW", "COOKED"}}
    )
    if invalid_states:
        raise ValueError(
            "Invalid class-map state(s): "
            + ", ".join(repr(state) for state in invalid_states)
            + "; expected RAW or COOKED"
        )
    return contents


def draw_run_status(cv2, frame, source: str | int) -> None:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    label = f"{timestamp}  |  {source_label(source)}"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 38), (20, 20, 20), -1)
    cv2.putText(
        frame,
        label,
        (12, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{label}{suffix}: ").strip()
    return answer or (default or "")


def _first_matching_file(folder: Path, patterns: tuple[str, ...]) -> Path | None:
    if not folder.is_dir():
        return None
    matches = sorted(
        (path for pattern in patterns for path in folder.glob(pattern)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def interactive_arguments() -> list[str]:
    """Collect the minimal inputs needed for a live PattyOps run."""
    print("\nPattyOps live cooking logger")
    print("1) Process a video file")
    print("2) Connect a webcam / USB camera")
    while True:
        source_type = _prompt("Choose a source", "1").casefold()
        if source_type in {"1", "video", "v"}:
            default_video = _first_matching_file(
                Path("input"), ("*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm")
            )
            source = _prompt(
                "Video path", str(default_video) if default_video else None
            )
            if source:
                break
            print("Please enter a video path.")
        elif source_type in {"2", "webcam", "camera", "w", "c"}:
            camera_index = _prompt("Camera index", "0")
            if camera_index.isdigit():
                source = f"camera:{camera_index}"
                break
            print("Camera index must be 0 or a positive whole number.")
        else:
            print("Enter 1 for video or 2 for webcam.")

    default_model = _first_matching_file(Path.cwd(), ("best.pt", "*.pt"))
    while True:
        model = _prompt(
            "Ultralytics model (.pt)",
            str(default_model) if default_model else None,
        )
        if model:
            break
        print("Please enter the model exported from your latest dataset training.")

    database = _prompt("SQLite log file", str(Path("Database") / "pattyops.db"))
    arguments = [
        "run",
        "--model",
        model,
        "--source",
        source,
        "--db",
        database,
        "--show",
    ]

    save_video = _prompt("Save an annotated recording? (y/N)", "N").casefold()
    if save_video in {"y", "yes"}:
        default_output = Path("output") / datetime.now().strftime(
            "pattyops_%Y%m%d_%H%M%S.mp4"
        )
        arguments.extend(
            ["--save-video", _prompt("Recording path", str(default_output))]
        )

    class_map = _prompt("Optional class-map JSON (press Enter to skip)")
    if class_map:
        arguments.extend(["--class-map", class_map])
    return arguments


def run_tracking(args: argparse.Namespace) -> int:
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:
        print(
            "Missing runtime dependency. Run: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        return 2

    model_path = Path(args.model).expanduser().resolve()
    if not model_path.is_file():
        print(f"Model not found: {model_path}", file=sys.stderr)
        return 2

    source = parse_source(args.source)
    if isinstance(source, str) and not source.startswith(
        ("http://", "https://", "rtsp://")
    ):
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            print(f"Source not found: {source_path}", file=sys.stderr)
            return 2
        source = str(source_path)

    try:
        class_map = load_class_map(args.class_map)
        fps = probe_source(cv2, source, args.fps)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Source error: {exc}", file=sys.stderr)
        return 2

    model = YOLO(str(model_path))
    database = PattyOpsDatabase(args.db)
    session_id = database.start_session(source_label(source), str(model_path))
    manager = PattyStateManager(
        database=database,
        session_id=session_id,
        history_size=args.history_size,
        min_stable_observations=args.min_stable,
        stable_ratio=args.stable_ratio,
        removal_grace_frames=args.removal_grace_frames,
        class_map=class_map,
    )

    writer = None
    started_monotonic = time.monotonic()
    last_video_seconds = 0.0
    status = "COMPLETED"
    try:
        results = model.track(
            source=source,
            stream=True,
            tracker=args.tracker,
            conf=args.confidence,
            iou=args.iou,
            verbose=False,
        )
        for frame_index, result in enumerate(results):
            last_video_seconds = (
                time.monotonic() - started_monotonic
                if isinstance(source, int)
                else frame_index / fps
            )
            observations: list[Observation] = []
            boxes = result.boxes
            if boxes is not None and boxes.is_track and boxes.id is not None:
                track_ids = boxes.id.int().cpu().tolist()
                class_ids = boxes.cls.int().cpu().tolist()
                confidences = boxes.conf.cpu().tolist()
                for track_id, class_id, confidence in zip(
                    track_ids, class_ids, confidences
                ):
                    observations.append(
                        Observation(
                            track_id=track_id,
                            class_name=result.names[class_id],
                            confidence=float(confidence),
                            video_seconds=last_video_seconds,
                        )
                    )

            for event in manager.update(
                frame_index, observations, frame_video_seconds=last_video_seconds
            ):
                print(event_line(event), flush=True)

            if args.show or args.save_video:
                annotated = result.plot()
                draw_run_status(cv2, annotated, source)
                if args.save_video and writer is None:
                    height, width = annotated.shape[:2]
                    output_path = Path(args.save_video).expanduser().resolve()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(
                        str(output_path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps,
                        (width, height),
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"Could not open video writer: {output_path}")
                if writer is not None:
                    writer.write(annotated)
                if args.show:
                    cv2.imshow("PattyOps", annotated)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    except KeyboardInterrupt:
        print("Stopping PattyOps...", file=sys.stderr)
    except Exception:
        status = "FAILED"
        raise
    finally:
        for event in manager.finalize(last_video_seconds):
            print(event_line(event), flush=True)
        database.finish_session(session_id, status=status)
        database.close()
        if writer is not None:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()

    print(f"Session {session_id} {status.lower()}. Database: {Path(args.db).resolve()}")
    return 0


def show_events(args: argparse.Namespace) -> int:
    database = PattyOpsDatabase(args.db)
    try:
        rows = list(reversed(database.list_events(args.limit)))
    finally:
        database.close()
    if not rows:
        print("No PattyOps events found.")
        return 0
    print("id  session  track  event          video_s  transition          confidence  occurred_at")
    print("--  -------  -----  -------------  -------  ------------------  ----------  -----------")
    for row in rows:
        transition = f"{row['previous_state'] or '-'}->{row['new_state'] or '-'}"
        video_seconds = "-" if row["video_seconds"] is None else f"{row['video_seconds']:.2f}"
        confidence = "-" if row["confidence"] is None else f"{row['confidence']:.3f}"
        print(
            f"{row['id']:<3} {row['session_id']:<8} {row['track_id']:<6} "
            f"{row['event_type']:<14} {video_seconds:<8} {transition:<19} "
            f"{confidence:<11} {row['occurred_at']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track patty cooking states and log lifecycle events to SQLite."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run YOLO tracking and event logging")
    run_parser.add_argument("--model", required=True, help="Path to trained best.pt")
    run_parser.add_argument(
        "--source",
        required=True,
        help="Video path, stream URL, or camera index such as 0",
    )
    run_parser.add_argument("--db", default="pattyops.db", help="SQLite database path")
    run_parser.add_argument("--tracker", default="botsort.yaml")
    run_parser.add_argument("--confidence", type=float, default=0.35)
    run_parser.add_argument("--iou", type=float, default=0.5)
    run_parser.add_argument("--history-size", type=int, default=12)
    run_parser.add_argument("--min-stable", type=int, default=5)
    run_parser.add_argument("--stable-ratio", type=float, default=0.65)
    run_parser.add_argument("--removal-grace-frames", type=int, default=45)
    run_parser.add_argument("--fps", type=float, default=30.0, help="Fallback FPS")
    run_parser.add_argument(
        "--class-map",
        metavar="JSON",
        help="Optional JSON mapping new detector class labels to RAW or COOKED",
    )
    run_parser.add_argument(
        "--show", action="store_true", help="Show annotated live view; q quits"
    )
    run_parser.add_argument(
        "--save-video",
        metavar="PATH",
        help="Optional path for an annotated MP4",
    )
    run_parser.set_defaults(handler=run_tracking)

    events_parser = subparsers.add_parser("events", help="Print recent database events")
    events_parser.add_argument("--db", default="pattyops.db")
    events_parser.add_argument("--limit", type=int, default=100)
    events_parser.set_defaults(handler=show_events)
    return parser


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:] if len(sys.argv) > 1 else interactive_arguments()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
