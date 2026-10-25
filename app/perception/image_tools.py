"""Explicit local-image inspection and comparison in resource-bounded workers.

ExifRead parses untrusted metadata only in a separate process. This boundary
limits resource use; it is not a general sandbox for arbitrary executable code.
Nothing scans folders, opens a camera, writes an image or performs identity work.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 24_000_000
WORKER_TIMEOUT = 20.0
MAX_INPUT_BYTES = 8192
MAX_OUTPUT_BYTES = 128 * 1024
MAX_ERROR_BYTES = 8192
WINDOWS_MEMORY_BYTES = 768 * 1024 * 1024
POSIX_ADDRESS_BYTES = 1536 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(2)
_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = [('Pillow', 'PIL'), ('opencv-python-headless', 'cv2'), ('ImageHash', 'imagehash'), ('ExifRead', 'exifread'), ('PyWavelets', 'pywt')]


def _guard(check):
    if check is not None and check() is False:
        raise PermissionError('Local image operation was cancelled.')


def _local_path(path):
    if not isinstance(path, (str, Path)):
        raise ValueError('Choose an explicit local image file.')
    text = str(path)
    if (not text or len(text) > 2000 or text.startswith(('\\\\', '//'))
            or re.match(r'(?i)^(?:[a-z][a-z0-9+.-]*):(?:/{2}|(?:data|file):)', text)
            or re.match(r'(?i)^(?:https?|file|data|ftp|smb):', text)
            or any(ord(char) < 32 for char in text)):
        raise ValueError('URLs, network shares and control characters are not supported; choose a local image file.')
    resolved = Path(text).expanduser().resolve(strict=True)
    if str(resolved).startswith(('\\\\', '//')) or not resolved.is_file():
        raise ValueError('Choose a regular local image file.')
    if os.name == 'nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)) == 4:
            raise ValueError('Mapped network drives are not supported for local image tools.')
    size = resolved.stat().st_size
    if not 1 <= size <= MAX_FILE_BYTES:
        raise ValueError('Each local image must contain between 1 byte and 25 MiB.')
    return resolved


class _MemoryJob:
    """Bound child memory on Windows and kill descendants when the handle closes."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes
        size_t = ctypes.c_size_t
        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong),
                        ('Flags', wintypes.DWORD), ('MinimumWorkingSet', size_t), ('MaximumWorkingSet', size_t),
                        ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', size_t),
                        ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('Io', Counters), ('ProcessMemoryLimit', size_t), ('JobMemoryLimit', size_t),
                        ('PeakProcessMemoryUsed', size_t), ('PeakJobMemoryUsed', size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError('Could not create the bounded image worker job.')
        limits = Extended()
        limits.Basic.Flags = 0x00002000 | 0x00000100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
        limits.ProcessMemoryLimit = WINDOWS_MEMORY_BYTES
        if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
            kernel.CloseHandle(handle)
            raise OSError('Could not apply the image worker memory boundary.')
        self.handle, self.kernel = handle, kernel

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _worker_environment():
    from app.safety.sandbox import ProcessSandbox
    environment = ProcessSandbox().environment()
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        environment[key] = '1'
    return environment


def _stop(process):
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _run_worker(operation, paths, check):
    _guard(check)
    checked = [str(_local_path(path)) for path in paths]
    payload = json.dumps({'operation': operation, 'paths': checked}, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_INPUT_BYTES:
        raise ValueError('Local image worker input exceeds its bounded request size.')
    acquired = False
    while not acquired:
        _guard(check)
        acquired = _SLOTS.acquire(timeout=.05)
    process, job, readers = None, None, []
    try:
        _guard(check)
        options = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                   'env': _worker_environment(), 'close_fds': True}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve()), '--worker'], **options)
        job = _MemoryJob(process)
        overflow, buffers = threading.Event(), {'output': bytearray(), 'error': bytearray()}
        def drain(stream, key, maximum):
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                if len(buffers[key]) + len(chunk) > maximum:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                buffers[key].extend(chunk)
        for stream, key, maximum in ((process.stdout, 'output', MAX_OUTPUT_BYTES), (process.stderr, 'error', MAX_ERROR_BYTES)):
            reader = threading.Thread(target=drain, args=(stream, key, maximum), daemon=True, name='hayat-image-worker-output')
            readers.append(reader)
            reader.start()
        process.stdin.write(payload)
        process.stdin.close()
        started = time.monotonic()
        while process.poll() is None:
            _guard(check)
            if overflow.is_set():
                raise RuntimeError('Local image worker exceeded its output boundary.')
            if time.monotonic() - started > WORKER_TIMEOUT:
                raise TimeoutError('Local image analysis exceeded its 20-second worker time budget.')
            time.sleep(.025)
        for reader in readers:
            reader.join(timeout=1)
        _guard(check)
        if overflow.is_set():
            raise RuntimeError('Local image worker exceeded its output boundary.')
        try:
            envelope = json.loads(bytes(buffers['output']))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('Local image worker returned no valid bounded result; input may exceed parser or memory limits.') from exc
        if process.returncode != 0 or envelope.get('ok') is not True:
            failure = envelope.get('error', 'worker_failed')
            if failure == 'missing_dependency':
                raise RuntimeError('Local image dependencies are missing; run scripts/setup_image_tools.py --install.')
            raise ValueError('Local image worker rejected an unsupported, malformed or over-limit image.')
        result = envelope.get('result')
        if (not isinstance(result, dict) or result.get('operation') != operation
                or result.get('local_only') is not True or result.get('identity_inference') is not False):
            raise RuntimeError('Local image worker returned an invalid result contract.')
        _guard(check)
        return {**result, 'trust': 'untrusted_external_data', 'worker_limits': {'timeout_seconds': WORKER_TIMEOUT, 'max_file_bytes': MAX_FILE_BYTES,
                                          'max_pixels': MAX_PIXELS, 'max_output_bytes': MAX_OUTPUT_BYTES,
                                          'memory_bytes': WINDOWS_MEMORY_BYTES if os.name == 'nt' else POSIX_ADDRESS_BYTES,
                                          'memory_scope': 'process_commit' if os.name == 'nt' else 'virtual_address_space'}}
    finally:
        if process is not None:
            _stop(process)
        if job is not None:
            job.close()
        for reader in readers:
            reader.join(timeout=1)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        _SLOTS.release()


