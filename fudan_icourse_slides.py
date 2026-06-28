#!/usr/bin/env python3
"""
Download Fudan iCourse replay slide images and combine each lesson into a PDF.

Authentication is explicit: pass either --token or --cookie copied from the
logged-in browser session. The script does not read browser profile files.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from PIL import Image


BASE_URL = "https://icourse.fudan.edu.cn"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Lesson:
    course_id: str
    sub_id: str
    tenant_code: str
    title: str = ""
    date: str = ""

    @property
    def safe_name(self) -> str:
        label = self.date or self.title or self.sub_id
        label = re.sub(r"[\\/:*?\"<>|]+", "_", label).strip()
        return f"{label}_{self.sub_id}" if label != self.sub_id else self.sub_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download iCourse replay PPT screenshots and make one PDF per lesson."
    )
    parser.add_argument("--course-url", help="Course detail URL or livingroom URL.")
    parser.add_argument("--course-id", default="")
    parser.add_argument("--tenant-code", default="")
    parser.add_argument("--sub-id", action="append", help="Lesson sub_id. Repeat for multiple lessons.")
    parser.add_argument("--token", help="Bearer token from document.cookie _token or localStorage.token.")
    parser.add_argument("--cookie", help="Raw Cookie header copied from browser devtools.")
    parser.add_argument("--lesson-json", help="JSON file containing lesson metadata/API response.")
    parser.add_argument("--browser-json", help="JSON file copied from browser-side probe output.")
    parser.add_argument("--out-dir", default="output/pdf/icourse_slides")
    parser.add_argument("--all", action="store_true", help="Try to discover all replay lessons for the course.")
    parser.add_argument("--dry-run", action="store_true", help="Only print discovered lessons/resources.")
    parser.add_argument("--print-browser-snippet", action="store_true", help="Print a snippet to run in DevTools.")
    return parser.parse_args()


def browser_snippet() -> str:
    return r"""
