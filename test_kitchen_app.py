import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kitchen_app import KitchenApp, cloud_records, export_records, local_records, sync_config
from pattyops_core import PattyOpsDatabase
import test_cloud_api


class KitchenStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'records.db'
        database = PattyOpsDatabase(self.path)
        for index in range(105):
            database.start_session(f'camera:{index}', 'model.pt')
        database.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_local_browser_paginates_without_mutation(self):
        before = self.path.read_bytes()
        first = local_records(self.path, 'cooking_sessions')
        second = local_records(self.path, 'cooking_sessions', first[-1]['id'])
        self.assertEqual(len(first), 100)
        self.assertEqual(len(second), 5)
        self.assertTrue(set(r['id'] for r in first).isdisjoint(r['id'] for r in second))
        self.assertEqual(before, self.path.read_bytes())

    def test_local_browser_rejects_arbitrary_tables(self):
        with self.assertRaises(ValueError):
            local_records(self.path, 'cooking_sessions; DROP TABLE patties')

    def test_missing_database_does_not_create_file(self):
        path = Path(self.temp.name) / 'missing.db'
        self.assertEqual(local_records(path, 'patty_events'), [])
        self.assertFalse(path.exists())

    def test_export_neutralizes_spreadsheet_formulas(self):
        path = Path(self.temp.name) / 'export.csv'
        export_records(path, [{'source': '=1+1', 'id': 3}])
        with path.open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['source'], "'=1+1")

    def test_cloud_rejects_credentials_in_url_and_remote_http(self):
        settings = dict(database=str(self.path), cloud_url='https://user:secret@example.com', device_id='kitchen', token='test')
        with self.assertRaises(ValueError):
            sync_config(settings)
        settings['cloud_url'] = 'http://example.com'
        with self.assertRaises(ValueError):
            sync_config(settings).validate()

    def test_desktop_tabs_empty_records_and_setup_persistence(self):
        with patch.object(KitchenApp, 'begin_sync'):
            app = KitchenApp(Path(self.temp.name) / 'app')
            try:
                app.withdraw()
                app.update()
                self.assertEqual(len(app.tabs.tabs()), 5)
                app.local.loaded(local_records(self.path, 'cooking_sessions'))
                self.assertEqual(len(app.local.tree.get_children()), 100)
                app.local.loaded([])
                self.assertIn('No records', app.local.notice.get())
                model = Path(self.temp.name) / 'model.pt'
                model.touch()
                app.fields['model'].set(str(model))
                app.fields['database'].set(str(self.path))
                app.save()
                saved = json.loads((app.data / 'settings.json').read_text())
                self.assertEqual(saved['database'], str(self.path.resolve()))
            finally:
                app.destroy()


class CloudHistoryTests(unittest.TestCase):
    setUp = test_cloud_api.CloudApiTests.setUp
    tearDown = test_cloud_api.CloudApiTests.tearDown
    event_payload = test_cloud_api.CloudApiTests.event_payload
    def test_pagination_filter_and_device_isolation(self):
        for index in range(1, 5):
            payload = self.event_payload()
            event = payload['events'][0]
            event['idempotency_key'] = f'kitchen-01:{index}'
            event['edge_event_id'] = index
            event['event_type'] = 'FLIPPED' if index % 2 else 'DETECTED'
            self.assertEqual(self.client.post('/v1/events/batch', json=payload, headers=self.headers).status_code, 200)
        first = self.client.get('/v1/events?limit=2', headers=self.headers).json()
        older = self.client.get(f"/v1/events?limit=2&before_id={first[-1]['id']}", headers=self.headers).json()
        self.assertEqual([r['edge_event_id'] for r in first + older], [4, 3, 2, 1])
        filtered = self.client.get('/v1/events?event_type=FLIPPED', headers=self.headers).json()
        self.assertEqual([r['edge_event_id'] for r in filtered], [3, 1])
        self.assertEqual(self.client.get('/v1/events?before_id=0', headers=self.headers).status_code, 422)
        wrong = dict(self.headers, **{'X-Device-ID': 'other-kitchen'})
        self.assertEqual(self.client.get('/v1/events?limit=2', headers=wrong).status_code, 401)


if __name__ == '__main__':
    unittest.main()