def inspect_image(path, *, check=None):
    return _run_worker('inspect', [path], check)


def compare_images(first, second, *, check=None):
    return _run_worker('compare', [first, second], check)


def toolkit_status():
    packages = []
    for distribution, module in _PACKAGES:
        choices = [distribution, 'opencv-python', 'opencv-contrib-python', 'opencv-contrib-python-headless'] if module == 'cv2' else [distribution]
        installed = None
        for choice in choices:
            try:
                installed = importlib.metadata.distribution(choice)
                break
            except importlib.metadata.PackageNotFoundError:
                continue
        spec = importlib.util.find_spec(module)
        license_text = ''
        if installed:
            license_text = installed.metadata.get('License-Expression') or installed.metadata.get('License') or ''
            if not license_text:
                license_text = '; '.join(item for item in installed.metadata.get_all('Classifier', []) if item.startswith('License ::'))
        packages.append({'name': distribution if installed is None else installed.metadata.get('Name', distribution),
                         'version': installed.version if installed else '', 'module': module,
                         'module_path': str(spec.origin or '') if spec else '', 'available': bool(installed and spec),
                         'inside_project': bool(spec and spec.origin and Path(spec.origin).resolve().is_relative_to(_ROOT)),
                         'license': license_text[:500] or 'Not reported in installed package metadata.'})
    return {'local_image_toolkit': True, 'available': all(row['available'] for row in packages), 'packages': packages,
            'project_root': str(_ROOT), 'python': sys.executable, 'local_only': True, 'identity_inference': False,
            'limits': {'file_bytes': MAX_FILE_BYTES, 'pixels': MAX_PIXELS, 'worker_timeout_seconds': WORKER_TIMEOUT,
                       'concurrent_workers': 2, 'parser_isolation': 'Separate process with memory/time/input/output bounds; not a general code sandbox.'}}


def _decode(path):
    from PIL import Image, ImageOps
    import warnings
    target = _local_path(path)
    with target.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Image grew beyond the file limit.')
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_PIXELS or image.width < 1 or image.height < 1:
            raise ValueError('Image dimensions exceed the pixel limit.')
        metadata = {'path': str(target), 'file_bytes': len(data), 'file_sha256': hashlib.sha256(data).hexdigest(),
                    'format': image.format or 'unknown', 'width': image.width, 'height': image.height,
                    'orientation': image.getexif().get(274, 1), 'frames': getattr(image, 'n_frames', 1)}
        if type(metadata['orientation']) is not int or metadata['orientation'] not in range(1, 9):
            metadata['orientation'] = None
        image.load()
        transposed = ImageOps.exif_transpose(image).convert('RGBA')
        image.close()
    metadata.update(display_width=transposed.width, display_height=transposed.height)
    return data, transposed, metadata


