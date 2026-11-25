"""Local pages for dev tasks: sticker packs, chat themes, daily buddies, and deploy.

Run from this folder:

    .venv\\Scripts\\python server.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from daily_buddy import pair_daily_buddies
from deploy_github import trigger_deploy_latest_main
from sticker_pack import (
    DEV_BUCKET,
    DEV_PROFILE,
    FolderScan,
    PackError,
    audience_lock,
    build_manifest,
    build_objects,
    check_audience,
    check_pack_id,
    clean_folder,
    cover_choice,
    inspect_folder,
    pack_id_from_name,
    stale_keys,
)
from sticker_s3 import StickerBucket
from wallpaper_theme import (
    RESERVED_THEME_IDS,
    ThemeError,
    background_name,
    build_theme_manifest,
    build_theme_objects,
    check_theme_id,
    clean_image,
    preview_name,
    read_sidecar_manifest,
    theme_id_from_name,
    theme_prefix,
)

HOST = "127.0.0.1"
PORT = 8765
_browse_lock = threading.Lock()


def preview_pack(body: dict) -> dict:
    scan = inspect_folder(clean_folder(body.get("folder", "")))
    pack_id = _optional_pack_id(body, scan)
    remote_name = ""
    remote_version = None
    exists = False
    if pack_id:
        bucket = StickerBucket(_profile(body))
        exists = bool(bucket.list_keys(pack_id))
        remote = bucket.read_manifest(pack_id) if exists else None
        if remote:
            remote_name = str(remote.get("name") or pack_id)
            if isinstance(remote.get("version"), int):
                remote_version = remote["version"]
    folder = scan.folder_manifest
    return {
        "bucket": DEV_BUCKET,
        "pack_id": pack_id,
        "stickers": [
            {"file": item.file, "label": item.label, "size": item.size} for item in scan.stickers
        ],
        "cover_file": scan.cover_file,
        "ignored": scan.ignored,
        "exists": exists,
        "remote_name": remote_name,
        "remote_version": remote_version,
        "suggested_version": remote_version + 1 if remote_version else 1,
        "audience_lock": audience_lock(pack_id) if pack_id else None,
        "folder": {
            "name": str(folder.get("name") or ""),
            "description": str(folder.get("description") or ""),
            "audience": str(folder.get("audience") or "").strip().lower(),
            "order": folder.get("order") if isinstance(folder.get("order"), int) else None,
        },
    }


def upload_pack(body: dict) -> dict:
    scan = inspect_folder(clean_folder(body.get("folder", "")))
    name = str(body.get("name") or scan.folder_manifest.get("name") or "").strip()
    pack_id = _optional_pack_id(body, scan) or pack_id_from_name(name)
    audience = check_audience(pack_id, str(body.get("audience") or "both"))
    manifest = build_manifest(
        name=name,
        description=str(body.get("description") or ""),
        order=_number(body.get("order"), 100, "List position"),
        version=_number(body.get("version"), 1, "Version"),
        audience=audience,
        labels=scan.labels,
    )
    cover = cover_choice(scan, str(body.get("cover") or ""))
    objects = build_objects(scan, pack_id=pack_id, manifest=manifest, cover=cover)
    bucket = StickerBucket(_profile(body))
    existing = bucket.list_keys(pack_id)
    if existing and not body.get("replace"):
        raise PackError(
            f"{pack_id} is already on the dev app. Tick “Replace the pack already on dev” and upload again."
        )
    for item in objects:
        bucket.put(item)
    removed = stale_keys(existing, {item.key for item in objects})
    if removed:
        bucket.delete(removed)
    return {
        "pack_id": pack_id,
        "name": manifest["name"],
        "audience": audience,
        "sticker_count": len(scan.stickers),
        "replaced": bool(existing),
        "removed": len(removed),
        "bucket": DEV_BUCKET,
    }


def _optional_pack_id(body: dict, scan: FolderScan) -> str:
    raw = str(body.get("pack_id") or "").strip()
    if raw:
        return check_pack_id(raw)
    name = str(body.get("name") or scan.folder_manifest.get("name") or "").strip()
    if not name:
        return ""
    return pack_id_from_name(name)


def _profile(body: dict) -> str:
    return str(body.get("profile") or DEV_PROFILE).strip() or DEV_PROFILE


def _number(value, default: int, label: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PackError(f"{label} must be a number.") from exc


def preview_theme(body: dict) -> dict:
    image = clean_image(str(body.get("image") or ""), label="wallpaper image", required=True)
    preview = _preview_image(body, image)
    assert image is not None
    theme_id = _optional_theme_id(body, image)
    remote_name = ""
    remote_version = None
    exists = False
    if theme_id:
        bucket = StickerBucket(_profile(body))
        exists = bool(bucket.list_prefix(theme_prefix(theme_id)))
        remote = bucket.read_json(f"{theme_prefix(theme_id)}manifest.json") if exists else None
        if remote:
            remote_name = str(remote.get("name") or theme_id)
            if isinstance(remote.get("version"), int):
                remote_version = remote["version"]
    sidecar = read_sidecar_manifest(image)
    order = sidecar.get("order")
    blocked_id = ""
    if not str(body.get("theme_id") or "").strip():
        candidate_name = str(body.get("name") or sidecar.get("name") or "").strip()
        if candidate_name:
            try:
                candidate = theme_id_from_name(candidate_name)
            except ThemeError:
                candidate = ""
            if candidate in RESERVED_THEME_IDS:
                blocked_id = candidate
    return {
        "bucket": DEV_BUCKET,
        "theme_id": theme_id,
        "background_file": image.name,
        "background_key": f"{theme_prefix(theme_id)}{background_name(image)}" if theme_id else background_name(image),
        "size": image.stat().st_size,
        "preview_file": preview.name if preview else "",
        "preview_key": f"{theme_prefix(theme_id)}{preview_name(preview)}" if theme_id and preview else "",
        "exists": exists,
        "remote_name": remote_name,
        "remote_version": remote_version,
        "suggested_version": remote_version + 1 if remote_version else 1,
        "blocked_id": blocked_id,
        "sidecar": {
            "name": str(sidecar.get("name") or ""),
            "description": str(sidecar.get("description") or ""),
            "order": order if isinstance(order, int) else None,
            "fallback_color": str(sidecar.get("fallback_color") or ""),
        },
    }


def upload_theme(body: dict) -> dict:
    image = clean_image(str(body.get("image") or ""), label="wallpaper image", required=True)
    preview = _preview_image(body, image)
    assert image is not None
    sidecar = read_sidecar_manifest(image)
    name = str(body.get("name") or sidecar.get("name") or "").strip()
    theme_id = check_theme_id(_optional_theme_id(body, image) or theme_id_from_name(name))
    manifest = build_theme_manifest(
        name=name,
        description=str(body.get("description") or ""),
        order=_number(body.get("order"), 100, "List position"),
        version=_number(body.get("version"), 1, "Version"),
        fallback_color=str(body.get("fallback_color") or ""),
    )
    objects = build_theme_objects(image, preview, theme_id=theme_id, manifest=manifest)
    bucket = StickerBucket(_profile(body))
    existing = bucket.list_prefix(theme_prefix(theme_id))
    if existing and not body.get("replace"):
        raise ThemeError(
            f"{theme_id} is already on the dev app. Tick “Replace the theme already on dev” and upload again."
        )
    for item in objects:
        bucket.put(item)
    removed = stale_keys(existing, {item.key for item in objects})
    if removed:
        bucket.delete(removed)
    return {
        "theme_id": theme_id,
        "name": manifest["name"],
        "replaced": bool(existing),
        "removed": len(removed),
        "bucket": DEV_BUCKET,
    }


def _preview_image(body: dict, image):
    preview = clean_image(str(body.get("preview") or ""), label="preview image", required=False)
    if preview is not None and image is not None and preview.resolve() == image.resolve():
        raise ThemeError("Preview is the same file as the wallpaper. Leave preview empty to use the wallpaper.")
    return preview


def _optional_theme_id(body: dict, image) -> str:
    raw = str(body.get("theme_id") or "").strip()
    if raw:
        return check_theme_id(raw)
    sidecar = read_sidecar_manifest(image)
    name = str(body.get("name") or sidecar.get("name") or "").strip()
    if not name:
        return ""
    derived = theme_id_from_name(name)
    if derived in RESERVED_THEME_IDS:
        return ""
    return derived


def browse_folder() -> str:
    return _pick("folder", "Sticker pack folder")


def browse_file(title: str) -> str:
    return _pick("file", title or "Wallpaper image")


def _pick(kind: str, title: str) -> str:
    if not _browse_lock.acquire(blocking=False):
        raise PackError("A picker is already open.")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        if kind == "file":
            chosen = filedialog.askopenfilename(
                title=title,
                filetypes=[
                    ("Wallpaper image", "*.jpg *.jpeg *.png *.webp"),
                    ("All files", "*.*"),
                ],
            )
        else:
            chosen = filedialog.askdirectory(title=title)
        root.destroy()
        return chosen or ""
    except PackError:
        raise
    except Exception as exc:
        label = "file" if kind == "file" else "folder"
        raise PackError(f"Could not open the {label} picker. Paste the path instead. ({exc})") from exc
    finally:
        _browse_lock.release()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        page = {"/": HOME, "/stickers": STICKERS, "/themes": THEMES, "/daily-buddy": BUDDY, "/deploy": DEPLOY}.get(path)
        if page is None:
            self._send(404, {"error": "Not found."})
            return
        self._html(page)

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            self._send(403, {"error": "This page only accepts requests from itself."})
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/browse":
                self._send(200, {"folder": browse_folder()})
                return
            if path == "/api/deploy":
                self._send(200, trigger_deploy_latest_main())
                return
            if path == "/api/daily-buddy":
                body = self._body()
                self._send(200, pair_daily_buddies(str(body.get("left") or ""), str(body.get("right") or "")))
                return
            body = self._body()
            if path == "/api/browse-file":
                self._send(200, {"file": browse_file(str(body.get("title") or ""))})
                return
            if path == "/api/preview":
                self._send(200, preview_pack(body))
                return
            if path == "/api/upload":
                self._send(200, upload_pack(body))
                return
            if path == "/api/theme/preview":
                self._send(200, preview_theme(body))
                return
            if path == "/api/theme/upload":
                self._send(200, upload_theme(body))
                return
            self._send(404, {"error": "Not found."})
        except PackError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — last resort so the page shows the failure
            self._send(500, {"error": f"Request failed: {exc}"})

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 1_000_000:
            raise PackError("That request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PackError("The form sent something this page could not read.") from exc
        if not isinstance(data, dict):
            raise PackError("The form sent something this page could not read.")
        return data

    def _html(self, page: str) -> None:
        html = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


HOME = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LocalCircle handler</title>
<style>
  body { font: 16px/1.45 "Segoe UI", sans-serif; margin: 32px auto; max-width: 720px; color: #1c1c1c; padding: 0 16px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  p.note { margin-top: 0; color: #444; }
  .cards { display: flex; flex-direction: column; gap: 12px; margin-top: 24px; }
  a.card { display: block; border: 1px solid #d0d0d0; border-radius: 8px; padding: 16px; text-decoration: none; color: inherit; }
  a.card:hover { border-color: #1769aa; }
  .card h2 { margin: 0 0 4px; font-size: 18px; }
  .card p { margin: 0; color: #444; }
</style>
</head>
<body>
  <h1>LocalCircle handler</h1>
  <p class="note">These pages talk to the dev app only. Nothing here publishes to prod.</p>
  <div class="cards">
    <a class="card" href="/stickers">
      <h2>Add a sticker pack</h2>
      <p>Upload a folder of webp or png stickers to the dev app.</p>
    </a>
    <a class="card" href="/themes">
      <h2>Add a theme</h2>
      <p>Upload one chat wallpaper to the dev app.</p>
    </a>
    <a class="card" href="/daily-buddy">
      <h2>Make a daily buddy</h2>
      <p>Pair two phone numbers as today's daily buddies on dev.</p>
    </a>
    <a class="card" href="/deploy">
      <h2>Deploy latest main to dev</h2>
      <p>Build and roll LokalCircleCore and LocalCircle main onto both dev servers.</p>
    </a>
  </div>
</body>
</html>
"""

