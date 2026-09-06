import tempfile
import unittest
from pathlib import Path

from edge_sync import EdgeSynchronizer, SyncConfig
from pattyops_core import PattyOpsDatabase


class RecordingTransport:
    def __init__(self, fail_events=False):
        self.calls = []
        self.fail_events = fail_events

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self.fail_events and path == "/v1/events/batch":
            raise RuntimeError("network unavailable")
        if path == "/v1/events/batch":
            return {
                "accepted": len(payload["events"]),
                "duplicates": 0,
                "acknowledged": len(payload["events"]),
            }
        return {"status": "ok"}


class EdgeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "pattyops.db"
        with PattyOpsDatabase(self.database_path) as database:
            session_id = database.start_session("webcam:0", "best.pt")
            database.create_patty(
                session_id=session_id,
                track_id=7,
                state="RAW",
                confidence=0.92,
                video_seconds=1.5,
                metadata={"detector_class": "Raw_Patty"},
            )

        self.config = SyncConfig(
            database_path=self.database_path,
            cloud_url="http://localhost:8000",
            device_id="kitchen-01",
            device_token="test-token-at-least-16-characters",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_uploads_event_and_advances_checkpoint(self):
        transport = RecordingTransport()
        synchronizer = EdgeSynchronizer(self.config, transport)

        self.assertEqual(synchronizer.sync_once(), 1)
        self.assertEqual(synchronizer.sync_once(), 0)

        batch_calls = [call for call in transport.calls if call[0] == "/v1/events/batch"]
        self.assertEqual(len(batch_calls), 1)
        event = batch_calls[0][1]["events"][0]
        key_parts = event["idempotency_key"].split(":")
        self.assertEqual(key_parts[0], "kitchen-01")
        self.assertEqual(key_parts[2], "1")
        self.assertEqual(event["event_type"], "DETECTED")

    def test_failed_upload_does_not_advance_checkpoint(self):
        failing_transport = RecordingTransport(fail_events=True)
        synchronizer = EdgeSynchronizer(self.config, failing_transport)

        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            synchronizer.sync_once()

        working_transport = RecordingTransport()
        retry = EdgeSynchronizer(self.config, working_transport)
        self.assertEqual(retry.sync_once(), 1)

    def test_non_https_public_endpoint_is_rejected(self):
        invalid_config = SyncConfig(
            database_path=self.database_path,
            cloud_url="http://example.com",
            device_id="kitchen-01",
            device_token="test-token-at-least-16-characters",
        )
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            EdgeSynchronizer(invalid_config, RecordingTransport())


if __name__ == "__main__":
    unittest.main()
