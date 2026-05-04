# OBJ → Knowledge Graph Pipeline

This pipeline parses OBJ files, extracts mesh information, saves parsed JSON files, and loads the data into Neo4j as an OBJ knowledge graph.

---

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Input

Place all `.obj` files inside the input OBJ folder configured in the `.env` file.

Example:

```env
INPUT_OBJ_DIR=obj_inputs
OUTPUT_JSON_DIR=obj_parsed_json_outputs
DEFAULT_LAYER=Layer 01
```

---

## Run OBJ Parser

Run:

```bash
python obj_parser_final.py
```

This script reads all `.obj` files from `INPUT_OBJ_DIR`, parses mesh objects, and extracts:

- Object names
- Layer names
- Material information
- Vertex counts
- Face counts
- N-gon counts
- Bounding boxes

Generated output:

```text
obj_parsed_json_outputs/
```

Each OBJ file is saved as a parsed JSON file.

---

## Load into Neo4j

Update the `.env` file with Neo4j details:

```env
NEO4J_URI=...
NEO4J_USER=...
NEO4J_PASSWORD=...

OUTPUT_JSON_DIR=obj_parsed_json_outputs
```

Then run:

```bash
python obj_graph_loader.py
```

This loads the parsed OBJ JSON files into Neo4j and creates the required graph nodes, relationships, constraints, and geometry concept links.

---

## Scripts

1. **obj_parser_final.py**  
   Parses OBJ files and converts mesh information into structured JSON.

2. **obj_graph_loader.py**  
   Loads parsed OBJ JSON files into Neo4j as graph nodes and relationships.

---

## Summary

```text
OBJ Files → Parsed JSON → Neo4j OBJ Knowledge Graph
```