#!/usr/bin/env python3
# stp_param_parse_refactored.py — Export Rhino-like STEP properties → JSON (pythonocc-core 7.x)
#
# NOTE:
#   - Output JSON is always written as neat, indented JSON (indent=2).
#
# Usage:
#   python stp_param_parse_refactored.py
#   python stp_param_parse_refactored.py --pretty   # kept for backwards compatibility


# =========================
# USER CONFIGURATION
# =========================
STEP_INPUT_DIR = "./stp_input_files"        
OUTPUT_JSON_DIR = "./output_json_files"      
# =========================

import argparse
import json
import math
import os
import statistics as stats
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import (
    GeomAbs_BSplineCurve,
    GeomAbs_Circle,
    GeomAbs_Ellipse,
    GeomAbs_Line,
)
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Extend.TopologyUtils import TopologyExplorer

DUP_TOL = 1e-6  # meters — tolerance to count duplicate adjacent control points
TWOPI = 2.0 * math.pi


# --------------------------- small value helpers ---------------------------

class GeoUtils:
    @staticmethod
    def pnt_to_xyz(p) -> List[float]:
        return [p.X(), p.Y(), p.Z()]

    @staticmethod
    def is_full_circle(u0: float, u1: float, tol: float = 1e-5) -> bool:
        return abs(abs(u1 - u0) - TWOPI) < tol

    @staticmethod
    def tol_stats(values: List[float]) -> Dict[str, float]:
        if not values:
            return {"min": 0.0, "max": 0.0, "median": 0.0, "average": 0.0}
        return {
            "min": float(min(values)),
            "max": float(max(values)),
            "median": float(stats.median(values)),
            "average": float(sum(values) / len(values)),
        }


class BSplineUtils:
    @staticmethod
    def knot_type(bs) -> str:
        try:
            if bs.IsUniform():
                return "uniform"
            if bs.IsQuasiUniform():
                return "quasi-uniform"
            if bs.IsPiecewiseBezier():
                return "piecewise-bezier"
        except Exception:
            pass
        return "non-uniform"

    @staticmethod
    def duplicates(bs, tol: float = DUP_TOL) -> int:
        dup = 0
        prev = None
        for i in range(1, bs.NbPoles() + 1):
            pt = bs.Pole(i)
            if prev is not None and pt.Distance(prev) <= tol:
                dup += 1
            prev = pt
        return dup

    @staticmethod
    def is_clamped(bs) -> bool:
        deg = bs.Degree()
        n_knots = bs.NbKnots()
        try:
            # clamped means multiplicity at each end is degree+1 (or higher)
            return (bs.Multiplicity(1) >= deg + 1) and (bs.Multiplicity(n_knots) >= deg + 1)
        except Exception:
            return False

    @staticmethod
    def is_smooth(bs) -> bool:
        # assume smooth unless repeated knots are seen
        try:
            for i in range(2, bs.NbKnots()):
                if bs.Multiplicity(i) > 1:
                    return False
            return True
        except Exception:
            return True


# --------------------------- extraction / analysis ---------------------------

class CurveExtractor:
    """Extracts curve-like properties from STEP edges."""

    def edge_props(self, edge, index: int) -> Dict[str, Any]:
        """Describe a single Edge as a curve-like JSON object."""
        ad = BRepAdaptor_Curve(edge)
        u0, u1 = ad.FirstParameter(), ad.LastParameter()
        p0, p1 = ad.Value(u0), ad.Value(u1)

        out: Dict[str, Any] = {
            "index": index,
            "type": "curve",
            "start": GeoUtils.pnt_to_xyz(p0),
            "end": GeoUtils.pnt_to_xyz(p1),
        }

        t = ad.GetType()

        if t == GeomAbs_Circle:
            circ = ad.Circle()
            center = circ.Location()
            r = circ.Radius()
            if GeoUtils.is_full_circle(u0, u1):
                out.update({
                    "subtype": "circle",
                    "center": GeoUtils.pnt_to_xyz(center),
                    "radius": float(r),
                })
            else:
                out.update({
                    "subtype": "arc",
                    "center": GeoUtils.pnt_to_xyz(center),
                    "radius": float(r),
                    "angle_degrees": float(abs(u1 - u0) * 180.0 / math.pi),
                })
            return out

        if t == GeomAbs_Line:
            out.update({"subtype": "line"})
            return out

        if t == GeomAbs_BSplineCurve:
            bs = ad.BSpline()
            out.update({
                "subtype": "nurbs",
                "closed": bool(bs.IsClosed()),
                "degree": int(bs.Degree()),
                "rational": bool(bs.IsRational()),
                "control_points": int(bs.NbPoles()),
                "duplicate_control_points": int(BSplineUtils.duplicates(bs)),
                "knot_type": BSplineUtils.knot_type(bs),
                "domain": [float(bs.FirstParameter()), float(bs.LastParameter())],
                "clamped": bool(BSplineUtils.is_clamped(bs)),
                "periodic": bool(bs.IsPeriodic()),
                "smooth": bool(BSplineUtils.is_smooth(bs)),
            })
            return out

        # Fallback for other analytic curves
        subtype = "ellipse" if t == GeomAbs_Ellipse else "other"
        out.update({"subtype": subtype})
        return out


