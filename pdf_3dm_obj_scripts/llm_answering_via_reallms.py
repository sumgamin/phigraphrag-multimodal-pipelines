import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.graphs import Neo4jGraph

load_dotenv()


SCHEMA_CONTEXT = """
You are an expert in generating Neo4j Cypher queries for a multi-format knowledge graph built from 3DM (Rhino CAD), OBJ (mesh files), PDF (documents), and other extensible file types.

---

1. GLOBAL STRUCTURE

- All data is anchored to a single root node:
  (:GlobalRoot {name: "RootNode"})

- Relationships:
  - (GlobalRoot)-[:HAS_3DM_ROOT]->(:Root3dmNode)
  - (GlobalRoot)-[:HAS_OBJ_ROOT]->(:ObjRoot)
  - (GlobalRoot)-[:HAS_PDF_ROOT]->(:PdfRoot)

- Each root connects to its respective file nodes

---

2. FILE NODES

- (:DMFile {file_key, name, path, kind: "3dm"})
- (:OBJFile {file_key, name, path, kind: "obj"})
- (:PDFFile {file_key, name, path, kind: "pdf"})

- Always anchor queries to file when possible

---

3. STRUCTURE PER FORMAT

3DM (Hierarchical CAD Graph)

- DMFile -> DMSummary
- DMSummary -> LayerRoot, GroupRoot, MaterialRoot, ObjectRoot
- Object -> CurveDetails, BrepDetails, MeshDetails, SurfaceDetails (conditional)

OBJ (Mesh-Centric Graph)

- OBJFile -> ObjObjectRoot -> ObjObject
- ObjObject -> ObjBoundingBox and mesh properties
- Focus on mesh geometry, bounding boxes, and volumes

PDF (Document Graph)

- PDFFile -> Document -> Page -> Text / Image
- Embeddings may exist for semantic retrieval
- Focus on textual and visual content

---

4. COMMON CONCEPTS

- file_key uniquely identifies file context
- Objects and entities are instances, not unique geometry
- Bounding boxes approximate spatial size
- Geometry and metadata are separated
- Not all nodes have detail subgraphs

---

5. QUERYING RULES

- Always anchor by file node when possible
- Follow explicit relationships and avoid cartesian joins
- Use correct format-specific traversal paths
- Use type flags such as is_mesh or is_curve where available
- Use bounding box properties for spatial calculations
- Do not assume all formats share the same structure

---

6. SEMANTIC GUIDELINES

- 3DM represents rich CAD hierarchy with multiple geometry types
- OBJ represents simpler, mesh-focused structures
- PDF represents document structure with semantic content
- Cross-format queries may compare geometry, metadata, or content

---

7. INFERENCE RULE

- If the question specifies a file type (3dm, obj, pdf), prioritize that subgraph
- If not, infer the most relevant format from the question

---

8. GOAL

- Generate correct, minimal, schema-aware Cypher queries
- Respect format-specific structures
- Avoid unnecessary expansions
- Return precise results

-----
Below are few examples for reference.

Example 1
User question: How many LineCurves are present in total across all 3dm files?
Cypher query:
MATCH (f:DMFile)
MATCH (f)-[:HAS_SUMMARY]->(:DMSummary)
      -[:HAS_OBJECTS]->(:ObjectRoot)
      -[:HAS_OBJECT]->(o:Object)
WHERE o.is_curve = true
  AND o.raw_type = "LineCurve"
RETURN count(o) AS total_linecurves;

--------------------------------------------------

Example 2
User question: What are the top 5 largest mesh objects in all OBJ files based on bounding box volume?
Cypher query:
MATCH (f:OBJFile)
MATCH (f)-[:HAS_OBJECT_ROOT]->(:ObjObjectRoot)
      -[:HAS_OBJECT]->(o:ObjObject)-[:HAS_BOUNDING_BOX]->(bb:ObjBoundingBox)
WHERE o.is_mesh = true
  AND bb.volume IS NOT NULL
RETURN
  f.name AS file_name,
  o.object_id AS object_id,
  bb.volume AS volume
ORDER BY volume DESC
LIMIT 5;

--------------------------------------------------

Example 3
User question: How many objects are present in each 3dm file?
Cypher query:
MATCH (f:DMFile)
MATCH (f)-[:HAS_SUMMARY]->(:DMSummary)
      -[:HAS_OBJECTS]->(:ObjectRoot)
      -[:HAS_OBJECT]->(o:Object)
RETURN f.name AS file_name, count(o) AS object_count
ORDER BY object_count DESC;

--------------------------------------------------

Example 4
User question: What are the longest LineCurves based on curve length?
Cypher query:
MATCH (f:DMFile)
MATCH (f)-[:HAS_SUMMARY]->(:DMSummary)
      -[:HAS_OBJECTS]->(:ObjectRoot)
      -[:HAS_OBJECT]->(o:Object)-[:HAS_CURVE_DETAILS]->(cd:CurveDetails)
WHERE o.is_curve = true
  AND cd.subtype = "LineCurve"
  AND cd.length IS NOT NULL
RETURN
  o.id AS object_id,
  cd.length AS length
ORDER BY length DESC
LIMIT 5;
"""


