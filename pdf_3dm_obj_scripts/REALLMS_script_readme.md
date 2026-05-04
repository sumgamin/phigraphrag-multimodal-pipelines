# LLM Cypher Answering with IU REALLMS

This script uses IU REALLMS to generate Neo4j Cypher queries from natural language questions, execute them on a Neo4j knowledge graph, and summarize the results into a final answer.

---

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Request access to the IU REALLMS API using the IU Knowledge Base article:

```text
https://servicenow.iu.edu/kb?id=kb_article_view&sysparm_article=KB0027272
```

The REALLMS API provides access to large language models hosted by IU for research and academic workflows.

After getting access, place the required REALLMS API variables in the `.env` file.

---

## Environment Variables

Create or update the `.env` file:

```env
NEO4J_URI=...
NEO4J_USER=...
NEO4J_PASSWORD=...

DIRECT_API_KEY=...
DIRECT_BASE_URL=...
DIRECT_MODEL=...

DIRECT_MAX_TOKENS=1200
DIRECT_TEMPERATURE=0
DIRECT_TIMEOUT=90
SEED=42
```

---

## Models Used

The main model is configured using:

```env
DIRECT_MODEL=gpt-oss-120b
```

Fallback models included in the script:

```text
gpt-oss-120b
llama-4-scout
DeepSeek-R1
code-llama
```

---

## Schema Context

The script includes a `SCHEMA_CONTEXT` section that explains the structure of the Neo4j knowledge graph to the LLM.

It describes:

- The global graph structure
- 3DM, OBJ, and PDF subgraphs
- Important node labels and relationships
- Querying rules for each file format
- Example natural language questions and correct Cypher queries

This helps the model generate more accurate and schema-aware Cypher queries instead of guessing labels, relationships, or traversal paths.

If the Neo4j graph structure is modified, the `SCHEMA_CONTEXT` should also be updated. For example, if new node labels, relationships, properties, or file formats are added, they should be reflected in `SCHEMA_CONTEXT` so the LLM can generate correct Cypher queries.

---

## Run the Script

Update the question inside `llm_answering_via_reallms.py`:

```python
question = "What is the largest entity in volume across all of the 3dm files?"
```

Then run:

```bash
python llm_answering_via_reallms.py
```

---

## What the Script Does

1. Connects to Neo4j using credentials from `.env`.
2. Reads the live Neo4j schema.
3. Sends the user question, schema context, and Neo4j schema to IU REALLMS.
4. Generates a read-only Cypher query.
5. Validates the generated Cypher query for safety.
6. Executes the query on Neo4j.
7. Sends the raw query results back to IU REALLMS.
8. Generates a concise final answer.

---

## Outputs

The script saves the generated Cypher query, final answer, and combined output.

```text
generated_cypher.txt
final_answer.txt
cypher_and_answer.txt
```

---

## Summary

```text
Question → Schema Context + Neo4j Schema → IU REALLMS → Cypher Query → Neo4j Results → Final Answer
```