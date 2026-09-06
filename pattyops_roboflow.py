import os
import cv2
import queue
import threading
from collections import deque, Counter
from pathlib import Path

from dotenv import load_dotenv

from inference_sdk import InferenceConfiguration, InferenceHTTPClient
from inference_sdk.webrtc import (
    VideoFileSource,
    StreamConfig,
    VideoMetadata
)

from pattyops_core import Observation, PattyOpsDatabase, PattyStateManager

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("ROBOFLOW_API_KEY")
API_URL = os.getenv(
    "ROBOFLOW_API_URL",
    "http://localhost:9001"
)
WORKSPACE = os.getenv(
    "ROBOFLOW_WORKSPACE",
    "abdullas-workspace-ifav6"
)
WORKFLOW = os.getenv(
    "ROBOFLOW_WORKFLOW",
    "pattycookingstate"
)

if not API_KEY:
    raise RuntimeError(
        "ROBOFLOW_API_KEY not found in .env"
    )


# ============================================================
# VIDEO SETTINGS
# ============================================================

VIDEO_PATH = os.getenv(
    "PATTYOPS_VIDEO_PATH",
    str(BASE_DIR / "input" / "Patty3.mp4"),
)
OUTPUT_ROOT = Path(
    os.getenv("PATTYOPS_OUTPUT_DIR", str(BASE_DIR / "output"))
).expanduser().resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = os.getenv(
    "PATTYOPS_OUTPUT_PATH",
    str(OUTPUT_ROOT / "pattyops_output.mp4"),
)
DATABASE_PATH = os.getenv(
    "PATTYOPS_DATABASE_PATH",
    str(BASE_DIR / "Database" / "pattyops.db"),
)
SHOW_GUI = os.getenv("PATTYOPS_SHOW_GUI", "false").lower() in {
    "1", "true", "yes", "on"
}

# IMPORTANT:
# False = process every frame.
#
# Use False while testing/training the system because
# realtime=True may drop frames to keep up with playback.
REALTIME_PROCESSING = os.getenv(
    "PATTYOPS_REALTIME_PROCESSING", "false"
).lower() in {"1", "true", "yes", "on"}


# ============================================================
# DETECTION / TRACKING SETTINGS
# ============================================================

# New object must be reasonably confident before
# we create a new track.
NEW_TRACK_THRESHOLD = 0.40

# Existing tracked object may temporarily fall lower.
KEEP_TRACK_THRESHOLD = 0.20

# How many completely missed frames before removing track.
#
# At 24 FPS:
# 8 frames ~= 0.33 seconds
MAX_MISSED_FRAMES = 8

# How much two boxes must overlap to be considered
# the same physical object.
MATCH_IOU_THRESHOLD = 0.20

# Bounding box smoothing.
#
# Higher = reacts faster
# Lower  = smoother
BOX_SMOOTHING = 0.35

# Smooth confidence values too.
CONFIDENCE_SMOOTHING = 0.35

# How many recent classifications to remember.
CLASS_HISTORY = 7

# Object must be detected this many times before
# displaying it.
MIN_HITS = 2


# ============================================================
# OPEN VIDEO
# ============================================================

if not os.path.exists(VIDEO_PATH):
    raise RuntimeError(
        f"Video not found: {VIDEO_PATH}"
    )

cap = cv2.VideoCapture(VIDEO_PATH)

if not cap.isOpened():
    raise RuntimeError(
        f"Could not open: {VIDEO_PATH}"
    )

FPS = cap.get(cv2.CAP_PROP_FPS)

WIDTH = int(
    cap.get(cv2.CAP_PROP_FRAME_WIDTH)
)

HEIGHT = int(
    cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
)

TOTAL_FRAMES = int(
    cap.get(cv2.CAP_PROP_FRAME_COUNT)
)

if FPS <= 0:
    FPS = 30.0


# ============================================================
# OUTPUT VIDEO
# ============================================================

writer = cv2.VideoWriter(
    OUTPUT_PATH,
    cv2.VideoWriter_fourcc(*"mp4v"),
    FPS,
    (WIDTH, HEIGHT)
)

if not writer.isOpened():
    raise RuntimeError(
        "Could not create output video."
    )


# ============================================================
# ROBOFLOW
# ============================================================

