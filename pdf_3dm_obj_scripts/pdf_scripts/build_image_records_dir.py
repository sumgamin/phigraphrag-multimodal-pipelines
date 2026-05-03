import re, json, base64
from pathlib import Path

import ollama
from PIL import Image
import torch
from sentence_transformers import SentenceTransformer
from transformers import CLIPProcessor, CLIPModel
from tqdm import tqdm


IMG_RE = re.compile(r"page_(\d+)_img_(\d+)\.png$", re.IGNORECASE)


def load_config() -> dict:
    import os
    cfg_path = os.environ.get("PIPELINE_CONFIG")
    if not cfg_path:
        raise RuntimeError("PIPELINE_CONFIG env var not set (run via supervisor).")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def b64_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def caption_image(path: Path, caption_model: str) -> str:
    prompt = "<image>\nWrite a concise caption (1-2 sentences)."
    resp = ollama.chat(
        model=caption_model,
        messages=[{"role": "user", "content": prompt, "images": [b64_image(path)]}],
    )
    return resp["message"]["content"].strip()


def clip_embed_image(clip_model, clip_processor, img: Image.Image, device: str):
    inputs = clip_processor(images=img, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        feats = clip_model.get_image_features(**inputs)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].detach().cpu().numpy().tolist()


def main():
    cfg = load_config()
    crops_dir = Path(cfg["crops_dir"])
    out_dir = Path(cfg["kg_out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "images.jsonl"

    caption_model = cfg.get("caption_model_ollama", "qwen2.5vl:3b")
    text_embed_model = cfg.get("text_embed_model", "all-MiniLM-L6-v2")
    clip_model_name = cfg.get("clip_model", "openai/clip-vit-base-patch32")

    device = "mps" if torch.backends.mps.is_available() else "cpu"

    text_embedder = SentenceTransformer(text_embed_model)
    clip_model = CLIPModel.from_pretrained(clip_model_name).to(device)
    clip_processor = CLIPProcessor.from_pretrained(clip_model_name)

    all_imgs = sorted(crops_dir.glob("*/*.png"))
    total = len(all_imgs)

    if total == 0:
        print(f"No images found under: {crops_dir} (expected */*.png)")
        return

    print(f"Found {total} images to process.\nDevice for CLIP: {device}\n")

    written = 0
    skipped = 0

    with out_path.open("w", encoding="utf-8") as f:
        for idx, img_path in enumerate(tqdm(all_imgs, desc="Processing images", unit="image"), start=1):
            m = IMG_RE.search(img_path.name)
            if not m:
                skipped += 1
                continue

            page_num = int(m.group(1))
            img_num = int(m.group(2))

            pdf_stem = img_path.parent.name
            image_id = f"{pdf_stem}_p{page_num:03d}_i{img_num:03d}"

            if idx == 1 or idx % 10 == 0 or idx == total:
                print(f"[{idx}/{total}] {img_path.name} (doc={pdf_stem}, page={page_num}, img={img_num})")

            cap = caption_image(img_path, caption_model=caption_model)
            cap_emb = text_embedder.encode(cap, normalize_embeddings=True).tolist()

            pil = Image.open(img_path).convert("RGB")
            img_emb = clip_embed_image(clip_model, clip_processor, pil, device=device)

            rec = {
                "pdf_stem": pdf_stem,
                "doc_id": f"{pdf_stem}.pdf",
                "page": page_num,
                "image_id": image_id,
                "image_path": str(img_path),
                "caption": cap,
                "caption_embedding": cap_emb,
                "image_embedding": img_emb
            }
            f.write(json.dumps(rec) + "\n")
            written += 1

    print("\n Done.")
    print(f"Written records: {written}")
    print(f"Skipped (filename mismatch): {skipped}")
    print("Wrote:", out_path)


if __name__ == "__main__":
    main()
