import argparse
import base64
import binascii
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT = (10, 330)
DEFAULT_BASE_URL = "http://221.0.79.252:18120/v1"
IMAGE_MODEL = "gpt-image-2"
MAX_PROMPT_LENGTH = 10_000
ASPECT_RATIO_SIZES = {
    "1:1": "1024x1024",
    "square": "1024x1024",
    "portrait": "1024x1792",
    "9:16": "1024x1792",
    "2:3": "1024x1792",
    "landscape": "1792x1024",
    "16:9": "1792x1024",
    "3:2": "1792x1024",
}
MAX_PROVIDER_ERROR_LENGTH = 2000
SECRET_FIELD_NAMES = (
    "api[_-]?key|access[_-]?token|token|key|signature|sig|password|authorization"
)


def _load_config() -> tuple[str, str]:
    api_key = os.getenv("IMAGE_GATEWAY_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "IMAGE_GATEWAY_KEY is required. Set it before running image generation."
        )

    base_url = os.getenv("IMAGE_GATEWAY_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("IMAGE_GATEWAY_BASE_URL cannot be empty.")

    return api_key, base_url


def _size_for_aspect_ratio(aspect_ratio: str) -> str:
    normalized = aspect_ratio.strip().lower()
    try:
        return ASPECT_RATIO_SIZES[normalized]
    except KeyError as exc:
        supported = ", ".join(ASPECT_RATIO_SIZES)
        raise ValueError(
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported values: {supported}."
        ) from exc


def _require_file(path_value: str, kind: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"The {kind} file does not exist: {path}")
    return path


def _sanitize_provider_text(text: str, api_key: str) -> str:
    sanitized = text.replace(api_key, "[REDACTED]") if api_key else text
    sanitized = re.sub(
        rf"(?i)(?P<key_quote>[\"'])(?P<key>{SECRET_FIELD_NAMES})(?P=key_quote)(?P<separator>\s*:\s*)(?P<value_quote>[\"'])(?P<value>.*?)(?P=value_quote)",
        lambda match: (
            f"{match.group('key_quote')}{match.group('key')}{match.group('key_quote')}{match.group('separator')}{match.group('value_quote')}[REDACTED]{match.group('value_quote')}"
        ),
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)(\bauthorization\s*:\s*)(?:(?:basic|bearer|digest|negotiate|token)\s+)?[^\s,;\"'}\]]+",
        r"\1[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        rf"(?i)([?&](?:{SECRET_FIELD_NAMES})=)[^&\s\"']+",
        r"\1[REDACTED]",
        sanitized,
    )
    if len(sanitized) > MAX_PROVIDER_ERROR_LENGTH:
        return f"{sanitized[:MAX_PROVIDER_ERROR_LENGTH]}... [truncated]"
    return sanitized


def _request_error(action: str, exc: BaseException, api_key: str) -> RuntimeError:
    details = _sanitize_provider_text(str(exc), api_key)
    return RuntimeError(f"{action}: {details}" if details else action)


class _ReadTimeoutHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, read_timeout: int, **kwargs):
        self.read_timeout = read_timeout
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()
        self.sock.settimeout(self.read_timeout)


class _ReadTimeoutHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, read_timeout: int, **kwargs):
        self.read_timeout = read_timeout
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()
        self.sock.settimeout(self.read_timeout)


class _ReadTimeoutHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, read_timeout: int):
        self.read_timeout = read_timeout
        super().__init__()

    def http_open(self, req):
        connection = lambda *args, **kwargs: _ReadTimeoutHTTPConnection(
            *args, read_timeout=self.read_timeout, **kwargs
        )
        return self.do_open(connection, req)


class _ReadTimeoutHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, read_timeout: int):
        self.read_timeout = read_timeout
        super().__init__()

    def https_open(self, req):
        connection = lambda *args, **kwargs: _ReadTimeoutHTTPSConnection(
            *args, read_timeout=self.read_timeout, **kwargs
        )
        return self.do_open(connection, req)


def _urlopen(request: urllib.request.Request, timeout: tuple[int, int]):
    opener = urllib.request.build_opener(
        _ReadTimeoutHTTPHandler(timeout[1]),
        _ReadTimeoutHTTPSHandler(timeout[1]),
    )
    return opener.open(request, timeout=timeout[0])


def _read_response(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        response = _urlopen(request, REQUEST_TIMEOUT)
        with response:
            return response.getcode(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_PROVIDER_ERROR_LENGTH + 1)


def _request_bytes(
    request: urllib.request.Request,
    action: str,
    api_key: str,
) -> bytes:
    try:
        status_code, body = _read_response(request)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _request_error(action, exc, api_key) from exc
    if status_code >= 400:
        details = _sanitize_provider_text(
            body.decode("utf-8", errors="replace").strip(), api_key
        )
        suffix = f" Provider response: {details}" if details else ""
        raise RuntimeError(
            f"Image gateway {'download' if action.startswith('Failed to download') else 'request'} failed with HTTP {status_code}.{suffix}"
        )
    return body


def _response_item(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Image API returned invalid JSON.") from exc

    try:
        item = payload["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Image API response must contain a non-empty data array."
        ) from exc
    if not isinstance(item, dict):
        raise RuntimeError("Image API data[0] must be an object.")
    return item


def _image_bytes(response_body: bytes, api_key: str) -> bytes:
    item = _response_item(response_body)
    encoded = item.get("b64_json")
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise RuntimeError(
                "Image API returned invalid b64_json image data."
            ) from exc

    image_url = item.get("url")
    if image_url:
        request = urllib.request.Request(image_url, method="GET")
        content = _request_bytes(
            request, "Failed to download generated image URL", api_key
        )
        if not content:
            raise RuntimeError("Generated image URL returned an empty response body.")
        return content

    raise RuntimeError("Image API data[0] must contain b64_json or url.")


def _authorization_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _post_generation(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    size: str,
) -> bytes:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
    ).encode("utf-8")
    headers = _authorization_headers(api_key)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}/images/generations",
        data=body,
        headers=headers,
        method="POST",
    )
    return _request_bytes(request, "Could not reach the image API", api_key)


def _atomic_write(output_path: Path, image_bytes: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(image_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def generate_image(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
) -> str:
    if reference_images:
        raise ValueError(
            "The current DFCode image gateway does not support image editing or reference images."
        )
    api_key, base_url = _load_config()
    prompt_path = _require_file(prompt_file, "prompt")
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError(f"The prompt file is empty: {prompt_path}")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(
            f"The image prompt exceeds the maximum length of {MAX_PROMPT_LENGTH:,} characters."
        )
    size = _size_for_aspect_ratio(aspect_ratio)
    response_body = _post_generation(base_url, api_key, IMAGE_MODEL, prompt, size)

    image_bytes = _image_bytes(response_body, api_key)
    output_path = Path(output_file).expanduser()
    _atomic_write(output_path, image_bytes)
    return f"Successfully generated image to {output_path.resolve()}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate images using the DFCode image gateway"
    )
    parser.add_argument(
        "--prompt-file",
        required=True,
        help="Path to a UTF-8 prompt file",
    )
    parser.add_argument(
        "--reference-images",
        nargs="*",
        default=[],
        help="Paths to reference images (space-separated)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output path for the generated image",
    )
    parser.add_argument(
        "--aspect-ratio",
        default="16:9",
        help="Output ratio: 1:1, portrait, landscape, 16:9, 9:16, 2:3, or 3:2",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = generate_image(
            args.prompt_file,
            args.reference_images,
            args.output_file,
            args.aspect_ratio,
        )
    except Exception as exc:
        print(f"Image generation failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
