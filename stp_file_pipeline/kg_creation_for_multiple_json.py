"""
env file
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=


GITHUB_REPO_URL=
GITHUB_JSON_DIR=<containing the json parsed files>
GITHUB_BRANCH=main
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from neo4j import GraphDatabase
from dotenv import load_dotenv


GLOBAL_SUBTYPES = ["line", "circle", "arc", "nurbs", "other"]
SECTIONS = ["Curves", "Polysurfaces", "Points", "Summary"]


# -------------------- helpers --------------------

def safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def derive_file_name_from_path(file_path: str) -> str:
    # Similar intent as Cypher: strip dirs and .stp/.step extension
    base = re.sub(r"^.*[/\\]", "", file_path)
    return re.sub(r"\.(stp|step)$", "", base, flags=re.IGNORECASE)


def derive_owner_repo(repo_url: str) -> Tuple[str, str]:
    """
    Accepts:
      - https://github.com/OWNER/REPO
      - https://github.com/OWNER/REPO.git
    Returns (OWNER, REPO)
    """
    repo_url = repo_url.strip()
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not m:
        raise ValueError(f"Invalid GITHUB_REPO_URL: {repo_url}")
    return m.group(1), m.group(2)


def list_github_dir_files(owner: str, repo: str, folder: str, branch: str = "main") -> List[Dict[str, Any]]:
    """
    Uses GitHub Contents API to list files in a repo folder.
    Works for public repos without token.
    """
    folder = folder.strip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{folder}?ref={branch}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, list):
        raise RuntimeError(f"GitHub API did not return a directory listing for {folder}. Got: {type(data)}")

    return data


def fetch_json(url: str) -> Dict[str, Any]:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.json()


# -------------------- importer  --------------------

class Neo4jKGImporter:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def run(self, query: str, params: Optional[Dict[str, Any]] = None):
        with self.driver.session() as session:
            session.run(query, params or {}).consume()

    def ensure_global_root(self):
        """
        Creates a single GlobalRoot and links it to
        STP root and PDF root.
        Safe to run multiple times.
        """

        # Query 1: Create global root
        self.run(
            """
            MERGE (g:GlobalRoot {name: "Root Node"})
            RETURN g;
            """
        )

        # Query 2: Link to STP root and PDF root
        self.run(
            """
            MATCH (g:GlobalRoot {name:"Root Node"})
            MATCH (stp:STPRoot {name:"STP_Files"})
            MATCH (pdf:PdfRoot)
            MERGE (g)-[:HAS_STP_ROOT]->(stp)
            MERGE (g)-[:HAS_PDF_ROOT]->(pdf)
            RETURN g, stp, pdf;
            """
        )


    def ensure_root_and_subtypes(self):
        self.run("MERGE (:STPRoot {name: 'STP_Files'})")
        self.run(
            """
            UNWIND $subtypes AS st
            MERGE (:CurveSubtype {name: st})
            """,
            {"subtypes": GLOBAL_SUBTYPES},
        )

    def upsert_file_node(self, file_path: str, file_name: str, summary: Dict[str, Any]):
        self.run(
            """
            MERGE (root:STPRoot {name:'STP_Files'})
            MERGE (f:STPFile {path:$path})
            SET f.name = $name,
                f.curve_count = $curve_count,
                f.point_count = $point_count,
                f.polysurface_count = $polysurface_count
            MERGE (root)-[:HAS_FILE]->(f)
            """,
            {
                "path": file_path,
                "name": file_name,
                "curve_count": int(summary.get("curve_count", 0) or 0),
                "point_count": int(summary.get("point_count", 0) or 0),
                "polysurface_count": int(summary.get("polysurface_count", 0) or 0),
            },
        )

    def create_sections(self, file_path: str):
        self.run(
            """
            MATCH (f:STPFile {path:$path})
            UNWIND $sections AS secName
            MERGE (s:Section {key: f.path + '|' + secName})
            SET s.name = secName
            MERGE (f)-[:HAS_SECTION]->(s)
            """,
            {"path": file_path, "sections": SECTIONS},
        )

    def link_curves_section_to_global_subtypes(self, file_path: str):
        self.run(
            """
            MATCH (f:STPFile {path:$path})
            MATCH (sec:Section {key: f.path + '|Curves'})
            UNWIND $subtypes AS st
            MATCH (t:CurveSubtype {name: st})
            MERGE (sec)-[:HAS_SUBTYPE]->(t)
            """,
            {"path": file_path, "subtypes": GLOBAL_SUBTYPES},
        )

    def upsert_curves(self, file_path: str, curves: List[Dict[str, Any]]):
        # FULL property set 
        self.run(
            """
            MATCH (f:STPFile {path:$path})
            MATCH (sec:Section {key: f.path + '|Curves'})
            UNWIND $curves AS c
            WITH f, sec, c, coalesce(c.subtype, 'other') AS st
            MATCH (t:CurveSubtype {name: st})
            MERGE (cv:Curve {uid: f.path + ':curve:' + toString(c.index)})

            SET cv.index = c.index,
                cv.type = c.type,
                cv.subtype = st,
                cv.start = c.start,
                cv.end = c.end,
                cv.center = c.center,
                cv.radius = c.radius,
                cv.angle_degrees = c.angle_degrees,
                cv.closed = c.closed,
                cv.degree = c.degree,
                cv.rational = c.rational,
                cv.control_points = c.control_points,
                cv.duplicate_control_points = c.duplicate_control_points,
                cv.knot_type = c.knot_type,
                cv.domain = c.domain,
                cv.clamped = c.clamped,
                cv.periodic = c.periodic,
                cv.smooth = c.smooth

            MERGE (f)-[:HAS_CURVE]->(cv)
            MERGE (sec)-[:CONTAINS]->(cv)
            MERGE (cv)-[:IS_SUBTYPE]->(t)
            """,
            {"path": file_path, "curves": curves},
        )

    def upsert_polysurfaces(self, file_path: str, polysurfaces: List[Dict[str, Any]]):
        # Flatten nested maps (edge_tally, edge_tolerances, vertex_tolerances)
        flat_rows = []
        for p in polysurfaces:
            edge_tally = p.get("edge_tally") or {}
            edge_tol = p.get("edge_tolerances") or {}
            vertex_tol = p.get("vertex_tolerances") or {}
            flat_rows.append(
                {
                    "index": p.get("index"),
                    "type": p.get("type"),
                    "closed_solid": p.get("closed_solid"),
                    "face_count": p.get("face_count"),
                    "render_mesh_present": p.get("render_mesh_present"),
                    "analysis_mesh_present": p.get("analysis_mesh_present"),
                    "seam_edges": safe_get({"x": edge_tally}, ["x", "seam_edges"], None),
                    "manifold_edges": safe_get({"x": edge_tally}, ["x", "manifold_edges"], None),
                    "total_edges": safe_get({"x": edge_tally}, ["x", "total_edges"], None),
                    "edge_tol_min": safe_get({"x": edge_tol}, ["x", "min"], None),
                    "edge_tol_max": safe_get({"x": edge_tol}, ["x", "max"], None),
                    "edge_tol_median": safe_get({"x": edge_tol}, ["x", "median"], None),
                    "edge_tol_avg": safe_get({"x": edge_tol}, ["x", "average"], None),
                    "vertex_tol_min": safe_get({"x": vertex_tol}, ["x", "min"], None),
                    "vertex_tol_max": safe_get({"x": vertex_tol}, ["x", "max"], None),
                    "vertex_tol_median": safe_get({"x": vertex_tol}, ["x", "median"], None),
                    "vertex_tol_avg": safe_get({"x": vertex_tol}, ["x", "average"], None),
                }
            )

        self.run(
            """
            MATCH (f:STPFile {path:$path})
            MATCH (sec:Section {key: f.path + '|Polysurfaces'})
            UNWIND $polys AS p
            MERGE (ps:Polysurface {uid: f.path + ':poly:' + toString(p.index)})

            SET ps.index = p.index,
                ps.type = p.type,
                ps.closed_solid = p.closed_solid,
                ps.face_count = p.face_count,
                ps.render_mesh_present = p.render_mesh_present,
                ps.analysis_mesh_present = p.analysis_mesh_present,

                ps.seam_edges = p.seam_edges,
                ps.manifold_edges = p.manifold_edges,
                ps.total_edges = p.total_edges,

                ps.edge_tol_min = p.edge_tol_min,
                ps.edge_tol_max = p.edge_tol_max,
                ps.edge_tol_median = p.edge_tol_median,
                ps.edge_tol_avg = p.edge_tol_avg,

                ps.vertex_tol_min = p.vertex_tol_min,
                ps.vertex_tol_max = p.vertex_tol_max,
                ps.vertex_tol_median = p.vertex_tol_median,
                ps.vertex_tol_avg = p.vertex_tol_avg

            MERGE (f)-[:HAS_POLYSURFACE]->(ps)
            MERGE (sec)-[:CONTAINS]->(ps)
            """,
            {"path": file_path, "polys": flat_rows},
        )

    def upsert_points(self, file_path: str, points: List[Dict[str, Any]]):
        # Points are list index-based like the Cypher approach
        rows = []
        for i, p in enumerate(points):
            rows.append(
                {
                    "i": i,
                    "type": p.get("type"),
                    "position": p.get("position"),
                }
            )

        self.run(
            """
            MATCH (f:STPFile {path:$path})
            MATCH (sec:Section {key: f.path + '|Points'})
            UNWIND $pts AS p
            MERGE (pt:Point {uid: f.path + ':pt:' + toString(p.i)})
            SET pt.type = p.type,
                pt.position = p.position
            MERGE (f)-[:HAS_FREE_POINT]->(pt)
            MERGE (sec)-[:CONTAINS]->(pt)
            """,
            {"path": file_path, "pts": rows},
        )


# -------------------- main -------------------

def main():
    load_dotenv()

    # Neo4j
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")

    # GitHub source
    repo_url = os.getenv("GITHUB_REPO_URL")
    json_dir = os.getenv("GITHUB_JSON_DIR")
    branch = os.getenv("GITHUB_BRANCH", "main")

    # Optional: allow filtering by suffix list (defaults to .json)
    suffix_filter_raw = os.getenv("JSON_SUFFIX_FILTER", ".json,.props_v2.json")
    suffixes = [s.strip().lower() for s in suffix_filter_raw.split(",") if s.strip()]

    missing = [k for k, v in [
        ("NEO4J_URI", uri),
        ("NEO4J_USER", user),
        ("NEO4J_PASSWORD", password),
        ("GITHUB_REPO_URL", repo_url),
        ("GITHUB_JSON_DIR", json_dir),
    ] if not v]
    if missing:
        raise SystemExit(f"Missing environment variables in .env: {', '.join(missing)}")

    owner, repo = derive_owner_repo(repo_url)

    # List JSON files in the GitHub folder
    items = list_github_dir_files(owner, repo, json_dir, branch=branch)

    json_files = []
    for it in items:
        if it.get("type") != "file":
            continue
        name = (it.get("name") or "").lower()
        if any(name.endswith(suf) for suf in suffixes):
            if it.get("download_url"):
                json_files.append(it)

    if not json_files:
        raise SystemExit(f"No JSON files found in {repo_url}/{json_dir} (branch={branch}).")

    print(f"Found {len(json_files)} JSON files in {owner}/{repo}/{json_dir} (branch={branch})")

    importer = Neo4jKGImporter(uri, user, password)
    try:
        importer.ensure_root_and_subtypes()

        ok = 0
        fail = 0

        for it in sorted(json_files, key=lambda x: x.get("name", "")):
            url = it["download_url"]
            name = it.get("name", url)

            try:
                data = fetch_json(url)
            except Exception as e:
                fail += 1
                print(f"[FAIL] download/parse JSON: {name} -> {e}")
                continue

            file_path = data.get("file")
            if not file_path:
                fail += 1
                print(f"[FAIL] JSON missing top-level 'file': {name}")
                continue

            file_name = derive_file_name_from_path(file_path)
            summary = data.get("summary") or {}

            curves = data.get("curves") or []
            polysurfaces = data.get("polysurfaces") or []
            points = data.get("points") or []

            try:
                importer.upsert_file_node(file_path, file_name, summary)
                importer.create_sections(file_path)
                importer.link_curves_section_to_global_subtypes(file_path)

                importer.upsert_curves(file_path, curves)
                importer.upsert_polysurfaces(file_path, polysurfaces)
                importer.upsert_points(file_path, points)

                ok += 1
                print(f"[OK] {file_name} | curves={len(curves)} polysurfaces={len(polysurfaces)} points={len(points)}")
            except Exception as e:
                fail += 1
                print(f"[FAIL] ingest: {file_name} ({name}) -> {e}")
        
        importer.ensure_global_root()
        print(f"\nDone. Imported OK: {ok}, Failed: {fail}")

    finally:
        importer.close()


if __name__ == "__main__":
    main()
