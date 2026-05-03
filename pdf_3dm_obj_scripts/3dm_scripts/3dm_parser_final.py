#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Reads a .3dm file and writes a JSON report of objects (layer, groups, material, and geometry details)


Batch mode (directory):
  - Set INPUT_3DM_DIR and OUTPUT_JSON_DIR at the top
  - For each *.3dm in INPUT_3DM_DIR, write: <input_stem>_parsed.json into OUTPUT_JSON_DIR
  - Skip processing if that output JSON already exists (and is non-empty)
"""
import os
import json
import math
from pathlib import Path
from typing import Any, Dict, List
import rhino3dm as r3d
from dotenv import load_dotenv

load_dotenv()

INPUT_3DM_DIR = os.getenv("INPUT_3DM_DIR")
OUTPUT_JSON_DIR = os.getenv("OUTPUT_JSON_DIR","output_3dm_final_parsed")



# -------------------------- helpers -------------------------- #

def pt3d(p) -> List[float]:
    return [float(p.X), float(p.Y), float(p.Z)]

# def get_domain(curve) -> List[float]:
#     """Return domain [T0, T1] for any rhino3dm object that has .Domain"""
#     try:
#         d = curve.Domain
#         return [float(d.T0), float(d.T1)]
#     except Exception:
#         try:
#             return [float(curve.Domain[0]), float(curve.Domain[1])]
#         except Exception:
#             return [0.0, 0.0]

def bbox_dict(geom) -> Dict[str, Any]:
    """Return axis-aligned bbox or an error object (never None)."""
    try:
        bb = geom.GetBoundingBox()
        return {"min": pt3d(bb.Min), "max": pt3d(bb.Max)}
    except Exception as e:
        return {
            "error": str(e),
            "geom_type": getattr(geom, "__class__", type(geom)).__name__
        }

def material_source_name(src: int) -> str:
    return {0: "from layer", 1: "from obj"}.get(int(src), "unknown")

def _is_empty_guid_str(g: str) -> bool:
    """
    Rhino uses an 'empty' GUID to mean 'no parent'. Handle common representations.
    """
    if not g:
        return True
    s = str(g).strip().lower()
    return s in ("", "00000000-0000-0000-0000-000000000000")


# --------------------- geometry-specific --------------------- #

def line_info(line: r3d.LineCurve) -> Dict[str, Any]:
    start = line.PointAtStart
    end = line.PointAtEnd
    length = math.sqrt(
        (end.X - start.X) ** 2 +
        (end.Y - start.Y) ** 2 +
        (end.Z - start.Z) ** 2
    )
    return {
        "subtype": "LineCurve",
        "start": pt3d(start),
        "end": pt3d(end),
        #"domain": get_domain(line),
        "length": float(length),
    }

def polylinecurve_info(plc: r3d.PolylineCurve) -> Dict[str, Any]:
    """Extract vertices from PolylineCurve using ToPolyline() or TryGetPolyline."""
    pts = []
    poly = None

    if hasattr(plc, "ToPolyline"):
        try:
            poly = plc.ToPolyline()
        except Exception:
            poly = None

    if poly is None and hasattr(plc, "TryGetPolyline"):
        try:
            ok, poly = plc.TryGetPolyline()
            if not ok:
                poly = None
        except Exception:
            poly = None

    if poly is not None:
        pts = [pt3d(poly[i]) for i in range(len(poly))]
    else:
        d = plc.Domain
        t0, t1 = getattr(d, "T0", 0.0), getattr(d, "T1", 1.0)
        samples = 20
        pts = [pt3d(plc.PointAt(t0 + (t1 - t0) * (i / samples))) for i in range(samples + 1)]
        if plc.IsClosed and (not pts or pts[0] != pts[-1]):
            pts.append(pts[0])

    return {
        "subtype": "Closed polyline" if plc.IsClosed else "Open polyline",
        "point_count": len(pts),
        "points": pts,
        #"domain": get_domain(plc),
    }

def nurbscurve_info(curve) -> Dict[str, Any]:
    """Handle both Curve and NurbsCurve objects safely."""
    nc = curve if isinstance(curve, r3d.NurbsCurve) else curve.ToNurbsCurve()
    if not nc:
        return {"subtype": "Curve", "note": "Conversion to NURBS failed"}

    info: Dict[str, Any] = {
        "subtype": "NurbsCurve",
        "degree": int(nc.Degree),
        "rational": bool(nc.IsRational),
        "point_count": len(nc.Points),
    
    }

    if hasattr(nc, "Knots"):
        try:
            info["knot_count"] = len(nc.Knots)
        except Exception:
            pass
            


    return info

def polycurve_info(pc: r3d.PolyCurve) -> Dict[str, Any]:
    segs = []
    for i in range(pc.SegmentCount):
        seg = pc.SegmentCurve(i)
        try:
            segs.append(curve_info(seg))
        except Exception as e:
            segs.append({"subtype": seg.__class__.__name__, "error": str(e)})

    return {
        "subtype": "Closed polycurve" if pc.IsClosed else "Open polycurve",
        "segment_count": int(pc.SegmentCount),
        "segments": segs,
      
    }

def brep_info(b: r3d.Brep) -> Dict[str, Any]:
    return {
        "subtype": "closed solid polysurface" if b.IsSolid else "open polysurface",
        "faces": len(b.Faces),
        "edges": len(b.Edges),
        "vertices": len(b.Vertices),
    }

def surface_info(s: r3d.Surface) -> Dict[str, Any]:
    ns = s.ToNurbsSurface()
    if not ns:
        out: Dict[str, Any] = {"subtype": "Surface"}
    else:
        out = {
            "subtype": "NURBS surface",
            "rational": bool(ns.IsRational),
            "U": {"degree": int(ns.Degree(0)), "cv_count": int(ns.Points.CountU)},
            "V": {"degree": int(ns.Degree(1)), "cv_count": int(ns.Points.CountV)},
        }
    return out

def mesh_info(m: r3d.Mesh) -> Dict[str, Any]:
    return {
        "subtype": "Mesh",
        "vertices": len(m.Vertices),
        "faces": len(m.Faces),
    }


# --------------------- curve dispatcher (ORDER MATTERS) --------------------- #

def curve_info(c: r3d.Curve) -> Dict[str, Any]:
    if isinstance(c, r3d.LineCurve):
        return line_info(c)

    if isinstance(c, r3d.PolylineCurve):
        return polylinecurve_info(c)

    if isinstance(c, r3d.PolyCurve):
        return polycurve_info(c)

    if isinstance(c, r3d.NurbsCurve):
        return nurbscurve_info(c)

    if hasattr(c, "ToNurbsCurve"):
        return nurbscurve_info(c)

    return {"subtype": c.__class__.__name__} #"domain": get_domain(c)}


# -------------------------- geometry multi-extractor -------------------------- #

def collect_geometry_details(obj: r3d.File3dmObject) -> Dict[str, Any]:
    g = obj.Geometry
    out: Dict[str, Any] = {"raw_type": g.__class__.__name__}

    # record what the object "is" according to your checks
    flags = {
        "is_curve": isinstance(g, r3d.Curve),
        "is_brep": isinstance(g, r3d.Brep),
        "is_surface": isinstance(g, r3d.Surface),
        "is_mesh": isinstance(g, r3d.Mesh),
    }
    out["type_flags"] = flags

    out["bbox"] = bbox_dict(g)

    try:
        if flags["is_curve"]:
            out["curve_details"] = curve_info(g)
    except Exception as e:
        out["curve_details_error"] = str(e)

    try:
        if flags["is_brep"]:
            out["brep_details"] = brep_info(g)
    except Exception as e:
        out["brep_details_error"] = str(e)

    try:
        if flags["is_surface"]:
            out["surface_details"] = surface_info(g)
    except Exception as e:
        out["surface_details_error"] = str(e)

    try:
        if flags["is_mesh"]:
            out["mesh_details"] = mesh_info(g)
    except Exception as e:
        out["mesh_details_error"] = str(e)

    # optional but very useful: what detail blocks exist
    out["detail_keys"] = [k for k in out.keys() if k.endswith("_details")]

    return out



# -------------------------- object dump -------------------------- #

def dump_object(obj: r3d.File3dmObject, doc: r3d.File3dm) -> Dict[str, Any]:
    a = obj.Attributes

    layer_name = None
    if 0 <= a.LayerIndex < len(doc.Layers):
        layer_name = doc.Layers[a.LayerIndex].Name

    base: Dict[str, Any] = {
        "id": str(a.Id),
        "object_name": a.Name or "(not named)",
        "layer_name": layer_name or "Default",
        "render_material": {
            "source": material_source_name(getattr(a, "MaterialSource", -1)),
            "index": int(getattr(a, "MaterialIndex", -1)),
        },
    }

    groups = None

    if hasattr(a, "GetGroupList"):
        try:
            gl = list(a.GetGroupList())
            if len(gl) == 0:
                groups = "No group assigned"
            else:
                names = []
                for gi in gl:
                    if 0 <= gi < len(doc.Groups):
                        names.append(doc.Groups[gi].Name)
                groups = names if names else "No group assigned"
        except Exception as e:
            base["groups_error"] = str(e)
            groups = "No group assigned"   

    base["groups"] = groups


    base["geometry"] = collect_geometry_details(obj)
    return base


# -------------------------- layers (tree) + summary tables -------------------------- #

def _layer_dict(L: r3d.Layer, idx: int) -> Dict[str, Any]:
    """
    Layer dict includes:
      - hierarchy info: id, parent_id, fullpath
      - material info: material_index, render_material_index
    """
    layer_id = str(getattr(L, "Id", ""))
    parent_id = str(getattr(L, "ParentLayerId", ""))

    return {
        "index": idx,
        "name": L.Name,
        "id": layer_id,
        "parent_id": parent_id,
        "fullpath": getattr(L, "FullPath", None),
        "material_index": int(getattr(L, "MaterialIndex", -1)),
        "render_material_index": int(getattr(L, "RenderMaterialIndex", -1)),
        "children": [],
    }

def _build_layer_tree(doc: r3d.File3dm) -> List[Dict[str, Any]]:
    """
    Convert Rhino's flat layer table into a nested tree using ParentLayerId.
    """
    nodes: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}

    # Create nodes
    for i, L in enumerate(doc.Layers):
        nd = _layer_dict(L, i)
        nodes.append(nd)
        if nd["id"]:
            by_id[nd["id"]] = nd

    # Link children to parents
    roots: List[Dict[str, Any]] = []
    for nd in nodes:
        pid = nd.get("parent_id", "")
        if _is_empty_guid_str(pid) or pid not in by_id:
            roots.append(nd)
        else:
            by_id[pid]["children"].append(nd)

    # Keep ordering stable like Rhino's layer table (by index)
    def sort_children(n: Dict[str, Any]) -> None:
        n["children"].sort(key=lambda x: x.get("index", 10**9))
        for ch in n["children"]:
            sort_children(ch)

    roots.sort(key=lambda x: x.get("index", 10**9))
    for r in roots:
        sort_children(r)

    return roots

def summary_tables(doc: r3d.File3dm) -> Dict[str, Any]:
    # layers as a tree (to match Rhino UI sublayering)
    layers = _build_layer_tree(doc)

    groups = [{"index": i, "name": G.Name} for i, G in enumerate(doc.Groups)]
    mats = [{"index": i, "name": M.Name} for i, M in enumerate(doc.Materials)]
    return {"layers": layers, "groups": groups, "materials": mats}


# -------------------------- parse -------------------------- #

def parse_3dm(path: str) -> Dict[str, Any]:
    doc = r3d.File3dm.Read(path)
    if not doc:
        raise RuntimeError(f"Cannot read file: {path}")

    out: Dict[str, Any] = {"file": path, "summary": summary_tables(doc), "objects": []}
    for o in doc.Objects:
        out["objects"].append(dump_object(o, doc))
    return out


# -------------------------- main -------------------------- #

def main():
    in_dir = Path(INPUT_3DM_DIR)
    out_dir = Path(OUTPUT_JSON_DIR)

    out_dir.mkdir(parents=True, exist_ok=True)

    three_dm_files = sorted(in_dir.glob("*.3dm"))
    if not three_dm_files:
        print(f"No .3dm files found in: {in_dir}")
        return

    processed = 0
    skipped = 0
    failed = 0

    for three_dm_path in three_dm_files:
        output_json_path = out_dir / f"{three_dm_path.stem}_parsed.json"

        # Skip if already processed (exists + non-empty)
        if output_json_path.exists():
            try:
                if output_json_path.stat().st_size > 0:
                    skipped += 1
                    continue
            except Exception:
                pass

        try:
            data = parse_3dm(str(three_dm_path))
            with open(output_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
            processed += 1
            print(f"Processed: {three_dm_path.name} -> {output_json_path.name}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {three_dm_path.name}: {e}")

    print(f"Done. processed={processed}, skipped={skipped}, failed={failed}, output_dir={out_dir}")


if __name__ == "__main__":
    main()
