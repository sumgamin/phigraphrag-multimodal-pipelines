import os
import re
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
from neo4j import GraphDatabase
import json

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
JSON_INPUT_DIR = os.getenv("OUTPUT_JSON_DIR")


def slugify_file_key(filename: str):
    name = filename.rsplit(".", 1)[0]
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    name = name.strip("_").lower()
    return name



def create_constraints(tx):
    queries = [
        """
        CREATE CONSTRAINT obj_file_key IF NOT EXISTS
        FOR (f:OBJFile)
        REQUIRE f.file_key IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_summary_key IF NOT EXISTS
        FOR (s:ObjSummary)
        REQUIRE s.file_key IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_object_uid IF NOT EXISTS
        FOR (o:ObjObject)
        REQUIRE o.object_uid IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_mesh_details_uid IF NOT EXISTS
        FOR (m:ObjMeshDetails)
        REQUIRE m.object_uid IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_bbox_uid IF NOT EXISTS
        FOR (b:ObjBoundingBox)
        REQUIRE b.object_uid IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_layer_key IF NOT EXISTS
        FOR (l:ObjLayer)
        REQUIRE l.layer_key IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_material_key IF NOT EXISTS
        FOR (m:ObjMaterial)
        REQUIRE m.material_key IS UNIQUE
        """,
        """
        CREATE CONSTRAINT obj_group_key IF NOT EXISTS
        FOR (g:ObjObjectGroup)
        REQUIRE g.group_key IS UNIQUE
        """,
        """
        CREATE CONSTRAINT geometry_concept_name IF NOT EXISTS
        FOR (c:GeometryConcept)
        REQUIRE c.name IS UNIQUE
        """
    ]

    for query in queries:
        tx.run(query)


def create_file_structure(tx, file_key, file_name, source_url, totals):
    tx.run(
        """
        MERGE (global:GlobalRoot {id: "global_root"})
        SET global.name = "global_root"

        MERGE (root:ObjRoot {name: "ObjRoot"})

        MERGE (global)-[:HAS_OBJ_FILES]->(root)

        MERGE (file:OBJFile {file_key: $file_key})
        SET file.name = $file_name,
            file.kind = "obj",
            file.source_format = "OBJ",
            file.parser_output_type = "json",
            file.source_url = $source_url

        MERGE (root)-[:HAS_OBJ_FILE]->(file)

        MERGE (summary:ObjSummary {file_key: $file_key})
        SET summary.mesh_count = $mesh_count,
            summary.total_vertices = $total_vertices,
            summary.total_polygons = $total_polygons,
            summary.avg_vertices_per_mesh =
                CASE WHEN $mesh_count > 0
                THEN toFloat($total_vertices) / $mesh_count
                ELSE 0 END,
            summary.avg_polygons_per_mesh =
                CASE WHEN $mesh_count > 0
                THEN toFloat($total_polygons) / $mesh_count
                ELSE 0 END

        MERGE (file)-[:HAS_SUMMARY]->(summary)

        MERGE (objectRoot:ObjObjectRoot {file_key: $file_key})
        SET objectRoot.name = "Object Root"

        MERGE (layerRoot:ObjLayerRoot {file_key: $file_key})
        SET layerRoot.name = "Layer Root"

        MERGE (materialRoot:ObjMaterialRoot {file_key: $file_key})
        SET materialRoot.name = "Material Root"

        MERGE (file)-[:HAS_OBJECT_ROOT]->(objectRoot)
        MERGE (file)-[:HAS_LAYER_ROOT]->(layerRoot)
        MERGE (file)-[:HAS_MATERIAL_ROOT]->(materialRoot)
        """,
        file_key=file_key,
        file_name=file_name,
        source_url=source_url,
        mesh_count=totals.get("meshes", 0),
        total_vertices=totals.get("vertices", 0),
        total_polygons=totals.get("polygons", 0),
    )