class SolidExtractor:
    """Extracts Rhino-like 'polysurface' properties from closed solids."""

    def solid_props(self, solid, index: int) -> Dict[str, Any]:
        topo_s = TopologyExplorer(solid)

        edges = list(topo_s.edges())
        vertices = list(topo_s.vertices())
        faces = list(topo_s.faces())

        # Manifold & seam detection via adjacency from TopologyExplorer
        manifold_edges = 0
        seam_edges_set = set()
        edge_tols: List[float] = []

        for e in edges:
            adj_faces = list(topo_s.faces_from_edge(e))
            if len(adj_faces) == 2:
                manifold_edges += 1
            # seam if the same edge is closed on any adjacent face (e.g. cylinder seam)
            for f in adj_faces:
                try:
                    if BRep_Tool.IsClosed(e, f):
                        seam_edges_set.add(hash(e))  # unique per TopoDS_Shape
                        break
                except Exception:
                    pass
            try:
                edge_tols.append(BRep_Tool.Tolerance(e))
            except Exception:
                pass

        vertex_tols: List[float] = []
        for v in vertices:
            try:
                vertex_tols.append(BRep_Tool.Tolerance(v))
            except Exception:
                pass

        return {
            "index": index,
            "type": "polysurface",
            "closed_solid": True,
            "face_count": int(len(faces)),
            "edge_tally": {
                "seam_edges": int(len(seam_edges_set)),
                "manifold_edges": int(manifold_edges),
                "total_edges": int(len(edges)),
            },
            "edge_tolerances": GeoUtils.tol_stats(edge_tols),
            "vertex_tolerances": GeoUtils.tol_stats(vertex_tols),
            "render_mesh_present": False,
            "analysis_mesh_present": False,
        }


class PointExtractor:
    """Find standalone points (vertices not used by edges)."""

    def find_free_points(self, shape) -> List[Dict[str, Any]]:
        topo = TopologyExplorer(shape)

        used_vertex_hashes = set()
        for e in topo.edges():
            for v in topo.vertices_from_edge(e):
                used_vertex_hashes.add(hash(v))

        free_pts: List[Dict[str, Any]] = []
        for v in topo.vertices():
            if hash(v) not in used_vertex_hashes:
                p = BRep_Tool.Pnt(v)
                free_pts.append({"type": "point", "position": GeoUtils.pnt_to_xyz(p)})
        return free_pts


# --------------------------- orchestration ---------------------------

@dataclass(frozen=True)
class ExportOptions:
    path: str
    json_out: Optional[str] = None
    pretty: bool = False  # kept for backwards compatibility


class StepShapeLoader:
    def load(self, path: str):
        reader = STEPControl_Reader()
        if reader.ReadFile(path) != IFSelect_RetDone:
            raise SystemExit(f"Failed to read STEP file: {path}")
        reader.TransferRoots()
        return reader.OneShape()


class StepPropertiesExporter:
    def __init__(
        self,
        loader: Optional[StepShapeLoader] = None,
        curve_extractor: Optional[CurveExtractor] = None,
        point_extractor: Optional[PointExtractor] = None,
        solid_extractor: Optional[SolidExtractor] = None,
    ) -> None:
        self._loader = loader or StepShapeLoader()
        self._curves = curve_extractor or CurveExtractor()
        self._points = point_extractor or PointExtractor()
        self._solids = solid_extractor or SolidExtractor()

    def export(self, opts: ExportOptions) -> Dict[str, Any]:
        shape = self._loader.load(opts.path)
        topo = TopologyExplorer(shape)

        # Curves/edges
        curves: List[Dict[str, Any]] = []
        for i, e in enumerate(topo.edges(), 1):
            try:
                curves.append(self._curves.edge_props(e, i))
            except Exception as ex:
                curves.append({"index": i, "type": "curve", "error": str(ex)})

        # Standalone points (not vertices used by edges)
        try:
            points = self._points.find_free_points(shape)
        except Exception as ex:
            points = [{"type": "point", "error": f"free-point-scan failed: {ex}"}]

        # Solids (“polysurfaces”)
        polysurfaces: List[Dict[str, Any]] = []
        for i, s in enumerate(topo.solids(), 1):
            try:
                polysurfaces.append(self._solids.solid_props(s, i))
            except Exception as ex:
                polysurfaces.append({"index": i, "type": "polysurface", "error": str(ex)})

        return {
            "file": opts.path,
            "summary": {
                "curve_count": len(curves),
                "point_count": len(points),
                "polysurface_count": len(polysurfaces),
            },
            "curves": curves,
            "points": points,
            "polysurfaces": polysurfaces,
        }


class JsonWriter:
    def write(self, doc: Dict[str, Any], out_path: str) -> None:
        # Always write neat indented JSON
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")


# --------------------------- CLI (kept minimal) ---------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export Rhino-like properties from STEP to JSON (directory batch).")
    ap.add_argument(
        "--pretty",
        action="store_true",
        help="Kept for compatibility (output is always indented in this refactor).",
    )
    return ap.parse_args()




def main() -> None:
    _ = parse_args()  # pretty flag, but not required

    in_dir = Path(STEP_INPUT_DIR)
    out_dir = Path(OUTPUT_JSON_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists() or not in_dir.is_dir():
        raise SystemExit(f"Input directory not found or not a directory: {in_dir}")

    step_files = sorted([*in_dir.glob("*.stp"), *in_dir.glob("*.step")])
    if not step_files:
        raise SystemExit(f"No .stp/.step files found in: {in_dir}")

    exporter = StepPropertiesExporter()
    writer = JsonWriter()

    processed = 0
    skipped = 0

    for step_path in step_files:
        out_path = out_dir / (step_path.stem + ".props_v2.json")

    
        if out_path.exists():
            skipped += 1
            print(f"Skipping (already exists) → {out_path}")
            continue

        opts = ExportOptions(path=str(step_path), json_out=str(out_path), pretty=False)
        doc = exporter.export(opts)
        writer.write(doc, str(out_path))
        processed += 1
        print(f"Wrote JSON → {out_path}")

    print(f"\nDone. New files processed: {processed}, skipped: {skipped}, output dir: {out_dir}")


if __name__ == "__main__":
    main()
