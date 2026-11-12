"""Manual desktop control of the installed, loopback-only Colibri engine.

Two model profiles can be served:

* ``original``   - the original-Qwen int4 Colibri container (2026-10-08 trial; different weights from hayat:latest);
* ``foundation`` - HAYAT's own brain weights. Colibri v2.1.0 reads a GGUF in place, so the very file Ollama serves as
  hayat:latest is imported in link mode (``coli import --mode link``): nothing copied, nothing re-quantized. A
  comparison on this profile is a comparison of inference engines on identical weights.

Three engines: CPU, Vulkan and CUDA. The CUDA engine bounds the RAM expert cache; the 2026-10-08 CUDA start with 256
slots per layer crossed the RAM guard.

Nothing here downloads, converts or changes HAYAT's configured model roles. Starting refuses beside a large loaded
Ollama model and without the RAM the engine was measured to need; stopping touches only the process this service
started.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from typing import Any

import httpx

from app.config import PROJECT_ROOT

#: Installed engine versions, newest first; the newest complete one is used.
RUNTIMES = ('runtime/colibri-v2.1.0', 'runtime/colibri-v2.0.0')

PROFILES: dict[str, dict[str, Any]] = {
    'original': {
        'label': 'Original Qwen int4 container (different weights)',
        'model_dir': 'models/colibri/qwen36-35b-a3b-i4-gs64', 'marker': 'model-globals.safetensors',
        'model_id': 'qwen36-35b-a3b-colibri-i4-gs64', 'same_weights_as_brain': False, 'min_runtime': (2, 0, 0)},
    'foundation': {
        'label': "HAYAT's own brain weights (GGUF read in place)",
        'model_dir': 'models/colibri/hayat-foundation-q4km', 'marker': 'gguf.json',
        'model_id': 'hayat-foundation-q4km-colibri', 'same_weights_as_brain': True, 'min_runtime': (2, 1, 0)},
}

#: How each engine is started, and the available RAM it needs before startup (measured, plus margin).
ENGINES: dict[str, dict[str, Any]] = {
    'cpu': {'label': 'CPU', 'gpu': 'none', 'cap': 128, 'env': {'COLI_CUDA': '0', 'COLI_VULKAN': '0'},
            'min_ram_gib': 16},
    # 96 RAM slots per layer: HAYAT's own weights peaked at 9.3 GiB this way (2026-11-11); the 2026-10-08 trial of
    # the original container used 128 slots and needed 23 GiB free.
    'vulkan': {'label': 'Vulkan (GPU)', 'gpu': 'auto', 'cap': 96,
               'env': {'COLI_CUDA': '0', 'COLI_VULKAN': '1', 'COLI_VK_TIER_GB': '3'}, 'min_ram_gib': 18},
    # 5 GB of experts on the 8 GB card leaves room for the dense layers, KV and the CUDA context; 96 RAM slots per
    # layer keep the host cache near 7 GB instead of the 19 GB the automatic plan asks for.
    'cuda': {'label': 'CUDA (NVIDIA GPU)', 'gpu': '0', 'cap': 96, 'vram_gb': 5, 'env': {'COLI_VULKAN': '0'},
             'min_ram_gib': 16},
}

#: The brain's Ollama manifest, whose model layer is the GGUF the foundation profile links to.
BRAIN_MANIFEST = 'runtime/ollama-models/manifests/registry.ollama.ai/library/hayat/latest'


def _version(path: Path) -> tuple[int, ...]:
    try:
        text = (path / 'version.py').read_text(encoding='utf-8')
    except OSError:
        return ()
    for line in text.splitlines():
        if line.startswith('__version__'):
            return tuple(int(part) for part in line.split('=')[1].strip().strip('"\'').split('.') if part.isdigit())
    return ()


def describe_status(status: dict[str, Any]) -> str:
    """A status in plain words, with the engine's own counters when it reports them."""
    installed = status.get('profiles_installed') or {}
    if not status.get('ready'):
        if status.get('owned'):
            return 'Colibri is loading; this takes a minute or two. Check again shortly.'
        if 'exit_code' in status:
            return f'Colibri stopped with exit code {status["exit_code"]}; the log says why: {status.get("log", "")}'
        have = [PROFILES[name]['label'] for name, present in installed.items() if present]
        return ('Not running. Installed: ' + '; '.join(have) + '.') if have else 'Not running, and no model is installed.'
    profile = PROFILES.get(str(status.get('serving')), PROFILES['original'])
    engine = ENGINES.get(str(status.get('engine')), {}).get('label', 'an engine started outside HAYAT')
    text = f'Serving {profile["label"]} on {engine}.'
    telemetry = status.get('telemetry') or {}
    if telemetry.get('available'):
        parts = [f'{telemetry.get("completed_total") or 0} answered', f'{telemetry.get("failed_total") or 0} failed',
                 f'{telemetry.get("active") or 0} working', f'{telemetry.get("queued") or 0} waiting']
        text += ' Engine counters: ' + ', '.join(parts) + '.'
        if telemetry.get('mean_first_output_seconds') is not None:
            text += f' First words after {telemetry["mean_first_output_seconds"]:.1f} s on average.'
    else:
        text += ' The engine reported no counters.'
    return text


