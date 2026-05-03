#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase
import json

load_dotenv()

# ------------------------- helpers ------------------------- #

def fetch_json(url: str) -> Dict[str, Any]:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()

def basename(path: str) -> str:
    path = path.replace("\\", "/")
    return path.split("/")[-1] if "/" in path else path

def safe_xyz(v: Any) -> Optional[Tuple[float, float, float]]:
    if not isinstance(v, list) or len(v) < 3:
        return None
    try:
        return float(v[0]), float(v[1]), float(v[2])
    except Exception:
        return None

def flatten_layers(layer_tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any]) -> None:
        out.append(n)
        for ch in (n.get("children") or []):
            walk(ch)

    for r in (layer_tree or []):
        walk(r)
    return out

def is_empty_guid(g: Any) -> bool:
    if g is None:
        return True
    s = str(g).strip().lower()
    return s == "" or s == "00000000-0000-0000-0000-000000000000"

def segment_leaf_props(seg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Leaf-only segment props: keep scalar/bool/None only (no nested dict/list).
    """
    out: Dict[str, Any] = {}
    for k, v in (seg or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out


# ------------------------- cypher ------------------------- #

# Minimal constraints
CYPHER_CONSTRAINTS = [
    "CREATE CONSTRAINT dmroot_unique IF NOT EXISTS FOR (r:Root3dmNode) REQUIRE r.name IS UNIQUE",
    "CREATE CONSTRAINT dmfile_unique IF NOT EXISTS FOR (f:DMFile) REQUIRE f.file_key IS UNIQUE",
    "CREATE CONSTRAINT dmsummary_unique IF NOT EXISTS FOR (s:DMSummary) REQUIRE s.file_key IS UNIQUE",
    "CREATE CONSTRAINT layer_unique IF NOT EXISTS FOR (l:Layer) REQUIRE (l.file_key, l.id) IS UNIQUE",
    "CREATE CONSTRAINT group_unique IF NOT EXISTS FOR (g:Group) REQUIRE (g.file_key, g.index) IS UNIQUE",
    "CREATE CONSTRAINT material_unique IF NOT EXISTS FOR (m:Material) REQUIRE (m.file_key, m.index) IS UNIQUE",
    "CREATE CONSTRAINT object_unique IF NOT EXISTS FOR (o:Object) REQUIRE (o.file_key, o.id) IS UNIQUE",
    "CREATE CONSTRAINT layerroot_unique IF NOT EXISTS FOR (lr:LayerRoot) REQUIRE lr.file_key IS UNIQUE",
    "CREATE CONSTRAINT grouproot_unique IF NOT EXISTS FOR (gr:GroupRoot) REQUIRE gr.file_key IS UNIQUE",
    "CREATE CONSTRAINT materialroot_unique IF NOT EXISTS FOR (mr:MaterialRoot) REQUIRE mr.file_key IS UNIQUE",
    "CREATE CONSTRAINT objectroot_unique IF NOT EXISTS FOR (or:ObjectRoot) REQUIRE or.file_key IS UNIQUE",
    "CREATE CONSTRAINT curvedetails_unique IF NOT EXISTS FOR (d:CurveDetails) REQUIRE (d.file_key, d.object_id) IS UNIQUE",
    "CREATE CONSTRAINT brepdetails_unique IF NOT EXISTS FOR (d:BrepDetails) REQUIRE (d.file_key, d.object_id) IS UNIQUE",
    "CREATE CONSTRAINT meshdetails_unique IF NOT EXISTS FOR (d:MeshDetails) REQUIRE (d.file_key, d.object_id) IS UNIQUE",
    "CREATE CONSTRAINT surfacedetails_unique IF NOT EXISTS FOR (d:SurfaceDetails) REQUIRE (d.file_key, d.object_id) IS UNIQUE",
    "CREATE CONSTRAINT segment_unique IF NOT EXISTS FOR (s:Segment) REQUIRE (s.file_key, s.object_id, s.index) IS UNIQUE",
    "CREATE CONSTRAINT point_unique IF NOT EXISTS FOR (p:Point) REQUIRE (p.file_key, p.object_id, p.index) IS UNIQUE",
]

CYPHER_ROOT_AND_CONTAINERS = """
// GlobalRoot (create if missing)
MERGE (g:GlobalRoot {id: "global_root"})
SET g.name = "global_root"

// 3DM root
MERGE (r:Root3dmNode {name: "Root3dmNode"})

// Link global → 3dm root
MERGE (g)-[:HAS_3DM_NODE]->(r)


// File node under global root
MERGE (f:DMFile {file_key: $file_key})
SET f.name = $file_name,
    f.path = $file_path,
    f.kind = "3dm"

MERGE (r)-[:HAS_3DM_FILE]->(f)

// Summary node
MERGE (s:DMSummary {file_key: $file_key})
SET s.name = "summary"
MERGE (f)-[:HAS_SUMMARY]->(s)

// Container roots under summary
MERGE (lr:LayerRoot {file_key: $file_key})
SET lr.name = "layers_root"
MERGE (s)-[:HAS_LAYERS]->(lr)

MERGE (gr:GroupRoot {file_key: $file_key})
SET gr.name = "groups_root"
MERGE (s)-[:HAS_GROUPS]->(gr)

MERGE (mr:MaterialRoot {file_key: $file_key})
SET mr.name = "materials_root"
MERGE (s)-[:HAS_MATERIALS]->(mr)

MERGE (or:ObjectRoot {file_key: $file_key})
SET or.name = "objects_root"
MERGE (s)-[:HAS_OBJECTS]->(or)
"""

CYPHER_UPSERT_LAYERS = """
UNWIND $layers AS L
MERGE (ly:Layer {file_key: $file_key, id: L.id})
SET ly.index = toInteger(L.index),
    ly.name = L.name,
    ly.fullpath = L.fullpath,
    ly.parent_id = L.parent_id,
    ly.material_index = toInteger(coalesce(L.material_index, -1)),
    ly.render_material_index = toInteger(coalesce(L.render_material_index, -1)),
    ly.has_children = coalesce(L.has_children, false)
"""

CYPHER_LINK_LAYER_ROOTS = """
MATCH (lr:LayerRoot {file_key: $file_key})
UNWIND $root_layer_ids AS rid
MATCH (ly:Layer {file_key: $file_key, id: rid})
MERGE (lr)-[:HAS_LAYER_ROOT]->(ly)
"""

CYPHER_LINK_LAYER_CHILDREN = """
UNWIND $edges AS E
MATCH (p:Layer {file_key: $file_key, id: E.parent_id})
MATCH (c:Layer {file_key: $file_key, id: E.child_id})
MERGE (p)-[:HAS_CHILD_LAYER]->(c)
"""

CYPHER_UPSERT_GROUPS = """
MATCH (gr:GroupRoot {file_key: $file_key})
UNWIND $groups AS G
MERGE (g:Group {file_key: $file_key, index: toInteger(G.index)})
SET g.name = G.name
MERGE (gr)-[:HAS_GROUP]->(g)
"""

CYPHER_UPSERT_MATERIALS = """
MATCH (mr:MaterialRoot {file_key: $file_key})
UNWIND $materials AS M
MERGE (m:Material {file_key: $file_key, index: toInteger(M.index)})
SET m.name = M.name
MERGE (mr)-[:HAS_MATERIAL]->(m)
"""

CYPHER_UPSERT_OBJECTS = """
MATCH (or:ObjectRoot {file_key: $file_key})
UNWIND $objects AS obj

MERGE (o:Object {file_key: $file_key, id: obj.id})
SET o.object_name = obj.object_name,
    o.layer_name  = obj.layer_name,

    o.render_material_source = obj.render_material_source,
    o.render_material_index  = toInteger(obj.render_material_index),

    o.groups = obj.groups,

    o.raw_type = obj.raw_type,

    // bbox corners as Neo4j POINTs
    o.bbox_min = CASE WHEN obj.bbox_min_x IS NULL THEN NULL
                 ELSE point({x: obj.bbox_min_x, y: obj.bbox_min_y, z: obj.bbox_min_z}) END,
    o.bbox_max = CASE WHEN obj.bbox_max_x IS NULL THEN NULL
                 ELSE point({x: obj.bbox_max_x, y: obj.bbox_max_y, z: obj.bbox_max_z}) END,

    o.is_curve   = coalesce(obj.is_curve, false),
    o.is_brep    = coalesce(obj.is_brep, false),
    o.is_surface = coalesce(obj.is_surface, false),
    o.is_mesh    = coalesce(obj.is_mesh, false),

    o.detail_keys = coalesce(obj.detail_keys, [])

MERGE (or)-[:HAS_OBJECT]->(o)
"""

CYPHER_UPSERT_DETAILS = """
UNWIND $details AS d
MATCH (o:Object {file_key: $file_key, id: d.object_id})

FOREACH (_ IN CASE WHEN d.curve IS NULL THEN [] ELSE [1] END |
  MERGE (cd:CurveDetails {file_key: $file_key, object_id: d.object_id})
  SET cd.subtype = d.curve.subtype,
    cd.segment_count = toInteger(coalesce(d.curve.segment_count, -1)),
    cd.point_count   = toInteger(coalesce(d.curve.point_count, -1)),
    cd.length        = toFloat(coalesce(d.curve.length, -1)),

    cd.start_pt = CASE
      WHEN d.curve.start_x IS NULL THEN NULL
      ELSE point({x:d.curve.start_x, y:d.curve.start_y, z:d.curve.start_z})
    END,

    cd.end_pt = CASE
      WHEN d.curve.end_x IS NULL THEN NULL
      ELSE point({x:d.curve.end_x, y:d.curve.end_y, z:d.curve.end_z})
    END

  MERGE (o)-[:HAS_CURVE_DETAILS]->(cd)
)

FOREACH (_ IN CASE WHEN d.brep IS NULL THEN [] ELSE [1] END |
  MERGE (bd:BrepDetails {file_key: $file_key, object_id: d.object_id})
  SET bd.subtype  = d.brep.subtype,
      bd.faces    = toInteger(coalesce(d.brep.faces, 0)),
      bd.edges    = toInteger(coalesce(d.brep.edges, 0)),
      bd.vertices = toInteger(coalesce(d.brep.vertices, 0))
  MERGE (o)-[:HAS_BREP_DETAILS]->(bd)
)

FOREACH (_ IN CASE WHEN d.mesh IS NULL THEN [] ELSE [1] END |
  MERGE (md:MeshDetails {file_key: $file_key, object_id: d.object_id})
  SET md.subtype  = d.mesh.subtype,
      md.vertices = toInteger(coalesce(d.mesh.vertices, 0)),
      md.faces    = toInteger(coalesce(d.mesh.faces, 0))
  MERGE (o)-[:HAS_MESH_DETAILS]->(md)
)

FOREACH (_ IN CASE WHEN d.surface IS NULL THEN [] ELSE [1] END |
  MERGE (sd:SurfaceDetails {file_key: $file_key, object_id: d.object_id})
  SET sd.subtype   = d.surface.subtype,
      sd.rational  = coalesce(d.surface.rational, false),
      sd.u_degree  = toInteger(coalesce(d.surface.u_degree, -1)),
      sd.u_cv_count= toInteger(coalesce(d.surface.u_cv_count, -1)),
      sd.v_degree  = toInteger(coalesce(d.surface.v_degree, -1)),
      sd.v_cv_count= toInteger(coalesce(d.surface.v_cv_count, -1))
  MERGE (o)-[:HAS_SURFACE_DETAILS]->(sd)
)
"""

CYPHER_UPSERT_SEGMENTS = """
UNWIND $segments AS s
MERGE (seg:Segment {file_key: $file_key, object_id: s.object_id, index: toInteger(s.index)})
SET seg += s.props
WITH seg, s
MATCH (cd:CurveDetails {file_key: $file_key, object_id: s.object_id})
MERGE (cd)-[:HAS_SEGMENT]->(seg)
"""

CYPHER_UPSERT_POINTS = """
UNWIND $points AS p
MERGE (pt:Point {file_key: $file_key, object_id: p.object_id, index: toInteger(p.index)})
SET pt.coordinates =
  CASE
    WHEN p.coordinates IS NULL OR size(p.coordinates) < 2 THEN NULL
    WHEN size(p.coordinates) = 2 THEN
      point({ x: p.coordinates[0], y: p.coordinates[1] })
    ELSE
      point({ x: p.coordinates[0], y: p.coordinates[1], z: p.coordinates[2] })
  END
WITH pt, p
MATCH (cd:CurveDetails {file_key: $file_key, object_id: p.object_id})
MERGE (cd)-[:HAS_POINT]->(pt)
"""

# ------------------------- ingestion (single JSON -> Neo4j) ------------------------- #

def ingest_one(session, data: Dict[str, Any]) -> Tuple[str, int, int]:
    file_path = data.get("file")
    if not file_path:
        raise ValueError("JSON missing top-level 'file' field.")

    file_name = data.get("file_name") or basename(str(file_path))
    file_key = str(file_path)  # stable key derived from JSON

    summary = data.get("summary") or {}
    layers_tree = summary.get("layers") or []
    groups = summary.get("groups") or []
    materials = summary.get("materials") or []
    objects = data.get("objects") or []

    # ---- Layers payloads ----
    flat_layers = flatten_layers(layers_tree)
    layers_payload: List[Dict[str, Any]] = []
    root_layer_ids: List[str] = []
    edges: List[Dict[str, str]] = []

    for L in flat_layers:
        lid = str(L.get("id") or "")
        pid = str(L.get("parent_id") or "")
        has_children = bool(L.get("children")) and len(L.get("children") or []) > 0

        layers_payload.append({
            "id": lid,
            "index": L.get("index"),
            "name": L.get("name"),
            "fullpath": L.get("fullpath"),
            "parent_id": pid,
            "material_index": L.get("material_index", -1),
            "render_material_index": L.get("render_material_index", -1),
            "has_children": has_children,
        })

        if is_empty_guid(pid):
            root_layer_ids.append(lid)
        else:
            edges.append({"parent_id": pid, "child_id": lid})

    # ---- Objects payloads ----
    objects_payload: List[Dict[str, Any]] = []
    details_payload: List[Dict[str, Any]] = []
    segments_payload: List[Dict[str, Any]] = []
    points_payload: List[Dict[str, Any]] = []

    for obj in objects:
        oid = str(obj.get("id"))
        geom = obj.get("geometry") or {}
        bbox = geom.get("bbox") or {}
        bmin = safe_xyz((bbox.get("min")))
        bmax = safe_xyz((bbox.get("max")))

        flags = geom.get("type_flags") or {}
        objects_payload.append({
            "id": oid,
            "object_name": obj.get("object_name"),
            "layer_name": obj.get("layer_name"),
            "render_material_source": (obj.get("render_material") or {}).get("source"),
            "render_material_index": (obj.get("render_material") or {}).get("index", -1),
            "groups": obj.get("groups"),
            "raw_type": geom.get("raw_type"),
            "bbox_min_x": bmin[0] if bmin else None,
            "bbox_min_y": bmin[1] if bmin else None,
            "bbox_min_z": bmin[2] if bmin else None,
            "bbox_max_x": bmax[0] if bmax else None,
            "bbox_max_y": bmax[1] if bmax else None,
            "bbox_max_z": bmax[2] if bmax else None,
            "is_curve": bool(flags.get("is_curve", False)),
            "is_brep": bool(flags.get("is_brep", False)),
            "is_surface": bool(flags.get("is_surface", False)),
            "is_mesh": bool(flags.get("is_mesh", False)),
            "detail_keys": geom.get("detail_keys") or [],
        })

        curve_details = geom.get("curve_details")
        brep_details = geom.get("brep_details")
        mesh_details = geom.get("mesh_details")
        surface_details = geom.get("surface_details")

        d: Dict[str, Any] = {"object_id": oid, "curve": None, "brep": None, "mesh": None, "surface": None}

        if isinstance(curve_details, dict):
            start_xyz = safe_xyz(curve_details.get("start"))
            end_xyz = safe_xyz(curve_details.get("end"))

            d["curve"] = {
                "subtype": curve_details.get("subtype"),
                "segment_count": curve_details.get("segment_count"),
                "point_count": curve_details.get("point_count"),
                "length": curve_details.get("length"),
                "start_x": start_xyz[0] if start_xyz else None,
                "start_y": start_xyz[1] if start_xyz else None,
                "start_z": start_xyz[2] if start_xyz else None,
                "end_x": end_xyz[0] if end_xyz else None,
                "end_y": end_xyz[1] if end_xyz else None,
                "end_z": end_xyz[2] if end_xyz else None,
            }

            segs = curve_details.get("segments")
            if isinstance(segs, list):
                for i, seg in enumerate(segs):
                    if isinstance(seg, dict):
                        segments_payload.append({
                            "object_id": oid,
                            "index": i,
                            "props": segment_leaf_props(seg),
                        })

            pts = curve_details.get("points")
            if isinstance(pts, list):
                for i, p in enumerate(pts):
                    xyz = safe_xyz(p)
                    if xyz:
                        points_payload.append({
                            "object_id": oid,
                            "index": i,
                            "coordinates": [xyz[0], xyz[1], xyz[2]],
                        })

        if isinstance(brep_details, dict):
            d["brep"] = {
                "subtype": brep_details.get("subtype"),
                "faces": brep_details.get("faces", 0),
                "edges": brep_details.get("edges", 0),
                "vertices": brep_details.get("vertices", 0),
            }

        if isinstance(mesh_details, dict):
            d["mesh"] = {
                "subtype": mesh_details.get("subtype"),
                "vertices": mesh_details.get("vertices", 0),
                "faces": mesh_details.get("faces", 0),
            }

        if isinstance(surface_details, dict):
            U = surface_details.get("U") or {}
            V = surface_details.get("V") or {}
            d["surface"] = {
                "subtype": surface_details.get("subtype"),
                "rational": surface_details.get("rational", False),
                "u_degree": U.get("degree", -1),
                "u_cv_count": U.get("cv_count", -1),
                "v_degree": V.get("degree", -1),
                "v_cv_count": V.get("cv_count", -1),
            }

        details_payload.append(d)

    def run(cypher: str, params: Dict[str, Any]) -> None:
        session.run(cypher, params).consume()

    base_params = {"file_key": file_key, "file_path": str(file_path), "file_name": file_name}

    # root + main file node + summary + container roots
    run(CYPHER_ROOT_AND_CONTAINERS, base_params)

    # layers
    run(CYPHER_UPSERT_LAYERS, {**base_params, "layers": layers_payload})
    run(CYPHER_LINK_LAYER_ROOTS, {**base_params, "root_layer_ids": root_layer_ids})
    if edges:
        run(CYPHER_LINK_LAYER_CHILDREN, {**base_params, "edges": edges})

    # groups/materials
    run(CYPHER_UPSERT_GROUPS, {**base_params, "groups": groups})
    run(CYPHER_UPSERT_MATERIALS, {**base_params, "materials": materials})

    # objects + details + segments/points
    if objects_payload:
        run(CYPHER_UPSERT_OBJECTS, {**base_params, "objects": objects_payload})
    if details_payload:
        run(CYPHER_UPSERT_DETAILS, {**base_params, "details": details_payload})
    if segments_payload:
        run(CYPHER_UPSERT_SEGMENTS, {**base_params, "segments": segments_payload})
    if points_payload:
        run(CYPHER_UPSERT_POINTS, {**base_params, "points": points_payload})

    return file_name, len(objects_payload), len(details_payload)

# ------------------------- script ------------------------- #

def main() -> None:
    # Local directory input (NEW)
    json_dir = os.environ.get("OUTPUT_JSON_DIR")

    # Optional filter
    suffix_filter_raw = os.environ.get("JSON_SUFFIX_FILTER", ".json")
    suffixes = [s.strip().lower() for s in suffix_filter_raw.split(",") if s.strip()]

    if not json_dir:
        raise SystemExit("Missing JSON_INPUT_DIR in .env")

    if not os.path.isdir(json_dir):
        raise SystemExit(f"Invalid JSON_INPUT_DIR: {json_dir}")

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    pwd = os.environ.get("NEO4J_PASSWORD")

    if not (uri and user and pwd):
        raise SystemExit("Missing NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in .env")

    # -------- READ LOCAL FILES -------- #
    all_files = os.listdir(json_dir)

    json_files = [
        os.path.join(json_dir, f)
        for f in all_files
        if os.path.isfile(os.path.join(json_dir, f))
        and any(f.lower().endswith(suf) for suf in suffixes)
    ]

    if not json_files:
        raise SystemExit(f"No matching JSON files found in {json_dir}")

    print(f"Found {len(json_files)} JSON files in {json_dir}")

    driver = GraphDatabase.driver(uri, auth=(user, pwd))

    try:
        # Create constraints once
        with driver.session() as session:
            for c in CYPHER_CONSTRAINTS:
                session.run(c, {}).consume()

        ok = 0
        fail = 0

        # -------- INGEST LOCAL JSON FILES -------- #
        for file_path in sorted(json_files):
            name = os.path.basename(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = f.read()
                
                data = json.loads(data)

            except Exception as e:
                fail += 1
                print(f"[FAIL] read/parse JSON: {name} -> {e}")
                continue

            try:
                with driver.session() as session:
                    file_name, obj_n, det_n = ingest_one(session, data)

                ok += 1
                print(f"[OK] {file_name} | objects={obj_n} details={det_n}")

            except Exception as e:
                fail += 1
                print(f"[FAIL] ingest: {name} -> {e}")

        print(f"\nDone. Imported OK: {ok}, Failed: {fail}")

    finally:
        driver.close()

if __name__ == "__main__":
    main()
