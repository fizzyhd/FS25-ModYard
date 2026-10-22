"""ModHub browsing and validated updates from ModHub or GitHub releases."""

from __future__ import annotations

import os
import hashlib
import re
import shutil
import tempfile
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from . import __version__
from .core import HubError, atomic_write, mod_info, read_json, require_game_closed, valid_mod_name, version_number, write_json
from .github import GitHubClient, GitHubError, KNOWN_REPOSITORIES, github_url, same_identity
from .categories import MODHUB_CATEGORIES, modhub_category_for_query

BASE = "https://www.farming-simulator.com/"
MAX_PAGE = 2 * 1024 * 1024
MAX_DOWNLOAD = 8 * 1024 * 1024 * 1024


def official_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and not parsed.username and not parsed.password and
            parsed.port in (None, 443) and
            (host in {"farming-simulator.com", "www.farming-simulator.com", "giants-software.com"}
             or host.endswith(".giants-software.com")))


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_url(newurl):
            raise HubError("ModHub redirected outside the official GIANTS download servers.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalized(value):
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower())


class ModPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.links, self.headings = [], [], []
        self.cell_depth = 0
        self.cell = ""
        self.heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "table-cell" in attrs.get("class", "").split() and not self.cell_depth:
            self.cell_depth, self.cell = 1, ""
        elif tag == "div" and self.cell_depth:
            self.cell_depth += 1
        if tag in {"h1", "h2"}:
            self.heading = ""
        if tag == "a" and attrs.get("href"):
            self.links.append(urllib.parse.urljoin(BASE, attrs["href"]))

    def handle_data(self, data):
        if self.cell_depth:
            self.cell += data
        if self.heading is not None:
            self.heading += data

    def handle_endtag(self, tag):
        if tag == "div" and self.cell_depth:
            self.cell_depth -= 1
            if not self.cell_depth:
                self.cells.append(" ".join(self.cell.split()))
        if tag in {"h1", "h2"} and self.heading is not None:
            self.headings.append(" ".join(self.heading.split()))
            self.heading = None


class SearchPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.depth = 0
        self.item = None
        self.heading = False
        self.author = False
        self.page_numbers = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if not self.depth and "mod-item" in attrs.get("class", "").split():
                self.depth = 1
                self.item = {"id": "", "title": "", "author": "", "image_url": ""}
            elif self.depth:
                self.depth += 1
        if self.item is not None:
            if tag == "h4":
                self.heading = True
            if tag == "p":
                self.author = True
            if tag == "img":
                self.item["image_url"] = urllib.parse.urljoin(BASE, attrs.get("src", ""))
            if tag == "a":
                parsed = urllib.parse.urlsplit(attrs.get("href", ""))
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path.endswith("mod.php") and query.get("title") == ["fs2025"]:
                    self.item["id"] = query.get("mod_id", [""])[0]
        if tag == "a":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(attrs.get("href", "")).query)
            value = query.get("page", [""])[0]
            if value.isdigit():
                self.page_numbers.add(int(value))

    def handle_data(self, data):
        if self.item is not None and self.heading:
            self.item["title"] += data
        if self.item is not None and self.author:
            self.item["author"] += data

    def handle_endtag(self, tag):
        if tag == "h4":
            self.heading = False
        if tag == "p":
            self.author = False
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                if self.item["id"].isdigit():
                    self.item["title"] = self.item["title"].strip()
                    self.item["author"] = self.item["author"].strip().removeprefix("By:").strip()
                    self.items.append(self.item)
                self.item = None


def parse_mod_page(page, mod_id):
    parsed = ModPage()
    parsed.feed(page)
    fields = {}
    for index, cell in enumerate(parsed.cells[:-1]):
        if cell in {"Game", "Filename", "Version", "Author", "Released", "Size", "Category"}:
            fields[cell] = parsed.cells[index + 1]
    filename = fields.get("Filename", "")
    if fields.get("Game") != "Farming Simulator 25" or not valid_mod_name(filename) or not filename.lower().endswith(".zip"):
        raise HubError("This is not a readable FS25 mod page on the official ModHub.")
    download = next((u for u in parsed.links if official_url(u) and
                     urllib.parse.unquote(urllib.parse.urlsplit(u).path).endswith("/" + filename)), "")
    if not download or not version_number(fields.get("Version", "")):
        raise HubError("ModHub did not provide a usable version and ZIP download for this mod.")
    return {"id": str(mod_id), "filename": filename, "title": next(iter(parsed.headings), filename),
            "author": fields.get("Author", ""), "version": fields["Version"], "released": fields.get("Released", ""),
            "category": fields.get("Category", "ModHub mod").rsplit(" - ", 1)[-1],
            "category_path": fields.get("Category", "ModHub mod"),
            "size": fields.get("Size", ""), "download_url": download,
            "url": BASE + "mod.php?" + urllib.parse.urlencode({"mod_id": str(mod_id), "title": "fs2025", "lang": "en"}),
            "checked_at": datetime.now(timezone.utc).isoformat()}


