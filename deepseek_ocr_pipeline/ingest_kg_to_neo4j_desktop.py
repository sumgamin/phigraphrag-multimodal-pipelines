#!/usr/bin/env python3
"""
ingest_kg_to_neo4j_desktop.py

- Reads Neo4j credentials from a .env file using python-dotenv
- Uses TEXT_URL and IMAGES_URL constants at the top of this file
- Creates constraints + non-vector indexes
- Creates root node (:PdfRoot {id:"pdf_node"})
- Ingests JSONL from GitHub raw URLs using APOC (apoc.load.json)
- Always attempts to create vector indexes (skips gracefully if unsupported)
- NO verification queries
- NO :NEXT reading order edges

Requirements:
  pip install neo4j python-dotenv

.env expected keys:
  NEO4J_URI=bolt://localhost:7687
  NEO4J_USER=neo4j
  NEO4J_PASSWORD=your_password
"""

import os
import sys
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from dotenv import load_dotenv

# =========================
# CONFIG: Put your URLs here
# =========================
TEXT_URL = "https://raw.githubusercontent.com/***/text_chunks.jsonl"
IMAGES_URL = "https://raw.githubusercontent.com/***/images.jsonl"


# =========================
# Cypher 
# =========================
CYPHER_CONSTRAINTS_AND_INDEXES = [
    # Constraints
    """
    CREATE CONSTRAINT pdf_root_id IF NOT EXISTS
    FOR (r:PdfRoot) REQUIRE r.id IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT document_doc_id IF NOT EXISTS
    FOR (d:Document) REQUIRE d.doc_id IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT page_key IF NOT EXISTS
    FOR (p:Page) REQUIRE (p.doc_id, p.page) IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT chunk_chunk_id IF NOT EXISTS
    FOR (c:TextChunk) REQUIRE c.chunk_id IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT image_image_id IF NOT EXISTS
    FOR (i:Image) REQUIRE i.image_id IS UNIQUE;
    """,
    # Non-vector indexes
    """
    CREATE INDEX chunk_doc_page IF NOT EXISTS
    FOR (c:TextChunk) ON (c.doc_id, c.page);
    """,
    """
    CREATE INDEX image_doc_page IF NOT EXISTS
    FOR (i:Image) ON (i.doc_id, i.page);
    """,
]

CYPHER_CREATE_ROOT = """
MERGE (root:PdfRoot {id: "pdf_node"})
SET root.name = "pdf_node";
"""


CYPHER_INGEST_TEXT = """
WITH $url AS url
MATCH (root:PdfRoot {id: "pdf_node"})
CALL apoc.load.json(url) YIELD value
WITH root, value

MERGE (d:Document {doc_id: value.doc_id})
SET d.pdf_stem = coalesce(d.pdf_stem, value.pdf_stem)
MERGE (root)-[:HAS_DOCUMENT]->(d)

MERGE (p:Page {doc_id: value.doc_id, page: toInteger(value.page)})
MERGE (d)-[:HAS_PAGE]->(p)

MERGE (c:TextChunk {chunk_id: value.chunk_id})
SET
  c.doc_id     = value.doc_id,
  c.pdf_stem   = value.pdf_stem,
  c.page       = toInteger(value.page),
  c.chunk_text = value.chunk_text,
  c.embedding  = value.embedding

MERGE (p)-[:HAS_CHUNK]->(c);
"""

CYPHER_INGEST_IMAGES = """
WITH $url AS url
MATCH (root:PdfRoot {id: "pdf_node"})
CALL apoc.load.json(url) YIELD value
WITH root, value

MERGE (d:Document {doc_id: value.doc_id})
SET d.pdf_stem = coalesce(d.pdf_stem, value.pdf_stem)
MERGE (root)-[:HAS_DOCUMENT]->(d)

MERGE (p:Page {doc_id: value.doc_id, page: toInteger(value.page)})
MERGE (d)-[:HAS_PAGE]->(p)

MERGE (i:Image {image_id: value.image_id})
SET
  i.doc_id            = value.doc_id,
  i.pdf_stem          = value.pdf_stem,
  i.page              = toInteger(value.page),
  i.image_path        = value.image_path,
  i.caption           = value.caption,
  i.caption_embedding = value.caption_embedding,
  i.image_embedding   = value.image_embedding

MERGE (p)-[:HAS_IMAGE]->(i);
"""