class ColibriService:
    MODEL = PROFILES['original']['model_id']
    PORT = 8000
    BASE = f'http://127.0.0.1:{PORT}/v1'

    def __init__(self, root=PROJECT_ROOT):
        self.root = Path(root)
        self.process = None
        self.profile = None
        self.engine = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- installation
    def runtime_dir(self, profile: str = 'original') -> Path:
        """The newest installed engine new enough for the profile (the newest of all when none is)."""
        wanted = PROFILES[profile]['min_runtime']
        installed = [self.root / rel for rel in RUNTIMES if (self.root / rel / 'coli').is_file()]
        for path in installed:
            if _version(path) >= wanted:
                return path
        return installed[0] if installed else self.root / RUNTIMES[0]

    def installed(self) -> dict[str, bool]:
        return {name: (self.root / spec['model_dir'] / spec['marker']).is_file() for name, spec in PROFILES.items()}

    def brain_blob(self) -> Path | None:
        """The GGUF Ollama serves as hayat:latest, read from its manifest."""
        import json

        try:
            manifest = json.loads((self.root / BRAIN_MANIFEST).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        for layer in manifest.get('layers', []):
            if layer.get('mediaType') == 'application/vnd.ollama.image.model':
                return self.root / 'runtime/ollama-models/blobs' / str(layer.get('digest', '')).replace(':', '-')
        return None

    def weights_check(self, profile: str) -> dict[str, Any]:
        """Whether the profile's weight file is the brain's own: the same file on disk, not a similar name."""
        import json

        spec = PROFILES[profile]
        result: dict[str, Any] = {'profile': profile, 'same_as_brain': False}
        if not spec['same_weights_as_brain']:
            result['detail'] = 'Different weights from hayat:latest (original Qwen); a model comparison.'
            return result
        try:
            pointer = json.loads((self.root / spec['model_dir'] / 'gguf.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            result['detail'] = 'Not imported yet: run scripts/setup_colibri_foundation.py.'
            return result
        gguf, blob = Path(str(pointer.get('gguf', ''))), self.brain_blob()
        result.update(gguf=str(gguf), arch=pointer.get('arch'), mode=pointer.get('mode'), brain_blob=str(blob or ''))
        try:
            same = blob is not None and os.path.samefile(gguf, blob)
        except OSError:
            same = False
        result['same_as_brain'] = same
        result['detail'] = ('The imported GGUF is the same file on disk as hayat:latest\'s model layer.' if same else
                            'The imported GGUF is not the file hayat:latest uses; treat results as a model comparison.')
        return result

    # ---------------------------------------------------------------- launch
    def command(self, profile: str = 'original', engine: str = 'cpu', port: int | None = None, *,
                ngen: int | None = None, temp: float | None = None) -> tuple[list[str], dict[str, str]]:
        """The exact engine command and environment for a profile and engine (shared with the benchmark)."""
        if profile not in PROFILES:
            raise ValueError('Choose the original or the foundation profile.')
        if engine not in ENGINES:
            raise ValueError('Choose CPU, Vulkan or CUDA.')
        spec, run = PROFILES[profile], ENGINES[engine]
        command = [sys.executable if self.root == PROJECT_ROOT else str(self.root / '.venv/Scripts/python.exe'),
                   str(self.runtime_dir(profile) / 'coli'), 'serve', '--model', str(self.root / spec['model_dir']),
                   '--model-id', spec['model_id'], '--host', '127.0.0.1', '--port', str(port or self.PORT),
                   '--ctx', '4096', '--gpu', run['gpu'], '--cap', str(run['cap']), '--no-think']
        if run.get('vram_gb'):
            command += ['--vram', str(run['vram_gb'])]
        if ngen is not None:
            command += ['--ngen', str(ngen)]
        if temp is not None:
            command += ['--temp', str(temp)]
        env = dict(os.environ, OMP_NUM_THREADS='8', MKL_NUM_THREADS='8', **run['env'])
        env['COLI_USAGE'] = str(self.root / f'data/cache/colibri-{profile}-usage.bin')
        return command, env

    def preflight(self, engine: str) -> None:
        """Refuse beside a large loaded Ollama model, or without the RAM the engine needs."""
        import psutil

        for port in (11434, 11435):
            try:
                response = httpx.get(f'http://127.0.0.1:{port}/api/ps', timeout=2, trust_env=False)
                models = response.json().get('models', [])
            except (httpx.HTTPError, ValueError):
                continue
            if any(int(model.get('size', 0)) > 4 * 1024**3 for model in models):
                raise RuntimeError('A large Ollama model is loaded (HAYAT\'s brain). Unload it before starting '
                                   'Colibri: both together do not fit in this computer\'s memory.')
        minimum = ENGINES[engine]['min_ram_gib']
        available = psutil.virtual_memory().available
        if available < minimum * 1024**3:
            raise RuntimeError(f'{ENGINES[engine]["label"]} needs at least {minimum} GiB of free memory before it '
                               f'starts; {available / 1024**3:.1f} GiB is free now.')

    def start(self, backend='cpu', profile='original'):
        if backend not in ENGINES:
            raise ValueError('Choose CPU, Vulkan or CUDA.')
        if profile not in PROFILES:
            raise ValueError('Choose the original or the foundation profile.')
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError('The owned Colibri process is already starting or running.')
            spec = PROFILES[profile]
            runtime = self.runtime_dir(profile)
            for required in (runtime / 'coli', self.root / spec['model_dir'] / spec['marker']):
                if not required.is_file():
                    raise FileNotFoundError('Colibri installation is incomplete: '
                                            + str(required.relative_to(self.root)).replace('\\', '/'))
            if _version(runtime) < spec['min_runtime']:
                raise FileNotFoundError('Colibri installation is incomplete: this profile needs engine '
                                        + '.'.join(map(str, spec['min_runtime'])) + ' or newer.')
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', self.PORT)) == 0:
                    raise RuntimeError(f'Port {self.PORT} is in use. Existing services are never replaced or stopped.')
            self.preflight(backend)
            command, env = self.command(profile, backend)
            log = self.root / 'data/logs/colibri-gui.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as output:
                self.process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                                stdout=output, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.profile, self.engine = profile, backend
        return {'detail': f'Colibri launch requested: {spec["label"]} on {ENGINES[backend]["label"]}. Loading takes '
                          'a minute or two; refresh status to check readiness, or read the local log if it exits.',
                'log': str(log)}

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

    # ---------------------------------------------------------------- observation
    def status(self):
        process = self.process
        owned = process is not None and process.poll() is None
        installed = self.installed()
        result = {'installed': installed['original'], 'profiles_installed': installed, 'owned': owned,
                  'ready': False, 'models': [], 'log': str(self.root / 'data/logs/colibri-gui.log')}
        if owned:
            result.update(profile=self.profile, engine=self.engine)
        try:
            response = httpx.get(self.BASE + '/models', timeout=2, trust_env=False)
            response.raise_for_status()
            result['models'] = [str(row['id']) for row in response.json().get('data', []) if row.get('id')]
            ids = {spec['model_id']: name for name, spec in PROFILES.items()}
            serving = [ids[model] for model in result['models'] if model in ids]
            result['ready'] = bool(serving)
            if serving:
                result['serving'] = serving[0]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            result['detail'] = type(error).__name__
        if result['ready']:
            result['telemetry'] = self.telemetry()
        if process is not None and process.poll() is not None:
            result['exit_code'] = process.returncode
        return result

    def telemetry(self) -> dict[str, Any]:
        """The engine's own scheduler counters from /metrics: real activity, never drawn when absent."""
        from app.models.colibri import parse_metrics, summarize_metrics

        try:
            response = httpx.get(self.BASE.removesuffix('/v1') + '/metrics', timeout=2, trust_env=False)
            response.raise_for_status()
            values = parse_metrics(response.text)
        except (httpx.HTTPError, AttributeError, TypeError) as error:
            return {'available': False, 'detail': type(error).__name__}
        if not values:
            return {'available': False, 'detail': 'no counters'}
        return {'available': True, **summarize_metrics(values)}

    def test(self, prompt):
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
            raise ValueError('Enter a test prompt of 1–2000 characters.')
        status = self.status()
        if not status['ready']:
            raise RuntimeError('The installed Colibri model is not ready. Start it and refresh status first.')
        from app.models.colibri import ColibriProvider
        from app.models.base import Message
        model = PROFILES[status.get('serving', 'original')]['model_id']
        provider = ColibriProvider(self.BASE, model, timeout=180)
        return provider.generate([Message('user', prompt.strip())], max_tokens=192)
