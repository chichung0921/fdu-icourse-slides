# 复旦 iCourse 课件截图批量导出教程

本文说明如何把复旦 iCourse 回放里的 PPT 课件截图批量抓取下来，并按“每节课一个 PDF”合成文件。教程面向所有复旦 iCourse 课程：使用时只需要替换目标课程的 `course_id`、`tenant_code` 和输出目录。

## 1. 准备环境

先进入放置脚本的目录。目录中应包含：

```text
scripts/fudan_icourse_slides.py
```

确认 Python 能运行脚本：

```bash
python3 scripts/fudan_icourse_slides.py --help
```

如果提示缺少依赖，安装：

```bash
python3 -m pip install requests pillow
```

## 2. 找到课程参数

打开目标课程详情页，URL 通常类似：

```text
https://icourse.fudan.edu.cn/coursedetail?course_id=COURSE_ID&tenant_code=TENANT_CODE
```

记录两个参数：

- `COURSE_ID`：课程 ID，例如 URL 里的 `course_id`
- `TENANT_CODE`：租户代码，例如 URL 里的 `tenant_code`

后续命令里凡是出现 `COURSE_ID`、`TENANT_CODE` 的地方，都替换成目标课程的真实值。

## 3. 登录课程页

在浏览器里打开目标课程详情页，并确认自己已经登录、能看到课程回放列表。脚本不保存账号密码，推荐直接复用浏览器里的登录态生成索引文件。

## 4. 生成课件索引 JSON

推荐使用 Safari 的 AppleScript 方式生成索引。这个索引文件只记录课程课次和课件截图接口返回值，不需要手动复制 token。

### 4.1 开启 Safari JavaScript 自动化

Safari 菜单栏打开：

```text
Develop -> Allow JavaScript from Apple Events
```

如果没有 `Develop` 菜单，先到：

```text
Safari -> Settings -> Advanced -> Show features for web developers
```

然后确保 Safari 当前标签页是目标课程详情页，域名是：

```text
icourse.fudan.edu.cn
```

### 4.2 运行索引生成命令

把下面命令中的 `COURSE_ID` 和 `TENANT_CODE` 替换成目标课程参数，同时把输出文件名里的 `COURSE_ID` 也替换掉。

```bash
mkdir -p tmp/icourse

osascript <<'APPLESCRIPT' > tmp/icourse/icourse_COURSE_ID_ppt_index.json
tell application id "com.apple.Safari"
  set js to "
(async () => {
  const COURSE_ID = 'COURSE_ID';
  const TENANT_CODE = 'TENANT_CODE';

  function authHeaders() {
    const rawCookieToken = (document.cookie.match(/(?:^|;\\s*)_token=([^;]+)/) || [])[1] || '';
    let token = rawCookieToken ? decodeURIComponent(rawCookieToken) : (localStorage.getItem('token') || '');
    const match = token.match(/_token\\\";i:\\d+;s:\\d+:\\\"(.+?)\\\"/) || token.match(/_token[^\\\"]*\\\"(.+?)\\\"/);
    if (match) token = match[1];
    return token ? { Authorization: token.startsWith('Bearer ') ? token : `Bearer ${token}` } : {};
  }

  async function getJson(path, query = {}) {
    const url = new URL(path, location.origin);
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    });
    const res = await fetch(url, { headers: authHeaders() });
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch (error) {
      return { parse_error: String(error), status: res.status, text: text.slice(0, 1000) };
    }
  }

  const catalogue = await getJson('/courseapi/v2/course/catalogue', { course_id: COURSE_ID });
  const rawLessons = [];

  function walk(value) {
    if (Array.isArray(value)) {
      value.forEach(walk);
    } else if (value && typeof value === 'object') {
      if (value.sub_id || value.id) rawLessons.push(value);
      Object.values(value).forEach(walk);
    }
  }

  walk(catalogue);

  const seen = new Set();
  const lessons = [];
  for (const item of rawLessons) {
    const subId = String(item.sub_id || item.id || '');
    if (!subId || seen.has(subId)) continue;
    seen.add(subId);

    const title = item.sub_title || item.title || item.name || item.subject_name || '';
    const status = String(item.sub_status || item.status || '');
    const pptIndex = await getJson('/pptnote/v1/schedule/search-ppt', {
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
      ppt_index: pptIndex
    });
  }

  return JSON.stringify({
    course_id: COURSE_ID,
    tenant_code: TENANT_CODE,
    generated_at: new Date().toISOString(),
    lessons
  }, null, 2);
})()
"
  do JavaScript js in document 1
end tell
APPLESCRIPT
```