client = InferenceHTTPClient.init(
    api_url=API_URL,
    api_key=API_KEY
)

client.configure(
    InferenceConfiguration(api_key_transport="header")
)

source = VideoFileSource(
    VIDEO_PATH,
    realtime_processing=REALTIME_PROCESSING
)

config = StreamConfig(
    stream_output=[],
    data_output=["predictions"],
    realtime_processing=REALTIME_PROCESSING
)

session = client.webrtc.stream(
    source=source,
    workflow=WORKFLOW,
    workspace=WORKSPACE,
    image_input="image",
    config=config
)


# ============================================================
# GUI QUEUE
# ============================================================

frame_queue = queue.Queue(
    maxsize=2
)

finished = False

inference_failed = False


# ============================================================
# COLOURS
# ============================================================

CLASS_COLORS = {}

COLOR_PALETTE = [
    (0, 255, 0),
    (255, 0, 0),
    (0, 0, 255),
    (0, 255, 255),
    (255, 0, 255),
    (255, 255, 0),
    (255, 128, 0),
    (128, 0, 255),
    (0, 128, 255),
    (128, 255, 0)
]


def get_color(class_id):

    try:
        class_id = int(class_id)
    except (TypeError, ValueError):
        class_id = 0

    if class_id not in CLASS_COLORS:

        CLASS_COLORS[class_id] = (
            COLOR_PALETTE[
                class_id % len(COLOR_PALETTE)
            ]
        )

    return CLASS_COLORS[class_id]


# ============================================================
# FIND ROBOFLOW PREDICTIONS
# ============================================================

def find_detection_result(obj):
    """
    Searches recursively for a Roboflow detection result
    containing a 'predictions' list.
    """

    if isinstance(obj, dict):

        predictions = obj.get(
            "predictions"
        )

        if isinstance(predictions, list):

            if len(predictions) == 0:
                return obj

            first = predictions[0]

            if isinstance(first, dict):

                required = {
                    "x",
                    "y",
                    "width",
                    "height"
                }

                if required.issubset(
                    first.keys()
                ):
                    return obj

        for value in obj.values():

            result = find_detection_result(
                value
            )

            if result is not None:
                return result

    elif isinstance(obj, list):

        for value in obj:

            result = find_detection_result(
                value
            )

            if result is not None:
                return result

    return None


# ============================================================
# COORDINATE FUNCTIONS
# ============================================================

def detection_to_box(
    detection,
    scale_x=1.0,
    scale_y=1.0
):
    """
    Roboflow gives:

            x, y
          = centre

    Convert to:

        x1, y1, x2, y2
    """

    x = (
        float(detection["x"])
        * scale_x
    )

    y = (
        float(detection["y"])
        * scale_y
    )

    width = (
        float(detection["width"])
        * scale_x
    )

    height = (
        float(detection["height"])
        * scale_y
    )

    return [
        x - width / 2,
        y - height / 2,
        x + width / 2,
        y + height / 2
    ]


# ============================================================
# IoU
# ============================================================

def calculate_iou(
    box_a,
    box_b
):

    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)

    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    intersection_width = max(
        0,
        ix2 - ix1
    )

    intersection_height = max(
        0,
        iy2 - iy1
    )

    intersection_area = (
        intersection_width
        * intersection_height
    )

    area_a = max(
        0,
        (ax2 - ax1)
        * (ay2 - ay1)
    )

    area_b = max(
        0,
        (bx2 - bx1)
        * (by2 - by1)
    )

    union = (
        area_a
        + area_b
        - intersection_area
    )

    if union <= 0:
        return 0.0

    return (
        intersection_area
        / union
    )


# ============================================================
# BOX SMOOTHING
# ============================================================

def smooth_box(
    old_box,
    new_box
):

    return [
        (
            old_value
            * (1 - BOX_SMOOTHING)
        )
        +
        (
            new_value
            * BOX_SMOOTHING
        )

        for old_value, new_value
        in zip(
            old_box,
            new_box
        )
    ]


# ============================================================
# TEMPORAL TRACKER / STABILIZER
# ============================================================

