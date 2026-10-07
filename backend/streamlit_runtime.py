"""Explicit single-process demo runtime; no claim of Docker/OS isolation."""
import json
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from .api import create_app
from .common import digest
from .config import Settings
from .engine import UnsafePlan, execute
from .ingest import IngestError, ingest
from .sandbox import SandboxRejected
from .store import Store
from .worker import Worker


class TrustedLibraryRuntime:
    def ready(self):
        return True

    def run(self, root, request):
        start = time.monotonic()
        cpu = time.process_time()
        try:
            if request['mode'] == 'ingest':
                result = ingest(root, request['dataset'], request['limits'])
            elif request['mode'] == 'execute':
                for source in request['dataset']['files']:
                    if digest((root / source['id']).read_bytes()) != source['sha256']:
                        raise UnsafePlan('Source file changed; upload it again.')
                data = (root / 'tables.json').read_bytes()
                if digest(data) != request['tables_hash']:
                    raise UnsafePlan('Normalized data changed; upload it again.')
                result = execute(request['query'], request['profile'], json.loads(data))
            else:
                raise UnsafePlan('Unsupported runtime operation.')
        except (IngestError, UnsafePlan) as exc:
            raise SandboxRejected(str(exc)) from exc
        result['resources'] = {'duration_ms': int((time.monotonic()-start)*1000),
                               'cpu_time_ms': int((time.process_time()-cpu)*1000),
                               'peak_memory_bytes': 0}
        result['execution_mode'] = 'trusted_library'
        return result


class EmbeddedBackend:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='surecount-streamlit-', ignore_cleanup_errors=True)
        root = Path(self.directory.name)
        self.settings = Settings(data_dir=root, database_url='sqlite:///'+(root/'state.db').as_posix(),
                                 planner='deterministic', max_files=5, max_file_bytes=10*1024**2,
                                 max_dataset_bytes=25*1024**2, max_rows=50000, max_columns=100, max_tables=10)
        self.store = Store(self.settings.database_url)
        self.worker = Worker(self.settings, self.store, sandbox=TrustedLibraryRuntime())
        self.worker.checks = {'sandbox':'ready','model':'ready'}
        self.client = TestClient(create_app(self.settings, self.store))
        self.lock = threading.RLock()

    def request(self, method, path, **kwargs):
        with self.lock:
            self.worker.heartbeat()
            self.worker.cleanup()
            response = self.client.request(method, '/api/v1'+path, **kwargs)
            # Use the unchanged durable queue and verification pipeline. Fixed library
            # calculations run synchronously; never execute client/generated Python.
            if response.is_success:
                self.worker.run_once()
            return response

    def close(self):
        self.client.close()
        self.store.engine.dispose()
        self.directory.cleanup()