CYPHER_CHECK_VECTOR_FUNCTIONS = """
SHOW FUNCTIONS YIELD name
WHERE name STARTS WITH "db.index.vector"
RETURN name
LIMIT 1;
"""

CYPHER_CREATE_VECTOR_INDEX_TEXT = """
CREATE VECTOR INDEX textchunk_embedding_index IF NOT EXISTS
FOR (c:TextChunk) ON (c.embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
  }
};
"""

CYPHER_CREATE_VECTOR_INDEX_CAPTION = """
CREATE VECTOR INDEX image_caption_embedding_index IF NOT EXISTS
FOR (i:Image) ON (i.caption_embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
  }
};
"""

CYPHER_CREATE_VECTOR_INDEX_IMAGE = """
CREATE VECTOR INDEX image_embedding_index IF NOT EXISTS
FOR (i:Image) ON (i.image_embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 512,
    `vector.similarity_function`: 'cosine'
  }
};
"""


def run_write(session, cypher: str, params=None) -> None:
    session.run(cypher, params or {}).consume()


def run_read(session, cypher: str, params=None):
    return list(session.run(cypher, params or {}))


def main() -> None:
    # Load .env into environment
    load_dotenv()

    neo4j_uri = os.getenv("NEO4J_URI")
    neo4j_user = os.getenv("NEO4J_USER")
    neo4j_password = os.getenv("NEO4J_PASSWORD")

    if not neo4j_user or not neo4j_password:
        print("ERROR: NEO4J_USER and NEO4J_PASSWORD must be set in your .env file.", file=sys.stderr)
        sys.exit(1)

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    try:
        with driver.session() as session:
            # 1) Constraints + non-vector indexes
            for q in CYPHER_CONSTRAINTS_AND_INDEXES:
                run_write(session, q)
            print(" Constraints and indexes created (or already exist).")

            # 2) Root node
            run_write(session, CYPHER_CREATE_ROOT)
            print("Root node :PdfRoot {id:'pdf_node'} ensured.")

            # 3) Ingest text chunks via APOC
            try:
                run_write(session, CYPHER_INGEST_TEXT, {"url": TEXT_URL})
                print("text_chunks.jsonl ingested successfully.")
            except Neo4jError as e:
                print("\nERROR during text ingestion.", file=sys.stderr)
                print("Common causes:", file=sys.stderr)
                print(" - APOC not enabled in Neo4j Desktop", file=sys.stderr)
                print(" - GitHub raw URL not reachable / redirects", file=sys.stderr)
                print("Neo4j error:\n", e, file=sys.stderr)
                sys.exit(1)

            # 4) Ingest images via APOC
            try:
                run_write(session, CYPHER_INGEST_IMAGES, {"url": IMAGES_URL})
                print("images.jsonl ingested successfully.")
            except Neo4jError as e:
                print("\nERROR during images ingestion.", file=sys.stderr)
                print("Common causes:", file=sys.stderr)
                print(" - APOC not enabled in Neo4j Desktop", file=sys.stderr)
                print(" - GitHub raw URL not reachable / redirects", file=sys.stderr)
                print("Neo4j error:\n", e, file=sys.stderr)
                sys.exit(1)

            # 5)attempt vector indexes
            try:
                has_vector = bool(run_read(session, CYPHER_CHECK_VECTOR_FUNCTIONS))
                if has_vector:
                    run_write(session, CYPHER_CREATE_VECTOR_INDEX_TEXT)
                    run_write(session, CYPHER_CREATE_VECTOR_INDEX_CAPTION)
                    run_write(session, CYPHER_CREATE_VECTOR_INDEX_IMAGE)
                    

            except Neo4jError:
                # If vector features are not supported by your Neo4j build, ignore.
                pass
            print(" Vector indexes created (or skipped if unsupported).")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
