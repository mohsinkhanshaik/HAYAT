"""Persistent, crash-isolated speech recognition. Native imports stay in the child."""
from __future__ import annotations

import multiprocessing
import threading
import time
from dataclasses import asdict

from app.voice.models import Transcription


def _worker(connection, config):
    try:
        from app.voice.stt import FasterWhisperSTT
        provider = FasterWhisperSTT(config)
        while True:
            operation, audio = connection.recv()
            try:
                if operation == "warmup":
                    provider.warmup()
                    result = None
                else:
                    result = asdict(provider.transcribe(audio))
                connection.send({"result": result})
            except Exception as exc:
                connection.send({"error": f"{type(exc).__name__}: {exc}"})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class SpeechWorker:
    def __init__(self, config, *, worker=_worker):
        self.config = dict(config)
        self.timeout = max(1, min(float(config.get("worker_timeout_seconds", 180)), 600))
        self._worker = worker
        self._process = None
        self._connection = None
        self._lane = threading.Lock()
        self._state = threading.Lock()
        self._closed = False

    def _dispose(self):
        with self._state:
            process, connection = self._process, self._connection
            self._process = self._connection = None
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
        if connection is not None:
            connection.close()

    def close(self):
        self._closed = True
        # The request loop observes this flag and releases the lane promptly.
        with self._lane:
            self._dispose()

    def reopen(self):
        with self._lane:
            self._closed = False

    def _request(self, operation, audio=None, cancel=None):
        deadline = time.monotonic() + self.timeout
        while not self._lane.acquire(timeout=.05):
            if self._closed or (cancel and cancel.is_set()):
                return None
            if time.monotonic() >= deadline:
                raise TimeoutError("Speech worker is busy")
        try:
            if self._closed or (cancel and cancel.is_set()):
                return None
            if self._process is not None and not self._process.is_alive():
                self._dispose()
            if self._process is None:
                context = multiprocessing.get_context("spawn")
                parent, child = context.Pipe()
                process = context.Process(target=self._worker, args=(child, self.config), daemon=True)
                try:
                    process.start()
                except BaseException:
                    parent.close()
                    child.close()
                    raise
                child.close()
                self._connection, self._process = parent, process
            self._connection.send((operation, audio))
            while True:
                if self._closed or (cancel and cancel.is_set()):
                    self._dispose()
                    return None
                if time.monotonic() >= deadline:
                    self._dispose()
                    raise TimeoutError("Speech recognition timed out; its worker was stopped")
                if self._connection.poll(.05):
                    reply = self._connection.recv()
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                    return reply["result"]
                if not self._process.is_alive():
                    self._dispose()
                    raise RuntimeError("Speech worker exited unexpectedly; retry to restart it")
        except TimeoutError:
            raise
        except (EOFError, BrokenPipeError, OSError) as exc:
            self._dispose()
            raise RuntimeError("Speech worker exited unexpectedly; retry to restart it") from exc
        finally:
            self._lane.release()

class IsolatedWhisperSTT(SpeechWorker):
    name = "faster_whisper"

    def warmup(self):
        self._request("warmup")

    def transcribe(self, audio, cancel=None):
        if audio.sample_rate != 16000:
            raise ValueError("Speech recognition requires 16 kHz PCM audio")
        if len(audio.samples) > 16000 * 600 * max(1, audio.channels):
            raise ValueError("Speech recordings are limited to ten minutes")
        result = self._request("transcribe", audio, cancel)
        if result is None:
            return Transcription("")
        result["alternatives"] = tuple(result.get("alternatives", ()))
        return Transcription(**result)


def piper_worker(connection, config):
    try:
        voice = None
        while True:
            operation, text = connection.recv()
            try:
                import numpy as np
                from piper import PiperVoice
                from app.voice.rendering import collect_piper
                if voice is None:
                    voice = PiperVoice.load(config["model_path"])
                # A (text, speed) pair carries the owner's speaking speed; plain text keeps 1.0.
                speed = 1.0
                if isinstance(text, tuple):
                    text, speed = text
                rendered = collect_piper(voice, text, speed, config.get("sentence_pause", 0.16))
                samples, rate = rendered if rendered is not None else (np.array([], dtype="<f4"), 22050)
                samples = samples.astype("<f4")
                connection.send({"result": {"samples": samples.tobytes(), "sample_rate": rate}})
            except Exception as exc:
                connection.send({"error": f"{type(exc).__name__}: {exc}"})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()
