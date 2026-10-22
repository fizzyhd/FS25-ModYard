"""Portable loadout bundles containing real mod files and a server checklist."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .core import HubError, valid_mod_name


def file_stamp(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def export_loadout(hub, loadout, destination, progress=None):
    destination = Path(destination).expanduser().resolve()
    for mod in hub.mods.values():
        source = Path(mod["path"]).resolve()
        if destination == source or (source.is_dir() and destination.is_relative_to(source)):
            raise HubError("Choose an export location outside your source mod files.")
    names = set(loadout["mods"])
    required = hub.map_mod(loadout)
    if required:
        names.add(required)
    names, missing = hub.dependency_closure(names)
    if missing:
        raise HubError("This loadout cannot be exported completely. Missing mods or dependencies:\n" + "\n".join(sorted(missing)))
    if not names:
        raise HubError("Select some mods before exporting this loadout.")
    sources, output_names = [], set()
    for name in sorted(names):
        info = hub.mods[name]
        path = Path(info["path"])
        if info["error"] or not path.exists() or not valid_mod_name(name):
            raise HubError(f"Cannot export {name}: {info['error'] or 'source is missing or invalid'}")
        output = name if path.is_file() else name + ".zip"
        if output.casefold() in output_names:
            raise HubError(f"Two mods would use the same ZIP filename: {output}")
        output_names.add(output.casefold())
        sources.append((name, info, path, output))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hub-export-", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    manifest = {"format": "farmsim-hub-loadout", "version": 1, "name": loadout["name"],
                "created": datetime.now(timezone.utc).isoformat(), "game": "FS25", "mods": [],
                "dlcs": sorted(hub.selected_dlcs(loadout))}
    try:
        # ZIPs are already compressed; store them byte-for-byte in the bundle.
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for index, (name, info, path, output) in enumerate(sources, 1):
                if progress:
                    progress(f"Exporting {index} / {len(sources)}: {info['title']}")
                if path.is_dir():
                    with tempfile.TemporaryFile(dir=destination.parent) as packed:
                        with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as mod:
                            for member in sorted(path.rglob("*")):
                                if member.is_symlink() or not member.resolve().is_relative_to(path.resolve()):
                                    raise HubError(f"Unpack this linked file before exporting: {member}")
                                if member.is_file():
                                    before = file_stamp(member)
                                    mod.write(member, member.relative_to(path).as_posix())
                                    if file_stamp(member) != before:
                                        raise HubError(f"A source changed during export: {member.name}. Try again.")
                        packed.seek(0)
                        with bundle.open("mods/" + output, "w", force_zip64=True) as stream:
                            shutil.copyfileobj(packed, stream, length=1024 * 1024)
                else:
                    before = file_stamp(path)
                    bundle.write(path, "mods/" + output)
                    if file_stamp(path) != before:
                        raise HubError(f"A source changed during export: {name}. Try again.")
                manifest["mods"].append({"filename": output, "title": info["title"], "version": info["version"],
                                         "author": info["author"], "dependency_added": name not in loadout["mods"]})
            bundle.writestr("loadout.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            bundle.writestr("README.txt", (
                f"FS25 ModYard — {loadout['name']}\n\n"
                "1. Extract this bundle.\n"
                "2. Upload the individual ZIPs from mods/ to your dedicated server's mods folder or mod-upload page.\n"
                "3. Enable these mods in the server's save configuration.\n"
                "4. Players need the same mod versions as the server.\n\n"
                "This bundle includes selected mods, their dependencies, and any linked map mod.\n"
                "DLC selections are listed in loadout.json; install and activate the licensed DLC separately on the server.\n"
                "The bundle contains mod files, not your farm's savegame.\n"
            ))
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": destination, "mods": len(sources), "dlcs": len(manifest["dlcs"]), "size": destination.stat().st_size}
