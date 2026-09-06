import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cloud_api import create_app
from edge_sync import EdgeSynchronizer, SyncConfig
from pattyops_core import PattyOpsDatabase


class ProductionFlowTests(unittest.TestCase):
    def test_edge_event_reaches_cloud_and_retry_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edge_database = root / "edge.db"
            cloud_database = root / "cloud.db"

            with PattyOpsDatabase(edge_database) as database:
                session_id = database.start_session("webcam:0", "best.pt")
                database.create_patty(
                    session_id=session_id,
                    track_id=11,
                    state="COOKED",
                    confidence=0.94,
                    video_seconds=2.0,
                )

            token = "integration-token-at-least-16-characters"
            app = create_app(
                database_url=f"sqlite:///{cloud_database.as_posix()}",
                device_tokens={"kitchen-01": token},
            )

            with TestClient(app) as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "X-Device-ID": "kitchen-01",
                }

                def transport(path, payload):
                    response = client.post(path, json=payload, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()

                synchronizer = EdgeSynchronizer(
                    SyncConfig(
                        database_path=edge_database,
                        cloud_url="http://localhost:8000",
                        device_id="kitchen-01",
                        device_token=token,
                    ),
                    transport,
                )

                self.assertEqual(synchronizer.sync_once(), 1)
                self.assertEqual(synchronizer.sync_once(), 0)

                response = client.get("/v1/events", headers=headers)
                self.assertEqual(response.status_code, 200)
                events = response.json()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["track_id"], 11)
                self.assertEqual(events[0]["event_type"], "DETECTED")


if __name__ == "__main__":
    unittest.main()
