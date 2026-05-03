#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# ==========================
# PATHS FROM .env
# ==========================
INPUT_OBJ_DIR = os.getenv("INPUT_OBJ_DIR")
OUTPUT_DIR = os.getenv("OUTPUT_JSON_DIR","obj_parsed_json_outputs")
DEFAULT_LAYER = os.getenv("DEFAULT_LAYER", "Layer 01")
# ==========================

DETERMINISTIC_IDS = True

_FACE_REF_RE = re.compile(r"^(-?\d+)(?:/(-?\d*)?)?(?:/(-?\d+))?$")


def parse_face_ref(token: str) -> Tuple[int, Optional[int], Optional[int]]:
    m = _FACE_REF_RE.match(token)
    if not m:
        raise ValueError(f"Invalid face ref: {token}")
    v = int(m.group(1))
    vt_raw = m.group(2)
    vn_raw = m.group(3)
    vt = int(vt_raw) if (vt_raw is not None and vt_raw != "") else None
    vn = int(vn_raw) if (vn_raw is not None and vn_raw != "") else None
    return v, vt, vn


def resolve_index(idx: int, length: int) -> int:
    return (idx - 1) if idx > 0 else (length + idx)


def update_bbox(bbox: Optional[Dict[str, List[float]]], p: Tuple[float, float, float]):
    if bbox is None:
        return {"min": [p[0], p[1], p[2]], "max": [p[0], p[1], p[2]]}
    bbox["min"][0] = min(bbox["min"][0], p[0])
    bbox["min"][1] = min(bbox["min"][1], p[1])
    bbox["min"][2] = min(bbox["min"][2], p[2])
    bbox["max"][0] = max(bbox["max"][0], p[0])
    bbox["max"][1] = max(bbox["max"][1], p[1])
    bbox["max"][2] = max(bbox["max"][2], p[2])
    return bbox


def stable_uuid(namespace: uuid.UUID, name: str) -> str:
    return str(uuid.uuid5(namespace, name))


def count_ngons(face_sizes: List[int]) -> int:
    return sum(1 for n in face_sizes if n > 4)


def guess_layer_from_group_name(group_name: str) -> str:
    return DEFAULT_LAYER


@dataclass
class MeshBlock:
    id: str
    index: int
    object_name: str
    layer_name: str
    render_material: Dict[str, Any]
    geometry: Dict[str, Any]


def parse_obj_group_based(obj_path: str) -> List[MeshBlock]:
    vertices: List[Tuple[float, float, float]] = []
    blocks: List[MeshBlock] = []

    current_group: Optional[str] = None
    current_usemtl: Optional[str] = None

    unique_v: set[int] = set()
    face_count = 0
    face_sizes: List[int] = []
    bbox: Optional[Dict[str, List[float]]] = None

    ns = uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(obj_path))

    def flush_section():
        nonlocal unique_v, face_count, face_sizes, bbox
        # Only flush if we actually saw faces in this section
        if current_group is None or face_count == 0:
            unique_v = set()
            face_count = 0
            face_sizes = []
            bbox = None
            return

        mesh_index = len(blocks) + 2
        layer_name = guess_layer_from_group_name(current_group)
        object_name = current_group

        mesh_id = (
            stable_uuid(ns, f"{mesh_index}:{layer_name}:{object_name}:{current_usemtl or ''}")
            if DETERMINISTIC_IDS
            else str(uuid.uuid4())
        )

        render_material = {
            "source": "from object" if current_usemtl else "from layer",
            "index": -1,
            "name": current_usemtl,
        }

        geometry = {
            "Valid mesh.": True,
            "Open double precision polygon mesh": {
                "vertices": len(unique_v),
                "faces": face_count,
                "n_gons": count_ngons(face_sizes),
                "Bounding box": bbox,
            },
        }

        blocks.append(
            MeshBlock(
                id=mesh_id,
                index=mesh_index,
                object_name=object_name,
                layer_name=layer_name,
                render_material=render_material,
                geometry=geometry,
            )
        )

        # reset accumulators for next section
        unique_v = set()
        face_count = 0
        face_sizes = []
        bbox = None

    with open(obj_path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            head = parts[0]
            args = parts[1:]

            if head == "v" and len(args) >= 3:
                vertices.append(tuple(map(float, args[:3])))

            elif head == "g":
                # NEW GROUP => end previous section
                flush_section()
                current_group = " ".join(args) if args else "default"
                # Reset material state for new group? (OBJ keeps it, but many exporters treat as new)
                current_usemtl = None

            elif head == "usemtl":
                # NEW MATERIAL => end previous section (even if same name repeats later)
                flush_section()
                current_usemtl = " ".join(args) if args else None

            elif head == "f" and len(args) >= 3:
                if current_group is None:
                    current_group = "default"

                refs = [parse_face_ref(tok) for tok in args]
                v_indices: List[int] = []
                for (v, _, _) in refs:
                    v0 = resolve_index(v, len(vertices))
                    if v0 < 0 or v0 >= len(vertices):
                        v_indices = []
                        break
                    v_indices.append(v0)

                if not v_indices:
                    continue

                face_count += 1
                face_sizes.append(len(v_indices))

                for vi in v_indices:
                    unique_v.add(vi)
                    bbox = update_bbox(bbox, vertices[vi])

            else:
                pass

    flush_section()
    return blocks


def build_report(obj_path: str) -> Dict[str, Any]:
    blocks = parse_obj_group_based(obj_path)

    totals = {
        "meshes": len(blocks),
        "vertices": sum(
            b.geometry["Open double precision polygon mesh"]["vertices"] for b in blocks
        ),
        "polygons": sum(
            b.geometry["Open double precision polygon mesh"]["faces"] for b in blocks
        ),
    }

    report = {
        "meshes": [
            {
                "mesh": "mesh",
                "ID": {"value": b.id, "index": b.index},
                "Object name": b.object_name,
                "Layer name": b.layer_name,
                "Render Material": b.render_material,
                "Geometry": b.geometry,
            }
            for b in blocks
        ],
        "Mesh totals": totals,
    }

    return report


def write_reports() -> None:
    if not INPUT_OBJ_DIR:
        raise SystemExit("Missing INPUT_OBJ_DIR in .env")

    if not OUTPUT_DIR:
        raise SystemExit("Missing OUTPUT_DIR in .env")

    input_dir = os.path.abspath(INPUT_OBJ_DIR)
    output_dir = os.path.abspath(OUTPUT_DIR)

    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Invalid INPUT_OBJ_DIR: {input_dir}")

    os.makedirs(output_dir, exist_ok=True)

    obj_files = [
        f for f in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, f)) and f.lower().endswith(".obj")
    ]

    if not obj_files:
        print(f"No .obj files found in: {input_dir}")
        return

    processed = 0
    failed = 0

    for file_name in sorted(obj_files):
        obj_path = os.path.join(input_dir, file_name)
        output_path = os.path.join(
            output_dir,
            os.path.splitext(file_name)[0] + "_parsed.json",
        )

        try:
            report = build_report(obj_path)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            print(f"[OK] {file_name} -> {os.path.basename(output_path)}")
            processed += 1

        except Exception as e:
            print(f"[FAIL] {file_name} -> {e}")
            failed += 1

    print(f"\nDone. processed={processed}, failed={failed}, output_dir={output_dir}")


if __name__ == "__main__":
    write_reports()
