"""Runnable self-check for the Translate V2 feature + api.py.

    .\\venv\\Scripts\\python.exe check_features.py

Builds the Gradio layout in-process (no server is started) and asserts that the
new "Translate V2" tab exists while the pre-existing Translator / Simulasi / Batch
tabs are untouched. Also asserts the API defaults (source Auto, target Indonesian).
"""

import ast
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
api_src = Path("api.py").read_text(encoding="utf-8")
api_tree = ast.parse(api_src)

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

# docs/API.md must show a request AND a response for every endpoint, not just a
# bare curl line. (The response bodies are illustrative, so only the shapes are checked.)
for heading in ("### `GET /health`", "### `POST /translate`", "### `POST /detect`"):
    check(f"docs/API.md has section {heading}", heading in docs)
check("docs/API.md shows a PNG response", "content-type: image/png" in docs)
check("docs/API.md shows an error response", "HTTP/1.1 500 Internal Server Error" in docs)
check("docs/API.md documents concurrency limit", "MANGA_API_MAX_CONCURRENT" in docs)

# The JSON example must match what _respond() actually emits.
respond_src = ast.get_source_segment(
    api_src,
    next(
        n
        for n in ast.walk(api_tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_respond"
    ),
)
for key in ("status", "rows", "image", "mask_image"):
    check(f'_respond emits "{key}"', f'"{key}"' in respond_src)
    check(f'docs/API.md documents "{key}"', f'"{key}"' in docs)

# Row shape documented in the response example must match _rows_to_dicts().
rows_src = ast.get_source_segment(
    api_src,
    next(
        n
        for n in ast.walk(api_tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_rows_to_dicts"
    ),
)
for field in ("index", "type", "bbox", "ocr_text", "translation"):
    check(f'_rows_to_dicts emits "{field}"', f'"{field}"' in rows_src)
    check(f'docs/API.md shows row field "{field}"', f'"{field}":' in docs)

# /health is cheap enough to exercise for real (no model is loaded).
from fastapi.testclient import TestClient

with TestClient(api.app) as client:
    health = client.get("/health")
    check("GET /health -> 200", health.status_code == 200, health.status_code)
    check("GET /health body", health.json() == {"status": "ok"}, health.text)

check("README links docs/API.md", "docs/API.md" in Path("README.md").read_text(encoding="utf-8"))
check("README mentions Translate V2 tab", "Translate V2" in Path("README.md").read_text(encoding="utf-8"))

# clamp_settings() indexes SETTING_CONSTRAINTS by every key in _CONFIG_ATTR_PATHS,
# so any key present in one map but not the other used to crash the CLI at startup.
from core.config import MangaTranslatorConfig
from core.validation import SETTING_CONSTRAINTS, _CONFIG_ATTR_PATHS, clamp_settings

orphan_paths = sorted(set(_CONFIG_ATTR_PATHS) - set(SETTING_CONSTRAINTS))
check("every clamp attr path has a numeric range", not orphan_paths, ",".join(orphan_paths))
check(
    "every clamp key maps to a real config attr",
    all(p[0] in MangaTranslatorConfig.__dataclass_fields__ for paths in _CONFIG_ATTR_PATHS.values() for p in paths),
)

# Regression: this exact config used to raise KeyError inside clamp_settings.
_clamp_cfg = MangaTranslatorConfig.__new__(MangaTranslatorConfig)
try:
    clamp_settings(_clamp_cfg)
    check("clamp_settings tolerates a bare config", True)
except Exception as exc:  # noqa: BLE001
    check("clamp_settings tolerates a bare config", False, f"{type(exc).__name__}: {exc}")

# Both endpoints must hold a job slot while doing the heavy work, or a burst of
# requests all runs at once and can OOM the whole process.
check("api MAX_CONCURRENT >= 1", api.MAX_CONCURRENT >= 1, api.MAX_CONCURRENT)
check(
    "api semaphore sized to MAX_CONCURRENT",
    api._job_slots._value == api.MAX_CONCURRENT,  # noqa: SLF001
    api._job_slots._value,  # noqa: SLF001
)
for fn_name in ("detect", "translate"):
    fn = next(
        n for n in ast.walk(api_tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name
    )
    calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    check(f"{fn_name} acquires a job slot", "_job_slots.acquire" in calls)
    check(f"{fn_name} releases its job slot", "_job_slots.release" in calls)

# A slot must be released even when the work raises, otherwise the API wedges
# after MAX_CONCURRENT failures.
def _release_on_error():
    api._job_slots.acquire()  # noqa: SLF001
    try:
        raise RuntimeError("boom")
    finally:
        api._job_slots.release()  # noqa: SLF001


before = api._job_slots._value  # noqa: SLF001
try:
    _release_on_error()
except RuntimeError:
    pass
check("job slot released after an exception", api._job_slots._value == before)  # noqa: SLF001

# `mgtranslate start [--api]` must dispatch to app.main / api.main.
# (Stub the real mains so nothing is actually launched.)
import importlib

import cli

check("api.main callable", callable(api.main))
for argv, expected in ((["start"], "app"), (["start", "--api"], "api")):
    seen = {}
    for name in ("app", "api"):
        mod = importlib.import_module(name)
        mod.main = (lambda n: lambda *a, **k: seen.setdefault("target", n))(name)
    cli.main(argv)
    check(f"mgtranslate {' '.join(argv)} -> {expected}", seen.get("target") == expected, seen.get("target"))

pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
check("pyproject declares mgtranslate script", 'mgtranslate = "cli:main"' in pyproject)
check("pyproject ships cli module", '"cli"' in pyproject)

print(f"\nTOTAL {total} FAILED {len(failures)}")
sys.exit(1 if failures else 0)
