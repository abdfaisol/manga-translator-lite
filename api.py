"""MangaTranslator HTTP API — Translate V2 (detect & mask, then translate).

Run it yourself (never started automatically by the app):

    .\\venv\\Scripts\\python.exe api.py                 # http://127.0.0.1:8000/docs
    .\\venv\\Scripts\\python.exe api.py --port 9000

Endpoints:
    GET  /health     -> {"status": "ok"}
    POST /detect     -> annotated image with red masks (+ OCR text)
    POST /translate  -> Translate V2: detect & mask first, then translate & typeset

Defaults are source language ``Auto`` and target language ``Indonesian``.
Provider, API keys, model and font come from the app's saved config
(``%LOCALAPPDATA%/MangaTranslator/config.json``) or env vars, same as the GUI;
override per request with query parameters.
"""

import argparse
import base64
import io
import os
import tempfile
from pathlib import Path
import threading

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from PIL import Image

from core.device import get_best_device
from core.pipeline import simulate_full_pipeline, simulate_mask_and_ocr
from core.validation import autodetect_yolo_model_path
from ui.settings_manager import get_saved_settings
from ui.ui_models import UIConfigState, map_ui_to_backend_config

REPO_ROOT = Path(__file__).resolve().parent
MODELS_DIR = REPO_ROOT / "models"
FONTS_DIR = REPO_ROOT / "fonts"

DEFAULT_SOURCE_LANGUAGE = "Auto"
DEFAULT_TARGET_LANGUAGE = "Indonesian"

# How many pages may be processed at once. Extra requests wait instead of
# fighting over VRAM/RAM (and instead of taking the whole process down on OOM).
# Raise it if you have VRAM to spare, lower it to 1 on a weak GPU.
MAX_CONCURRENT = max(1, int(os.environ.get("MANGA_API_MAX_CONCURRENT", "2")))
_job_slots = threading.Semaphore(MAX_CONCURRENT)

app = FastAPI(title="MangaTranslator API", version="2.0")


def build_config(
    *,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    ocr_method: str | None = None,
    provider: str | None = None,
    model_name: str | None = None,
):
    """Build a backend config from the app's saved settings, with per-request overrides."""
    settings = get_saved_settings()
    for key, value in (
        ("provider", provider),
        ("model_name", model_name),
        ("ocr_method", ocr_method),
    ):
        if value:
            settings[key] = value

    state = UIConfigState.from_dict(settings)
    state.input_language = source_language
    state.output_language = target_language

    packs = (
        sorted(d.name for d in FONTS_DIR.iterdir() if d.is_dir())
        if FONTS_DIR.is_dir()
        else []
    )
    if state.font_pack not in packs:
        state.font_pack = packs[0] if packs else None

    config = map_ui_to_backend_config(state, FONTS_DIR, get_best_device(), False)
    config.verbose = False
    config.cleaning_only = False
    config.upscaling_only = False
    config.yolo_model_path = str(
        autodetect_yolo_model_path(MODELS_DIR, config.detection.bubble_detector_model)
    )
    return config


def _stage_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "input.png").suffix or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(upload.file.read())
        return Path(tmp.name)


def _rows_to_dicts(rows: list[list]) -> list[dict]:
    return [
        {
            "index": row[0],
            "type": row[1],
            "bbox": row[2],
            "ocr_text": row[3],
            "translation": row[4],
        }
        for row in rows
    ]


def _encode(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _respond(
    image: Image.Image,
    *,
    as_json: bool,
    status: str = "",
    rows: list[list] | None = None,
    mask_image: Image.Image | None = None,
):
    if not as_json:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(content=buffer.getvalue(), media_type="image/png")

    payload = {
        "status": status,
        "rows": _rows_to_dicts(rows or []),
        "image": _encode(image),
    }
    if mask_image is not None:
        payload["mask_image"] = _encode(mask_image)
    return JSONResponse(payload)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/detect", summary="Step 1: detect bubbles, draw masks, run OCR")
def detect(
    image: UploadFile = File(...),
    run_ocr: bool = Query(True),
    source_language: str = Query(DEFAULT_SOURCE_LANGUAGE),
    ocr_method: str | None = Query(None),
    provider: str | None = Query(None),
    model_name: str | None = Query(None),
    as_json: bool = Query(False, description="JSON with rows + base64 instead of PNG"),
):
    config = build_config(
        source_language=source_language,
        ocr_method=ocr_method,
        provider=provider,
        model_name=model_name,
    )
    path = _stage_upload(image)
    _job_slots.acquire()
    try:
        annotated, _, rows, status, _ = simulate_mask_and_ocr(
            path, config, run_ocr=run_ocr, verbose=False
        )
    except Exception as e:  # surface the real reason to the caller
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    finally:
        path.unlink(missing_ok=True)
        _job_slots.release()

    return _respond(annotated, as_json=as_json, status=status, rows=rows)


@app.post("/translate", summary="Translate V2: detect & mask, then translate & typeset")
def translate(
    image: UploadFile = File(...),
    source_language: str = Query(DEFAULT_SOURCE_LANGUAGE),
    target_language: str = Query(DEFAULT_TARGET_LANGUAGE),
    run_ocr: bool = Query(True),
    ocr_method: str | None = Query(None),
    provider: str | None = Query(None),
    model_name: str | None = Query(None),
    as_json: bool = Query(False, description="JSON with rows + base64 instead of PNG"),
):
    config = build_config(
        source_language=source_language,
        target_language=target_language,
        ocr_method=ocr_method,
        provider=provider,
        model_name=model_name,
    )
    path = _stage_upload(image)
    _job_slots.acquire()
    try:
        annotated, _, _, _, cache = simulate_mask_and_ocr(
            path, config, run_ocr=run_ocr, verbose=False
        )
        _, final, rows, status = simulate_full_pipeline(
            path,
            config,
            verbose=False,
            pre_detected_items=cache.get("sorted_items"),
            pre_extracted_ocr_texts=cache.get("ocr_texts"),
            pre_outside_work=cache.get("outside_work"),
        )
    except Exception as e:  # surface the real reason to the caller
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    finally:
        path.unlink(missing_ok=True)
        _job_slots.release()

    return _respond(
        final, as_json=as_json, status=status, rows=rows, mask_image=annotated
    )


def main(argv=None):
    """Run the API server. Reachable via `python api.py` or `mgtranslate start --api`."""
    import uvicorn

    parser = argparse.ArgumentParser(description="MangaTranslator HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    uvicorn.run("api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