class CypherRetriever:
    def __init__(self):
        self.api_key = os.getenv("DIRECT_API_KEY")
        if not self.api_key:
            raise ValueError("Missing DIRECT_API_KEY in environment.")

        self.base_url = os.getenv("DIRECT_BASE_URL")
        if not self.base_url:
            raise ValueError("Missing DIRECT_BASE_URL in environment.")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        self.model = os.getenv("DIRECT_MODEL", "gpt-oss-120b")
        self.model_fallbacks = [
            "gpt-oss-120b",
            "llama-4-scout",
            "DeepSeek-R1",
            "code-llama",
        ]

        self.max_tokens = int(os.getenv("DIRECT_MAX_TOKENS", "1200"))
        self.temperature = float(os.getenv("DIRECT_TEMPERATURE", "0"))
        self.request_timeout = int(os.getenv("DIRECT_TIMEOUT", "90"))
        self.seed = int(os.getenv("SEED", "42"))

        self.CYPHER_SYSTEM_PROMPT = """
You are an expert Neo4j Cypher query generator for a multi-format knowledge graph.

You will be given:
1. schema_context: Manually written graph rules, traversal patterns, and examples.
2. neo4j_schema: The auto-refreshed Neo4j schema from the current database.
3. question: The user's natural-language question.

Rules:
- Output ONLY the Cypher query.
- Do not include markdown code fences.
- Do not include explanations.
- Do not include comments.
- Generate read-only Cypher only. Do not use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL dbms, or APOC writes.
- Use only labels, relationships, and properties supported by the schema_context and neo4j_schema.
- Always anchor to file nodes when possible.
- Avoid cartesian joins.
- Prefer minimal, schema-aware queries.
- Do not reuse variables or aliases in the same WITH clause.
- If a computed field depends on an alias, use a separate WITH clause.
""".strip()

        self.SUMMARY_SYSTEM_PROMPT = """
You are an expert assistant that summarizes Neo4j query results for a user.

You will be given:
1. question: The original user question.
2. cypher_query: The Cypher query that was executed.
3. query_results: The raw results returned from Neo4j.

Rules:
- Answer the user directly in natural language.
- Use only the provided query_results. Do not invent missing values.
- If the result is empty, say that no matching data was found.
- If the result is a count, state the count clearly.
- If the result is a ranked/list output, summarize the key rows clearly.
- Keep the answer concise and easy to read.
- Do not include markdown code fences unless the user specifically asks for code.
""".strip()

    def get_graph_schema(self) -> str:
        graph = Neo4jGraph(
            url=os.getenv("NEO4J_URI"),
            username=os.getenv("NEO4J_USER"),
            password=os.getenv("NEO4J_PASSWORD"),
        )
        graph.refresh_schema()
        return graph.schema

    def _create_cypher_request(self, model_name: str, question: str, neo4j_schema: str) -> dict:
        url = f"{self.base_url.rstrip('/')}/chat/completions"

        body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": self.CYPHER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Generate a Neo4j Cypher query for the user's question.",
                            "schema_context": SCHEMA_CONTEXT,
                            "neo4j_schema": neo4j_schema,
                            "question": question,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": self.seed,
        }

        resp = requests.post(
            url,
            headers=self.headers,
            json=body,
            timeout=self.request_timeout,
        )

        if resp.status_code == 429:
            raise RuntimeError(f"429 rate limit: {resp.text}")

        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        return resp.json()

    def _extract_content(self, response_json: dict) -> str:
        try:
            content = response_json["choices"][0]["message"]["content"]
            return self.clean_cypher(content)
        except Exception as e:
            raise RuntimeError(
                f"Could not parse model response: {e}\nResponse: {response_json}"
            )

    @staticmethod
    def clean_cypher(text: str) -> str:
        text = (text or "").strip()

        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("cypher"):
                text = text[6:].strip()

        return text.strip()

    @staticmethod
    def validate_read_only_cypher(query: str) -> None:
        blocked = [
            "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE",
            "DROP", "LOAD CSV", "CALL DBMS", "APOC.CREATE", "APOC.MERGE",
            "APOC.PERIODIC", "APOC.LOAD.JSON", "APOC.LOAD.CSV"
        ]
        upper_query = query.upper()
        for keyword in blocked:
            if keyword in upper_query:
                raise ValueError(f"Unsafe generated Cypher contains blocked keyword: {keyword}")

    def generate_cypher(self, question: str, neo4j_schema: str) -> str:
        models_to_try = [self.model] + [
            m for m in self.model_fallbacks if m != self.model
        ]

        tried = []
        last_error = None

        for model_name in models_to_try:
            tried.append(model_name)
            try:
                print(f"Trying model: {model_name}")
                response_json = self._create_cypher_request(model_name, question, neo4j_schema)
                query = self._extract_content(response_json)
                self.validate_read_only_cypher(query)
                return query
            except Exception as e:
                last_error = e
                print(f"Model failed: {model_name} -> {e}")

        raise RuntimeError(
            f"All models failed. Tried: {tried}. Last error: {last_error}"
        )


    def _create_summary_request(
        self,
        model_name: str,
        question: str,
        cypher_query: str,
        query_results,
    ) -> dict:
        url = f"{self.base_url.rstrip('/')}/chat/completions"

        body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": self.SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Summarize the Neo4j query results for the user.",
                            "question": question,
                            "cypher_query": cypher_query,
                            "query_results": query_results,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": self.seed,
        }

        resp = requests.post(
            url,
            headers=self.headers,
            json=body,
            timeout=self.request_timeout,
        )

        if resp.status_code == 429:
            raise RuntimeError(f"429 rate limit: {resp.text}")

        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        return resp.json()

    def summarize_results(self, question: str, cypher_query: str, query_results) -> str:
        models_to_try = [self.model] + [
            m for m in self.model_fallbacks if m != self.model
        ]

        tried = []
        last_error = None

        for model_name in models_to_try:
            tried.append(model_name)
            try:
                print(f"Trying summarization model: {model_name}")
                response_json = self._create_summary_request(
                    model_name=model_name,
                    question=question,
                    cypher_query=cypher_query,
                    query_results=query_results,
                )
                return response_json["choices"][0]["message"]["content"].strip()
            except Exception as e:
                last_error = e
                print(f"Summarization model failed: {model_name} -> {e}")

        raise RuntimeError(
            f"All summarization models failed. Tried: {tried}. Last error: {last_error}"
        )

    def run_cypher(self, query: str):
        graph = Neo4jGraph(
            url=os.getenv("NEO4J_URI"),
            username=os.getenv("NEO4J_USER"),
            password=os.getenv("NEO4J_PASSWORD"),
        )
        return graph.query(query)

    @staticmethod
    def save_text(text: str, output_path: str):
        output_file = Path(output_path)
        if output_file.parent and str(output_file.parent) != ".":
            output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(text if text else "", encoding="utf-8")
        print(f"Saved to: {output_file}")

    @staticmethod
    def save_json(data, output_path: str):
        output_file = Path(output_path)
        if output_file.parent and str(output_file.parent) != ".":
            output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved to: {output_file}")


