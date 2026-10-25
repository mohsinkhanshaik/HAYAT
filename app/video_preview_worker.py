"""Small, silent, disposable decoder used by the chat's inline video cards."""
from __future__ import annotations

import json
import math
import struct
import sys
import time


def preview(request):
    import cv2
    cv2.setNumThreads(1)
    capture = cv2.VideoCapture(request['path'])
    try:
        if not capture.isOpened():
            raise ValueError('No decoder could open the video')
        width, height = capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = capture.get(cv2.CAP_PROP_FPS)
        if width * height > 35_000_000 or width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Unsupported video dimensions or frame rate')
        start = float(request.get('start', 0))
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        one = request.get('poster', False)
        deadline = time.monotonic() + 180
        for number in range(1 if one else 900):
            if time.monotonic() >= deadline:
                break
            seconds = start + number / 10
            capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            scale = min(1, 360 / frame.shape[1], 260 / frame.shape[0])
            frame = cv2.resize(frame, (max(1, round(frame.shape[1]*scale)), max(1, round(frame.shape[0]*scale))))
            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                raise ValueError('Frame preview encoding failed')
            data = jpeg.tobytes()
            sys.stdout.buffer.write(struct.pack('!Id', len(data), seconds) + data)
            sys.stdout.buffer.flush()
            if not one:
                time.sleep(.1)
    finally:
        capture.release()


if __name__ == '__main__':
    try:
        preview(json.loads(sys.stdin.readline(64000)))
    except Exception as exc:
        print(str(exc)[:400], file=sys.stderr)
        raise SystemExit(1) from exc