class DetectionStabilizer:

    def __init__(self):

        self.tracks = {}

        self.next_track_id = 1


    def update(
        self,
        raw_detections,
        scale_x=1.0,
        scale_y=1.0
    ):

        # ----------------------------------------------------
        # Keep detections usable for existing tracks
        # ----------------------------------------------------

        candidates = []

        for detection in raw_detections:

            confidence = float(
                detection.get(
                    "confidence",
                    0
                )
            )

            if (
                confidence
                >= KEEP_TRACK_THRESHOLD
            ):

                candidates.append(
                    detection
                )


        # ----------------------------------------------------
        # Find all possible IoU matches
        # ----------------------------------------------------

        possible_matches = []

        for (
            track_id,
            track
        ) in self.tracks.items():

            for (
                detection_index,
                detection
            ) in enumerate(candidates):

                new_box = detection_to_box(
                    detection,
                    scale_x,
                    scale_y
                )

                iou = calculate_iou(
                    track["box"],
                    new_box
                )

                if (
                    iou
                    >= MATCH_IOU_THRESHOLD
                ):

                    possible_matches.append(
                        (
                            iou,
                            track_id,
                            detection_index
                        )
                    )


        # Best IoU first
        possible_matches.sort(
            key=lambda x: x[0],
            reverse=True
        )


        matched_tracks = set()
        matched_detections = set()


        # ----------------------------------------------------
        # Apply best matches
        # ----------------------------------------------------

        for (
            iou,
            track_id,
            detection_index
        ) in possible_matches:

            if (
                track_id
                in matched_tracks
            ):
                continue

            if (
                detection_index
                in matched_detections
            ):
                continue


            track = self.tracks[
                track_id
            ]

            detection = candidates[
                detection_index
            ]


            new_box = detection_to_box(
                detection,
                scale_x,
                scale_y
            )


            # Smooth box position
            track["box"] = smooth_box(
                track["box"],
                new_box
            )


            new_confidence = float(
                detection.get(
                    "confidence",
                    0
                )
            )


            # Smooth confidence
            track["confidence"] = (
                track["confidence"]
                * (
                    1
                    - CONFIDENCE_SMOOTHING
                )
                +
                new_confidence
                * CONFIDENCE_SMOOTHING
            )


            class_name = detection.get(
                "class",
                "Unknown"
            )

            class_id = detection.get(
                "class_id",
                0
            )


            track["classes"].append(
                (
                    class_name,
                    class_id
                )
            )


            track["missed"] = 0
            track["hits"] += 1


            matched_tracks.add(
                track_id
            )

            matched_detections.add(
                detection_index
            )


        # ----------------------------------------------------
        # Mark unmatched tracks as temporarily missing
        # ----------------------------------------------------

        for track_id in list(
            self.tracks.keys()
        ):

            if (
                track_id
                not in matched_tracks
            ):

                track = self.tracks[
                    track_id
                ]

                track["missed"] += 1


                if (
                    track["missed"]
                    > MAX_MISSED_FRAMES
                ):

                    del self.tracks[
                        track_id
                    ]


        # ----------------------------------------------------
        # Create new tracks
        # ----------------------------------------------------

        for (
            detection_index,
            detection
        ) in enumerate(candidates):

            if (
                detection_index
                in matched_detections
            ):
                continue


            confidence = float(
                detection.get(
                    "confidence",
                    0
                )
            )


            # Lower-confidence detection may preserve
            # existing object, but cannot CREATE one.
            if (
                confidence
                < NEW_TRACK_THRESHOLD
            ):
                continue


            class_name = detection.get(
                "class",
                "Unknown"
            )

            class_id = detection.get(
                "class_id",
                0
            )


            track_id = (
                self.next_track_id
            )

            self.next_track_id += 1


            self.tracks[
                track_id
            ] = {

                "box":
                    detection_to_box(
                        detection,
                        scale_x,
                        scale_y
                    ),

                "confidence":
                    confidence,

                "missed":
                    0,

                "hits":
                    1,

                "classes":
                    deque(
                        [
                            (
                                class_name,
                                class_id
                            )
                        ],
                        maxlen=CLASS_HISTORY
                    )
            }


        # ----------------------------------------------------
        # Build stable output
        # ----------------------------------------------------

        output = []


        for (
            track_id,
            track
        ) in self.tracks.items():

            if (
                track["hits"]
                < MIN_HITS
            ):
                continue


            x1, y1, x2, y2 = (
                track["box"]
            )


            x = (
                x1 + x2
            ) / 2

            y = (
                y1 + y2
            ) / 2

            width = (
                x2 - x1
            )

            height = (
                y2 - y1
            )


            # ------------------------------------------------
            # Majority vote for class
            # ------------------------------------------------

            class_counter = Counter(
                track["classes"]
            )

            (
                class_name,
                class_id
            ) = class_counter.most_common(
                1
            )[0][0]


            output.append(
                {
                    "x": x,
                    "y": y,

                    "width": width,
                    "height": height,

                    "confidence":
                        track[
                            "confidence"
                        ],

                    "class":
                        class_name,

                    "class_id":
                        class_id,

                    "tracker_id":
                        track_id,

                    "missed":
                        track[
                            "missed"
                        ]
                }
            )


        return output


