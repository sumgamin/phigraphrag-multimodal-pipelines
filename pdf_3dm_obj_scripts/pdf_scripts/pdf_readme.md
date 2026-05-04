# PDF → Knowledge Graph Pipeline

This pipeline processes PDFs, extracts text and images, generates embeddings, and prepares data for Neo4j ingestion.

---

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Ollama and pull the required Ollama models:

```bash
ollama pull deepseek-ocr
ollama pull qwen2.5vl:3b
```

---

## Models Used

- **OCR**: `deepseek-ocr` through Ollama
- **Image Captioning**: `qwen2.5vl:3b` through Ollama
- **Text Embeddings**: `all-MiniLM-L6-v2`
- **Image Embeddings**: `openai/clip-vit-base-patch32`

---

## Input

Place all PDF files inside:

```text
pdf_inputs/
```

---

## Run Pipeline

Run the supervisor script:

```bash
python supervisor_pipeline.py
```

This runs the scripts in the following order:

1. **ocr_folder_to_markdown.py**  
   Converts PDFs to markdown using OCR.

2. **crop_all_pdfs_from_markdown.py**  
   Extracts and crops detected images from PDFs.

3. **build_text_chunks_dir.py**  
   Splits markdown text into chunks and generates text embeddings.

4. **build_image_records_dir.py**  
   Generates image captions, caption embeddings, and image embeddings.

Generated outputs:

```text
kg_out/text_chunks.jsonl
kg_out/images.jsonl
```

---

## Load into Neo4j

Create or update the `.env` file:

```env
NEO4J_URI=...
NEO4J_USER=...
NEO4J_PASSWORD=...

TEXT_FILE_PATH=kg_out/text_chunks.jsonl
IMAGES_FILE_PATH=kg_out/images.jsonl
```

Then run:

```bash
python pdf_graph_loader.py
```

This loads the text chunks and image records into Neo4j and creates the required graph nodes, relationships, constraints, and vector indexes.

---

## Summary

```text
PDFs → OCR → Image Crops → Text & Image Embeddings → JSONL → Neo4j Knowledge Graph
```