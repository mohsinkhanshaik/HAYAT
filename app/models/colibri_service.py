"""Manual desktop control of the installed, loopback-only Colibri profile."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import threading

import httpx

from app.config import PROJECT_ROOT


class ColibriService:
    MODEL = 'qwen36-35b-a3b-colibri-i4-gs64'
    BASE = 'http://127.0.0.1:8000/v1'

    def __init__(self, root=PROJECT_ROOT):
        self.root = Path(root)
        self.process = None
        self._lock = threading.Lock()

    def status(self):
        process = self.process
        owned = process is not None and process.poll() is None
        installed = (self.root / 'models/colibri/qwen36-35b-a3b-i4-gs64/model-globals.safetensors').is_file()
        result = {'installed': installed, 'owned': owned, 'ready': False, 'models': [],
                  'log': str(self.root / 'data/logs/colibri-gui.log')}
        try:
            response = httpx.get(self.BASE + '/models', timeout=2, trust_env=False)
            response.raise_for_status()
            result['models'] = [str(row['id']) for row in response.json().get('data', []) if row.get('id')]
            result['ready'] = self.MODEL in result['models']
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            result['detail'] = type(error).__name__
        if process is not None and process.poll() is not None:
            result['exit_code'] = process.returncode
        return result

    def start(self, backend='cpu'):
        if backend not in {'cpu', 'vulkan'}:
            raise ValueError('Choose CPU or Vulkan.')
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError('The owned Colibri process is already starting or running.')
            for required in ('.venv/Scripts/python.exe', 'scripts/start_colibri.ps1', 'runtime/colibri-v2.0.0/coli',
                             'models/colibri/qwen36-35b-a3b-i4-gs64/model-globals.safetensors'):
                if not (self.root / required).is_file():
                    raise FileNotFoundError('Colibri installation is incomplete: ' + required)
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', 8000)) == 0:
                    raise RuntimeError('Port 8000 is in use. Existing services are never replaced or stopped.')
            # The checked-in launcher enforces available RAM and loaded-model
            # constraints before launching the native engine. Never download here.
            log = self.root / 'data/logs/colibri-gui.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as output:
                self.process = subprocess.Popen(
                    ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(self.root / 'scripts/start_colibri.ps1'),
                     '-Backend', backend, '-Port', '8000'], cwd=self.root, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return {'detail': 'Colibri launch requested. Refresh status to check readiness; inspect the local log if it exits.', 'log': str(log)}

    def stop(self):
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                return {'detail': 'No GUI-owned Colibri process is running. External services were left alone.'}
            import psutil
            try:
                parent = psutil.Process(self.process.pid)
                children = parent.children(recursive=True)
                # Stop the launcher first so it cannot spawn another child.
                parent.suspend()
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                parent.terminate()
                _, alive = psutil.wait_procs([*children, parent], timeout=3)
                for process in alive:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
            self.process.wait(timeout=5)
        return {'detail': 'GUI-owned Colibri stopped.'}

    def test(self, prompt):
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
            raise ValueError('Enter a test prompt of 1–2000 characters.')
        if not self.status()['ready']:
            raise RuntimeError('The installed Colibri model is not ready. Start it and refresh status first.')
        from app.models.colibri import ColibriProvider
        from app.models.base import Message
        provider = ColibriProvider(self.BASE, self.MODEL, timeout=180)
        return provider.generate([Message('user', prompt.strip())], max_tokens=192)
