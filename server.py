"""Local page for uploading a sticker pack to the dev bucket.

Run from this folder:

    .venv\\Scripts\\python server.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

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


def browse_folder() -> str:
    if not _browse_lock.acquire(blocking=False):
        raise PackError("A folder picker is already open.")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        chosen = filedialog.askdirectory(title="Sticker pack folder")
        root.destroy()
        return chosen or ""
    except PackError:
        raise
    except Exception as exc:
        raise PackError(f"Could not open the folder picker. Paste the path instead. ({exc})") from exc
    finally:
        _browse_lock.release()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/":
            self._send(404, {"error": "Not found."})
            return
        html = (INDEX).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            self._send(403, {"error": "This page only accepts requests from itself."})
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/browse":
                self._send(200, {"folder": browse_folder()})
                return
            body = self._body()
            if path == "/api/preview":
                self._send(200, preview_pack(body))
                return
            if path == "/api/upload":
                self._send(200, upload_pack(body))
                return
            self._send(404, {"error": "Not found."})
        except PackError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — last resort so the page shows the failure
            self._send(500, {"error": f"Upload failed: {exc}"})

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

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


INDEX = """<!DOCTYPE html>
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
</style>
</head>
<body>
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

  <label>Cover
    <select id="cover"></select>
  </label>

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
    cover: $("cover").value,
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
    const cover = $("cover");
    cover.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = data.cover_file ? "Use " + data.cover_file : "First sticker";
    cover.appendChild(auto);
    for (const sticker of data.stickers) {
      const option = document.createElement("option");
      option.value = sticker.file;
      option.textContent = sticker.file;
      cover.appendChild(option);
    }
    const lines = [data.stickers.length + " stickers:"];
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


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Add a sticker pack: http://{HOST}:{PORT}")
    print(f"Dev bucket: {DEV_BUCKET}  profile: {DEV_PROFILE}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
