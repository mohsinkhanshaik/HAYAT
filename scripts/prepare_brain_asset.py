"""Build HAYAT's small offline brain mesh from the pinned CC-BY Sketchfab asset.

Development-only dependencies: trimesh==5.1.1, fast-simplification==0.2.0,
numpy==2.5.2. The GUI uses only the generated JSON and Python's standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = '5cb8665dd5d7c02ccc8fbb9369a7b1c1248d4c1e00316fd69c6c118d102af443'
MODEL_URL = 'https://sketchfab.com/3d-models/human-brain-7a27c17fd6c0488bb31ab093236a47fb'
REVISION = '556637099bf4fa79ea7b239d0d9c328b8a2e9ac8'
LIMITS = {'Low': 384, 'Normal': 1536, 'High': 2560, 'Ultra': 4850}


def prepare(source: Path, destination: Path):
    import numpy as np
    import trimesh

    if source.stat().st_size > 10_000_000 or hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Input is not the reviewed, licensed Human Brain GLB.')
    mesh = trimesh.load_scene(source).to_mesh()
    original_faces = len(mesh.faces)
    # Apply the scene transforms, centre, uniformly scale, and convert Y-up
    # coordinates to the existing canvas's Y-down convention. No textures.
    mesh.vertices -= mesh.bounds.mean(axis=0)
    mesh.vertices *= 2.3 / float(np.max(mesh.extents))
    mesh.vertices[:, 1] *= -1
    mesh.faces = mesh.faces[:, ::-1]  # preserve winding after reflection
    mesh.visual = trimesh.visual.ColorVisuals(mesh)
    mesh.merge_vertices()
    lods = {}
    for detail, budget in LIMITS.items():
        # Ultra preserves all reviewed source triangles; lower levels are decimated.
        simplified = mesh.copy() if len(mesh.faces) <= budget else mesh.simplify_quadric_decimation(face_count=budget)
        assert 0 < len(simplified.faces) <= budget
        lods[detail] = {'vertices': np.round(simplified.vertices, 6).tolist(),
                        'faces': simplified.faces.tolist()}
    destination.mkdir(parents=True, exist_ok=True)
    geometry = destination / 'human-brain.json'
    geometry.write_text(json.dumps({'schema': 1, 'levels': lods}, separators=(',', ':')), encoding='utf-8')
    manifest = {
        'title': 'Human Brain', 'creator': '3DRT STUDIOS',
        'creator_url': 'https://sketchfab.com/Hanako.com', 'source_url': MODEL_URL,
        'license': 'CC-BY-4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
        'acquired_from': 'https://huggingface.co/datasets/allenai/objaverse',
        'dataset_revision': REVISION,
        'dataset_path': 'glbs/000-012/7a27c17fd6c0488bb31ab093236a47fb.glb',
        'dataset_license': 'ODC-By-1.0',
        'source_sha256': SOURCE_SHA256, 'source_triangles': original_faces,
        'changes': 'Scene transforms applied; centred, uniformly scaled, Y reflected; textures omitted; three simplified detail levels plus full-source Ultra; recoloured by GUI theme.',
        'geometry_sha256': hashlib.sha256(geometry.read_bytes()).hexdigest(),
        'triangles': {detail: len(lod['faces']) for detail, lod in lods.items()},
        'geometry_bytes': geometry.stat().st_size,
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'app/assets/brain')
    args = parser.parse_args()
    prepare(args.source, args.output)