def create_mesh_object(tx, file_key, mesh_obj):
    object_id = mesh_obj.get("ID", {}).get("value")
    object_index = mesh_obj.get("ID", {}).get("index")
    object_name = mesh_obj.get("Object name")
    layer_name = mesh_obj.get("Layer name", "Unknown Layer")

    render_material = mesh_obj.get("Render Material", {})
    material_name = render_material.get("name", "Unknown Material")

    geometry = mesh_obj.get("Geometry", {})
    mesh_data = geometry.get("Open double precision polygon mesh", {})
    bbox = mesh_data.get("Bounding box", {})

    bbox_min = bbox.get("min", [0, 0, 0])
    bbox_max = bbox.get("max", [0, 0, 0])

    object_uid = f"{file_key}:{object_id}"
    layer_key = f"{file_key}:{layer_name}"
    material_key = f"{file_key}:{material_name}"

    tx.run(
        """
        MATCH (objectRoot:ObjObjectRoot {file_key: $file_key})
        MATCH (layerRoot:ObjLayerRoot {file_key: $file_key})
        MATCH (materialRoot:ObjMaterialRoot {file_key: $file_key})

        MERGE (obj:ObjObject {object_uid: $object_uid})
        SET obj.file_key = $file_key,
            obj.object_id = $object_id,
            obj.object_index = $object_index,
            obj.object_name = $object_name,
            obj.raw_type = "mesh",
            obj.is_mesh = true,
            obj.layer_name = $layer_name,
            obj.material_name = $material_name

        MERGE (objectRoot)-[:HAS_OBJECT]->(obj)

        MERGE (details:ObjMeshDetails {object_uid: $object_uid})
        SET details.valid = $valid_mesh,
            details.mesh_type = "Open double precision polygon mesh",
            details.vertices = $vertices,
            details.faces = $faces,
            details.n_gons = $n_gons

        MERGE (obj)-[:HAS_MESH_DETAILS]->(details)

        MERGE (bb:ObjBoundingBox {object_uid: $object_uid})
        SET bb.min_x = $min_x,
            bb.min_y = $min_y,
            bb.min_z = $min_z,
            bb.max_x = $max_x,
            bb.max_y = $max_y,
            bb.max_z = $max_z,
            bb.width = $max_x - $min_x,
            bb.height = $max_y - $min_y,
            bb.depth = $max_z - $min_z,
            bb.volume = abs(
                ($max_x - $min_x) *
                ($max_y - $min_y) *
                ($max_z - $min_z)
            )

        MERGE (obj)-[:HAS_BOUNDING_BOX]->(bb)

        MERGE (layer:ObjLayer {layer_key: $layer_key})
        SET layer.file_key = $file_key,
            layer.name = $layer_name

        MERGE (layerRoot)-[:HAS_LAYER]->(layer)
        MERGE (obj)-[:ON_LAYER]->(layer)

        MERGE (mat:ObjMaterial {material_key: $material_key})
        SET mat.file_key = $file_key,
            mat.name = $material_name,
            mat.source = $material_source,
            mat.index = $material_index

        MERGE (materialRoot)-[:HAS_MATERIAL]->(mat)
        MERGE (obj)-[:USES_MATERIAL]->(mat)
        """,
        file_key=file_key,
        object_uid=object_uid,
        object_id=object_id,
        object_index=object_index,
        object_name=object_name,
        layer_name=layer_name,
        material_name=material_name,
        valid_mesh=geometry.get("Valid mesh.", False),
        vertices=mesh_data.get("vertices", 0),
        faces=mesh_data.get("faces", 0),
        n_gons=mesh_data.get("n_gons", 0),
        min_x=bbox_min[0],
        min_y=bbox_min[1],
        min_z=bbox_min[2],
        max_x=bbox_max[0],
        max_y=bbox_max[1],
        max_z=bbox_max[2],
        layer_key=layer_key,
        material_key=material_key,
        material_source=render_material.get("source"),
        material_index=render_material.get("index"),
    )


def create_object_groups(tx, file_key):
    tx.run(
        """
        MATCH (obj:ObjObject {file_key: $file_key})
        WITH obj.file_key AS file_key,
             obj.object_name AS object_name,
             collect(obj) AS objects
        WHERE size(objects) > 1

        MERGE (group:ObjObjectGroup {
            group_key: file_key + ":" + object_name
        })
        SET group.file_key = file_key,
            group.name = object_name,
            group.object_count = size(objects)

        WITH group, objects
        UNWIND objects AS obj
        MERGE (group)-[:HAS_OBJECT_PART]->(obj)
        """,
        file_key=file_key,
    )


def create_geometry_concept(tx, file_key):
    tx.run(
        """
        MERGE (concept:GeometryConcept {name: "Mesh"})
        SET concept.description = "Polygon mesh geometry from OBJ file"

        MATCH (obj:ObjObject {file_key: $file_key, is_mesh: true})
        MERGE (obj)-[:INSTANCE_OF_CONCEPT]->(concept)
        """,
        file_key=file_key,
    )


def load_one_json_file(session, file_path):
    file_name = os.path.basename(file_path)
    file_key = slugify_file_key(file_name)

    print(f"Loading: {file_name}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    totals = data.get("Mesh totals", {})
    meshes = data.get("meshes", [])

    session.execute_write(
        create_file_structure,
        file_key,
        file_name,
        file_path,   # using local path instead of URL
        totals
    )

    for mesh_obj in meshes:
        session.execute_write(create_mesh_object, file_key, mesh_obj)

    session.execute_write(create_object_groups, file_key)
    session.execute_write(create_geometry_concept, file_key)

    print(f"Loaded {len(meshes)} meshes from {file_name}")


def main():
    if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, JSON_INPUT_DIR]):
        raise ValueError("Missing required values in .env file.")

    if not os.path.isdir(JSON_INPUT_DIR):
        raise ValueError(f"Invalid JSON_INPUT_DIR: {JSON_INPUT_DIR}")

    json_files = [
        os.path.join(JSON_INPUT_DIR, f)
        for f in os.listdir(JSON_INPUT_DIR)
        if f.lower().endswith(".json")
    ]

    if not json_files:
        print("No JSON files found in directory.")
        return

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD)
    )

    with driver.session() as session:
        session.execute_write(create_constraints)

        for file_path in sorted(json_files):
            try:
                load_one_json_file(session, file_path)
            except Exception as e:
                print(f"[FAIL] {file_path} -> {e}")

    driver.close()

    print("OBJ graph loading completed.")


if __name__ == "__main__":
    main()