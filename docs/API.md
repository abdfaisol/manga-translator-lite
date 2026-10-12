# API

Run `api.py` to translate pages over HTTP instead of the Web UI or CLI.

```bash
mgtranslate start --api          # http://127.0.0.1:8000  (docs at /docs)
mgtranslate start --api --port 9000
mgtranslate start --api --host 0.0.0.0 --reload
```

`mgtranslate start` (without `--api`) starts the Web UI on port `7676` instead. `--port`, `--host`, `--reload`, `--models` and `--cpu` are forwarded to the underlying launcher.

`mgtranslate` comes from the package entry point, so it needs an editable install once:

```bash
pip install -e . --no-deps
```

Without the entry point, `python api.py` does the same thing:

```bash
python api.py
```

Interactive docs (Swagger UI) are available at `http://127.0.0.1:8000/docs`.

> [!NOTE]
> The API is local-only by default (`127.0.0.1`) and has **no authentication**. Only bind to `0.0.0.0` on a trusted network.

### Quick start

Send a page and get the translated page back as a PNG:

```bash
curl.exe -X POST "http://127.0.0.1:8000/translate" -F "image=@page.jpg" -o translated.png
```

```
HTTP/1.1 200 OK
content-type: image/png

<binary PNG bytes, written to translated.png>
```

Add `as_json=true` to get the OCR text and translations alongside the image:

```bash
curl.exe -X POST "http://127.0.0.1:8000/translate?as_json=true" -F "image=@page.jpg"
```

```json
{
  "status": "Simulation completed in 18.42s.\n• Total items: 6 (5 speech bubbles, 1 outside text)\n• OCR: Auto",
  "rows": [
    {"index": 1, "type": "Bubble", "bbox": "[118, 74, 322, 244]", "ocr_text": "おはよう", "translation": "Selamat pagi"}
  ],
  "image": "iVBORw0KGgoAAAANSUhEUg...<base64 PNG>",
  "mask_image": "iVBORw0KGgoAAAANSUhEUg...<base64 PNG>"
}
```

