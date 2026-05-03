import json
import subprocess
import sys
import time
from pathlib import Path


# =============================================================================
# EDIT ONLY THIS SECTION
# =============================================================================

PROJECT_ROOT = Path(".")

PDF_INPUT_DIR   = PROJECT_ROOT / "pdf_inputs"
MARKDOWN_DIR    = PROJECT_ROOT / "ocr_outputs" / "md"
CROPS_DIR       = PROJECT_ROOT / "ocr_outputs" / "crops"
KG_OUT_DIR      = PROJECT_ROOT / "kg_out"

MODEL_OCR       = "deepseek-ocr"
DPI             = 200

PAD             = 20  # crop padding (pixels)

CAPTION_MODEL_OLLAMA = "qwen2.5vl:3b"     # for image captions
TEXT_EMBED_MODEL     = "all-MiniLM-L6-v2"    # for text & caption embeddings
CLIP_MODEL           = "openai/clip-vit-base-patch32"  # image embeddings

STOP_ON_ERROR = True

# =============================================================================


CONFIG_PATH = PROJECT_ROOT / "pipeline_config.json"

SCRIPTS = [
    PROJECT_ROOT / "ocr_folder_to_markdown.py",
    PROJECT_ROOT / "crop_all_pdfs_from_markdown.py",
    PROJECT_ROOT / "build_text_chunks_dir.py",
    PROJECT_ROOT / "build_image_records_dir.py",
]


def write_config():
    cfg = {
        "pdf_input_dir": str(PDF_INPUT_DIR),
        "markdown_dir": str(MARKDOWN_DIR),
        "crops_dir": str(CROPS_DIR),
        "kg_out_dir": str(KG_OUT_DIR),

        "ocr_model": MODEL_OCR,
        "dpi": DPI,

        "pad": PAD,

        "caption_model_ollama": CAPTION_MODEL_OLLAMA,
        "text_embed_model": TEXT_EMBED_MODEL,
        "clip_model": CLIP_MODEL,
    }
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f" Wrote config: {CONFIG_PATH}")


def run_step(script_path: Path):
    print("\n" + "=" * 90)
    print(f" Running: {script_path.name}")
    print("=" * 90)

    env = dict(**dict(**__import__("os").environ))
    env["PIPELINE_CONFIG"] = str(CONFIG_PATH)

    start = time.time()
    try:
        subprocess.run([sys.executable, str(script_path)], check=True, env=env)
    except subprocess.CalledProcessError:
        print(f"\n Step failed: {script_path.name}")
        if STOP_ON_ERROR:
            sys.exit(1)
        else:
            print(" Continuing...")
    else:
        print(f"\n Finished {script_path.name} in {time.time() - start:.2f}s")


def sanity_checks():
    if not PDF_INPUT_DIR.exists():
        raise FileNotFoundError(f"PDF_INPUT_DIR not found: {PDF_INPUT_DIR}")

    pdfs = list(PDF_INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        raise RuntimeError(f"No PDFs found in: {PDF_INPUT_DIR}")

    for s in SCRIPTS:
        if not s.exists():
            raise FileNotFoundError(f"Missing script: {s}")

    print(f" PDFs found: {len(pdfs)}")
    print(" All scripts found")


def main():
    print("\n Starting pipeline")
    print(f"PDF_INPUT_DIR : {PDF_INPUT_DIR}")
    print(f"MARKDOWN_DIR  : {MARKDOWN_DIR}")
    print(f"CROPS_DIR     : {CROPS_DIR}")
    print(f"KG_OUT_DIR    : {KG_OUT_DIR}")

    sanity_checks()
    write_config()

    for script in SCRIPTS:
        run_step(script)

    print("\n" + "=" * 90)
    print(" PIPELINE COMPLETE")
    print(f"Outputs:")
    print(f"  - {KG_OUT_DIR / 'text_chunks.jsonl'}")
    print(f"  - {KG_OUT_DIR / 'images.jsonl'}")
    print("=" * 90)


if __name__ == "__main__":
    main()
