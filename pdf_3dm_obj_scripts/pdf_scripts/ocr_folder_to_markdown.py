import json
import base64
from pathlib import Path
from io import BytesIO

import ollama
from pdf2image import convert_from_path
from PIL import Image


def load_config() -> dict:
    import os
    cfg_path = os.environ.get("PIPELINE_CONFIG")
    if not cfg_path:
        raise RuntimeError("PIPELINE_CONFIG env var not set (run via supervisor).")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def pil_to_base64_jpeg(img: Image.Image, quality: int = 90) -> str:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def page_image_to_markdown(page_img: Image.Image, model: str) -> str:
    base64_image = pil_to_base64_jpeg(page_img)
    prompt = "<image>\n<|grounding|>Convert the document to markdown."
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": [base64_image]}],
    )
    return response["message"]["content"]


def pdf_to_markdown(pdf_path: Path, markdown_dir: Path, dpi: int, model: str) -> Path:
    pages = convert_from_path(str(pdf_path), dpi=dpi)
    if not pages:
        raise RuntimeError(f"No pages rendered: {pdf_path.name}")

    first_W, first_H = pages[0].size
    page_size_consistent = all(p.size == (first_W, first_H) for p in pages)

    doc_meta = {
        "document": pdf_path.name,
        "pdf_stem": pdf_path.stem,
        "width": first_W,
        "height": first_H,
        "dpi": dpi,
        "page_count": len(pages),
        "page_size_consistent": page_size_consistent,
    }

    combined_md = []
    combined_md.append(json.dumps(doc_meta, indent=2))
    combined_md.append("\n\n")
    combined_md.append(f"# {pdf_path.name}\n")

    for i, page_img in enumerate(pages, start=1):
        W, H = page_img.size
        md = page_image_to_markdown(page_img, model=model)
        combined_md.append(f"\n## Page {i}\n")
        combined_md.append(f"<!-- image_size: width={W}, height={H} -->\n\n")
        combined_md.append(f"{md}\n")
        print(f"Processed OCR: {pdf_path.name} page {i}/{len(pages)}")

    markdown_dir.mkdir(parents=True, exist_ok=True)
    md_path = markdown_dir / f"{pdf_path.stem}_markdown.md"
    md_path.write_text("".join(combined_md), encoding="utf-8")
    print(" Saved markdown:", md_path)
    return md_path


def main():
    cfg = load_config()
    pdf_dir = Path(cfg["pdf_input_dir"])
    markdown_dir = Path(cfg["markdown_dir"])
    dpi = int(cfg.get("dpi", 200))
    model = cfg.get("ocr_model", "deepseek-ocr")

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs in: {pdf_dir}")

    for pdf_path in pdfs:
        pdf_to_markdown(pdf_path, markdown_dir, dpi=dpi, model=model)


if __name__ == "__main__":
    main()
