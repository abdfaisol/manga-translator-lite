"""Runnable self-check for the Translate V2 feature + api.py.

    .\\venv\\Scripts\\python.exe check_features.py

Builds the Gradio layout in-process (no server is started) and asserts that the
new "Translate V2" tab exists while the pre-existing Translator / Simulasi / Batch
tabs are untouched. Also asserts the API defaults (source Auto, target Indonesian).
"""

import sys
from pathlib import Path

import api
from core.device import get_best_device
from ui import layout as ui_layout

failures = []
total = 0


def check(label, condition, detail=""):
    global total
    total += 1
    print(f"{'PASS' if condition else 'FAIL'}: {label}{f' ({detail})' if detail else ''}")
    if not condition:
        failures.append(label)


app = ui_layout.create_layout(
    models_dir=Path("models"),
    fonts_base_dir=Path("fonts"),
    target_device=get_best_device(),
)

labels = []
for block in app.blocks.values():
    if type(block).__name__ == "Tab":
        label = getattr(block, "label", None)
        if label:
            labels.append(label)

check("Translate V2 tab present", "Translate V2" in labels, ",".join(labels))
check("Simulasi tab still present", "Simulasi" in labels)
check("Translator tab still present", "Translator" in labels)
check("Batch tab still present", "Batch" in labels)

elem_ids = {getattr(b, "elem_id", None) for b in app.blocks.values()}
for elem_id in (
    "v2_input_image",
    "v2_run_ocr",
    "v2_source_language",
    "v2_target_language",
    "v2_status_message",
    "v2_dataframe",
    "v2_mask_image",
    "v2_cleaned_image",
    "v2_final_image",
    "simulation_input_image",
    "simulation_status_message",
    "translator_input_image",
):
    check(f"elem_id {elem_id}", elem_id in elem_ids)

# The V2 handler must be wired to the V2 button.
handlers = [d.fn for d in app.fns.values()]
names = {getattr(getattr(f, "func", f), "__name__", "") for f in handlers}
check("handle_translate_v2_click wired", "handle_translate_v2_click" in names, ",".join(sorted(names)))

check(
    "api defaults Auto -> Indonesian",
    api.DEFAULT_SOURCE_LANGUAGE == "Auto" and api.DEFAULT_TARGET_LANGUAGE == "Indonesian",
)
cfg = api.build_config()
check("api config input Auto", cfg.translation.input_language == "Auto")
check("api config output Indonesian", cfg.translation.output_language == "Indonesian")
check("api yolo model exists", Path(cfg.yolo_model_path).is_file(), Path(cfg.yolo_model_path).name)
check("api font dir exists", Path(cfg.rendering.font_dir).is_dir(), Path(cfg.rendering.font_dir).name)
check("api translation not skipped", not cfg.cleaning_only and not cfg.upscaling_only)

overridden = api.build_config(
    source_language="Korean", target_language="English", ocr_method="Auto"
)
check(
    "api per-request overrides applied",
    overridden.translation.input_language == "Korean"
    and overridden.translation.output_language == "English"
    and overridden.translation.ocr_method == "Auto",
)

routes = {r.path for r in api.app.routes}
check("api has /health", "/health" in routes)
check("api has /detect", "/detect" in routes)
check("api has /translate", "/translate" in routes)

# Keep docs/API.md honest: every documented parameter must exist with the documented default.
schema = api.app.openapi()
docs = Path("docs/API.md").read_text(encoding="utf-8")

EXPECTED = {
    "/detect": {
        "run_ocr": True,
        "source_language": "Auto",
        "ocr_method": None,
        "provider": None,
        "model_name": None,
        "as_json": False,
    },
    "/translate": {
        "run_ocr": True,
        "source_language": "Auto",
        "target_language": "Indonesian",
        "ocr_method": None,
        "provider": None,
        "model_name": None,
        "as_json": False,
    },
}
for path, expected in EXPECTED.items():
    params = {
        p["name"]: p["schema"].get("default")
        for p in schema["paths"][path]["post"]["parameters"]
        if p["in"] == "query"
    }
    for name, default in expected.items():
        check(f"{path} param {name} default={default!r}", params.get(name) == default, repr(params.get(name)))
        check(f"docs/API.md documents {name}", f"`{name}`" in docs)

    body = schema["paths"][path]["post"]["requestBody"]["content"]
    check(f"{path} accepts multipart image", "multipart/form-data" in body)
    body_schema = body["multipart/form-data"]["schema"]
    if "$ref" in body_schema:
        ref = body_schema["$ref"].rsplit("/", 1)[-1]
        body_schema = schema["components"]["schemas"][ref]
    check(f"{path} multipart field named image", "image" in body_schema.get("properties", {}))
    check(f"{path} documented in docs/API.md", f"`{path}`" in docs)

check("README links docs/API.md", "docs/API.md" in Path("README.md").read_text(encoding="utf-8"))
check("README mentions Translate V2 tab", "Translate V2" in Path("README.md").read_text(encoding="utf-8"))

print(f"\nTOTAL {total} FAILED {len(failures)}")
sys.exit(1 if failures else 0)
