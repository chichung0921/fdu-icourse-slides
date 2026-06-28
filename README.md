# Fudan iCourse Slides

Download PPT screenshots from Fudan iCourse replay pages and stitch each lesson into a PDF.

This tool is designed for courses that expose replay PPT screenshots through iCourse. It uses the login session already present in Safari to discover lesson PPT screenshot indexes, then downloads the images and builds one PDF per lesson.

## Requirements

- macOS with Safari
- Python 3.9+
- Safari logged in to `icourse.fudan.edu.cn`

Install Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Quick Start

Open the target iCourse course page in Safari, then follow:

```bash
python3 fudan_icourse_slides.py --help
```

The full workflow is documented in:

```text
FUDAN_ICOURSE_SLIDES_TUTORIAL.md
```

## Basic Workflow

1. Open the target course page in Safari and log in.
2. Generate a browser-side PPT index JSON using the Safari AppleScript polling command in the tutorial.
3. Convert the indexed PPT screenshots into PDFs:

```bash
python3 fudan_icourse_slides.py \
  --browser-json tmp/icourse/icourse_COURSE_ID_ppt_index.json \
  --out-dir output/icourse_COURSE_ID_slides
```

Lessons without PPT screenshot indexes are skipped.

Safari's AppleScript bridge does not wait for JavaScript promises directly, so the tutorial uses a polling pattern instead of returning an `async` result from `do JavaScript`.

## Safety

Do not commit cookies, tokens, downloaded course materials, or generated PDFs. The `.gitignore` excludes common output directories and media files by default.
