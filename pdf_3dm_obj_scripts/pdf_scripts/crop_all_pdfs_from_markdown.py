import json
import re
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image


IMAGE_REF_RE = re.compile(
    r"<\|ref\|>image<\|/ref\|><\|det\|>\[\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\]<\|/det\|>",
    re.IGNORECASE
)
PAGE_HDR_RE = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def load_config() -> dict:
    import os
    cfg_path = os.environ.get("PIPELINE_CONFIG")
    if not cfg_path:
        raise RuntimeError("PIPELINE_CONFIG env var not set (run via supervisor).")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def read_md_and_meta(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    header, body = text.split("\n\n", 1)
    return json.loads(header), body


def split_pages(md_body: str):
    matches = list(PAGE_HDR_RE.finditer(md_body))
    pages = {}
    for i, m in enumerate(matches):
        p = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_body)
        pages[p] = md_body[start:end]
    return pages


def render_pdf_page(pdf_path: Path, page_num_1based: int, dpi: int) -> Image.Image:
    imgs = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=page_num_1based,
        last_page=page_num_1based,
    )
    if not imgs:
        raise RuntimeError(f"Failed to render {pdf_path.name} page {page_num_1based}")
    return imgs[0].convert("RGB")


def denorm_999(b, W, H):
    x1, y1, x2, y2 = b
    return (int(x1 / 999 * W), int(y1 / 999 * H), int(x2 / 999 * W), int(y2 / 999 * H))


def crop_one_pdf(pdf_path: Path, md_path: Path, crops_dir: Path, pad: int):
    meta, body = read_md_and_meta(md_path)
    dpi = int(meta.get("dpi", 200))
    pdf_stem = meta.get("pdf_stem", pdf_path.stem)

    out_dir = crops_dir / pdf_stem
    out_dir.mkdir(parents=True, exist_ok=True)

    page_sections = split_pages(body)
    total_saved = 0

    for page_num in sorted(page_sections.keys()):
        section = page_sections[page_num]

        bboxes_0_999 = []
        for m in IMAGE_REF_RE.finditer(section):
            x1, y1, x2, y2 = map(int, m.groups())
            if x2 < x1: x1, x2 = x2, x1
            if y2 < y1: y1, y2 = y2, y1
            bboxes_0_999.append((x1, y1, x2, y2))

        if not bboxes_0_999:
            continue

        page_img = render_pdf_page(pdf_path, page_num, dpi=dpi)
        W, H = page_img.size

        for img_idx, b in enumerate(bboxes_0_999, start=1):
            x1, y1, x2, y2 = denorm_999(b, W, H)

            # pad + clamp
            x1 -= pad; y1 -= pad; x2 += pad; y2 += pad
            x1 = max(0, min(W, x1)); x2 = max(0, min(W, x2))
            y1 = max(0, min(H, y1)); y2 = max(0, min(H, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            crop = page_img.crop((x1, y1, x2, y2))
            out_path = out_dir / f"page_{page_num:03d}_img_{img_idx:03d}.png"
            crop.save(out_path)
            total_saved += 1

        print(f"Cropped: {pdf_path.name} page {page_num} → {len(bboxes_0_999)} images")

    print(f" Crops saved for {pdf_path.name}: {total_saved} → {out_dir}")


def main():
    cfg = load_config()
    pdf_dir = Path(cfg["pdf_input_dir"])
    md_dir = Path(cfg["markdown_dir"])
    crops_dir = Path(cfg["crops_dir"])
    pad = int(cfg.get("pad", 20))

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs in: {pdf_dir}")

    for pdf_path in pdfs:
        md_path = md_dir / f"{pdf_path.stem}_markdown.md"
        if not md_path.exists():
            print(f" Missing markdown for {pdf_path.name}: {md_path}")
            continue
        crop_one_pdf(pdf_path, md_path, crops_dir=crops_dir, pad=pad)


if __name__ == "__main__":
    main()
