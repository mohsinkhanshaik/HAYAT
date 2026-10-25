"""Validated, bundled geometry; never downloads or imports model code at runtime."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

ASSET = Path(__file__).parent / 'assets/brain/human-brain.json'
LIMITS = {'Low': 384, 'Normal': 1536, 'High': 2560, 'Ultra': 4850}
MODEL_CREDIT = 'Human Brain · 3DRT STUDIOS · CC BY 4.0 · adapted'
MODEL_CREDITS = '''Human Brain by 3DRT STUDIOS (Hanako.com)
https://sketchfab.com/3d-models/human-brain-7a27c17fd6c0488bb31ab093236a47fb
Creator: https://sketchfab.com/Hanako.com

Licensed under Creative Commons Attribution 4.0 International:
https://creativecommons.org/licenses/by/4.0/

Adapted for HAYAT: scene transforms applied, centred, uniformly scaled,
Y reflected, textures omitted, mesh simplified and theme colours applied.
The creator does not endorse HAYAT. This is an illustrative GUI surface;
the subsystem overlay is software architecture, not anatomical brain activity.

Obtained from Ai2's public Objaverse collection (individual asset: CC BY 4.0).
Dataset: https://huggingface.co/datasets/allenai/objaverse
Dataset license: https://opendatacommons.org/licenses/by/1-0/
Objaverse: A Universe of Annotated 3D Objects, Deitke et al., 2022.
'''


def read_facets(path: Path, detail: str):
    if detail not in LIMITS:
        raise ValueError('Unknown model detail')
    if path.stat().st_size > 500_000:
        raise ValueError('Bundled mesh exceeds size limit')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unknown mesh schema')
    level = data['levels'][detail]
    vertices, faces = level['vertices'], level['faces']
    if not 3 <= len(vertices) <= 4096 or not 1 <= len(faces) <= LIMITS[detail]:
        raise ValueError('Mesh exceeds geometry budget')
    for point in vertices:
        if (len(point) != 3 or any(type(value) not in (int, float) or not math.isfinite(value)
                                   or abs(value) > 1.6 for value in point)):
            raise ValueError('Invalid mesh vertex')
    for face in faces:
        if len(face) != 3 or len(set(face)) != 3 or any(type(index) is not int or not 0 <= index < len(vertices) for index in face):
            raise ValueError('Invalid mesh triangle')
    return tuple(tuple(tuple(vertices[index]) for index in face) for face in faces)


@lru_cache(maxsize=4)
def asset_facets(detail: str):
    return read_facets(ASSET, detail)