BUDDY = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Make a daily buddy</title>
<style>
  body { font: 16px/1.45 "Segoe UI", sans-serif; margin: 32px auto; max-width: 720px; color: #1c1c1c; padding: 0 16px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  p.note { margin-top: 0; color: #444; }
  label { display: block; margin-top: 16px; font-weight: 600; }
  input { font: inherit; width: 100%; max-width: 280px; padding: 8px; margin-top: 4px; box-sizing: border-box; }
  button { font: inherit; padding: 8px 14px; cursor: pointer; }
  button.primary { background: #1769aa; color: white; border: 0; padding: 10px 16px; margin-top: 20px; }
  button:disabled { opacity: 0.6; cursor: default; }
  #status { margin-top: 16px; white-space: pre-wrap; }
  .ok { color: #0d6b2f; }
  .bad { color: #9d1c1c; }
  .nav { margin: 0 0 8px; }
  .nav a { color: #1769aa; }
</style>
</head>
<body>
  <p class="nav"><a href="/">Home</a></p>
  <h1>Make a daily buddy</h1>
  <p class="note">Pairs two dev accounts for today. Each person can have one daily buddy a day. If either already has a different open buddy today, nothing is changed.</p>
  <label for="left">First number</label>
  <input id="left" inputmode="numeric" autocomplete="off" placeholder="9876543211">
  <label for="right">Second number</label>
  <input id="right" inputmode="numeric" autocomplete="off" placeholder="8307847231">
  <p><button type="button" class="primary" id="pair">Make daily buddy</button></p>
  <div id="status"></div>
<script>
const status = document.getElementById("status");
const pair = document.getElementById("pair");

function setStatus(text, kind) {
  status.className = kind || "";
  status.textContent = text;
}

pair.onclick = async () => {
  pair.disabled = true;
  setStatus("Pairing on dev…");
  try {
    const response = await fetch("/api/daily-buddy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        left: document.getElementById("left").value,
        right: document.getElementById("right").value
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed.");
    setStatus(data.message, "ok");
  } catch (error) {
    setStatus(error.message, "bad");
  } finally {
    pair.disabled = false;
  }
};
</script>
</body>
</html>
"""

DEPLOY = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Deploy latest main to dev</title>
<style>
  body { font: 16px/1.45 "Segoe UI", sans-serif; margin: 32px auto; max-width: 720px; color: #1c1c1c; padding: 0 16px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  p.note { margin-top: 0; color: #444; }
  button { font: inherit; padding: 8px 14px; cursor: pointer; }
  button.primary { background: #1769aa; color: white; border: 0; padding: 10px 16px; }
  button:disabled { opacity: 0.6; cursor: default; }
  #status { margin-top: 16px; white-space: pre-wrap; }
  .ok { color: #0d6b2f; }
  .bad { color: #9d1c1c; }
  .nav { margin: 0 0 8px; }
  .nav a { color: #1769aa; }
  a.run { color: #1769aa; }
</style>
</head>
<body>
  <p class="nav"><a href="/">Home</a></p>
  <h1>Deploy latest main to dev</h1>
  <p class="note">Starts the GitHub Actions workflow that builds <strong>LokalCircleCore@main</strong> and <strong>LocalCircle@main</strong>, then rolls both dev servers. Takes about 15–20 minutes. This does not publish to prod.</p>
  <p style="margin-top:24px">
    <button type="button" class="primary" id="deploy">Deploy latest main to dev</button>
  </p>
  <div id="status"></div>
<script>
const status = document.getElementById("status");
const deploy = document.getElementById("deploy");

function setStatus(text, kind, runUrl) {
  status.className = kind || "";
  status.textContent = "";
  status.appendChild(document.createTextNode(text));
  if (runUrl) {
    status.appendChild(document.createTextNode(String.fromCharCode(10)));
    const link = document.createElement("a");
    link.className = "run";
    link.href = runUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open the GitHub run";
    status.appendChild(link);
  }
}

deploy.onclick = async () => {
  deploy.disabled = true;
  setStatus("Starting deploy on GitHub…");
  try {
    const response = await fetch("/api/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed.");
    setStatus(data.message, "ok", data.run_url || data.actions_url);
  } catch (error) {
    setStatus(error.message, "bad");
  } finally {
    deploy.disabled = false;
  }
};
</script>
</body>
</html>
"""

STICKERS = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Add a sticker pack</title>
<style>
  body { font: 16px/1.45 "Segoe UI", sans-serif; margin: 32px auto; max-width: 720px; color: #1c1c1c; padding: 0 16px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  p.note { margin-top: 0; color: #444; }
  label { display: block; margin-top: 16px; font-weight: 600; }
  #replace-row[hidden] { display: none; }
  .hint { font-weight: 400; color: #555; }
  input, select, textarea { width: 100%; box-sizing: border-box; margin-top: 4px; padding: 8px; font: inherit; }
  input[type="checkbox"] { width: auto; margin-right: 8px; }
  textarea { min-height: 72px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row input { flex: 1; }
  button { font: inherit; padding: 8px 14px; cursor: pointer; }
  button.primary { background: #1769aa; color: white; border: 0; padding: 10px 16px; }
  button:disabled { opacity: 0.6; cursor: default; }
  #files { margin-top: 12px; white-space: pre-wrap; }
  #status { margin-top: 16px; white-space: pre-wrap; }
  .ok { color: #0d6b2f; }
  .bad { color: #9d1c1c; }
  .replace { margin-top: 16px; font-weight: 600; }
  .nav { margin: 0 0 8px; }
  .nav a { color: #1769aa; }
</style>
</head>
<body>
  <p class="nav"><a href="/">Home</a></p>
  <h1>Add a sticker pack</h1>
  <p class="note">Uploads to the dev sticker bucket <strong>localcircle-stickers-ap-south-1</strong>. The app reads that bucket. After it succeeds, open Stickers and pull to refresh. It can take about a minute. This does not publish to prod.</p>

  <label>Folder of sticker images <span class="hint">webp or png, loose in the folder</span>
    <span class="row">
      <input id="folder" placeholder="C:/stickers/monsoon">
      <button type="button" id="browse">Browse</button>
      <button type="button" id="check">Check folder</button>
    </span>
  </label>

  <label>Pack name <span class="hint">shown on the pack card</span>
    <input id="name" maxlength="120">
  </label>

  <label>Pack id <span class="hint">folder name on the bucket. Filled from the pack name. Leave it unless you need a specific id.</span>
    <input id="pack-id" maxlength="63" spellcheck="false">
  </label>

  <label>Description <span class="hint">optional</span>
    <textarea id="description" maxlength="400"></textarea>
  </label>

  <label>Who sees it
    <select id="audience">
      <option value="both">Male and female</option>
      <option value="male">Male only</option>
      <option value="female">Female only</option>
    </select>
  </label>

  <label>List position <span class="hint">lower numbers appear first. Everyday Desi is 1.</span>
    <input id="order" type="number" min="0" max="9999" value="100">
  </label>

  <label>Version
    <input id="version" type="number" min="1" max="9999" value="1">
  </label>

  <p class="note">The first sticker in the folder is the pack thumbnail.</p>

  <label class="replace" id="replace-row" hidden>
    <input id="replace" type="checkbox"> Replace the pack already on dev
  </label>

  <p><button class="primary" id="upload" type="button">Upload pack</button></p>
  <div id="files"></div>
  <div id="status"></div>

<script>
const $ = (id) => document.getElementById(id);
const status = $("status");

function formBody() {
  return {
    folder: $("folder").value,
    name: $("name").value,
    pack_id: $("pack-id").value,
    description: $("description").value,
    audience: $("audience").value,
    order: $("order").value,
    version: $("version").value,
    replace: $("replace").checked,
    profile: "vinit"
  };
}

function setStatus(text, kind) {
  status.className = kind || "";
  status.textContent = text;
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function fillIfEmpty(id, value) {
  const el = $(id);
  if (!el.value.trim() && value) el.value = value;
}

$("browse").onclick = async () => {
  setStatus("Choose a folder…");
  try {
    const data = await post("/api/browse");
    if (data.folder) $("folder").value = data.folder;
    setStatus(data.folder ? "" : "No folder selected.");
  } catch (error) {
    setStatus(error.message, "bad");
  }
};

$("check").onclick = async () => {
  $("check").disabled = true;
  setStatus("Checking the folder and the dev bucket…");
  try {
    const data = await post("/api/preview", formBody());
    fillIfEmpty("name", data.folder.name);
    fillIfEmpty("pack-id", data.pack_id);
    fillIfEmpty("description", data.folder.description);
    if (data.folder.audience === "male" || data.folder.audience === "female" || data.folder.audience === "both") {
      $("audience").value = data.folder.audience;
    }
    if (data.folder.order !== null && $("order").value === "100") $("order").value = data.folder.order;
    $("version").value = data.suggested_version;
    const lines = [data.stickers.length + " stickers. The first one is the thumbnail."];
    for (const sticker of data.stickers) lines.push(sticker.file + "  —  " + sticker.label);
    if (data.ignored.length) lines.push("", "Not uploaded:", ...data.ignored);
    if (data.audience_lock) lines.push("", "This pack id is fixed as " + data.audience_lock + " in the app.");
    $("files").textContent = lines.join(String.fromCharCode(10));
    $("replace-row").hidden = !data.exists;
    $("replace").checked = false;
    $("upload").textContent = data.exists ? "Replace pack" : "Upload pack";
    setStatus(data.exists
      ? "A pack with this id is already on dev" + (data.remote_name ? " (“" + data.remote_name + "”)" : "") + ". Tick replace to overwrite it. Version is set to " + data.suggested_version + "."
      : "Ready to upload " + data.stickers.length + " stickers as " + (data.pack_id || "a new pack") + ".",
      data.exists ? "bad" : "ok");
  } catch (error) {
    $("files").textContent = "";
    setStatus(error.message, "bad");
  } finally {
    $("check").disabled = false;
  }
};

$("upload").onclick = async () => {
  $("upload").disabled = true;
  setStatus("Uploading to the dev bucket…");
  try {
    const data = await post("/api/upload", formBody());
    const who = data.audience === "both" ? "male and female" : data.audience;
    const lines = [
      "Uploaded “" + data.name + "” (" + data.pack_id + ") with " + data.sticker_count + " stickers for " + who + ".",
      "Open Stickers in the app and pull to refresh. It can take about a minute."
    ];
    if (data.replaced) lines.push("Phones that already added this pack keep the old copy until they remove it and add it again.");
    setStatus(lines.join(String.fromCharCode(10)), "ok");
  } catch (error) {
    setStatus(error.message, "bad");
  } finally {
    $("upload").disabled = false;
  }
};
</script>
</body>
</html>
"""


THEMES = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Add a theme</title>
<style>
  body { font: 16px/1.45 "Segoe UI", sans-serif; margin: 32px auto; max-width: 720px; color: #1c1c1c; padding: 0 16px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  p.note { margin-top: 0; color: #444; }
  label { display: block; margin-top: 16px; font-weight: 600; }
  #replace-row[hidden] { display: none; }
  .hint { font-weight: 400; color: #555; }
  input, textarea { width: 100%; box-sizing: border-box; margin-top: 4px; padding: 8px; font: inherit; }
  input[type="checkbox"] { width: auto; margin-right: 8px; }
  textarea { min-height: 72px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row input { flex: 1; }
  button { font: inherit; padding: 8px 14px; cursor: pointer; }
  button.primary { background: #1769aa; color: white; border: 0; padding: 10px 16px; }
  button:disabled { opacity: 0.6; cursor: default; }
  #files { margin-top: 12px; white-space: pre-wrap; }
  #status { margin-top: 16px; white-space: pre-wrap; }
  .ok { color: #0d6b2f; }
  .bad { color: #9d1c1c; }
  .replace { margin-top: 16px; font-weight: 600; }
  .nav { margin: 0 0 8px; }
  .nav a { color: #1769aa; }
</style>
</head>
<body>
  <p class="nav"><a href="/">Home</a></p>
  <h1>Add a theme</h1>
  <p class="note">Uploads one chat wallpaper to the dev bucket <strong>localcircle-stickers-ap-south-1</strong>, under <strong>wallpapers/</strong>. The app reads that bucket. After it succeeds, open a chat, then Theme store, and pull to refresh. It can take about a minute. This does not publish to prod.</p>

  <label>Wallpaper image <span class="hint">jpg, png, or webp</span>
    <span class="row">
      <input id="image" placeholder="C:/themes/soft_rain.jpg">
      <button type="button" id="browse-image">Browse</button>
      <button type="button" id="check">Check image</button>
    </span>
  </label>

  <label>Preview image <span class="hint">optional. Leave empty to use the wallpaper.</span>
    <span class="row">
      <input id="preview" placeholder="C:/themes/soft_rain_preview.jpg">
      <button type="button" id="browse-preview">Browse</button>
    </span>
  </label>

  <label>Theme name <span class="hint">shown in the theme store</span>
    <input id="name" maxlength="120">
  </label>

  <label>Theme id <span class="hint">folder name on the bucket. Filled from the theme name. Leave it unless you need a specific id.</span>
    <input id="theme-id" maxlength="63" spellcheck="false">
  </label>

  <label>Description <span class="hint">optional</span>
    <textarea id="description" maxlength="240"></textarea>
  </label>

  <label>List position <span class="hint">lower numbers appear first</span>
    <input id="order" type="number" min="0" max="9999" value="100">
  </label>

  <label>Version
    <input id="version" type="number" min="1" max="9999" value="1">
  </label>

  <label>Fallback color <span class="hint">shown while the image loads</span>
    <input id="color" maxlength="7" value="#D7F0DE" spellcheck="false">
  </label>

  <label class="replace" id="replace-row" hidden>
    <input id="replace" type="checkbox"> Replace the theme already on dev
  </label>

  <p><button class="primary" id="upload" type="button">Upload theme</button></p>
  <div id="files"></div>
  <div id="status"></div>

<script>
const $ = (id) => document.getElementById(id);
const status = $("status");

function formBody() {
  return {
    image: $("image").value,
    preview: $("preview").value,
    name: $("name").value,
    theme_id: $("theme-id").value,
    description: $("description").value,
    order: $("order").value,
    version: $("version").value,
    fallback_color: $("color").value,
    replace: $("replace").checked,
    profile: "vinit"
  };
}

function setStatus(text, kind) {
  status.className = kind || "";
  status.textContent = text;
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function fillIfEmpty(id, value) {
  const el = $(id);
  if (!el.value.trim() && value) el.value = value;
}

async function browseInto(inputId, title) {
  setStatus("Choose an image…");
  try {
    const data = await post("/api/browse-file", { title });
    if (data.file) $(inputId).value = data.file;
    setStatus(data.file ? "" : "No image selected.");
  } catch (error) {
    setStatus(error.message, "bad");
  }
}

$("browse-image").onclick = () => browseInto("image", "Wallpaper image");
$("browse-preview").onclick = () => browseInto("preview", "Preview image");

$("check").onclick = async () => {
  $("check").disabled = true;
  setStatus("Checking the image and the dev bucket…");
  try {
    const data = await post("/api/theme/preview", formBody());
    fillIfEmpty("name", data.sidecar.name);
    fillIfEmpty("theme-id", data.theme_id);
    fillIfEmpty("description", data.sidecar.description);
    if (data.sidecar.order !== null && $("order").value === "100") $("order").value = data.sidecar.order;
    if (data.sidecar.fallback_color && $("color").value.toUpperCase() === "#D7F0DE") {
      $("color").value = data.sidecar.fallback_color;
    }
    $("version").value = data.suggested_version;
    const kb = Math.max(1, Math.round(data.size / 1024));
    const lines = [data.background_file + "  —  " + kb + " KB", "Uploads as " + data.background_key];
    if (data.preview_file) lines.push("Preview " + data.preview_file + " uploads as " + data.preview_key);
    $("files").textContent = lines.join(String.fromCharCode(10));
    $("replace-row").hidden = !data.exists;
    $("replace").checked = false;
    $("upload").textContent = data.exists ? "Replace theme" : "Upload theme";
    if (data.blocked_id) {
      setStatus("“" + data.blocked_id + "” is already in the app, so phones would not show this upload. Type a new theme id, then check again.", "bad");
    } else if (data.exists) {
      setStatus("A theme with this id is already on dev" + (data.remote_name ? " (“" + data.remote_name + "”)" : "") + ". Tick replace to overwrite it. Version is set to " + data.suggested_version + ".", "bad");
    } else {
      setStatus("Ready to upload “" + (data.sidecar.name || $("name").value || data.theme_id || "this theme") + "”.", "ok");
    }
  } catch (error) {
    $("files").textContent = "";
    setStatus(error.message, "bad");
  } finally {
    $("check").disabled = false;
  }
};

$("upload").onclick = async () => {
  $("upload").disabled = true;
  setStatus("Uploading to the dev bucket…");
  try {
    const data = await post("/api/theme/upload", formBody());
    const lines = [
      "Uploaded “" + data.name + "” (" + data.theme_id + ").",
      "Open a chat, then Theme store, and pull to refresh. It can take about a minute."
    ];
    if (data.replaced) lines.push("Chats already using this theme keep the old picture until the phone downloads it again.");
    setStatus(lines.join(String.fromCharCode(10)), "ok");
  } catch (error) {
    setStatus(error.message, "bad");
  } finally {
    $("upload").disabled = false;
  }
};
</script>
</body>
</html>
"""


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"LocalCircle handler: http://{HOST}:{PORT}")
    print(f"Dev bucket: {DEV_BUCKET}  profile: {DEV_PROFILE}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