Full request/response pairs for every endpoint are in [Examples](#examples).

### Run detached (no console window)

```powershell
Start-Process -WindowStyle Hidden .\venv\Scripts\mgtranslate.exe -ArgumentList "start","--api"
```

Stop it with `taskkill /IM mgtranslate.exe /F`. For auto-start at logon, point Task Scheduler at the same command with "Run whether user is logged on or not". Note that idle models still hold RAM/VRAM once loaded.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `POST` | `/detect` | Step 1: detect bubbles/OSB, draw masks, run OCR (no translation) |
| `POST` | `/translate` | **Translate V2**: detect & mask, then translate & typeset in one request |

Both `POST` endpoints take a multipart file upload named `image` and accept the same query parameters:

| Parameter | Default | Description |
| --- | --- | --- |
| `source_language` | `Auto` | Source language, or `Auto` to let the model detect it |
| `target_language` | `Indonesian` | Target language (`/translate` only) |
| `run_ocr` | `true` | Run OCR during detection |
| `ocr_method` | *(saved config)* | `LLM`, `Auto`, `manga-ocr`, `paddleocr-vl-1.6`, `rapidocr` |
| `provider` | *(saved config)* | LLM provider (e.g. `Google`, `[OI]-Compatible`) |
| `model_name` | *(saved config)* | Model override for the provider |
| `as_json` | `false` | Return JSON with base64 images instead of a PNG |

Provider, API keys, model, sampling and font pack come from your saved settings (`%LOCALAPPDATA%/MangaTranslator/config.json`) or environment variables, exactly like the Web UI and CLI. Parameters are per-request overrides and are never written back to the config.

### Concurrency

At most **2** pages are processed at once; extra requests wait in line instead of all running together. Change the limit with the `MANGA_API_MAX_CONCURRENT` environment variable before starting the server:

```powershell
$env:MANGA_API_MAX_CONCURRENT = "1"   # weak GPU / low VRAM
mgtranslate start --api
```

Requests beyond the limit are queued, not rejected, so the client just sees a slower response. Raising the limit above ~2 is rarely worth it: the Flux inpainting lock serializes that stage anyway, and a burst of simultaneous requests is the fastest way to an out-of-memory crash that kills **every** in-flight request.

## Responses

By default the response body **is the image** (`image/png`):

- `/detect` → annotated image with red masks.
- `/translate` → final typeset image.

With `as_json=true` the response is JSON:

```json
{
  "status": "Simulation completed in 12.34s.\n• Total items: 8 (7 speech bubbles, 1 outside text)\n• OCR: Auto",
  "rows": [
    {
      "index": 1,
      "type": "Bubble",
      "bbox": "[120, 88, 340, 260]",
      "ocr_text": "おはよう",
      "translation": "Selamat pagi"
    }
  ],
  "image": "<base64 PNG>",
  "mask_image": "<base64 PNG>"
}
```

`rows[].translation` is empty for `/detect`. `mask_image` is only included by `/translate`.

Errors return HTTP `500` with `{"detail": "<ExceptionType>: <message>"}`.

## Examples

### `GET /health`

```bash
curl.exe http://127.0.0.1:8000/health
```

```json
{"status": "ok"}
```

### `POST /translate` — default, returns the finished PNG

Defaults are source `Auto` → target `Indonesian`, so no parameters are needed.

```bash
curl.exe -X POST "http://127.0.0.1:8000/translate" -F "image=@page.jpg" -o translated.png
```

```
HTTP/1.1 200 OK
content-type: image/png

<binary PNG bytes, written to translated.png>
```

### `POST /translate` — JSON, Korean → English

```bash
curl.exe -X POST "http://127.0.0.1:8000/translate?source_language=Korean&target_language=English&as_json=true" -F "image=@page.jpg"
```

```json
{
  "status": "Simulation completed in 18.42s.\n• Total items: 6 (5 speech bubbles, 1 outside text)\n• OCR: rapidocr-korean",
  "rows": [
    {
      "index": 1,
      "type": "Bubble",
      "bbox": "[118, 74, 322, 244]",
      "ocr_text": "안녕하세요",
      "translation": "Hello"
    },
    {
      "index": 2,
      "type": "Bubble",
      "bbox": "[330, 80, 512, 236]",
      "ocr_text": "오늘 날씨가 좋네요",
      "translation": "The weather is nice today"
    }
  ],
  "image": "iVBORw0KGgoAAAANSUhEUg...<base64 PNG>",
  "mask_image": "iVBORw0KGgoAAAANSUhEUg...<base64 PNG>"
}
```

### `POST /detect` — step 1 only, masks + OCR without translating

```bash
curl.exe -X POST "http://127.0.0.1:8000/detect?as_json=true" -F "image=@page.jpg"
```

```json
{
  "status": "Simulation completed in 7.91s.\n• Total items: 6 (5 speech bubbles, 1 outside text)\n• OCR: Auto",
  "rows": [
    {
      "index": 1,
      "type": "Bubble",
      "bbox": "[118, 74, 322, 244]",
      "ocr_text": "안녕하세요",
      "translation": ""
    }
  ],
  "image": "iVBORw0KGgoAAAANSUhEUg...<base64 PNG with red masks>"
}
```

`translation` is always empty here, and there is no `mask_image` key — for `/detect` the `image` field *is* the masked image.

### Python

```python
import base64, requests

with open("page.jpg", "rb") as f:
    r = requests.post(
        "http://127.0.0.1:8000/translate",
        files={"image": f},
        params={"as_json": True, "source_language": "Auto", "target_language": "Indonesian"},
        timeout=600,
    )
r.raise_for_status()
data = r.json()
print(data["status"])
for row in data["rows"]:
    print(row["ocr_text"], "->", row["translation"])
open("translated.png", "wb").write(base64.b64decode(data["image"]))
```

Without `as_json`, drop `.json()` and write the bytes directly:

```python
with open("page.jpg", "rb") as f:
    r = requests.post("http://127.0.0.1:8000/translate", files={"image": f}, timeout=600)
r.raise_for_status()
open("translated.png", "wb").write(r.content)
```

### Errors

```bash
curl.exe -i -X POST "http://127.0.0.1:8000/translate?provider=Google" -F "image=@page.jpg"
```

```
HTTP/1.1 500 Internal Server Error
content-type: application/json

{"detail": "ValueError: Google provider selected but no API key configured"}
```

The `detail` string is always `<ExceptionType>: <message>`, so the underlying reason is visible without reading server logs.

> [!TIP]
> `/translate` runs detection once and reuses it for translation. Use `/detect` only when you want the masks/OCR without translating — for example to review boxes first.

*For batch or folder processing, use the CLI ([CLI](CLI.md)) or the Web UI Batch tab.*
