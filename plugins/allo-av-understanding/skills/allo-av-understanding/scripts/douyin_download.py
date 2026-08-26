#!/usr/bin/env python3
"""Minimal Douyin public-share video downloader demo.

This is a standalone proof of concept for the chain described in the notes:

share text / v.douyin.com short URL
  -> canonical share/video page
  -> SSR router data
  -> video.play_addr uri / url_list
  -> aweme play endpoint
  -> CDN mp4

It intentionally does not remove watermarks and does not do any local video
understanding. The output is just a downloaded media file that can be uploaded
to the remote Allo video-understanding service.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

RATIOS = ("1080p", "720p", "540p", "360p")


@dataclass
class ProbeResult:
    url: str
    final_url: str
    size: int
    status: int
    content_type: str


@dataclass
class ResolveResult:
    input_url: str
    page_url: str
    aweme_id: str
    title: str
    candidates: list[str]
    best: ProbeResult


class DouyinDownloader:
    def __init__(self) -> None:
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

    def build_request(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> urllib.request.Request:
        merged = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if headers:
            merged.update(headers)
        return urllib.request.Request(url, data=data, headers=merged)

    def open(self, request: urllib.request.Request, timeout: int = 20):
        return self.opener.open(request, timeout=timeout)

    def register_ttwid(self) -> None:
        """Try to get an anonymous ttwid cookie for SSR share-page rendering."""
        payload = {
            "aid": 6383,
            "service": "www.douyin.com",
            "union": True,
            "needFid": False,
            "region": "cn",
            "cbUrlProtocol": "https",
            "migrate_info": {"ticket": "", "source": "node"},
        }
        request = self.build_request(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.douyin.com",
                "Referer": "https://www.douyin.com/",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with self.open(request, timeout=10) as response:
                response.read(4096)
        except Exception as exc:  # pragma: no cover - best effort only
            print(f"[warn] ttwid register failed: {exc}", file=sys.stderr)

    def fetch_text(self, url: str, *, referer: str = "") -> tuple[str, str]:
        headers = {"Referer": referer} if referer else None
        request = self.build_request(url, headers=headers)
        with self.open(request, timeout=25) as response:
            final_url = response.geturl()
            raw = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
        return final_url, raw.decode(encoding, errors="replace")

    def resolve(self, input_text: str) -> ResolveResult:
        input_url = extract_url_or_aweme(input_text)
        self.register_ttwid()

        if input_url.isdigit():
            aweme_id = input_url
            page_url = canonical_share_url(aweme_id)
            page_url, page_text = self.fetch_text(page_url)
        else:
            page_url, page_text = self.fetch_text(input_url)
            aweme_id = find_aweme_id(page_text, page_url)
            if aweme_id:
                # Canonical share page tends to contain the SSR router data.
                try:
                    page_url, page_text = self.fetch_text(
                        canonical_share_url(aweme_id), referer=page_url
                    )
                except Exception as exc:
                    print(
                        f"[warn] canonical share fetch failed: {exc}", file=sys.stderr
                    )

        aweme_id = find_aweme_id(page_text, page_url) or aweme_id
        data = extract_page_data(page_text)
        title = pick_title(data, aweme_id)
        candidates = build_play_urls(collect_play_candidates(data))

        if not candidates:
            raise RuntimeError("could not find video.play_addr metadata in share page")

        probes = [self.probe(candidate, page_url) for candidate in candidates]
        probes = [probe for probe in probes if probe.final_url]
        if not probes:
            raise RuntimeError("could not resolve any play URL to a downloadable mp4")

        best = max(
            probes,
            key=lambda item: (item.size, is_video_content_type(item.content_type)),
        )
        return ResolveResult(
            input_url=input_url,
            page_url=page_url,
            aweme_id=aweme_id,
            title=title,
            candidates=candidates,
            best=best,
        )

    def probe(self, url: str, referer: str) -> ProbeResult:
        request = self.build_request(
            url,
            headers={"Accept": "*/*", "Referer": referer, "Range": "bytes=0-1"},
        )
        try:
            with self.open(request, timeout=20) as response:
                response.read(2)
                size = parse_total_size(response.headers)
                return ProbeResult(
                    url=url,
                    final_url=response.geturl() or url,
                    size=size,
                    status=response.status,
                    content_type=response.headers.get("Content-Type", ""),
                )
        except Exception as exc:
            print(f"[warn] probe failed: {shorten(url)}: {exc}", file=sys.stderr)
            return ProbeResult(url=url, final_url="", size=0, status=0, content_type="")

    def download(self, result: ResolveResult, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = unique_path(
            output_dir / safe_filename(result.title, result.aweme_id)
        )
        request = self.build_request(
            result.best.final_url,
            headers={"Accept": "*/*", "Referer": result.page_url},
        )
        with self.open(request, timeout=60) as response, output_path.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
        if output_path.stat().st_size <= 0:
            raise RuntimeError("downloaded file is empty")
        return output_path


def extract_url_or_aweme(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"\d{10,}", text):
        return text
    match = re.search(r"https?://[^\s，。；、)）\]】>]+", text)
    if not match:
        raise RuntimeError("no URL or aweme_id found in input")
    return match.group(0)


def canonical_share_url(aweme_id: str) -> str:
    return f"https://www.iesdouyin.com/share/video/{aweme_id}/"


def find_aweme_id(text: str, page_url: str) -> str:
    patterns = (
        r"/share/video/(\d+)",
        r"/video/(\d+)",
        r"aweme_id[=:](\d+)",
        r'"awemeId"\s*:\s*"?(\d+)"?',
        r'"aweme_id"\s*:\s*"?(\d+)"?',
        r"modal_id=(\d+)",
    )
    for haystack in (page_url, text):
        for pattern in patterns:
            match = re.search(pattern, haystack)
            if match:
                return match.group(1)
    return ""


def extract_page_data(text: str) -> Any:
    """Extract SSR data from common Douyin page script blocks."""
    for pattern in (
        r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
        r"window\._ROUTER_DATA\s*=\s*(\{.*?\});",
    ):
        for raw in re.findall(pattern, text, flags=re.S):
            parsed = parse_json_loose(raw)
            if parsed is not None:
                return parsed

    render_match = re.search(
        r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>', text, flags=re.S
    )
    if render_match:
        raw = urllib.parse.unquote(html.unescape(render_match.group(1).strip()))
        parsed = parse_json_loose(raw)
        if parsed is not None:
            return parsed

    return None


def parse_json_loose(raw: str) -> Any | None:
    raw = html.unescape(raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def collect_play_candidates(data: Any) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if data is None:
        return candidates

    for item in walk(data):
        for key in (
            "play_addr",
            "playAddr",
            "download_addr",
            "downloadAddr",
            "playwm_addr",
        ):
            value = item.get(key)
            if not isinstance(value, dict):
                continue

            uri = value.get("uri") or value.get("url_key") or value.get("video_id")
            if isinstance(uri, str) and uri:
                candidates.append(("uri", uri))

            url_list = value.get("url_list") or value.get("urlList") or []
            if isinstance(url_list, list):
                for media_url in url_list:
                    if isinstance(media_url, str) and media_url.startswith("http"):
                        candidates.append(("url", media_url.replace("\\u0026", "&")))

    deduped: list[tuple[str, str]] = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def build_play_urls(candidates: list[tuple[str, str]]) -> list[str]:
    urls: list[str] = []
    for kind, value in candidates:
        if kind == "url":
            urls.append(value)
            continue
        encoded = urllib.parse.quote(value, safe="")
        for ratio in RATIOS:
            urls.append(
                "https://aweme.snssdk.com/aweme/v1/play/"
                f"?video_id={encoded}&ratio={ratio}&line=0"
            )

    deduped: list[str] = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def pick_title(data: Any, aweme_id: str) -> str:
    if data is not None:
        for item in walk(data):
            for key in ("desc", "description", "title", "nickname"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return f"douyin_{aweme_id or int(time.time())}"


def parse_total_size(headers) -> int:
    content_range = headers.get("Content-Range", "")
    match = re.search(r"/(\d+)\s*$", content_range)
    if match:
        return int(match.group(1))
    content_length = headers.get("Content-Length")
    if content_length and content_length.isdigit():
        return int(content_length)
    return 0


def is_video_content_type(content_type: str) -> int:
    return (
        1
        if "video" in content_type.lower() or "octet-stream" in content_type.lower()
        else 0
    )


def safe_filename(title: str, aweme_id: str) -> str:
    title = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", title).strip("._")
    if not title:
        title = f"douyin_{aweme_id or int(time.time())}"
    return f"{title[:120]}.mp4"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many duplicate output files for {path}")


def shorten(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def as_json(result: ResolveResult) -> str:
    return json.dumps(
        {
            "input_url": result.input_url,
            "page_url": result.page_url,
            "aweme_id": result.aweme_id,
            "title": result.title,
            "candidate_count": len(result.candidates),
            "best": {
                "url": result.best.url,
                "final_url": result.best.final_url,
                "size": result.best.size,
                "status": result.best.status,
                "content_type": result.best.content_type,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download public Douyin video by share URL"
    )
    parser.add_argument(
        "input", help="Douyin share text, short URL, long URL, or aweme_id"
    )
    parser.add_argument("-o", "--output-dir", default="douyin_demo_downloads")
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve metadata only; do not download"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    downloader = DouyinDownloader()
    result = downloader.resolve(args.input)
    print(as_json(result), file=sys.stderr)
    if args.dry_run:
        return 0
    output_path = downloader.download(result, Path(args.output_dir))
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