// Run this in the icourse.fudan.edu.cn course page console.
// Replace COURSE_ID and TENANT_CODE, then save/copy the JSON output.
(async () => {
  const params = new URLSearchParams(location.search);
  const COURSE_ID = params.get("course_id") || params.get("id") || "COURSE_ID";
  const TENANT_CODE = params.get("tenant_code") || "TENANT_CODE";

  function authHeaders() {
    const rawCookieToken = (document.cookie.match(/(?:^|;\s*)_token=([^;]+)/) || [])[1] || "";
    let token = rawCookieToken ? decodeURIComponent(rawCookieToken) : (localStorage.getItem("token") || "");
    const match = token.match(/_token\";i:\d+;s:\d+:\"(.+?)\"/) || token.match(/_token[^\"]*\"(.+?)\"/);
    if (match) token = match[1];
    return token ? { Authorization: token.startsWith("Bearer ") ? token : `Bearer ${token}` } : {};
  }

  async function getJson(path, query = {}) {
    const url = new URL(path, location.origin);
    Object.entries(query || {}).forEach(([k, v]) => v !== "" && v != null && url.searchParams.set(k, v));
    const res = await fetch(url, { headers: authHeaders() });
    const text = await res.text();
    try { return {url: url.href, status: res.status, json: JSON.parse(text)}; }
    catch { return {url: url.href, status: res.status, text: text.slice(0, 1000)}; }
  }

  function rawJson(response) {
    return response && response.json ? response.json : response;
  }

  function walk(value, out = []) {
    if (Array.isArray(value)) {
      value.forEach((item) => walk(item, out));
    } else if (value && typeof value === "object") {
      if (value.sub_id || value.id) out.push(value);
      Object.values(value).forEach((item) => walk(item, out));
    }
    return out;
  }

  const catalogueResponse = await getJson("/courseapi/v2/course/catalogue", { course_id: COURSE_ID });
  const rawLessons = walk(rawJson(catalogueResponse));
  const seen = new Set();
  const lessons = [];

  for (const item of rawLessons) {
    const subId = String(item.sub_id || item.id || "");
    if (!subId || seen.has(subId)) continue;
    seen.add(subId);

    const title = item.sub_title || item.title || item.name || item.subject_name || "";
    const status = String(item.sub_status || item.status || "");
    const pptResponse = await getJson("/pptnote/v1/schedule/search-ppt", {
      course_id: COURSE_ID,
      sub_id: subId,
      page: 1,
      per_page: 200
    });

    lessons.push({
      course_id: COURSE_ID,
      sub_id: subId,
      tenant_code: TENANT_CODE,
      title,
      date: title,
      status,
      ppt_index: rawJson(pptResponse)
    });
  }

  const payload = {
    course_id: COURSE_ID,
    tenant_code: TENANT_CODE,
    generated_at: new Date().toISOString(),
    lessons
  };
  const text = JSON.stringify(payload, null, 2);
  if (typeof copy === "function") copy(text);
  console.log(text);
})();
""".strip()


def parse_url_args(args: argparse.Namespace) -> None:
    if not args.course_url:
        return
    query = parse_qs(urlparse(args.course_url).query)
    if query.get("course_id"):
        args.course_id = query["course_id"][0]
    if query.get("id"):
        args.course_id = query["id"][0]
    if query.get("tenant_code"):
        args.tenant_code = query["tenant_code"][0]
    if query.get("sub_id") and not args.sub_id:
        args.sub_id = [query["sub_id"][0]]


def make_session(args: argparse.Namespace) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/605.1.15 Safari/605.1.15",
            "Referer": args.course_url or f"{BASE_URL}/coursedetail?course_id={args.course_id}&tenant_code={args.tenant_code}",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    if args.token:
        token = args.token if args.token.startswith("Bearer ") else f"Bearer {args.token}"
        session.headers["Authorization"] = token
    if args.cookie:
        session.headers["Cookie"] = args.cookie
    return session


def get_json(session: requests.Session, path: str, params: dict[str, Any]) -> Any:
    url = urljoin(BASE_URL, path)
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    text = response.text.strip()
    if text == "Auth Forbidden":
        raise RuntimeError(f"Auth Forbidden from {url}; pass --token or --cookie from the logged-in browser.")
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"Expected JSON from {response.url}, got: {text[:200]}") from exc


def walk(obj: Any) -> Iterable[Any]:
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def extract_lessons(obj: Any, course_id: str, tenant_code: str) -> list[Lesson]:
    lessons: dict[str, Lesson] = {}
    if isinstance(obj, dict) and isinstance(obj.get("lessons"), list):
        for item in obj["lessons"]:
            if not isinstance(item, dict) or not item.get("sub_id"):
                continue
            lesson = Lesson(
                str(item.get("course_id") or course_id),
                str(item["sub_id"]),
                str(item.get("tenant_code") or tenant_code),
                str(item.get("title") or ""),
                str(item.get("date") or ""),
            )
            lessons[lesson.sub_id] = lesson
        return list(lessons.values())
    for item in walk(obj):
        if not isinstance(item, dict):
            continue
        sub_id = item.get("sub_id") or item.get("sub") or item.get("subject_id") or item.get("id")
        c_id = item.get("course_id") or item.get("course") or course_id
        if not sub_id:
            continue
        text = json.dumps(item, ensure_ascii=False)
        if "回放生成中" in text or "课程已下架" in text:
            continue
        if "sub_status" in item and str(item.get("sub_status")) not in {"", "6", "0", "1", "2", "4"}:
            continue
        sub_id = str(sub_id)
        title = str(item.get("title") or item.get("name") or item.get("subject_name") or "")
        date = str(item.get("course_date") or item.get("date") or item.get("start_date") or "")
        match = re.search(r"20\d{2}-\d{2}-\d{2}[^\"，,]*", text)
        if not date and match:
            date = match.group(0)[:16]
        lessons[sub_id] = Lesson(str(c_id), sub_id, tenant_code, title, date)
    return list(lessons.values())


def discover_lessons(session: requests.Session, args: argparse.Namespace) -> list[Lesson]:
    if args.sub_id:
        return [Lesson(args.course_id, sid, args.tenant_code) for sid in args.sub_id]

    sources: list[Any] = []
    if args.lesson_json:
        sources.append(json.loads(Path(args.lesson_json).read_text()))
    if args.browser_json:
        sources.append(json.loads(Path(args.browser_json).read_text()))
    if args.all:
        for path, params in [
            (f"/courseapi/v2/course/{args.course_id}", {}),
            (
                "/courseapi/v2/course-live/search-live-course-list",
                {
                    "all": 1,
                    "show_all": 1,
                    "show_delete": 2,
                    "with_sub_data": 1,
                    "course_id": args.course_id,
                    "course_type": "multi",
                },
            ),
        ]:
            try:
                sources.append(get_json(session, path, params))
            except Exception as exc:
                print(f"[warn] lesson discovery failed for {path}: {exc}", file=sys.stderr)

    lessons: list[Lesson] = []
    for source in sources:
        lessons.extend(extract_lessons(source, args.course_id, args.tenant_code))
    unique: dict[str, Lesson] = {lesson.sub_id: lesson for lesson in lessons}
    return list(unique.values())


def is_image_url(value: str) -> bool:
    parsed = urlparse(value)
    ext = Path(parsed.path).suffix.lower()
    if ext in IMAGE_EXTS:
        return True
    return any(part in value.lower() for part in ["/ppt", "slide", "page"]) and "http" in value.lower()


def extract_urls(obj: Any) -> list[str]:
    urls: list[str] = []
    for item in walk(obj):
        if isinstance(item, dict):
            value = item.get("pptimgurl")
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
        elif isinstance(item, str):
            stripped = item.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    nested = json.loads(stripped)
                except Exception:
                    nested = None
                if nested is not None:
                    urls.extend(extract_urls(nested))
            if item.startswith("//"):
                urls.append("https:" + item)
            elif item.startswith("http") and (is_image_url(item) or "download" in item.lower()):
                urls.append(item)
            elif item.startswith("/"):
                candidate = urljoin(BASE_URL, item)
                if is_image_url(candidate) or "download" in candidate.lower():
                    urls.append(candidate)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if Path(urlparse(url).path).name.startswith("s_"):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def lesson_payloads(session: requests.Session, lesson: Lesson) -> list[Any]:
    params = {
        "course_id": lesson.course_id,
        "sub_id": lesson.sub_id,
        "tenant_code": lesson.tenant_code,
    }
    calls = [
        ("/courseapi/v3/sub-resource/get-course-resource", params),
        ("/courseapi/vlabpassportapi/v1/account-profile/rcourse/export/download-sub-ppt", params),
        ("/courseapi/vlabpassportapi/v1/account-profile/rcourse/export/download-sub-file", params),
    ]
    payloads: list[Any] = []
    for path, query in calls:
        try:
            payloads.append({"_endpoint": path, "data": get_json(session, path, query)})
        except Exception as exc:
            print(f"[warn] {lesson.sub_id} {path}: {exc}", file=sys.stderr)
    return payloads


def filename_from_response(url: str, response: requests.Response, index: int) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
    if match:
        return re.sub(r"[\\/:*?\"<>|]+", "_", match.group(1))
    ext = Path(urlparse(url).path).suffix
    if not ext:
        ext = mimetypes.guess_extension(response.headers.get("Content-Type", "").split(";")[0]) or ".bin"
    return f"{index:03d}{ext}"


def download_urls(session: requests.Session, urls: list[str], lesson_dir: Path) -> list[Path]:
    downloads = lesson_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for index, url in enumerate(urls, 1):
        response = session.get(url, timeout=60)
        response.raise_for_status()
        name = filename_from_response(url, response, index)
        path = downloads / name
        path.write_bytes(response.content)
        files.append(path)
    return files


def collect_images(files: list[Path], lesson_dir: Path) -> list[Path]:
    images: list[Path] = []
    extracted = lesson_dir / "extracted"
    for path in files:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            images.append(path)
        elif suffix == ".zip":
            extracted.mkdir(exist_ok=True)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(extracted)
            for child in sorted(extracted.rglob("*")):
                if child.suffix.lower() in IMAGE_EXTS:
                    images.append(child)
    return sorted(images, key=natural_key)


def natural_key(path: Path) -> list[Any]:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(path))]


def images_to_pdf(images: list[Path], pdf_path: Path) -> None:
    if not images:
        raise RuntimeError("No slide images found.")
    pil_images: list[Image.Image] = []
    for image_path in images:
        image = Image.open(image_path)
        if image.mode in ("RGBA", "LA"):
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        pil_images.append(image)
    first, rest = pil_images[0], pil_images[1:]
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(pdf_path, "PDF", resolution=150.0, save_all=True, append_images=rest)


def browser_payload_for_lesson(browser_json: Any, lesson: Lesson) -> list[Any]:
    if not browser_json:
        return []
    if isinstance(browser_json, dict) and isinstance(browser_json.get("lessons"), list):
        for item in browser_json["lessons"]:
            if isinstance(item, dict) and str(item.get("sub_id")) == lesson.sub_id:
                return [{"_endpoint": "browser_json:lesson", "data": item}]
        return []
    payloads: list[Any] = []
    root_sub = str(browser_json.get("sub_id") or "") if isinstance(browser_json, dict) else ""
    if root_sub and root_sub != lesson.sub_id:
        return []
    if isinstance(browser_json, dict):
        for key in ("ppt_index", "resource", "export_ppt", "export_file", "live_list", "course"):
            if browser_json.get(key) is not None:
                payloads.append({"_endpoint": f"browser_json:{key}", "data": browser_json[key]})
    return payloads


def save_lesson(
    session: requests.Session,
    lesson: Lesson,
    out_dir: Path,
    dry_run: bool,
    payload_override: list[Any] | None = None,
    allow_api: bool = True,
) -> None:
    lesson_dir = out_dir / lesson.safe_name
    lesson_dir.mkdir(parents=True, exist_ok=True)
    payloads = list(payload_override or [])
    if allow_api:
        payloads.extend(lesson_payloads(session, lesson))
    (lesson_dir / "api_payloads.json").write_text(json.dumps(payloads, ensure_ascii=False, indent=2))
    urls = extract_urls(payloads)
    (lesson_dir / "urls.txt").write_text("\n".join(urls))
    print(f"[lesson] {lesson.safe_name}: {len(urls)} candidate URLs")
    if dry_run:
        for url in urls:
            print("  ", url)
        return
    if not urls:
        print(f"[skip] {lesson.safe_name}: no PPT image URLs found")
        return
    files = download_urls(session, urls, lesson_dir)
    images = collect_images(files, lesson_dir)
    print(f"[lesson] {lesson.safe_name}: {len(images)} images")
    if not images:
        print(f"[skip] {lesson.safe_name}: downloads contained no images")
        return
    pdf_path = out_dir / f"{lesson.safe_name}.pdf"
    images_to_pdf(images, pdf_path)
    print(f"[pdf] {pdf_path}")


def main() -> int:
    args = parse_args()
    if args.print_browser_snippet:
        print(browser_snippet())
        return 0
    parse_url_args(args)
    if not args.course_id and not args.browser_json and not args.lesson_json:
        print("Missing course id. Pass --course-url, --course-id, --browser-json, or --lesson-json.", file=sys.stderr)
        return 2
    session = make_session(args)
    browser_json = json.loads(Path(args.browser_json).read_text()) if args.browser_json else None
    lessons = discover_lessons(session, args)
    if not lessons:
        print("No lessons discovered. Pass --sub-id, --lesson-json, --browser-json, or --all with auth.", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    for lesson in lessons:
        override = browser_payload_for_lesson(browser_json, lesson)
        allow_api = bool(args.token or args.cookie or not override)
        save_lesson(session, lesson, out_dir, args.dry_run, override, allow_api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