# ============================================================
# CREATE STABILIZER ONCE
# ============================================================

stabilizer = DetectionStabilizer()


# ============================================================
# PATTYOPS SQLITE LOGGER
# ============================================================

database = PattyOpsDatabase(DATABASE_PATH)
event_lock = threading.RLock()

cooking_session_id = database.start_session(
    VIDEO_PATH,
    f"roboflow://{WORKSPACE}/{WORKFLOW}"
)

state_manager = PattyStateManager(
    database=database,
    session_id=cooking_session_id,
    history_size=12,
    min_stable_observations=5,
    stable_ratio=0.65,
    removal_grace_frames=12,
)


def print_patty_event(event):

    transition = (
        f"{event.previous_state or '-'}"
        f" -> {event.new_state or '-'}"
    )

    print(
        f"\n[EVENT] track={event.track_id} "
        f"{event.event_type} {transition}"
    )


# ============================================================
# DRAW DETECTIONS
# ============================================================

def draw_boxes(
    frame,
    detections
):

    for prediction in detections:

        confidence = float(
            prediction.get(
                "confidence",
                0
            )
        )


        x = float(
            prediction["x"]
        )

        y = float(
            prediction["y"]
        )

        width = float(
            prediction["width"]
        )

        height = float(
            prediction["height"]
        )


        x1 = int(
            x - width / 2
        )

        y1 = int(
            y - height / 2
        )

        x2 = int(
            x + width / 2
        )

        y2 = int(
            y + height / 2
        )


        # Keep inside image
        x1 = max(
            0,
            min(
                x1,
                frame.shape[1] - 1
            )
        )

        y1 = max(
            0,
            min(
                y1,
                frame.shape[0] - 1
            )
        )

        x2 = max(
            0,
            min(
                x2,
                frame.shape[1] - 1
            )
        )

        y2 = max(
            0,
            min(
                y2,
                frame.shape[0] - 1
            )
        )


        class_name = prediction.get(
            "class",
            "Unknown"
        )

        class_id = prediction.get(
            "class_id",
            0
        )

        tracker_id = prediction.get(
            "tracker_id",
            "?"
        )

        missed = prediction.get(
            "missed",
            0
        )


        color = get_color(
            class_id
        )


        # ----------------------------------------------------
        # Box
        # ----------------------------------------------------

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            color,
            2
        )


        # ----------------------------------------------------
        # Label
        # ----------------------------------------------------

        label = (
            f"#{tracker_id} "
            f"{class_name} "
            f"{confidence * 100:.0f}%"
        )


        # Show when detection is being temporarily
        # preserved by tracker.
        if missed > 0:

            label += (
                f" [{missed}]"
            )


        font = (
            cv2.FONT_HERSHEY_SIMPLEX
        )

        font_scale = 0.55
        thickness = 2


        (
            text_width,
            text_height
        ), baseline = cv2.getTextSize(
            label,
            font,
            font_scale,
            thickness
        )


        text_y = max(
            y1,
            text_height + 12
        )


        # Background
        cv2.rectangle(
            frame,

            (
                x1,
                text_y
                - text_height
                - 10
            ),

            (
                min(
                    frame.shape[1] - 1,
                    x1
                    + text_width
                    + 10
                ),
                text_y + 3
            ),

            color,
            -1
        )


        # Text
        cv2.putText(
            frame,
            label,

            (
                x1 + 5,
                text_y - 5
            ),

            font,
            font_scale,

            (0, 0, 0),

            thickness,
            cv2.LINE_AA
        )


    return frame


