"""Build a sticker pack the chat API can read from a folder of images.

The chat service lists ``packs/{pack_id}/`` on the sticker bucket. A pack is
webp or png files, an optional cover, and a manifest. No app release is involved.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PACK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_LEADING_INDEX = re.compile(r"^\d+_")
_NON_ID = re.compile(r"[^a-z0-9]+")

STICKER_EXTENSIONS = {".webp", ".png"}
MAX_STICKERS = 100
MAX_FILE_BYTES = 4 * 1024 * 1024
DEV_BUCKET = "localcircle-stickers-ap-south-1"
DEV_REGION = "ap-south-1"
DEV_PROFILE = "vinit"


class PackError(Exception):
    """The folder or the form cannot become a pack."""


@dataclass
class StickerFile:
    file: str
    slug: str
    label: str
    size: int


@dataclass
class FolderScan:
    folder: Path
    stickers: list[StickerFile]
    cover_file: str | None
    ignored: list[str]
    folder_manifest: dict
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class UploadObject:
    key: str
    content_type: str
    data: bytes


def clean_folder(raw: str) -> Path:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise PackError("Enter the folder that holds the sticker images.")
    path = Path(text).expanduser()
    if not path.exists():
        raise PackError(f"That folder does not exist: {path}")
    if not path.is_dir():
        raise PackError("That path is a file. Pass the folder that contains the images.")
    return path


def pack_id_from_name(name: str) -> str:
    text = _NON_ID.sub("_", (name or "").strip().lower()).strip("_")
    text = text[:63]
    if not PACK_ID.match(text):
        raise PackError("Pack name needs a letter or number so a pack id can be made.")
    return text


def check_pack_id(pack_id: str) -> str:
    text = (pack_id or "").strip().lower()
    if not PACK_ID.match(text):
        raise PackError(
            "Pack id must be lowercase letters, numbers, _ or -, start with a letter or number, and be at most 63 characters."
        )
    return text


def audience_lock(pack_id: str) -> str | None:
    """These ids are fixed in the app and the chat API. A manifest cannot change them."""
    if pack_id == "everyday_desi" or pack_id.startswith("everyday_desi"):
        return "both"
    if pack_id == "male_office":
        return "male"
    if pack_id == "female_office":
        return "female"
    return None


def humanize_slug(slug: str) -> str:
    text = _LEADING_INDEX.sub("", slug).replace("_", " ").replace("-", " ").strip()
    if not text:
        return "Sticker"
    return (text[:1].upper() + text[1:])[:80]


def content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".zip"):
        return "application/zip"
    if lower.endswith(".json"):
        return "application/json"
    raise PackError(f"Cannot upload {name}.")


def _read_manifest(folder: Path) -> dict:
    path = folder / "manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"manifest.json in that folder is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError("manifest.json must be a JSON object.")
    return data


def inspect_folder(folder: Path) -> FolderScan:
    manifest = _read_manifest(folder)
    raw_labels = manifest.get("labels") if isinstance(manifest.get("labels"), dict) else {}
    labels = {
        str(key): str(value).strip()[:80]
        for key, value in raw_labels.items()
        if str(value).strip()
    }
    stickers: list[StickerFile] = []
    cover: str | None = None
    ignored: list[str] = []
    for entry in folder.iterdir():
        name = entry.name
        if name.startswith("."):
            continue
        if entry.is_dir():
            ignored.append(f"{name}/ (a subfolder is not uploaded)")
            continue
        if not entry.is_file():
            continue
        lower = name.lower()
        if lower in {"manifest.json", "pack.zip"}:
            continue
        if lower in {"cover.webp", "cover.png"}:
            cover = name
            _check_size(entry)
            continue
        if entry.suffix.lower() in STICKER_EXTENSIONS:
            _check_size(entry)
            slug = name.rsplit(".", 1)[0]
            label = labels.get(slug) or humanize_slug(slug)
            stickers.append(StickerFile(file=name, slug=slug, label=label, size=entry.stat().st_size))
            continue
        ignored.append(name)
    ignored.sort(key=str.lower)
    stickers.sort(key=lambda item: item.file)
    if len(stickers) > MAX_STICKERS:
        raise PackError(f"A pack can have at most {MAX_STICKERS} stickers. This folder has {len(stickers)}.")
    if not stickers:
        raise PackError("That folder has no .webp or .png stickers.")
    slugs = [item.slug for item in stickers]
    if len(slugs) != len(set(slugs)):
        raise PackError("Two stickers would share the same name with different extensions. Rename one of them.")
    return FolderScan(
        folder=folder,
        stickers=stickers,
        cover_file=cover,
        ignored=ignored,
        folder_manifest=manifest,
        labels={item.slug: item.label for item in stickers if item.slug in labels},
    )


def _check_size(path: Path) -> None:
    size = path.stat().st_size
    if size <= 0:
        raise PackError(f"{path.name} is empty.")
    if size > MAX_FILE_BYTES:
        raise PackError(f"{path.name} is larger than 4 MB.")


def check_audience(pack_id: str, audience: str) -> str:
    chosen = (audience or "").strip().lower()
    if chosen not in {"male", "female", "both"}:
        raise PackError("Audience must be male, female, or both.")
    locked = audience_lock(pack_id)
    if locked is not None and chosen != locked:
        raise PackError(
            f"The pack id {pack_id} is fixed as {locked} in the app. Choose another pack id, or set audience to {locked}."
        )
    return chosen


def build_manifest(
    *,
    name: str,
    description: str,
    order: int,
    version: int,
    audience: str,
    labels: dict[str, str],
) -> dict:
    title = (name or "").strip()
    if not title:
        raise PackError("Enter the pack name shown in the app.")
    if len(title) > 120:
        raise PackError("Pack name must be 120 characters or fewer.")
    if order < 0 or order > 9999:
        raise PackError("List position must be from 0 to 9999. Lower numbers appear first.")
    if version < 1 or version > 9999:
        raise PackError("Version must be from 1 to 9999.")
    text = (description or "").strip()
    if len(text) > 400:
        raise PackError("Description must be 400 characters or fewer.")
    manifest: dict = {
        "name": title,
        "version": version,
        "order": order,
        "audience": audience,
    }
    if text:
        manifest["description"] = text
    if labels:
        manifest["labels"] = labels
    return manifest


def cover_choice(scan: FolderScan, requested: str | None) -> str | None:
    """A cover file name to upload, or None when the first sticker is the cover."""
    choice = (requested or "").strip()
    names = {item.file for item in scan.stickers}
    if scan.cover_file:
        names.add(scan.cover_file)
    if choice in {"", "auto"}:
        return scan.cover_file
    if choice not in names:
        raise PackError("Choose a cover from the files in this folder.")
    if choice.lower() in {"cover.webp", "cover.png"}:
        return choice
    return choice


def _cover_upload_name(source_name: str) -> str:
    if source_name.lower() in {"cover.webp", "cover.png"}:
        return source_name
    extension = ".png" if source_name.lower().endswith(".png") else ".webp"
    return f"cover{extension}"


def build_objects(
    scan: FolderScan,
    *,
    pack_id: str,
    manifest: dict,
    cover: str | None,
) -> list[UploadObject]:
    prefix = f"packs/{pack_id}"
    objects: list[UploadObject] = []
    zip_files: list[tuple[str, bytes]] = []
    for sticker in scan.stickers:
        data = (scan.folder / sticker.file).read_bytes()
        objects.append(UploadObject(f"{prefix}/{sticker.file}", content_type(sticker.file), data))
        zip_files.append((sticker.file, data))
    if cover:
        source = (scan.folder / cover).read_bytes()
        cover_name = _cover_upload_name(cover)
        if cover_name != cover:
            objects.append(UploadObject(f"{prefix}/{cover_name}", content_type(cover_name), source))
        elif not any(item.key.endswith("/" + cover_name) for item in objects):
            objects.append(UploadObject(f"{prefix}/{cover_name}", content_type(cover_name), source))
        if not any(name == cover_name for name, _ in zip_files):
            zip_files.append((cover_name, source))
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    objects.append(UploadObject(f"{prefix}/manifest.json", "application/json", manifest_bytes))
    objects.append(UploadObject(f"{prefix}/pack.zip", "application/zip", _zip_bytes(zip_files)))
    return objects


def _zip_bytes(files: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return buffer.getvalue()


def stale_keys(existing: list[str], keep: set[str]) -> list[str]:
    return sorted(key for key in existing if key not in keep)
