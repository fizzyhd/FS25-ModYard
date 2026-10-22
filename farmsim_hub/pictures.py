"""Read mod artwork without extracting archives, and cache scaled GTK images."""

import hashlib
import json
import struct
import zipfile
from pathlib import Path, PurePosixPath

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from .core import atomic_write

MAX_ICON_BYTES = 8 * 1024 * 1024
MAX_ICON_DIMENSION = 4096
CACHE_SIZE = 160
THUMBNAIL_SIZE = 48
PREVIEW_SIZE = 112


def icon_candidates(reference):
    """GIANTS permits PNG references whose distributed file is actually DDS."""
    reference = reference.strip().replace("\\", "/")
    if not reference or ":" in reference or reference.startswith("$"):
        return []
    path = PurePosixPath(reference)
    if path.is_absolute() or ".." in path.parts or not path.name:
        return []
    result = [path.as_posix()]
    for extension in [".dds", ".png"]:
        alternative = path.with_suffix(extension).as_posix()
        if alternative not in result:
            result.append(alternative)
    return result


def folder_icon(folder: Path, reference: str):
    """Resolve relative names, including case differences, inside the mod folder."""
    root = folder.resolve()
    for candidate in icon_candidates(reference):
        path = root
        try:
            for part in PurePosixPath(candidate).parts:
                direct = path / part
                path = direct if direct.exists() else next(p for p in path.iterdir() if p.name.casefold() == part.casefold())
                if not path.resolve().is_relative_to(root):
                    break
            else:
                path = path.resolve()
                if path.is_file() and path.stat().st_size <= MAX_ICON_BYTES:
                    return path
        except (OSError, StopIteration, ValueError):
            continue
    return None


def read_icon(mod):
    source = Path(mod["path"])
    reference = mod.get("icon", "")
    if source.is_dir():
        path = folder_icon(source, reference)
        if path:
            with path.open("rb") as stream:
                data = stream.read(MAX_ICON_BYTES + 1)
                return data if len(data) <= MAX_ICON_BYTES else None
        return None
    candidates = icon_candidates(reference)
    if not candidates:
        return None
    with zipfile.ZipFile(source) as archive:
        members = {m.filename.casefold(): m for m in archive.infolist()}
        for candidate in candidates:
            member = members.get(candidate.casefold())
            if member and not member.is_dir() and member.file_size <= MAX_ICON_BYTES:
                with archive.open(member) as stream:
                    data = stream.read(MAX_ICON_BYTES + 1)
                    return data if len(data) <= MAX_ICON_BYTES else None
    return None


def cache_key(mod):
    fields = [1, mod.get("fingerprint"), mod["path"], mod.get("icon", "")]
    if Path(mod["path"]).is_dir():
        asset = folder_icon(Path(mod["path"]), mod.get("icon", ""))
        if asset:
            info = asset.stat()
            fields.extend([str(asset), info.st_size, info.st_mtime_ns])
    return hashlib.sha256(json.dumps(fields).encode()).hexdigest()


def decode_icon(data):
    if not data:
        return None
    # Check DDS/PNG dimensions before asking a native decoder to allocate memory.
    if data[:4] == b"DDS " and len(data) >= 20:
        height, width = struct.unpack_from("<II", data, 12)
    elif data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack_from(">II", data, 16)
    else:
        width = height = 1
    if not (0 < width <= MAX_ICON_DIMENSION and 0 < height <= MAX_ICON_DIMENSION):
        return None
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    try:
        return GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, CACHE_SIZE, CACHE_SIZE, True, None)
    finally:
        stream.close(None)


def scaled(pixbuf, size):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    ratio = min(size / width, size / height)
    return pixbuf.scale_simple(max(1, round(width * ratio)), max(1, round(height * ratio)), GdkPixbuf.InterpType.BILINEAR)


class ModPictures:
    def __init__(self, cache_folder):
        self.cache_folder = Path(cache_folder)
        self.images = {}
        self.thumbnails = {}
        self.failures = {}

    def prepare(self, mods, progress=None):
        images, thumbnails, failures = {}, {}, {}
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        for index, (name, mod) in enumerate(mods.items()):
            if progress and index % 30 == 0:
                progress(f"Loading mod pictures {index + 1} / {len(mods)}…")
            try:
                path = self.cache_folder / (cache_key(mod) + ".png")
                pixbuf = None
                if path.is_file():
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), CACHE_SIZE, CACHE_SIZE, True)
                    except GLib.Error:
                        pass  # A damaged cache entry can be regenerated from the mod.
                if pixbuf is None:
                    pixbuf = decode_icon(read_icon(mod))
                    if pixbuf is not None:
                        ok, encoded = pixbuf.save_to_bufferv("png", [], [])
                        if ok:
                            try:
                                atomic_write(path, encoded)
                            except OSError:
                                pass  # A cache write failure must not hide the picture.
                if pixbuf is not None:
                    images[name] = pixbuf
                    thumbnails[name] = scaled(pixbuf, THUMBNAIL_SIZE)
                else:
                    failures[name] = "No readable mod picture"
            except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, GLib.Error) as exc:
                failures[name] = str(exc)
        self.images, self.thumbnails, self.failures = images, thumbnails, failures

    def preview(self, name):
        image = self.images.get(name)
        return scaled(image, PREVIEW_SIZE) if image is not None else None