生成后可以简单检查：

```bash
python3 -m json.tool tmp/icourse/icourse_COURSE_ID_ppt_index.json > /dev/null
```

如果 JSON 文件为空或不是合法 JSON，通常是 Safari 没有开启 Apple Events JavaScript，或者当前标签页没有停在 `icourse.fudan.edu.cn`。

## 5. 批量生成每节课 PDF

索引 JSON 生成后，运行：

```bash
python3 scripts/fudan_icourse_slides.py \
  --browser-json tmp/icourse/icourse_COURSE_ID_ppt_index.json \
  --out-dir output/icourse_COURSE_ID_slides
```

脚本会自动：

- 读取每节课的 `sub_id`
- 提取该节课的 PPT 截图 URL
- 下载原图，过滤缩略图
- 按顺序合成 PDF
- 跳过没有 PPT 截图索引的课次

输出目录里通常会有：

- `课程名_sub_id.pdf`：合成后的 PDF
- `课程名_sub_id/downloads/`：下载到的图片
- `课程名_sub_id/urls.txt`：识别到的图片 URL
- `课程名_sub_id/api_payloads.json`：用于排查的接口数据

## 6. 只抓某一节课

如果已经知道某节课的 `sub_id`，可以只生成这一节：

```bash
python3 scripts/fudan_icourse_slides.py \
  --browser-json tmp/icourse/icourse_COURSE_ID_ppt_index.json \
  --sub-id SUB_ID \
  --out-dir output/icourse_COURSE_ID_slides
```

`SUB_ID` 可以从索引 JSON 里查：

```bash
python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("tmp/icourse/icourse_COURSE_ID_ppt_index.json").read_text())
for lesson in data.get("lessons", []):
    print(lesson.get("sub_id"), lesson.get("title") or lesson.get("date"))
PY
```

## 7. 换一门课程时怎么做

换课程只需要重新做三件事：

1. 在浏览器打开新课程详情页并登录。
2. 重新运行索引生成命令，替换 `COURSE_ID` 和 `TENANT_CODE`。
3. 用新的索引 JSON 运行批量生成命令。

建议每门课使用独立输出目录：

```text
output/icourse_COURSE_ID_slides
```

这样不同课程的 PDF 和下载图片不会混在一起。

## 8. 常见问题

### 生成的 PDF 数量少于课次数量

常见原因：

- 某节课本身没有 PPT。
- 回放还在生成中。
- 平台 PPT 识别失败，视频能看，但 `/pptnote/v1/schedule/search-ppt` 返回 `total: 0`。
- 课程目录里有已删除、未发布或不可访问的课次。

可以检查哪些课没有 PPT 截图索引：

```bash
python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("tmp/icourse/icourse_COURSE_ID_ppt_index.json").read_text())
for lesson in data.get("lessons", []):
    ppt = lesson.get("ppt_index") or {}
    if int(ppt.get("total") or 0) == 0:
        print(lesson.get("sub_id"), lesson.get("title") or lesson.get("date"), "no ppt screenshots")
PY
```

如果某节课 `ppt_index.total` 是 `0`，说明平台没有给出 PPT 截图索引，脚本不会凭空生成 PDF。

### 出现 Auth Forbidden 或下载失败

说明登录态可能失效。重新在浏览器登录课程页，然后重新生成索引 JSON。

### Safari 提示不允许执行 JavaScript

确认已经打开：

```text
Develop -> Allow JavaScript from Apple Events
```

并且 Safari 当前标签页是目标课程页。

### 图片顺序不对

脚本会按文件名里的数字自然排序。正常情况下平台图片名就是页码顺序。如果某门课文件名异常，可以检查：

```text
output/icourse_COURSE_ID_slides/<lesson>/urls.txt
```

必要时可以手动调整图片文件名后再重新合成 PDF。

### 某节课只有视频，没有 PPT

这不是脚本问题。可以选择从视频里抽帧做近似讲义，但那不是平台原始 PPT 截图。

## 9. 安全说明

- 不要把账号密码写进脚本或教程。
- 不要公开包含个人登录态的 cookie、token 或完整浏览器会话信息。
- 本流程只使用已登录浏览器当前用户可访问的课程资源。
