import json
import re
from pathlib import Path

from sentence_transformers import SentenceTransformer


PAGE_HDR_RE = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.MULTILINE)
REF_TAG_RE = re.compile(r"<\|ref\|>.*?<\|/ref\|><\|det\|>\[\[.*?\]\]<\|/det\|>", re.DOTALL)


def load_config() -> dict:
    import os
    cfg_path = os.environ.get("PIPELINE_CONFIG")
    if not cfg_path:
        raise RuntimeError("PIPELINE_CONFIG env var not set (run via supervisor).")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def read_md(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    header, body = text.split("\n\n", 1)
    return json.loads(header), body


def split_pages(body: str):
    matches = list(PAGE_HDR_RE.finditer(body))
    pages = {}
    for i, m in enumerate(matches):
        p = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pages[p] = body[start:end]
    return pages


def clean_text(s: str) -> str:
    s = REF_TAG_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def chunk_words(text: str, size=220, overlap=40):
    words = text.split()
    out = []
    i = 0
    step = max(1, size - overlap)
    while i < len(words):
        chunk = " ".join(words[i:i + size]).strip()
        if chunk:
            out.append(chunk)
        i += step
    return out


def main():
    cfg = load_config()
    md_dir = Path(cfg["markdown_dir"])
    out_dir = Path(cfg["kg_out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "text_chunks.jsonl"

    embed_model = cfg.get("text_embed_model", "all-MiniLM-L6-v2")
    model = SentenceTransformer(embed_model)

    md_files = sorted(md_dir.glob("*_markdown.md"))
    if not md_files:
        raise FileNotFoundError(f"No markdown files found in: {md_dir}")

    CHUNK_WORDS = 220
    OVERLAP_WORDS = 40

    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for md_path in md_files:
            meta, body = read_md(md_path)
            doc_id = meta["document"]
            pdf_stem = meta.get("pdf_stem", Path(doc_id).stem)
            dpi = meta.get("dpi")

            pages = split_pages(body)

            for page_num in sorted(pages.keys()):
                page_text = clean_text(pages[page_num])
                chunks = chunk_words(page_text, CHUNK_WORDS, OVERLAP_WORDS)

                for j, chunk in enumerate(chunks, start=1):
                    chunk_id = f"{pdf_stem}_p{page_num:03d}_c{j:03d}"
                    emb = model.encode(chunk, normalize_embeddings=True).tolist()

                    rec = {
                        "doc_id": doc_id,
                        "pdf_stem": pdf_stem,
                        "page": page_num,
                        "chunk_id": chunk_id,
                        "chunk_text": chunk,
                        "embedding": emb,
                        "dpi": dpi
                    }
                    f.write(json.dumps(rec) + "\n")
                    written += 1

            print(f"Chunks built: {md_path.name}")

    print(f" Wrote {written} chunks to: {out_path}")


if __name__ == "__main__":
    main()
