import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pattyops_core import Observation, PattyOpsDatabase, PattyStateManager, normalize_patty_state
from pattyops import interactive_arguments, parse_source


class PattyOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "pattyops.db"
        self.database = PattyOpsDatabase(self.db_path)
        self.session_id = self.database.start_session("test.mp4", "best.pt")
        self.manager = PattyStateManager(
            database=self.database,
            session_id=self.session_id,
            history_size=4,
            min_stable_observations=3,
            stable_ratio=0.75,
            removal_grace_frames=3,
        )

    def tearDown(self):
        self.database.close()
        self.temp_dir.cleanup()

    def feed(self, start_frame, count, class_name, track_id=7, confidence=0.9):
        emitted = []
        for offset in range(count):
            frame = start_frame + offset
            emitted.extend(
                self.manager.update(
                    frame,
                    [Observation(track_id, class_name, confidence, frame / 30)],
                    frame / 30,
                )
            )
        return emitted

    def test_dataset_class_mapping(self):
        self.assertEqual(normalize_patty_state("Raw_Patty"), "RAW")
        self.assertEqual(normalize_patty_state("CookedPatty"), "COOKED")
        self.assertEqual(normalize_patty_state("CookedPatty_w-cheese"), "COOKED")
        self.assertIsNone(normalize_patty_state("SaltPepper"))

    def test_updated_dataset_label_can_be_mapped_without_code_change(self):
        manager = PattyStateManager(
            database=self.database,
            session_id=self.session_id,
            history_size=2,
            min_stable_observations=2,
            stable_ratio=1.0,
            removal_grace_frames=3,
            class_map={"Patty_Ready_v2": "COOKED"},
        )
        emitted = []
        for frame in range(2):
            emitted.extend(
                manager.update(
                    frame,
                    [Observation(21, "patty-ready-v2", 0.93, frame / 30)],
                    frame / 30,
                )
            )
        self.assertEqual(emitted[0].new_state, "COOKED")

    def test_emitted_timestamp_matches_persisted_event_timestamp(self):
        emitted = self.feed(0, 4, "Raw_Patty")
        row = self.database.list_events(limit=1)[0]
        self.assertEqual(emitted[0].occurred_at, row["occurred_at"])
        self.assertRegex(row["occurred_at"], r"[+-]\d\d:\d\d$")

    def test_repeated_frames_create_only_one_detected_event(self):
        emitted = self.feed(0, 10, "Raw_Patty")
        self.assertEqual([event.event_type for event in emitted], ["DETECTED"])
        rows = self.database.list_events()
        self.assertEqual([row["event_type"] for row in rows], ["DETECTED"])

    def test_cooked_to_raw_is_logged_as_flip(self):
        self.feed(0, 4, "CookedPatty_w-cheese")
        emitted = self.feed(4, 4, "Raw_Patty")
        self.assertEqual(emitted[-1].event_type, "FLIPPED")
        self.assertEqual(emitted[-1].previous_state, "COOKED")
        self.assertEqual(emitted[-1].new_state, "RAW")
        patty = self.database.connection.execute(
            "SELECT flip_count, current_state FROM patties WHERE track_id = 7"
        ).fetchone()
        self.assertEqual(dict(patty), {"flip_count": 1, "current_state": "RAW"})

    def test_raw_to_cooked_is_state_change_not_flip(self):
        self.feed(0, 4, "Raw_Patty")
        emitted = self.feed(4, 4, "CookedPatty")
        self.assertEqual(emitted[-1].event_type, "STATE_CHANGED")

    def test_saltpepper_does_not_create_a_patty(self):
        self.feed(0, 8, "SaltPepper")
        count = self.database.connection.execute("SELECT COUNT(*) FROM patties").fetchone()[0]
        self.assertEqual(count, 0)

    def test_missing_track_is_removed_only_once(self):
        self.feed(0, 4, "Raw_Patty")
        emitted = []
        for frame in range(4, 10):
            emitted.extend(self.manager.update(frame, [], frame / 30))
        self.assertEqual([event.event_type for event in emitted], ["REMOVED"])
        removed_count = self.database.connection.execute(
            "SELECT COUNT(*) FROM patty_events WHERE event_type = 'REMOVED'"
        ).fetchone()[0]
        self.assertEqual(removed_count, 1)

    def test_worker_thread_can_write_events(self):
        failures = []

        def write_from_callback_thread():
            try:
                for frame in range(3):
                    self.manager.update(
                        frame,
                        [Observation(12, "Raw_Patty", 0.95, frame / 30)],
                        frame / 30,
                    )
            except Exception as error:  # pragma: no cover - assertion aid
                failures.append(error)

        callback_thread = threading.Thread(target=write_from_callback_thread)
        callback_thread.start()
        callback_thread.join()

        self.assertEqual(failures, [])
        self.assertEqual(len(self.database.list_events()), 1)

    def test_interactive_webcam_setup_builds_live_run_arguments(self):
        answers = iter(["2", "0", "latest-best.pt", "Database/live.db", "n", ""])
        with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            arguments = interactive_arguments()
        self.assertEqual(parse_source("camera:0"), 0)
        self.assertIn("--show", arguments)
        self.assertEqual(arguments[arguments.index("--source") + 1], "camera:0")
        self.assertEqual(arguments[arguments.index("--model") + 1], "latest-best.pt")


if __name__ == "__main__":
    unittest.main()
