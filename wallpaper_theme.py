"""Build a chat theme the chat API can read from one wallpaper image.

The chat service lists ``wallpapers/{theme_id}/`` on the sticker bucket.
A theme is ``background.jpg`` (or png / webp), an optional ``preview`` image,
and ``manifest.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sticker_pack import PACK_ID, PackError, UploadObject, pack_id_from_name, stale_keys

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# The app ignores these, or keeps the picture shipped inside the app.
RESERVED_THEME_IDS = {
    "default",
    "midnight_nebula",
    "soft_embrace",
    "childhood_swing",
    "moonlit_romance",
    "picnic_friends",
}

_COLOR = re.compile(r"^#?[0-9a-fA-F]{6}$")


class ThemeError(PackError):
    """The image or the form cannot become a chat theme."""


def theme_id_from_name(name: str) -> str:
    try:
        return pack_id_from_name(name)
    except PackError as exc:
        raise ThemeError("Theme name needs a letter or number so a theme id can be made.") from exc


def check_theme_id(theme_id: str) -> str:
    text = (theme_id or "").strip().lower()
    if not PACK_ID.match(text):
        raise ThemeError(
            "Theme id must be lowercase letters, numbers, _ or -, start with a letter or number, and be at most 63 characters."
        )
    if text in RESERVED_THEME_IDS:
        raise ThemeError(
            f"{text} is already in the app, so phones would not show this upload. Pick a new theme id."
        )
    return text


def clean_image(raw: str, *, label: str, required: bool) -> Path | None:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        if required:
            raise ThemeError(f"Choose the {label}.")
        return None
    path = Path(text).expanduser()
    if not path.exists():
        raise ThemeError(f"That file does not exist: {path}")
    if not path.is_file():
        raise ThemeError(f"That path is not a file. Choose the {label}.")
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ThemeError(f"The {label} must be a jpg, png, or webp file.")
    size = path.stat().st_size
    if size <= 0:
        raise ThemeError(f"{path.name} is empty.")
    if size > MAX_IMAGE_BYTES:
        raise ThemeError(f"{path.name} is larger than 8 MB.")
    return path


def background_name(path: Path) -> str:
    return "background" + path.suffix.lower()


def preview_name(path: Path) -> str:
    return "preview" + path.suffix.lower()


def image_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    raise ThemeError(f"Cannot upload {name}.")


def normalize_color(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "#D7F0DE"
    if not _COLOR.match(text):
        raise ThemeError("Fallback color must be a color like #D7F0DE.")
    if not text.startswith("#"):
        text = f"#{text}"
    return text.upper()


def read_sidecar_manifest(image: Path) -> dict:
    path = image.parent / "manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThemeError(f"manifest.json next to that image is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ThemeError("manifest.json must be a JSON object.")
    return data


def build_theme_manifest(
    *,
    name: str,
    description: str,
    order: int,
    version: int,
    fallback_color: str,
) -> dict:
    title = (name or "").strip()
    if not title:
        raise ThemeError("Enter the theme name shown in the app.")
    if len(title) > 120:
        raise ThemeError("Theme name must be 120 characters or fewer.")
    if order < 0 or order > 9999:
        raise ThemeError("List position must be from 0 to 9999. Lower numbers appear first.")
    if version < 1 or version > 9999:
        raise ThemeError("Version must be from 1 to 9999.")
    text = (description or "").strip()
    if len(text) > 240:
        raise ThemeError("Description must be 240 characters or fewer.")
    manifest: dict = {
        "name": title,
        "order": order,
        "version": version,
        "fallback_color": normalize_color(fallback_color),
    }
    if text:
        manifest["description"] = text
    return manifest


def build_theme_objects(
    image: Path,
    preview: Path | None,
    *,
    theme_id: str,
    manifest: dict,
) -> list[UploadObject]:
    prefix = f"wallpapers/{theme_id}"
    objects = [
        UploadObject(
            f"{prefix}/{background_name(image)}",
            image_type(image.name),
            image.read_bytes(),
        )
    ]
    if preview is not None:
        objects.append(
            UploadObject(
                f"{prefix}/{preview_name(preview)}",
                image_type(preview.name),
                preview.read_bytes(),
            )
        )
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    objects.append(UploadObject(f"{prefix}/manifest.json", "application/json", manifest_bytes))
    return objects


def theme_prefix(theme_id: str) -> str:
    return f"wallpapers/{theme_id}/"


__all__ = [
    "ThemeError",
    "background_name",
    "build_theme_manifest",
    "build_theme_objects",
    "check_theme_id",
    "clean_image",
    "preview_name",
    "read_sidecar_manifest",
    "stale_keys",
    "theme_id_from_name",
    "theme_prefix",
]