def _inspect(path):
    import cv2
    import numpy as np
    import exifread
    cv2.setNumThreads(1)
    data, image, result = _decode(path)
    technical, metadata_error = {}, ''
    try:
        tags = exifread.process_file(io.BytesIO(data), details=False, extract_thumbnail=False, strict=True)
        for key in ('Image Make', 'Image Model', 'Image Orientation', 'Image Software'):
            if key in tags:
                value = ' '.join(str(tags[key]).split())[:200]
                # Do not surface contact-shaped values even inside technical tags.
                if not re.search(r'@|(?i:serial|owner|artist|copyright|gps|latitude|longitude)|(?:\+?\d[ ().-]*){8,}', value):
                    technical[key] = value
    except Exception:
        metadata_error = 'Technical EXIF could not be parsed; no unrestricted tags were returned.'
    grayscale = image.convert('L')
    grayscale.thumbnail((1024, 1024))
    pixels = np.asarray(grayscale)
    result.update(operation='inspect', local_image_inspection=True, technical_metadata=technical, metadata_error=metadata_error,
                  quality={'mean_luminance': round(float(pixels.mean()), 3),
                           'clipped_dark_fraction': round(float(np.count_nonzero(pixels <= 5) / pixels.size), 6),
                           'clipped_light_fraction': round(float(np.count_nonzero(pixels >= 250) / pixels.size), 6),
                           'laplacian_variance': round(float(cv2.Laplacian(pixels, cv2.CV_64F).var()), 3),
                           'analysis_width': grayscale.width, 'analysis_height': grayscale.height,
                           'basis': 'Grayscale thumbnail heuristics only; no identity, authenticity, suitability or quality guarantee.'},
                  local_only=True, identity_inference=False,
                  limitations=['Only the first frame is analysed; orientation is applied before decoded-pixel analysis.',
                               'Technical metadata is file-reported and may be missing or incorrect; GPS and personal metadata are omitted.',
                               'Luminance/clipping/blur metrics are image heuristics, not identity eligibility or manipulation detection.'])
    image.close()
    return result


def _compare(first, second):
    import imagehash
    from PIL import ImageChops
    first_data, first_image, first_meta = _decode(first)
    second_data, second_image, second_meta = _decode(second)
    pixel_equal = False
    if first_image.size == second_image.size:
        difference = ImageChops.difference(first_image, second_image)
        pixel_equal = all(channel.getbbox() is None for channel in difference.split())
        difference.close()
    phash = int(imagehash.phash(first_image, hash_size=8) - imagehash.phash(second_image, hash_size=8))
    dhash = int(imagehash.dhash(first_image, hash_size=8) - imagehash.dhash(second_image, hash_size=8))
    first_image.close()
    second_image.close()
    return {'operation': 'compare', 'local_image_comparison': True, 'first': first_meta, 'second': second_meta,
            'byte_equal': first_data == second_data, 'pixel_equal': pixel_equal, 'phash_distance': phash,
            'dhash_distance': dhash, 'hash_bits': 64, 'local_only': True, 'identity_inference': False,
            'limitations': ['Decoded-pixel equality compares orientation-normalized RGBA first frames.',
                           'pHash/dHash measure whole-image resemblance; lower Hamming distance is not facial identity or a probability.',
                           'Hashes can collide and overlook color/transparency changes. They do not establish authenticity or ownership.']}


def format_image_tool_report(value):
    if value.get('local_image_toolkit'):
        lines = ['Local image tools: ' + ('available' if value['available'] else 'setup required'), 'Python: ' + value['python']]
        for package in value['packages']:
            lines += [f"{package['name']}: {package['version'] or 'not installed'} | {package['license']}", package['module_path']]
        return '\n'.join(lines)
    if value.get('local_image_inspection'):
        quality = value['quality']
        lines = [value['path'], f"{value['format']} | {value['width']} × {value['height']} pixels | orientation {value['orientation']}",
                 f"Displayed: {value['display_width']} × {value['display_height']} | SHA-256: {value['file_sha256']}",
                 f"Mean luminance: {quality['mean_luminance']}/255; dark/light clipping: {quality['clipped_dark_fraction']:.2%} / {quality['clipped_light_fraction']:.2%}",
                 f"Laplacian variance: {quality['laplacian_variance']} (thumbnail blur heuristic)", 'Technical metadata:']
        lines += [f'{key}: {item}' for key, item in value['technical_metadata'].items()] or ['None reported.']
        if value.get('metadata_error'):
            lines.append(value['metadata_error'])
    elif value.get('local_image_comparison'):
        lines = [value['first']['path'], value['second']['path'], f"Identical file bytes: {value['byte_equal']}",
                 f"Identical normalized decoded pixels: {value['pixel_equal']}",
                 f"pHash distance: {value['phash_distance']}/64; dHash distance: {value['dhash_distance']}/64"]
    else:
        return 'No local image tool result was returned.'
    return '\n'.join([*lines, '', *value.get('limitations', [])])


def _worker_main():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (POSIX_ADDRESS_BYTES, POSIX_ADDRESS_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError('Oversized worker request.')
        request = json.loads(payload)
        paths = request.get('paths')
        if (request.get('operation') not in {'inspect', 'compare'} or not isinstance(paths, list)
                or len(paths) != (1 if request['operation'] == 'inspect' else 2)):
            raise ValueError('Unsupported worker request.')
        result = _inspect(paths[0]) if request['operation'] == 'inspect' else _compare(*paths)
        output = json.dumps({'ok': True, 'result': result}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError('Oversized worker result.')
        sys.stdout.buffer.write(output)
        return 0
    except ImportError:
        sys.stdout.write('{"ok":false,"error":"missing_dependency"}')
        return 2
    except Exception:
        sys.stdout.write('{"ok":false,"error":"invalid_image"}')
        return 1


if __name__ == '__main__' and sys.argv[1:] == ['--worker']:
    raise SystemExit(_worker_main())
