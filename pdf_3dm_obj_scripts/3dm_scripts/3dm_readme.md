# 3DM → Knowledge Graph Pipeline

This pipeline parses Rhino `.3dm` files, extracts CAD geometry and metadata, loads the parsed data into Neo4j, and supports natural language querying using an LLM-generated Cypher query.

---

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Create or update the `.env` file:

```env
INPUT_3DM_DIR=3dm_inputs
OUTPUT_JSON_DIR=output_3dm_final_parsed

NEO4J_URI=...
NEO4J_USER=...
NEO4J_PASSWORD=...

OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-nano
```

---

## Input

Place all `.3dm` files inside the input folder configured in `.env`.

Example:

```text
3dm_inputs/
```

---

## Run 3DM Parser

Run:

```bash
python 3dm_parser_final.py
```

This script reads `.3dm` files from `INPUT_3DM_DIR`, extracts file structure and geometry information, and saves parsed JSON files.

It extracts:

- Layers
- Groups
- Materials
- Objects
- Bounding boxes
- Curve details
- Brep details
- Surface details
- Mesh details

Generated output:

```text
output_3dm_final_parsed/
```

---

## Load into Neo4j

Run:

```bash
python kg_creation_from_directory.py
```

This script reads parsed JSON files from `OUTPUT_JSON_DIR` and loads them into Neo4j.

It creates:

- 3DM file nodes
- Summary nodes
- Layer, group, material, and object roots
- Object nodes
- Geometry detail nodes
- Relationships between files, objects, layers, materials, and details

---

## Query the Graph with LLM

Run:

```bash
python llm_cypher_retrieve.py
```

This script connects to Neo4j and uses an OpenAI model through LangChain to convert a natural language question into a Cypher query.

It prints:

- Generated Cypher query
- Final answer from the graph
- The generated query is also saved to:

```text
generated_cypher.txt
```

---

## Scripts

1. **3dm_parser_final.py**  
   Parses `.3dm` files and converts geometry, layer, group, material, and object information into JSON.

2. **kg_creation_from_directory.py**  
   Loads parsed 3DM JSON files into Neo4j and creates the knowledge graph structure.

3. **llm_cypher_retrieve.py**  
   Uses LangChain and OpenAI to generate Cypher queries from natural language questions and retrieve answers from Neo4j.

---

## Summary

```text
3DM Files → Parsed JSON → Neo4j 3DM Knowledge Graph → Natural Language Querying
```