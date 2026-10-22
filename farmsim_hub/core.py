"""Game and library operations. No GUI dependencies; no writes to game data on startup."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

APP_ID = "2300320"
PROFILE_SUFFIX = Path("pfx/drive_c/users/steamuser/Documents/My Games/FarmingSimulator2025")
MAX_XML = 4 * 1024 * 1024


def migrate_data_directory(data_home: Path) -> Path:
    """Move the pre-rebrand data folder to the current app name, preserving its contents."""
    current = data_home / "fs25-modyard"
    legacy = data_home / "farmsim-hub-linux"
    if legacy.exists() and not current.exists():
        legacy.rename(current)
    elif legacy.exists() and current.exists():
        # Merge only files absent from the new location; never overwrite newer user data.
        def copy_missing(source, destination):
            if not os.path.lexists(destination):
                shutil.copy2(source, destination)
            return destination

        shutil.copytree(legacy, current, dirs_exist_ok=True, symlinks=True, copy_function=copy_missing)
    config_path = current / "config.json"
    if config_path.is_file():
        old_root = legacy.as_posix()
        config_text = config_path.read_text(encoding="utf-8")
        if old_root in config_text:
            atomic_write(config_path, config_text.replace(old_root, current.as_posix()).encode("utf-8"))
    return current


class HubError(Exception):
    pass


def version_number(value):
    if not re.fullmatch(r"\d+(?:\.\d+)*", value.strip()):
        return None
    parts = [int(n) for n in value.strip().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def read_xml(data: bytes) -> ET.Element:
    if len(data) > MAX_XML or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise HubError("This XML file is too large or contains unsupported declarations.")
    try:
        return ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except ET.ParseError as exc:
        raise HubError(f"Cannot read XML: {exc}") from exc


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".hub-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(name, stat.S_IMODE(path.stat().st_mode))
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path, default):
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise HubError(f"Cannot read {path}. The file has been kept for recovery. {exc}") from exc


def write_json(path: Path, data):
    atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())


def mod_hash(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def steam_libraries(home: Path) -> list[Path]:
    roots = [home / ".local/share/Steam", home / ".steam/steam",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
             home / ".var/app/com.valvesoftware.Steam/.steam/steam"]
    for root in list(roots):
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            roots.extend(Path(p.replace("\\\\", "\\")) for p in
                         re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")))
    return list(dict.fromkeys(r.resolve() for r in roots if r.is_dir()))


def detect_profiles(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    found = []
    previous = read_json(home / ".config/fs25-mod-switcher/config.json", {})
    if previous.get("mods_path"):
        found.append(Path(previous["mods_path"]).expanduser().parent)
    for root in steam_libraries(home):
        prefix = root / f"steamapps/compatdata/{APP_ID}"
        found.append(prefix / PROFILE_SUFFIX)
        users = prefix / "pfx/drive_c/users"
        if users.is_dir():
            for user in users.iterdir():
                found.append(user / "Documents/My Games/FarmingSimulator2025")
    found.extend([home / "Documents/My Games/FarmingSimulator2025",
                  home / ".local/share/FarmingSimulator2025"])
    return list(dict.fromkeys(p.resolve() for p in found if (p / "gameSettings.xml").is_file()))


def game_running(proc_root: Path = Path("/proc")) -> bool:
    names = {"farmingsimulator2025game.exe", "farmingsimulator25.exe", "farmingsimulator2025.exe"}
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            if any(arg.decode(errors="replace").replace("\\", "/").rsplit("/", 1)[-1].lower()
                   in names for arg in args):
                return True
        except (OSError, ProcessLookupError):
            pass
    return False


def require_game_closed():
    if game_running():
        raise HubError("Close Farming Simulator 25 first. You can still edit saved loadouts while it is running.")


def valid_mod_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.zip)?", name, re.IGNORECASE))


def mod_info(path: Path, game_data_dirs=()) -> dict:
    info = {"name": path.name, "path": str(path.resolve()), "title": path.stem,
            "author": "", "version": "", "dependencies": [], "kind": "Equipment",
            "size": 0, "error": "", "icon": "", "categories": ["Equipment"], "category_terms": []}
    try:
        file_stat = path.stat()
        info["size"] = file_stat.st_size if path.is_file() else 0
        info["fingerprint"] = [str(path.resolve()), file_stat.st_size, file_stat.st_mtime_ns]
        if path.is_dir():
            desc = path / "modDesc.xml"
            if desc.stat().st_size > MAX_XML:
                raise HubError("modDesc.xml is too large")
            root = read_xml(desc.read_bytes())
            info["fingerprint"].append(desc.stat().st_mtime_ns)
        else:
            with zipfile.ZipFile(path) as archive:
                member = next((x for x in archive.infolist() if x.filename.lower() == "moddesc.xml"), None)
                if member is None:
                    raise HubError("No modDesc.xml at the root of this ZIP")
                if member.file_size > MAX_XML:
                    raise HubError("modDesc.xml is too large")
                root = read_xml(archive.read(member))
        title = root.find("title")
        if title is not None:
            info["title"] = ((title.findtext("en") or "").strip() or (title.text or "").strip() or
                             next((c.text.strip() for c in title if c.text and c.text.strip()), "") or path.stem)
        info["author"] = (root.findtext("author") or "").strip()
        info["version"] = (root.findtext("version") or "").strip()
        info["icon"] = (root.findtext("iconFilename") or "").strip()
        info["dependencies"] = [d.text.strip().removesuffix(".zip")
                                for d in root.findall("dependencies/dependency") if d.text and d.text.strip()]
        if root.find("maps/map") is not None:
            info["kind"] = "Maps"
        elif root.find("storeItems/storeItem") is None and root.find("extraSourceFiles/sourceFile") is not None:
            info["kind"] = "Scripts"
        if not valid_mod_name(path.name):
            raise HubError("The filename contains characters FS25 does not support")
        from .categories import read_mod_categories
        info.update(read_mod_categories(path, root, info["kind"], game_data_dirs))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, HubError, RuntimeError) as exc:
        info["error"] = str(exc)
        info["kind"] = "Needs attention"
        info["categories"] = ["Needs attention"]
    return info


def list_saves(profile: Path) -> list[dict]:
    saves = []
    if not profile.is_dir():
        return saves
    for directory in sorted(profile.iterdir()):
        if not re.fullmatch(r"savegame([1-9]|1[0-9]|20)", directory.name):
            continue
        career = directory / "careerSavegame.xml"
        if not career.is_file():
            continue
        try:
            root = read_xml(career.read_bytes())
            settings = root.find("settings")
            if settings is None:
                continue
            saves.append({"slot": int(directory.name[8:]), "name": settings.findtext("savegameName", "Untitled farm"),
                          "map": settings.findtext("mapTitle", "Unknown map"), "map_id": settings.findtext("mapId", ""),
                          "date": settings.findtext("saveDateFormatted", ""),
                          "mtime": career.stat().st_mtime, "settings": {c.tag: c.text or "" for c in settings},
                          "mods": [dict(m.attrib) for m in root.findall("mod")], "path": str(directory)})
        except (OSError, HubError):
            continue
    return sorted(saves, key=lambda s: s["slot"])


# Only established settings are editable; unknown fields and mod-specific XML are preserved.
SETTING_FIELDS = [
    ("savegameName", "Farm name", "text", None),
    ("economicDifficulty", "Economy difficulty", "choice", [("EASY", "Easy"), ("NORMAL", "Normal"), ("HARD", "Hard")]),
    ("plannedDaysPerPeriod", "Days per month", "int", (1, 28)),
    ("timeScale", "Time speed", "number", (0.5, 360)),
    ("autoSaveInterval", "Autosave interval (minutes, 0 = off)", "number", (0, 60)),
    ("trafficEnabled", "Traffic", "bool", None),
    ("fruitDestruction", "Crop destruction", "bool", None),
    ("plowingRequiredEnabled", "Periodic plowing", "bool", None),
    ("stonesEnabled", "Fieldstones", "bool", None),
    ("weedsEnabled", "Weeds", "bool", None),
    ("limeRequired", "Lime required", "bool", None),
    ("isSnowEnabled", "Snow", "bool", None),
    ("automaticMotorStartEnabled", "Automatic engine start", "bool", None),
    ("stopAndGoBraking", "Stop and go braking", "bool", None),
    ("trailerFillLimit", "Trailer fill limit", "bool", None),
    ("helperBuyFuel", "Workers buy fuel", "bool", None),
    ("helperBuySeeds", "Workers buy seeds", "bool", None),
    ("helperBuyFertilizer", "Workers buy fertilizer", "bool", None),
]


class Hub:
    def __init__(self, home: Path | None = None, data_dir: Path | None = None):
        self.home = home or Path.home()
        self.data = data_dir or migrate_data_directory(Path(os.environ.get("XDG_DATA_HOME", self.home / ".local/share")))
        self.config_path = self.data / "config.json"
        self.cache_path = self.data / "metadata-cache.json"
        self.lock = threading.RLock()
        self.config = read_json(self.config_path, {"version": 1, "loadouts": [], "collections": [], "favorites": [], "mod_dirs": []})
        for key in ["loadouts", "collections", "favorites", "mod_dirs"]:
            self.config.setdefault(key, [])
        profiles = detect_profiles(self.home)
        self.profile = Path(self.config.get("profile", profiles[0] if profiles else ""))
        self._migrate_profile_mod_directory()
        self.mods: dict[str, dict] = {}
        self.saves: list[dict] = []
        self.duplicates: list[dict] = []
        self.scan_errors: list[str] = []
        self.data.mkdir(parents=True, exist_ok=True)

    def _migrate_profile_mod_directory(self):
        settings_path = self.profile / "gameSettings.xml"
        if not settings_path.is_file() or game_running():
            return
        old_root = (self.data.parent / "farmsim-hub-linux").as_posix()
        original = settings_path.read_bytes()
        migrated = original.replace(old_root.encode("utf-8"), self.data.as_posix().encode("utf-8"))
        if migrated == original:
            return
        read_xml(migrated)
        if settings_path.read_bytes() != original:
            raise HubError("FS25 settings changed during the data-folder migration. Close the game and retry.")
        backup_dir = self.data / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(settings_path, backup_dir / f"{stamp}-before-data-folder-migration-gameSettings.xml")
        atomic_write(settings_path, migrated)

    def save_config(self):
        with self.lock:
            write_json(self.config_path, self.config)

    def set_profile(self, profile: Path):
        profile = profile.expanduser().resolve()
        if not (profile / "gameSettings.xml").is_file():
            raise HubError("Choose the FarmingSimulator2025 folder containing gameSettings.xml.")
        read_xml((profile / "gameSettings.xml").read_bytes())
        self.profile = profile
        self.config["profile"] = str(profile)
        self.save_config()

    def mod_directories(self) -> list[Path]:
        dirs = [Path(x).expanduser() for x in self.config["mod_dirs"]]
        dirs.extend([self.home / "Documents/FS25ModLibrary", self.data / "imports", self.profile / "mods"])
        old_profiles = self.home / "Documents/FS25ModProfiles"
        if old_profiles.is_dir():
            dirs.extend(x for x in old_profiles.iterdir() if x.is_dir())
        for loadout in self.config["loadouts"]:
            if loadout.get("extra_dir"):
                dirs.append(Path(loadout["extra_dir"]))
        return list(dict.fromkeys(p.resolve() for p in dirs if p.is_dir()))

    def scan(self, progress=None):
        from .categories import category_cache_valid
        self.saves = list_saves(self.profile)
        cache = read_json(self.cache_path, {})
        game_data = [p / "steamapps/common/Farming Simulator 25/data" for p in steam_libraries(self.home)]
        mods, duplicates, errors = {}, [], []
        files = []
        for directory in self.mod_directories():
            try:
                files.extend(p for p in sorted(directory.iterdir())
                             if (p.suffix.lower() == ".zip" and p.is_file()) or (p.is_dir() and (p / "modDesc.xml").is_file()))
            except OSError as exc:
                errors.append(str(exc))
        for i, path in enumerate(files):
            try:
                key = str(path.resolve())
                file_stat = path.stat()
                fp = [key, file_stat.st_size, file_stat.st_mtime_ns]
                if path.is_dir():
                    fp.append((path / "modDesc.xml").stat().st_mtime_ns)
                info = cache.get(key)
                if info is None or "icon" not in info or info.get("fingerprint") != fp or not category_cache_valid(info):
                    info = mod_info(path, game_data)
                cache[key] = info
                previous = mods.get(path.name)
                if previous and previous["path"] != key:
                    duplicates.append({"name": path.name, "used": previous["path"], "other": key})
                elif not previous:
                    mods[path.name] = info
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
            if progress and i % 40 == 0:
                progress(f"Reading mods {i + 1} / {len(files)}…")
        self.mods, self.duplicates, self.scan_errors = mods, duplicates, errors
        write_json(self.cache_path, cache)
        self.seed_loadouts()

    def seed_loadouts(self):
        with self.lock:
            existing = {l.get("source") for l in self.config["loadouts"]}
            existing.update(self.config.get("dismissed_loadout_sources", []))
            source_prefix = str(self.profile)
            for save in self.saves:
                source = f"{source_prefix}/savegame{save['slot']}"
                if source in existing:
                    continue
                by_stem = {Path(n).stem: n for n in self.mods}
                names = [by_stem.get(m["modName"], m["modName"] + ".zip")
                         for m in save["mods"] if not m["modName"].startswith("pdlc_")]
                self.config["loadouts"].append({"id": uuid.uuid4().hex, "name": f"{save['map']} · Save {save['slot']}",
                                                "mods": sorted(set(names)), "slot": save["slot"], "source": source,
                                                "profile": source_prefix})
            old_profiles = self.home / "Documents/FS25ModProfiles"
            if old_profiles.is_dir():
                for path in sorted(old_profiles.iterdir()):
                    source = f"import:{path}"
                    if path.is_dir() and source not in existing:
                        self.config["loadouts"].append({"id": uuid.uuid4().hex, "name": f"{path.name} (imported)",
                                                        "mods": sorted(p.name for p in path.iterdir() if p.name in self.mods),
                                                        "slot": None, "source": source, "profile": source_prefix})
            if not self.config["loadouts"] and not self.config.get("loadouts_initialized"):
                self.config["loadouts"].append({"id": uuid.uuid4().hex, "name": "My first loadout", "mods": [],
                                                "slot": None, "profile": source_prefix})
            self.config["loadouts_initialized"] = True
            self.save_config()

    def loadouts(self):
        return [l for l in self.config["loadouts"] if l.get("profile", str(self.profile)) == str(self.profile)]

    def loadout(self, key: str):
        return next(l for l in self.loadouts() if l["id"] == key)

    def new_loadout(self, name: str, mods=None, slot=None):
        if not name.strip():
            raise HubError("Give the loadout a name.")
        result = {"id": uuid.uuid4().hex, "name": name.strip()[:120], "mods": sorted(set(mods or [])),
                  "slot": slot, "profile": str(self.profile)}
        self.config["loadouts"].append(result)
        self.save_config()
        return result

    def _commit_config(self, **changes):
        """Commit replacement values without losing the in-memory state on failure."""
        with self.lock:
            previous = self.config.copy()
            self.config.update(changes)
            try:
                self.save_config()
            except BaseException:
                self.config.clear()
                self.config.update(previous)
                raise

    def set_collection_mods(self, collection, names):
        """Save membership without changing mod files or loadout selections."""
        with self.lock:
            if not any(c is collection for c in self.config["collections"]):
                raise HubError("This collection no longer exists.")
            previous = collection["mods"]
            names = sorted(set(names))
            unknown = set(names) - set(self.mods) - set(previous)
            if unknown:
                raise HubError("These mods are no longer in the library: " + ", ".join(sorted(unknown)))
            if names == previous:
                return
            collection["mods"] = names
            try:
                self.save_config()
            except BaseException:
                collection["mods"] = previous
                raise

    def delete_collection(self, collection):
        collections = self.config["collections"]
        if not any(c is collection for c in collections):
            return False
        self._commit_config(collections=[c for c in collections if c is not collection])
        return True

    def delete_loadout(self, loadout):
        if not any(l is loadout for l in self.loadouts()):
            return False
        remaining = [l for l in self.config["loadouts"] if l is not loadout]
        dismissed = set(self.config.get("dismissed_loadout_sources", []))
        if loadout.get("source"):
            dismissed.add(loadout["source"])
        selected = self.config.get("selected_loadout")
        if selected == loadout["id"]:
            selected = next((l["id"] for l in remaining if l.get("profile", str(self.profile)) == str(self.profile)), None)
        self._commit_config(loadouts=remaining, selected_loadout=selected,
                            dismissed_loadout_sources=sorted(dismissed), loadouts_initialized=True)
        return True

    def selected_dlcs(self, loadout):
        if "dlcs" in loadout:
            return set(loadout["dlcs"])
        save = self.save_for(loadout)
        return {m["modName"] for m in save["mods"] if m["modName"].startswith("pdlc_")} if save else set()

    def required_dlc(self, loadout):
        save = self.save_for(loadout)
        stem = save["map_id"].split(".", 1)[0] if save else ""
        return stem if stem.startswith("pdlc_") else None

    def set_loadout_dlcs(self, loadout, names, slot):
        previous = loadout.copy()
        loadout.update(dlcs=sorted(set(names)), slot=slot)
        required = self.required_dlc(loadout)
        if required:
            loadout["dlcs"] = sorted(set(loadout["dlcs"]) | {required})
        map_mod = self.map_mod(loadout)
        if map_mod:
            loadout["mods"] = sorted(self.dependency_closure(set(loadout["mods"]) | {map_mod})[0])
        try:
            self._validate_dlcs(loadout)
            self.save_config()
        except BaseException:
            loadout.clear()
            loadout.update(previous)
            raise

    def _validate_dlcs(self, loadout):
        if "dlcs" not in loadout:
            return  # Existing loadouts keep the save's selections until explicitly edited.
        selected = self.selected_dlcs(loadout)
        required = self.required_dlc(loadout)
        if required and required not in selected:
            raise HubError("The save's DLC map must stay selected.")
        catalog = {d["name"]: d for d in self.dlcs()}
        missing = sorted(n for n in selected if not catalog.get(n, {}).get("installed"))
        if missing:
            raise HubError("Install these DLCs through Steam before using them:\n" + "\n".join(missing))
        save = self.save_for(loadout)
        existing = {m["modName"] for m in save["mods"]} if save else set()
        unknown = [catalog[n]["title"] for n in selected - existing
                   if not catalog[n].get("version") or not catalog[n].get("fileHash")]
        if unknown:
            raise HubError("Start FS25 once, then rescan so its DLC versions can be read:\n" + "\n".join(sorted(unknown)))

    def dependency_closure(self, names):
        selected, missing = set(names), set()
        by_stem = {Path(n).stem: n for n in self.mods}
        todo = list(selected)
        while todo:
            name = todo.pop()
            info = self.mods.get(name)
            if not info:
                missing.add(name)
                continue
            for dep in info["dependencies"]:
                filename = by_stem.get(dep)
                if filename is None:
                    missing.add(dep)
                elif filename not in selected:
                    selected.add(filename)
                    todo.append(filename)
        return selected, missing

    def save_for(self, loadout):
        return next((s for s in self.saves if s["slot"] == loadout.get("slot")), None)

    def map_mod(self, loadout):
        save = self.save_for(loadout)
        if save and "." in save["map_id"]:
            stem = save["map_id"].split(".", 1)[0]
            if not stem.startswith("pdlc_"):
                return next((n for n in self.mods if Path(n).stem == stem), stem + ".zip")
        return None

    def set_selection(self, loadout, names):
        names = set(names)
        required = self.map_mod(loadout)
        if required:
            names.add(required)
        names, missing = self.dependency_closure(names)
        loadout["mods"] = sorted(names)
        self.save_config()
        return missing

    def validate_loadout(self, loadout):
        names = set(loadout["mods"])
        required = self.map_mod(loadout)
        if required and required not in names:
            raise HubError("The save's map mod must stay selected.")
        selected, missing = self.dependency_closure(names)
        if missing:
            raise HubError("Missing mods or dependencies:\n" + "\n".join(sorted(missing)))
        if selected != names:
            raise HubError("This loadout needs additional dependencies. Re-select its mods to include them.")
        broken = [n for n in names if self.mods[n]["error"] or not Path(self.mods[n]["path"]).exists()]
        if broken:
            raise HubError("Fix or deselect these mods before applying:\n" + "\n".join(sorted(broken)))
        if loadout.get("slot") and not self.save_for(loadout):
            raise HubError("The linked save no longer exists. Refresh the library and choose a different loadout.")
        self._validate_dlcs(loadout)
        return names

    def backup(self, slot: int | None, reason="Manual backup") -> Path:
        require_game_closed()
        self.check_profile()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self.data / "backups" / f"{stamp}-{uuid.uuid4().hex[:8]}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"format": 1, "profile": str(self.profile), "slot": slot,
                    "created": datetime.now(timezone.utc).isoformat(), "reason": reason}
        save = next((s for s in list_saves(self.profile) if s["slot"] == slot), None)
        if slot is not None and save is None:
            raise HubError("That save could not be found.")
        manifest["map"] = save["map"] if save else "Game configuration"
        try:
            with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.write(self.profile / "gameSettings.xml", "gameSettings.xml")
                if save:
                    save_dir = self.profile / f"savegame{slot}"
                    for file in save_dir.rglob("*"):
                        if file.is_symlink():
                            raise HubError("Cannot back up a save containing symbolic links.")
                        if file.is_file():
                            archive.write(file, str(file.relative_to(self.profile)))
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return dest

    def backups(self):
        result = []
        for path in sorted((self.data / "backups").glob("*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(path) as archive:
                    if archive.getinfo("manifest.json").file_size > MAX_XML:
                        continue
                    manifest = json.loads(archive.read("manifest.json"))
                    if manifest.get("profile") == str(self.profile):
                        manifest.update(path=str(path), size=path.stat().st_size)
                        result.append(manifest)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        return result

    def check_profile(self):
        if not (self.profile / "gameSettings.xml").is_file():
            raise HubError("Choose your FS25 user folder in Preferences first.")
        self._migrate_profile_mod_directory()

    def windows_path(self, path: Path):
        # Steam's Proton prefixes map drive Z: to /. Check the mapping rather than assume it.
        prefix = next((p for p in self.profile.parents if p.name == "pfx"), None)
        if prefix is None:
            raise HubError("Launching currently supports FS25 installed through Steam + Proton. Select its Proton user folder in Preferences.")
        for drive in sorted((prefix / "dosdevices").glob("[a-z]:")):
            if drive.is_symlink():
                try:
                    relative = path.resolve().relative_to(drive.resolve())
                    return f"{drive.name[0].upper()}:/{relative.as_posix()}"
                except ValueError:
                    continue
        raise HubError("No Proton drive maps to the loadout folder. Check the prefix's dosdevices links.")

    def _career_with_mods(self, save, names, progress=None, dlcs=None):
        root = read_xml((self.profile / f"savegame{save['slot']}/careerSavegame.xml").read_bytes())
        previous = {m.get("modName"): m for m in root.findall("mod")}
        for node in root.findall("mod"):
            if not node.get("modName", "").startswith("pdlc_") or dlcs is not None:
                root.remove(node)
        map_stem = save["map_id"].split(".", 1)[0]
        if dlcs is not None:
            catalog = {d["name"]: d for d in self.dlcs()}
            for name in sorted(dlcs):
                node = copy.deepcopy(previous.get(name))
                if node is None:
                    node = ET.Element("mod", modName=name, required="true" if name == map_stem else "false")
                for field in ["title", "version", "fileHash"]:
                    if catalog[name].get(field):
                        node.set(field, catalog[name][field])
                root.append(node)
        for index, name in enumerate(sorted(names)):
            info = self.mods[name]
            stem = Path(name).stem
            node = copy.deepcopy(previous.get(stem))
            needs_hash = node is None or node.get("version") != info["version"]
            if node is None:
                node = ET.Element("mod", modName=stem, title=info["title"], version=info["version"],
                                  required="true" if stem == map_stem else "false")
            if needs_hash:
                path = Path(info["path"])
                if path.is_file():
                    if progress:
                        progress(f"Preparing mod {index + 1} / {len(names)}: {info['title']}")
                    node.set("fileHash", mod_hash(path))
            node.set("title", info["title"])
            node.set("version", info["version"])
            root.append(node)
        return xml_bytes(root)

    def apply(self, loadout, progress=None):
        require_game_closed()
        self.check_profile()
        self.saves = list_saves(self.profile)
        names = self.validate_loadout(loadout)
        applied = self.data / "applied" / (uuid.uuid4().hex)
        windows = self.windows_path(applied)
        settings_path = self.profile / "gameSettings.xml"
        old_settings = settings_path.read_bytes()
        settings_root = read_xml(old_settings)
        override = settings_root.find("modsDirectoryOverride")
        if override is None:
            override = ET.SubElement(settings_root, "modsDirectoryOverride")
        override.set("active", "true")
        override.set("directory", windows)
        save = self.save_for(loadout)
        career_path = self.profile / f"savegame{save['slot']}/careerSavegame.xml" if save else None
        old_career = career_path.read_bytes() if career_path else None
        dlcs = self.selected_dlcs(loadout) if "dlcs" in loadout else None
        new_career = self._career_with_mods(save, names, progress, dlcs) if save else None
        require_game_closed()
        if settings_path.read_bytes() != old_settings or (career_path and career_path.read_bytes() != old_career):
            raise HubError("FS25 files changed during preparation. Refresh and try again.")
        if progress:
            progress("Backing up the complete save…")
        backup_path = self.backup(loadout.get("slot"), f"Before applying {loadout['name']}")
        applied.mkdir(parents=True)
        try:
            for name in names:
                (applied / name).symlink_to(self.mods[name]["path"], target_is_directory=Path(self.mods[name]["path"]).is_dir())
            if any(not p.exists() for p in applied.iterdir()):
                raise HubError("A mod source disappeared while preparing the loadout. Refresh and try again.")
            require_game_closed()
            if settings_path.read_bytes() != old_settings or (career_path and career_path.read_bytes() != old_career):
                raise HubError("The game files changed. No changes were applied.")
            if career_path:
                atomic_write(career_path, new_career)
            try:
                atomic_write(settings_path, xml_bytes(settings_root))
            except BaseException:
                if career_path:
                    atomic_write(career_path, old_career)
                raise
        except BaseException:
            shutil.rmtree(applied)
            raise
        self.config["last_applied"] = {"loadout": loadout["id"], "directory": str(applied), "backup": str(backup_path)}
        self.save_config()
        return backup_path

    def save_settings(self, slot: int, changes: dict):
        require_game_closed()
        self.check_profile()
        if not 1 <= slot <= 20:
            raise HubError("Invalid save slot.")
        path = self.profile / f"savegame{slot}/careerSavegame.xml"
        original = path.read_bytes()
        root = read_xml(original)
        settings = root.find("settings")
        if settings is None:
            raise HubError("This save does not contain settings.")
        allowed = {field[0]: field for field in SETTING_FIELDS}
        for key, value in changes.items():
            if key not in allowed or settings.find(key) is None:
                raise HubError(f"Unsupported setting: {key}")
            _, label, kind, values = allowed[key]
            value = str(value)
            if kind == "bool" and value not in {"true", "false"}:
                raise HubError(f"Invalid value for {label}")
            if kind == "choice" and value not in {v[0] for v in values}:
                raise HubError(f"Invalid value for {label}")
            if kind in {"int", "number"}:
                try:
                    number = float(value)
                    if not values[0] <= number <= values[1] or (kind == "int" and int(number) != number):
                        raise ValueError()
                except ValueError as exc:
                    raise HubError(f"Invalid value for {label}") from exc
            if kind == "text" and (not value.strip() or len(value) > 120):
                raise HubError("Use a farm name between 1 and 120 characters.")
            settings.find(key).text = value
        backup = self.backup(slot, "Before editing save settings")
        require_game_closed()
        if path.read_bytes() != original:
            raise HubError("This save changed while the backup was made. Refresh and try again.")
        atomic_write(path, xml_bytes(root))
        self.saves = list_saves(self.profile)
        return backup

    def clone_save(self, slot: int, new_slot: int, name: str):
        require_game_closed()
        self.check_profile()
        if not 1 <= slot <= 20 or not 1 <= new_slot <= 20 or not name.strip() or len(name) > 120:
            raise HubError("Choose valid save slots and a farm name.")
        source = self.profile / f"savegame{slot}"
        dest = self.profile / f"savegame{new_slot}"
        if not (source / "careerSavegame.xml").is_file() or dest.is_symlink() or (dest.exists() and any(dest.iterdir())):
            raise HubError("The source save is unavailable or the destination slot is not empty.")
        if any(p.is_symlink() for p in source.rglob("*")):
            raise HubError("Cannot clone a save containing symbolic links.")
        stage = Path(tempfile.mkdtemp(prefix=".hub-clone-", dir=self.profile))
        try:
            shutil.copytree(source, stage, dirs_exist_ok=True)
            path = stage / "careerSavegame.xml"
            root = read_xml(path.read_bytes())
            root.find("settings/savegameName").text = name.strip()
            atomic_write(path, xml_bytes(root))
            require_game_closed()
            os.rename(stage, dest)  # Only replaces an empty directory; never an occupied slot.
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        self.saves = list_saves(self.profile)
        self.seed_loadouts()

    def restore(self, backup_path: Path):
        require_game_closed()
        self.check_profile()
        backup_path = backup_path.resolve()
        if backup_path.parent != (self.data / "backups").resolve():
            raise HubError("Choose a backup created by this app.")
        with zipfile.ZipFile(backup_path) as archive:
            if archive.getinfo("manifest.json").file_size > MAX_XML:
                raise HubError("Invalid backup manifest.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != 1 or manifest.get("profile") != str(self.profile):
                raise HubError("This backup belongs to a different FS25 user folder.")
            slot = manifest.get("slot")
            if slot is not None and (type(slot) is not int or not 1 <= slot <= 20):
                raise HubError("Invalid backup save slot.")
            prefix = f"savegame{slot}/" if slot else None
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                    raise HubError("Unsafe path in backup.")
                if member.filename not in {"manifest.json", "gameSettings.xml"} and not (prefix and member.filename.startswith(prefix)):
                    raise HubError("Unexpected file in backup.")
            old_settings = (self.profile / "gameSettings.xml").read_bytes()
            if archive.getinfo("gameSettings.xml").file_size > MAX_XML:
                raise HubError("Invalid game settings in backup.")
            new_settings = archive.read("gameSettings.xml")
            old_data_path = (self.data.parent / "farmsim-hub-linux").as_posix().encode("utf-8")
            new_settings = new_settings.replace(old_data_path, self.data.as_posix().encode("utf-8"))
            read_xml(new_settings)
            if slot and f"{prefix}careerSavegame.xml" not in archive.namelist():
                raise HubError("The backup is missing its save file.")
            # First create a recovery backup of today's state, when a save still exists.
            existing_slot = slot if (slot and (self.profile / f"savegame{slot}/careerSavegame.xml").is_file()) else None
            recovery = self.backup(existing_slot, "Before restoring a backup")
            stage = Path(tempfile.mkdtemp(prefix=".hub-restore-", dir=self.profile))
            dest = self.profile / f"savegame{slot}" if slot else None
            old_dir = self.profile / f".hub-before-restore-{uuid.uuid4().hex}" if slot else None
            moved = False
            try:
                if slot:
                    for member in archive.infolist():
                        if not member.filename.startswith(prefix) or member.is_dir():
                            continue
                        target = stage / PurePosixPath(member.filename).relative_to(prefix)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as src, target.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                    read_xml((stage / "careerSavegame.xml").read_bytes())
                require_game_closed()
                if dest and dest.exists():
                    os.rename(dest, old_dir)
                    moved = True
                try:
                    if dest:
                        os.rename(stage, dest)
                    atomic_write(self.profile / "gameSettings.xml", new_settings)
                except BaseException:
                    if dest and dest.exists():
                        shutil.rmtree(dest)
                    if moved:
                        os.rename(old_dir, dest)
                    atomic_write(self.profile / "gameSettings.xml", old_settings)
                    raise
                if moved:
                    shutil.rmtree(old_dir)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        self.saves = list_saves(self.profile)
        return recovery

    def import_mods(self, files: list[Path], progress=None):
        dest = self.data / "imports"
        dest.mkdir(exist_ok=True)
        imported, skipped = [], []
        for i, path in enumerate(files):
            if progress:
                progress(f"Importing {i + 1} / {len(files)}: {path.name}")
            info = mod_info(path)
            if info["error"] or path.suffix.lower() != ".zip":
                skipped.append(f"{path.name}: {info['error'] or 'Choose ZIP mods'}")
                continue
            if path.name in self.mods or (dest / path.name).exists():
                skipped.append(f"{path.name}: already present; existing files were kept")
                continue
            temp = dest / (".import-" + uuid.uuid4().hex)
            try:
                shutil.copy2(path, temp)
                # Hard link creation is atomic and refuses to overwrite a concurrent import.
                os.link(temp, dest / path.name)
                imported.append(path.name)
            finally:
                temp.unlink(missing_ok=True)
        return imported, skipped

    def dlcs(self):
        found = {}
        # A DLC's archive is encrypted. Read its public metadata from saves and
        # FS25's own log rather than trying to unpack or modify the paid files.
        metadata = {}
        for save in sorted(self.saves, key=lambda s: s["mtime"]):
            for mod in save["mods"]:
                if mod["modName"].startswith("pdlc_"):
                    metadata[mod["modName"]] = dict(mod)
        log = self.profile / "log.txt"
        try:
            with log.open(errors="replace") as stream:
                for line in stream:
                    match = re.search(r"Available dlc: \(Hash: ([a-fA-F0-9]+)\) \(Version: ([\d.]+)\) (pdlc_[A-Za-z0-9_]+)", line)
                    if match:
                        digest, version, name = match.groups()
                        metadata.setdefault(name, {}).update(version=version, fileHash=digest)
        except OSError:
            pass
        for library in steam_libraries(self.home):
            folder = library / "steamapps/common/Farming Simulator 25/pdlc"
            for path in folder.glob("*.dlc"):
                name = "pdlc_" + path.stem
                title = re.sub(r"([a-z])([A-Z])", r"\1 \2", path.stem)
                found[name] = {**metadata.get(name, {}), "name": name,
                               "title": metadata.get(name, {}).get("title") or title[:1].upper() + title[1:],
                               "path": str(path), "size": path.stat().st_size, "installed": True}
        for name, info in metadata.items():
            if name not in found:
                found[name] = {**info, "name": name, "title": info.get("title", name), "path": "", "size": 0, "installed": False}
        return sorted(found.values(), key=lambda d: d["title"].casefold())

    @staticmethod
    def launch_command(slot=None):
        steam = shutil.which("steam")
        command = [steam] if steam else ["flatpak", "run", "com.valvesoftware.Steam"]
        command.extend(["-applaunch", APP_ID])
        if slot:
            command.extend(["-autoStartSavegameId", str(slot)])
        return command

    @staticmethod
    def launch(slot=None):
        require_game_closed()
        env = os.environ.copy()
        env.pop("LD_PRELOAD", None)
        subprocess.Popen(Hub.launch_command(slot), env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
