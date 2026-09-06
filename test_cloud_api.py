import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cloud_api import create_app


class CloudApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "cloud.db"
        self.app = create_app(
            database_url=f"sqlite:///{database_path.as_posix()}",
            device_tokens={"kitchen-01": "test-token-at-least-16-characters"},
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        self.headers = {
            "Authorization": "Bearer test-token-at-least-16-characters",
            "X-Device-ID": "kitchen-01",
        }

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def event_payload(self):
        return {
            "events": [
                {
                    "idempotency_key": "kitchen-01:1",
                    "edge_event_id": 1,
                    "session_id": 3,
                    "track_id": 7,
                    "event_type": "FLIPPED",
                    "occurred_at": "2026-09-02T14:32:10.123+05:00",
                    "video_seconds": 4.5,
                    "previous_state": "COOKED",
                    "new_state": "RAW",
                    "confidence": 0.91,
                    "metadata": {"reason": "stable_transition"},
                    "source": "webcam:0",
                    "model_path": "best.pt",
                }
            ]
        }

    def test_health_endpoint_does_not_require_device_credentials(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_event_ingestion_is_idempotent(self):
        first = self.client.post(
            "/v1/events/batch", json=self.event_payload(), headers=self.headers
        )
        second = self.client.post(
            "/v1/events/batch", json=self.event_payload(), headers=self.headers
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["accepted"], 1)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["duplicates"], 1)

        listing = self.client.get("/v1/events", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)
        self.assertEqual(listing.json()[0]["event_type"], "FLIPPED")

    def test_invalid_token_is_rejected(self):
        invalid_headers = dict(self.headers)
        invalid_headers["Authorization"] = "Bearer incorrect-token"
        response = self.client.post(
            "/v1/events/batch", json=self.event_payload(), headers=invalid_headers
        )
        self.assertEqual(response.status_code, 401)

    def test_device_cannot_submit_another_devices_key(self):
        payload = self.event_payload()
        payload["events"][0]["idempotency_key"] = "other-kitchen:1"
        response = self.client.post(
            "/v1/events/batch", json=payload, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