# ============================================================
# COUNTERS
# ============================================================

processed_frames = 0

total_raw_detections = 0

total_stable_detections = 0


# ============================================================
# PREDICTION CALLBACK
# ============================================================

@session.on_data("predictions")
def on_predictions(
    result,
    metadata: VideoMetadata
):

    global processed_frames
    global total_raw_detections
    global total_stable_detections


    # --------------------------------------------------------
    # Read matching original frame
    # --------------------------------------------------------

    success, frame = cap.read()

    if not success:

        print(
            f"\nCould not read frame "
            f"{metadata.frame_id}"
        )

        return


    # --------------------------------------------------------
    # Get predictions
    # --------------------------------------------------------

    detection_result = (
        find_detection_result(
            result
        )
    )

    raw_detections = []


    prediction_width = WIDTH
    prediction_height = HEIGHT


    if detection_result is not None:

        raw_detections = (
            detection_result.get(
                "predictions",
                []
            )
        )


        image_info = (
            detection_result.get(
                "image",
                {}
            )
        )


        prediction_width = (
            image_info.get(
                "width",
                WIDTH
            )
        )

        prediction_height = (
            image_info.get(
                "height",
                HEIGHT
            )
        )


    # --------------------------------------------------------
    # Scale Roboflow coordinates to original video
    # --------------------------------------------------------

    if prediction_width:

        scale_x = (
            frame.shape[1]
            / prediction_width
        )

    else:

        scale_x = 1.0


    if prediction_height:

        scale_y = (
            frame.shape[0]
            / prediction_height
        )

    else:

        scale_y = 1.0


    # --------------------------------------------------------
    # TEMPORAL STABILIZATION
    # --------------------------------------------------------

    stable_detections = (
        stabilizer.update(
            raw_detections,
            scale_x,
            scale_y
        )
    )


    # --------------------------------------------------------
    # PATTY LIFECYCLE EVENT LOGGING
    # --------------------------------------------------------

    try:

        frame_index = int(metadata.frame_id)

    except (TypeError, ValueError):

        frame_index = processed_frames


    video_seconds = frame_index / FPS


    observations = [
        Observation(
            track_id=int(detection["tracker_id"]),
            class_name=str(
                detection.get("class", "Unknown")
            ),
            confidence=float(
                detection.get("confidence", 0)
            ),
            video_seconds=video_seconds,
        )
        for detection in stable_detections
        if int(detection.get("missed", 0)) == 0
    ]


    with event_lock:

        emitted_events = state_manager.update(
            frame_index,
            observations,
            frame_video_seconds=video_seconds,
        )


    for event in emitted_events:

        print_patty_event(event)


    # --------------------------------------------------------
    # Draw stable boxes
    # --------------------------------------------------------

    frame = draw_boxes(
        frame,
        stable_detections
    )


    raw_count = len(
        raw_detections
    )

    stable_count = len(
        stable_detections
    )


    # --------------------------------------------------------
    # Information overlay
    # --------------------------------------------------------

    cv2.putText(
        frame,

        (
            f"Frame: "
            f"{metadata.frame_id}/"
            f"{TOTAL_FRAMES}"
        ),

        (20, 30),

        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,

        (255, 255, 255),

        2,
        cv2.LINE_AA
    )


    cv2.putText(
        frame,

        (
            f"Raw detections: "
            f"{raw_count}"
        ),

        (20, 60),

        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,

        (255, 255, 255),

        2,
        cv2.LINE_AA
    )


    cv2.putText(
        frame,

        (
            f"Tracked objects: "
            f"{stable_count}"
        ),

        (20, 90),

        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,

        (255, 255, 255),

        2,
        cv2.LINE_AA
    )


    # --------------------------------------------------------
    # Save EVERY processed frame
    # --------------------------------------------------------

    writer.write(
        frame
    )


    # --------------------------------------------------------
    # GUI queue
    # --------------------------------------------------------

    # GUI doesn't need to display every frame if inference
    # temporarily outruns the screen.

    if frame_queue.full():

        try:

            frame_queue.get_nowait()

        except queue.Empty:
            pass


    try:

        frame_queue.put_nowait(
            frame.copy()
        )

    except queue.Full:
        pass


    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    processed_frames += 1

    total_raw_detections += (
        raw_count
    )

    total_stable_detections += (
        stable_count
    )


    if processed_frames % 100 == 0 or processed_frames == TOTAL_FRAMES:

        print(
            f"\rFrame "
            f"{metadata.frame_id}/"
            f"{TOTAL_FRAMES}"
            f" | Raw: {raw_count}"
            f" | Stable: {stable_count}",
            end="",
            flush=True
        )


