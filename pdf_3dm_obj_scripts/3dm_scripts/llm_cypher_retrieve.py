import os
from dotenv import load_dotenv

from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain_community.graphs import Neo4jGraph
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.globals import set_debug
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

- DMFile → DMSummary  
- DMSummary → LayerRoot, GroupRoot, MaterialRoot, ObjectRoot  
- Object → CurveDetails, BrepDetails, MeshDetails, SurfaceDetails (conditional)

OBJ (Mesh-Centric Graph)

- OBJFile → ObjObjectRoot → ObjObject  
- ObjObject → ObjBoundingBox and mesh properties  
- Focus on mesh geometry, bounding boxes, and volumes

PDF (Document Graph)

- PDFFile → Document → Page → Text / Image  
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

set_debug(True)

def escape_curly_braces(text: str) -> str:
    """Escape { } so PromptTemplate doesn't treat them as variables."""
    return text.replace("{", "{{").replace("}", "}}")

custom_template = PromptTemplate(
    input_variables=["schema", "question"],
    template=f"""
# CONTEXT
{escape_curly_braces(SCHEMA_CONTEXT)}

# Neo4j schema (auto-refreshed)
{{schema}}

# Question
{{question}}

Return ONLY the Cypher query. No markdown, no explanation.

""".strip()
)

def write_generated_cypher(query: str, output_path: str = "generated_cypher.txt"):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(query if query else "")



def main():
    # Connect to Neo4j
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI"),
        username=os.getenv("NEO4J_USER"),
        password=os.getenv("NEO4J_PASSWORD"),
    )

    # Refresh schema so the LLM knows labels/relationships
    graph.refresh_schema()

    # LLM
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
        temperature=0,
    )

    # GraphCypherQAChain
    chain = GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        cypher_prompt=custom_template,
        return_intermediate_steps=True,
        allow_dangerous_requests=True,  # rely on DB read-only permissions
    )

    # Ask a question
    question = "What do you know about MONTESSORI SCHOOL DESIGN"
    result = chain.invoke({"query": question})

    

    # Extract generated Cypher
    generated_cypher = None
    if result.get("intermediate_steps"):
        generated_cypher = result["intermediate_steps"][0].get("query")

    # Print generated Cypher
    print("\n=== Generated Cypher ===")
    print(generated_cypher)

    # Save generated Cypher
    write_generated_cypher(generated_cypher)

    print("\n=== Final Answer ===")
    print(result.get("result"))



if __name__ == "__main__":
    main()
