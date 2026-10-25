# Plate recognition (ANPR)

HAYAT reads number plates from your own images and videos, your USB webcams,
and RTSP cameras you register as authorized on your own network. It is meant
for your vehicles, a parking site you manage, and test footage.

## What it will not do

- **Identify owners.** Only plate text is stored. There is no lookup of who
  owns a vehicle and no address search, and nothing connects to VAHAN, a DMV,
  NCIC or any other government or police database.
- **Issue fines or notices.** In parking mode a plate that is not on your
  vehicle list, or stays too long, becomes an internal *review event*. Any
  charge or notice has to go through your own authorized process.
- **Read public cameras.** Web streams, internet cameras and the public
  traffic cameras in Global Intelligence are refused. An RTSP camera must be
  registered by you, and its address must be on a private network (home,
  office or parking-site LAN). Reading plates from cameras you do not
  control would track other people's cars; that needs the operator's legal
  authority, so HAYAT does not offer it.

## Install

The recogniser uses packages HAYAT may not have yet:

```text
.\.venv\Scripts\python.exe -m pip install open-image-models fast-plate-ocr
```

- **open-image-models** (MIT): the YOLOv9 licence-plate detector, run with
  ONNX Runtime.
- **fast-plate-ocr** (MIT): an OCR model trained only on plates.
- **EasyOCR** is already part of HAYAT and is the second OCR engine.
  **PaddleOCR** is used if you install it (`pip install paddleocr paddlepaddle`).

The models download on first use into `data\models\anpr\` (about 30 MB),
inside HAYAT's own folder.

**GPU (CUDA).** Detection and fast-plate-ocr use ONNX Runtime. With
`onnxruntime-gpu` installed (matching your CUDA version), they run on the
NVIDIA GPU; EasyOCR uses the GPU when PyTorch is a CUDA build. Settings →
GPU is `auto` (use it when present), `on` or `off`. The window's header shows
what is actually in use. Without these packages everything runs on the CPU:
the tiny models read a plate in tens of milliseconds there too.

**On the owner's computer (since 2026-10-11) these models run on the NVIDIA RTX 5060
Laptop GPU.** HAYAT now uses `onnxruntime-gpu` 1.30.0 with the CUDA 13 and cuDNN 9
libraries from pip, all inside `.venv`. Measured on the sample images:

| Model | CPU | GPU |
|---|---|---|
| RF-DETR vehicle counting | 71 ms | 18 ms |
| YOLOv9 plate detector | 36 ms | 18 ms |

The vehicle counts and plate readings were identical on both. The brain model shares
this 8 GB card, so each vision model may take at most 1 GB of it. With the brain
loaded (6.3 GB) the models still ran on the GPU, at 7.3 GB in total, and the brain
kept answering. If the GPU fails anyway (out of memory, a CUDA error), the model moves
to the CPU and the same image is read again: a count or a plate never fails because
of the GPU. The dashboard header shows where each model really runs, which can be
"CPU" even when the GPU was asked for.

Without the two packages, a classical OpenCV detector still finds clear,
front-on plates and EasyOCR reads them. The header says which detector is
running.

## Use

Open **Studios → Plate recognition (ANPR)**.

- **Open image… / Open video…** reads a file. **Start webcam** reads a USB
  camera by number. **Start camera** reads a registered RTSP camera.
  **Stop** ends a live source. **Make test samples** writes synthetic vehicles
  with known plates for testing.
- The preview shows each plate's box, its text and the OCR confidence: green
  when the text fits the plate format, amber when it does not.
- **Records:** search by plate, filter by status, period and whether the
  vehicle is on your list. Delete records, or every record of one plate.
  Export JSON, CSV or SQLite.
- **Review queue:** readings below the confidence threshold, or that fit no
  plate format, with the plate image. Confirm, correct or reject.
- **Vehicles:** your own vehicles and permit holders (plate, label, group,
  notes).
- **Parking events:** internal review events (not on the list, overstay).
  Mark resolved or dismiss.
- **Settings & privacy:** plate format (India, US, UK, Europe, or your own
  masks such as `PK, Pakistan, LLLDDDD`), how long records are kept
  (default 30 days), whether plate images are kept, the review threshold,
  parking mode, the overstay limit, detector, OCR engine, GPU, frame stride,
  and authorized cameras. **Delete old records now** applies the retention
  period; **Delete everything** removes all records, images and events.

Video is tracked: a car that is in view for many frames is read many times,
the readings vote (each text scores the sum of its confidences), and one
record is stored per passing plate with how many readings agreed.

From the command line:

```text
python scripts/anpr.py image photo.jpg
python scripts/anpr.py video drive.mp4
python scripts/anpr.py webcam 0 --seconds 60
python scripts/anpr.py check            synthetic samples: read them and report accuracy
python scripts/anpr.py search MH12 --status review
python scripts/anpr.py export csv plates.csv
python scripts/anpr.py purge --days 7
```

## Plate formats

| Code | Format | Repair |
|---|---|---|
| IN | India: state, RTO, series, number (`MH12AB1234`, `DL3CAF0001`) and Bharat series (`22BH1234AA`) | Letters and digits are corrected by position (O/0, I/1, B/8, S/5, Z/2, G/6) |
| US | Any state: 2–8 letters and digits | Cleaned only (states differ) |
| UK | `AB12CDE` | By position |
| EU | 4–10 letters and digits | Cleaned only |
| Your own | Masks (`L` letter, `D` digit, `A` either) or `re:` a regular expression | By position for masks |

## Data

Everything is local: `data\anpr\anpr.db` (SQLite) and `data\anpr\crops\`
for plate images. Records older than the retention period are deleted every
time the recogniser opens, and on **Delete old records now**.

## Limits

- Accuracy depends on the picture. Plates that are small, blurred, at a
  steep angle, at night without infrared, or dirty will be read wrongly or
  not at all; low-confidence readings go to the review queue for this
  reason. Do not act on a reading without checking it.
- The synthetic samples are clean. Real footage is harder, so measure on
  your own cameras before relying on it.
- Two-line plates (common on Indian motorcycles) are read top to bottom by
  EasyOCR; fast-plate-ocr's global model handles many but not all layouts.