def file_stamp(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def validate_download(path, entry, source_label="ModHub"):
    digest = entry.get("digest")
    if digest and digest.startswith("sha256:"):
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        if checksum.hexdigest() != digest[7:]:
            raise HubError("The download did not match GitHub's SHA-256 checksum. The installed copy was kept.")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if sum(m.file_size for m in members) > 32 * 1024**3:
                raise HubError("The mod expands beyond the supported size.")
            for member in members:
                member_path = PurePosixPath(member.filename.replace("\\", "/"))
                if member_path.is_absolute() or ".." in member_path.parts or re.match(r"^[a-zA-Z]:", member.filename):
                    raise HubError("The ZIP contains an unsafe filename.")
            if archive.testzip():
                raise HubError("The downloaded ZIP failed its integrity check.")
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise HubError(f"{source_label} did not return a valid mod ZIP. Existing files were kept.") from exc
    updated = mod_info(path)
    if updated["error"] or version_number(updated["version"]) != version_number(entry["version"]):
        raise HubError(f"The downloaded mod's version or modDesc.xml did not match {source_label}. Existing files were kept.")
    return updated


class ModHubClient:
    def __init__(self, data_dir):
        self.data = Path(data_dir)
        self.cache_path = self.data / "modhub-cache.json"
        try:
            self.cache = read_json(self.cache_path, {"entries": {}})
        except HubError:
            self.cache = {"entries": {}}
        self.cache.setdefault("entries", {})
        self.lock = threading.RLock()
        self.github = GitHubClient()
        self.browse_cache_path = self.data / "modhub-browse-details.json"
        try:
            self.browse_details = read_json(self.browse_cache_path, {})
        except HubError:
            self.browse_details = {}

    def _open(self, url):
        if not official_url(url):
            raise HubError("Only official Farming Simulator and GIANTS HTTPS URLs are supported.")
        request = urllib.request.Request(url, headers={"User-Agent": f"FS25ModYard/{__version__}",
                                                     "Accept-Language": "en", "Referer": BASE})
        response = urllib.request.build_opener(OfficialRedirect()).open(request, timeout=30)
        if not official_url(response.geturl()):
            response.close()
            raise HubError("The download did not come from an official GIANTS server.")
        return response

    def _fetch(self, url):
        with self._open(url) as response:
            data = response.read(MAX_PAGE + 1)
        if len(data) > MAX_PAGE:
            raise HubError("The ModHub page was larger than expected.")
        return data.decode("utf-8", errors="replace")

    def detail(self, mod_id):
        if not str(mod_id).isdigit():
            raise HubError("Choose a valid official ModHub page.")
        url = BASE + "mod.php?" + urllib.parse.urlencode({"mod_id": mod_id, "title": "fs2025", "lang": "en"})
        return parse_mod_page(self._fetch(url), mod_id)

    def search(self, query="", page=0, progress=None, category=None):
        page = max(0, int(page))
        parameters = {"title": "fs2025", "lang": "en", "page": page}
        inferred = modhub_category_for_query(query)
        category = category or inferred
        if category and category not in MODHUB_CATEGORIES.values():
            raise HubError("Choose a supported ModHub category.")
        if category:
            parameters["filter"] = category
        if query.strip() and inferred != category:
            parameters["searchMod"] = query.strip()[:200]
        elif not category:
            parameters["filter"] = "latest"
        parsed = SearchPage()
        parsed.feed(self._fetch(BASE + "mods.php?" + urllib.parse.urlencode(parameters)))
        items = list({item["id"]: item for item in parsed.items}.values())
        pictures = self.data / "modhub-browse-pictures"
        pictures.mkdir(parents=True, exist_ok=True)

        def picture(item):
            fallback = next((label for label, key in MODHUB_CATEGORIES.items() if key == category), "ModHub mod")
            item["category"] = fallback
            cached = self.browse_details.get(item["id"])
            try:
                fresh = cached and "category" in cached and (datetime.now(timezone.utc) - datetime.fromisoformat(cached["checked_at"])).total_seconds() < 86400
                detail = cached if fresh else self.detail(item["id"])
                item.update({key: detail[key] for key in ["category", "filename", "version"] if key in detail})
                with self.lock:
                    self.browse_details[item["id"]] = detail
            except (OSError, HubError, ValueError, KeyError, TypeError):
                pass
            primary = item["category"].rsplit(" - ", 1)[-1]
            item["categories"] = [primary]
            if category and fallback.casefold() != primary.casefold():
                item["categories"].append(fallback)
            item["category"] = ", ".join(item["categories"])
            url = item["image_url"]
            item["picture"] = ""
            if not official_url(url):
                return
            path = pictures / (hashlib.sha256(url.encode()).hexdigest() + ".img")
            try:
                if not path.is_file():
                    with self._open(url) as response:
                        data = response.read(4 * 1024 * 1024 + 1)
                    if len(data) > 4 * 1024 * 1024 or not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
                        return
                    atomic_write(path, data)
                item["picture"] = str(path)
            except (OSError, HubError, ValueError):
                pass
        with ThreadPoolExecutor(max_workers=3) as pool:
            for index, _ in enumerate(pool.map(picture, items), 1):
                if progress:
                    progress(f"Loading ModHub categories and pictures {index} / {len(items)}…")
        write_json(self.browse_cache_path, self.browse_details)
        return {"query": query.strip(), "category": category or "", "page": page,
                "has_next": max(parsed.page_numbers, default=page) > page, "items": items}

    def download_mod(self, hub, mod_id, progress=None):
        entry = self.detail(mod_id)
        name = entry["filename"]
        if name in hub.mods or (hub.data / "imports" / name).exists():
            raise HubError("This mod is already installed. Use Mod updates to replace it with a newer version.")
        with tempfile.TemporaryDirectory(prefix=".hub-download-", dir=hub.data) as directory:
            download = Path(directory) / name
            self._download(entry["download_url"], download, progress)
            validate_download(download, entry)
            imported, skipped = hub.import_mods([download], progress)
            if not imported:
                raise HubError("\n".join(skipped) or "The mod could not be imported. Existing files were kept.")
        return {"name": name, "title": entry["title"], "version": entry["version"], "path": hub.data / "imports" / name}

    def _check_one(self, mod):
        cached = self.cache["entries"].get(mod["name"], {})
        repo = cached.get("repo") if cached.get("source") == "github" else None
        repo = repo or (KNOWN_REPOSITORIES.get(mod["name"]) if not cached.get("id") else None)
        if repo:
            try:
                return self.github.check_repo(mod, repo, cached, trusted=bool(cached.get("trusted_repo") or mod["name"] in KNOWN_REPOSITORIES))
            except (GitHubError, OSError, ValueError) as exc:
                return {**cached, "source": "github", "repo": repo, "url": "https://github.com/" + repo + "/releases", "error": str(exc)}
        if cached.get("id"):
            entry = self.detail(cached["id"])
            if entry["filename"] == mod["name"]:
                return entry
        title = mod.get("title") or Path(mod["name"]).stem
        query = urllib.parse.urlencode({"title": "fs2025", "lang": "en", "searchMod": title})
        parsed = SearchPage()
        parsed.feed(self._fetch(BASE + "mods.php?" + query))
        ranked = sorted(parsed.items, key=lambda item: SequenceMatcher(None, normalized(title), normalized(item["title"])).ratio(), reverse=True)
        for candidate in ranked[:4]:
            if SequenceMatcher(None, normalized(title), normalized(candidate["title"])).ratio() < 0.55:
                continue
            entry = self.detail(candidate["id"])
            # Titles are used only to search. Replacement requires an exact filename.
            if entry["filename"] == mod["name"]:
                return entry
        try:
            entry = self.github.find(mod, cached)
            if entry:
                return entry
        except (GitHubError, OSError, ValueError) as exc:
            return {"unmatched": True, "github_error": str(exc), "checked_at": datetime.now(timezone.utc).isoformat()}
        return {"unmatched": True, "checked_at": datetime.now(timezone.utc).isoformat()}

    def check_mods(self, mods, progress=None):
        mods = list(mods)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._check_one, mod): mod for mod in mods}
            failures = 0
            for index, future in enumerate(as_completed(futures), 1):
                mod = futures[future]
                try:
                    entry = future.result()
                except (HubError, OSError, ValueError) as exc:
                    entry = {**self.cache["entries"].get(mod["name"], {}), "error": str(exc),
                             "checked_at": datetime.now(timezone.utc).isoformat()}
                    failures += 1
                    if (isinstance(exc, urllib.error.HTTPError) and exc.code in {403, 429}) or failures >= 5:
                        for pending in futures:
                            pending.cancel()
                        write_json(self.cache_path, self.cache)
                        raise HubError("The update check could not finish. Results already checked have been kept. Try again later.\n" + str(exc)) from exc
                else:
                    failures = 0
                with self.lock:
                    self.cache["entries"][mod["name"]] = entry
                    if index % 20 == 0:
                        write_json(self.cache_path, self.cache)
                if progress:
                    progress(f"Checking updates {index} / {len(mods)}: {mod['title']}")
        self.cache["checked_at"] = datetime.now(timezone.utc).isoformat()
        write_json(self.cache_path, self.cache)
        return self.results({m["name"]: m for m in mods})

    def results(self, mods):
        results = []
        for name, mod in mods.items():
            entry = self.cache["entries"].get(name)
            if not entry:
                continue
            result = {**entry, "name": name, "local_title": mod["title"], "installed_version": mod["version"]}
            local, remote = version_number(mod["version"]), version_number(entry.get("version", ""))
            if entry.get("error"):
                result["status"] = "Check failed"
            elif entry.get("release_unavailable"):
                result["status"] = "No packaged release"
            elif entry.get("unmatched"):
                result["status"] = "No exact match"
            elif local is None or remote is None:
                result["status"] = "Review version"
            else:
                result["status"] = "Update available" if remote > local else "Current" if remote == local else "Local version is newer"
            result["can_update"] = result["status"] == "Update available" and Path(mod["path"]).is_file() and name.lower().endswith(".zip")
            results.append(result)
        order = {"Update available": 0, "Check failed": 1, "Review version": 2, "No exact match": 3}
        return sorted(results, key=lambda r: (order.get(r["status"], 4), r["local_title"].casefold()))

    def link(self, mod, url):
        if urllib.parse.urlsplit(url.strip()).hostname in {"github.com", "www.github.com"} or ("://" not in url and re.fullmatch(r"[\w.-]+/[\w.-]+", url.strip())):
            from .github import repository
            entry = self.github.check_repo(mod, repository(url), trusted=True)
            self.cache["entries"][mod["name"]] = entry
            write_json(self.cache_path, self.cache)
            return
        parsed = urllib.parse.urlsplit(url.strip())
        if not official_url(url.strip()) or parsed.hostname not in {"www.farming-simulator.com", "farming-simulator.com"} or parsed.path != "/mod.php":
            raise HubError("Paste an official farming-simulator.com/mod.php page URL.")
        mod_id = urllib.parse.parse_qs(parsed.query).get("mod_id", [""])[0]
        entry = self.detail(mod_id)
        if entry["filename"] != mod["name"]:
            raise HubError(f"That page is for {entry['filename']}, not {mod['name']}. Existing files were kept.")
        self.cache["entries"][mod["name"]] = entry
        write_json(self.cache_path, self.cache)

    def _download(self, url, destination, progress=None):
        opener = self.github.open if github_url(url) else self._open
        with opener(url) as response, destination.open("xb") as stream:
            expected = int(response.headers.get("Content-Length", "0"))
            if expected > MAX_DOWNLOAD:
                raise HubError("This download exceeds the 8 GB per-mod limit.")
            received, next_notice = 0, 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_DOWNLOAD:
                    raise HubError("This download exceeds the 8 GB per-mod limit.")
                stream.write(chunk)
                if progress and received >= next_notice:
                    progress(f"Downloading {destination.name}: {received / 1024 / 1024:.0f} MB" +
                             (f" / {expected / 1024 / 1024:.0f} MB" if expected else ""))
                    next_notice = received + 4 * 1024 * 1024
            if expected and received != expected:
                raise HubError("The mod download was incomplete. The installed copy was kept.")
            stream.flush()
            os.fsync(stream.fileno())

    def install_update(self, hub, name, progress=None):
        require_game_closed()
        mod = hub.mods.get(name)
        entry = self.cache["entries"].get(name, {})
        if not mod or not (entry.get("id") or entry.get("repo")) or not name.lower().endswith(".zip") or not Path(mod["path"]).is_file():
            raise HubError("Check this ZIP mod's update source before updating it.")
        is_github = entry.get("source") == "github"
        entry = self.github.check_repo(mod, entry["repo"], entry, trusted=entry.get("trusted_repo", False)) if is_github else self.detail(entry["id"])
        source_label = "GitHub" if is_github else "ModHub"
        if entry.get("release_unavailable"):
            raise HubError("This repository has no packaged release for the installed ZIP filename.")
        if entry["filename"] != name:
            raise HubError("The release ZIP filename changed. The installed file was kept.")
        local, remote = version_number(mod["version"]), version_number(entry["version"])
        if local is None or remote is None or remote <= local:
            raise HubError(f"{source_label} no longer lists a newer version. Check for updates again.")
        target = Path(mod["path"]).resolve()
        original_stamp = file_stamp(target)
        if mod_info(target)["version"] != mod["version"]:
            raise HubError("The installed mod changed. Rescan before updating.")
        with tempfile.TemporaryDirectory(prefix=".hub-update-", dir=target.parent) as directory:
            download = Path(directory) / name
            self._download(entry["download_url"], download, progress)
            if progress:
                progress("Checking the downloaded mod ZIP…")
            updated = validate_download(download, entry, source_label)
            if is_github and not same_identity(mod, updated, entry.get("trusted_repo", False)):
                raise HubError("The downloaded GitHub ZIP does not match this mod's identity. The installed copy was kept.")
            prospective = {**hub.mods, name: updated}
            by_stem = {Path(n).stem: n for n in prospective}
            required, todo, missing = {name}, [name], set()
            while todo:
                for dependency in prospective[todo.pop()]["dependencies"]:
                    filename = by_stem.get(dependency)
                    if filename is None or prospective[filename]["error"] or not Path(prospective[filename]["path"]).exists():
                        missing.add(dependency)
                    elif filename not in required:
                        required.add(filename)
                        todo.append(filename)
            if missing:
                raise HubError("The new version needs these dependencies. Import them first:\n" + "\n".join(sorted(missing)))
            require_game_closed()
            if file_stamp(target) != original_stamp:
                raise HubError("The installed file changed during download. It was kept; rescan and try again.")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = hub.data / "mod-backups" / f"{stamp}-{uuid.uuid4().hex[:8]}"
            backup.mkdir(parents=True)
            backup_file = backup / name
            if progress:
                progress("Backing up the installed mod…")
            shutil.copy2(target, backup_file)
            write_json(backup / "update.json", {"name": name, "path": str(target), "previous_version": mod["version"],
                                               "version": updated["version"], "source": "github" if is_github else "modhub", "url": entry["url"]})
            (backup / "README.txt").write_text("To restore this version, close FS25 and copy the ZIP in this folder back to:\n" + str(target) + "\nThen rescan FS25 ModYard.\n")
            require_game_closed()
            if file_stamp(target) != original_stamp:
                raise HubError("The installed file changed while backing it up. It was kept.")
            selections = [(l, l["mods"]) for l in hub.config["loadouts"] if name in l["mods"]]
            for loadout, names in selections:
                loadout["mods"] = sorted(set(names) | required)
            try:
                shutil.copymode(target, download)
                os.replace(download, target)
                try:
                    hub.save_config()
                except BaseException:
                    shutil.copy2(backup_file, download)
                    os.replace(download, target)
                    raise
            except BaseException:
                for loadout, names in selections:
                    loadout["mods"] = names
                raise
        hub.mods[name] = mod_info(target)
        self.cache["entries"][name] = entry
        # A cache failure cannot undo a successful, backed-up mod replacement.
        try:
            write_json(self.cache_path, self.cache)
        except OSError:
            pass
        return {"name": name, "version": updated["version"], "backup": backup}