def main():
    # Give only the question here.
    # Neo4j credentials and API credentials come from .env.
    question = "What is the largest entity in volume across all of the 3dm files?"

    # Outputs are controlled in the script, not from .env.
    cypher_output_path = "generated_cypher.txt"
    answer_output_path = "final_answer.txt"
    combined_output_path = "cypher_and_answer.txt"

    retriever = CypherRetriever()

    # 1. Read the live Neo4j schema.
    neo4j_schema = retriever.get_graph_schema()

    # 2. Generate Cypher from the question.
    generated_cypher = retriever.generate_cypher(question, neo4j_schema)

    print("\n=== Generated Cypher ===")
    print(generated_cypher)
    retriever.save_text(generated_cypher, cypher_output_path)

    # 3. Execute the generated Cypher against Neo4j.
    query_results = retriever.run_cypher(generated_cypher)

    print("\n=== Raw Query Result ===")
    print(json.dumps(query_results, indent=2, ensure_ascii=False, default=str))

    # 4. Summarize the raw Neo4j results into a user-facing answer.
    final_answer = retriever.summarize_results(
        question=question,
        cypher_query=generated_cypher,
        query_results=query_results,
    )

    print("\n=== Final Answer ===")
    print(final_answer)
    retriever.save_text(final_answer, answer_output_path)

    # 5. Save both the generated Cypher and final answer together.
    combined_output = f"Question:\n{question}\n\nGenerated Cypher:\n{generated_cypher}\n\nFinal Answer:\n{final_answer}\n"
    retriever.save_text(combined_output, combined_output_path)


if __name__ == "__main__":
    main()
