"""Provision and verify the local FakeCloud test environment (never real AWS)."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.fakecloud'
API = 'http://localhost:8000'
DEVICE = 'kitchen-test-01'
SMOKE = 'fakecloud-smoke-test'


def credentials():
    STATE.mkdir(exist_ok=True)
    path = STATE / 'credentials.json'
    if not path.exists():
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'password': secrets.token_hex(24), 'token': secrets.token_hex(32)}, stream)
    return json.loads(path.read_text(encoding='utf-8'))


def rds():
    return boto3.client('rds', endpoint_url='http://localhost:4566', region_name='us-east-1',
                        aws_access_key_id='test', aws_secret_access_key='test',
                        config=Config(connect_timeout=5, read_timeout=300, retries={'max_attempts': 1}))


def instance():
    return rds().describe_db_instances(DBInstanceIdentifier='pattyops-test')['DBInstances'][0]


def database_url():
    endpoint = instance()['Endpoint']
    return f"postgresql+psycopg://pattyops:{credentials()['password']}@{endpoint['Address']}:{endpoint['Port']}/pattyops"


def request(path, token=None):
    headers = {'Authorization': 'Bearer ' + (token or credentials()['token']), 'X-Device-ID': SMOKE}
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=headers), timeout=10) as response:
        return json.load(response)


def setup():
    client = rds()
    private = credentials()
    try:
        instance()
    except client.exceptions.DBInstanceNotFoundFault:
        print('Creating FakeCloud PostgreSQL; the first image build may take several minutes.', flush=True)
        client.create_db_instance(DBInstanceIdentifier='pattyops-test', Engine='postgres', EngineVersion='16',
                                  DBInstanceClass='db.t3.micro', AllocatedStorage=20, DBName='pattyops',
                                  MasterUsername='pattyops', MasterUserPassword=private['password'])
    for _ in range(180):
        current = instance()
        if current['DBInstanceStatus'] == 'available' and current.get('Endpoint'):
            break
        time.sleep(5)
    else:
        raise RuntimeError('FakeCloud database did not become available within 15 minutes')
    from sqlalchemy import create_engine, text
    with create_engine(database_url()).connect() as connection:
        assert connection.execute(text('SELECT 1')).scalar() == 1
    # Back up the existing desktop setup before selecting the isolated test database.
    data = Path(os.environ['LOCALAPPDATA']) / 'PattyOps'
    data.mkdir(exist_ok=True)
    path = data / 'settings.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    backup = STATE / 'kitchen-settings-before-fakecloud.json'
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    saved.update(cloud_url=API, device_id=DEVICE, token=private['token'], database=str(data / 'fakecloud-test.db'))
    if not saved.get('model'):
        saved['model'] = str(ROOT / 'models' / 'best.pt')
    saved.setdefault('source', '0')
    from pattyops_core import PattyOpsDatabase
    with PattyOpsDatabase(Path(saved['database'])):
        pass
    path.write_text(json.dumps(saved, indent=2), encoding='utf-8')
    print('Database connection verified. Kitchen settings saved; previous settings backed up if present.')


def serve():
    import uvicorn
    from cloud_api import create_app
    token = credentials()['token']
    app = create_app(database_url=database_url(), device_tokens={DEVICE: token, SMOKE: token})
    uvicorn.run(app, host='127.0.0.1', port=8000)


def verify():
    from edge_sync import EdgeSynchronizer, SyncConfig
    from pattyops_core import PattyOpsDatabase
    before = len(request('/v1/events?limit=1000'))
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / 'smoke.db'
        with PattyOpsDatabase(db) as records:
            session = records.start_session('fakecloud-integration-test', 'synthetic-test-no-camera')
            records.create_patty(session_id=session, track_id=11, state='COOKED', confidence=0.94, video_seconds=2.0)
        sync = EdgeSynchronizer(SyncConfig(db, API, SMOKE, credentials()['token']))
        # A failed upload must leave the event available for a later successful retry.
        def offline(path, payload):
            raise RuntimeError('Simulated network outage')
        failing = EdgeSynchronizer(sync.config, offline)
        try:
            failing.sync_once()
        except RuntimeError:
            pass
        else:
            raise AssertionError('Offline simulation unexpectedly succeeded')
        assert sync.sync_once() == 1
        assert sync.sync_once() == 0
        # Force a replay to exercise server deduplication as well as the edge checkpoint.
        with closing(sqlite3.connect(db)) as connection:
            with connection:
                connection.execute('UPDATE cloud_sync_state SET last_event_id=0 WHERE device_id=?', (SMOKE,))
        assert sync.sync_once() == 1
    after = len(request('/v1/events?limit=1000'))
    assert after == before + 1, (before, after)
    try:
        request('/v1/events', 'invalid-token')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError('Invalid credentials accepted')
    print('PASS: real PostgreSQL event sync, offline recovery, replay deduplication, and invalid-token rejection.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'serve', 'verify'])
    globals()[parser.parse_args().action]()