# ============================================================
# ERROR CALLBACK
# ============================================================

@session.on_error
def on_error(
    errors,
    metadata: VideoMetadata
):

    print(
        f"\nERROR frame "
        f"{metadata.frame_id}: "
        f"{errors}"
    )


# ============================================================
# INFERENCE THREAD
# ============================================================

def run_inference():

    global finished
    global inference_failed

    try:

        session.run()

    except Exception as error:

        inference_failed = True

        print(
            "\nInference error:"
        )

        print(
            error
        )

    finally:

        finished = True


inference_thread = threading.Thread(
    target=run_inference,
    daemon=True
)


# ============================================================
# OPEN GUI
# ============================================================

WINDOW_NAME = (
    "PattyOps - Stable Detection"
)

if SHOW_GUI:

    cv2.namedWindow(
        WINDOW_NAME,
        cv2.WINDOW_NORMAL
    )

    cv2.resizeWindow(
        WINDOW_NAME,
        1000,
        700
    )


# ============================================================
# INFORMATION
# ============================================================

print()
print(
    "============================================"
)
print(
    "                PATTYOPS"
)
print(
    "============================================"
)

print(
    f"Video:              {VIDEO_PATH}"
)

print(
    f"Resolution:         "
    f"{WIDTH}x{HEIGHT}"
)

print(
    f"FPS:                "
    f"{FPS:.2f}"
)

print(
    f"Frames:             "
    f"{TOTAL_FRAMES}"
)

print(
    f"New track threshold:"
    f" {NEW_TRACK_THRESHOLD:.2f}"
)

print(
    f"Keep threshold:     "
    f" {KEEP_TRACK_THRESHOLD:.2f}"
)

print(
    f"Miss buffer:        "
    f" {MAX_MISSED_FRAMES} frames"
)

print(
    "============================================"
)

print()
print(
    "Waiting for Roboflow..."
)
print(
    "Press Q to stop."
)
print()


# ============================================================
# START INFERENCE
# ============================================================

inference_thread.start()


# ============================================================
# MAIN GUI LOOP
# ============================================================

while True:

    try:

        frame = frame_queue.get(
            timeout=0.03
        )

        if SHOW_GUI:

            cv2.imshow(
                WINDOW_NAME,
                frame
            )

    except queue.Empty:
        pass


    # Keeps Windows window responsive
    key = (
        cv2.waitKey(1) & 0xFF
        if SHOW_GUI
        else 255
    )


    if key == ord("q"):

        print(
            "\n\nStopping..."
        )

        break


    if (
        finished
        and frame_queue.empty()
    ):

        break


# ============================================================
# CLEANUP
# ============================================================

try:

    session.close()

except Exception:
    pass


cap.release()

writer.release()

if SHOW_GUI:

    cv2.destroyAllWindows()


last_video_seconds = processed_frames / FPS


with event_lock:

    final_events = state_manager.finalize(
        last_video_seconds
    )


for event in final_events:

    print_patty_event(event)


database.finish_session(
    cooking_session_id,
    status=(
        "FAILED"
        if inference_failed
        else "COMPLETED"
    ),
)

database.close()


# ============================================================
# RESULTS
# ============================================================

print()
print()
print(
    "============================================"
)
print(
    "          PROCESSING COMPLETE"
)
print(
    "============================================"
)

print(
    f"Frames processed:    "
    f"{processed_frames}"
)

print(
    f"Raw detections:      "
    f"{total_raw_detections}"
)

print(
    f"Stable detections:   "
    f"{total_stable_detections}"
)

print(
    f"Output:              "
    f"{OUTPUT_PATH}"
)

print(
    f"Database:            "
    f"{DATABASE_PATH}"
)

print(
    "============================================"
)
