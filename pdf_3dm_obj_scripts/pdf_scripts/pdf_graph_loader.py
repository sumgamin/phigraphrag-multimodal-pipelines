#!/usr/bin/env python3

import os
import sys
import json
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from dotenv import load_dotenv


load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

TEXT_FILE_PATH = os.getenv("TEXT_FILE_PATH")
IMAGES_FILE_PATH = os.getenv("IMAGES_FILE_PATH")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))


CYPHER_CONSTRAINTS_AND_INDEXES = [
    """
    CREATE CONSTRAINT global_root_id IF NOT EXISTS
    FOR (g:GlobalRoot) REQUIRE g.id IS UNIQUE;
    """,
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
    """
    CREATE INDEX chunk_doc_page IF NOT EXISTS
    FOR (c:TextChunk) ON (c.doc_id, c.page);
    """,
    """
    CREATE INDEX image_doc_page IF NOT EXISTS
    FOR (i:Image) ON (i.doc_id, i.page);
    """,
]


CYPHER_CREATE_ROOT = [
    """
    MERGE (g:GlobalRoot {id: "global_root"})
    SET g.name = "global_root";
    """,
    """
    MERGE (p:PdfRoot {id: "pdf_node"})
    SET p.name = "pdf_node";
    """,
    """
    MATCH (g:GlobalRoot {id: "global_root"})
    MATCH (p:PdfRoot {id: "pdf_node"})
    MERGE (g)-[:HAS_PDF_NODE]->(p);
    """,
]


CYPHER_INGEST_TEXT_BATCH = """
MATCH (root:PdfRoot {id: "pdf_node"})
UNWIND $rows AS value

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


CYPHER_INGEST_IMAGES_BATCH = """
MATCH (root:PdfRoot {id: "pdf_node"})
UNWIND $rows AS value

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


def read_jsonl_in_batches(file_path: str, batch_size: int):
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    batch = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                batch.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_number} in {file_path}: {e}")

            if len(batch) >= batch_size:
                yield batch
                batch = []

    if batch:
        yield batch


def ingest_jsonl_file(session, file_path: str, cypher: str, label: str):
    total = 0

    for batch in read_jsonl_in_batches(file_path, BATCH_SIZE):
        run_write(session, cypher, {"rows": batch})
        total += len(batch)
        print(f"{label}: ingested {total} rows")

    print(f"{label} completed. Total rows ingested: {total}")


def main() -> None:
    if not NEO4J_URI:
        print("ERROR: NEO4J_URI must be set in .env", file=sys.stderr)
        sys.exit(1)

    if not NEO4J_USER or not NEO4J_PASSWORD:
        print("ERROR: NEO4J_USER and NEO4J_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)

    if not TEXT_FILE_PATH:
        print("ERROR: TEXT_FILE_PATH must be set in .env", file=sys.stderr)
        sys.exit(1)

    if not IMAGES_FILE_PATH:
        print("ERROR: IMAGES_FILE_PATH must be set in .env", file=sys.stderr)
        sys.exit(1)

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD)
    )

    try:
        with driver.session() as session:
            for q in CYPHER_CONSTRAINTS_AND_INDEXES:
                run_write(session, q)
            print("Constraints and indexes created or already exist.")

            for q in CYPHER_CREATE_ROOT:
                run_write(session, q)
            print("GlobalRoot and PdfRoot ensured.")

            ingest_jsonl_file(
                session,
                TEXT_FILE_PATH,
                CYPHER_INGEST_TEXT_BATCH,
                "text_chunks"
            )

            ingest_jsonl_file(
                session,
                IMAGES_FILE_PATH,
                CYPHER_INGEST_IMAGES_BATCH,
                "images"
            )

            try:
                run_write(session, CYPHER_CREATE_VECTOR_INDEX_TEXT)
                run_write(session, CYPHER_CREATE_VECTOR_INDEX_CAPTION)
                run_write(session, CYPHER_CREATE_VECTOR_INDEX_IMAGE)
                print("Vector indexes created or already exist.")
            except Neo4jError as e:
                print("Vector indexes skipped.")
                print(str(e))

    finally:
        driver.close()

    print("PDF KG ingestion completed.")


if __name__ == "__main__":
    main()