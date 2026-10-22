"""Discover public mod repositories and read packaged GitHub releases."""

import hashlib
import io
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from . import __version__
from .core import HubError, MAX_XML, read_xml, version_number

API = "https://api.github.com/"
SMALL_PACKAGE = 8 * 1024 * 1024
# The main AutoDrive README links these companion repositories explicitly.
KNOWN_REPOSITORIES = {
    "FS25_AutoDrive.zip": "Stephan-S/FS25_AutoDrive",
    "FS25_AutoDrive_XT_Intersection.zip": "Williwillswisse/AD_XandT-intersections",
    "FS25_AutoDrive_XT_Intersection_RHD.zip": "Williwillswisse/AD_XandT-intersections",
    "FS25_AutoDrive_xCrossing.zip": "Williwillswisse/AD_xCrossing",
    "FS25_AutoDrive_xCrossing_RHD.zip": "Williwillswisse/AD_xCrossing",
    "FS25_AutoDrive_T_intersection.zip": "Williwillswisse/AD_T_intersection",
    "FS25_AutoDrive_T_intersection_RHD.zip": "Williwillswisse/AD_T_intersection",
    "FS25_AutoDrive_reverseToMarker.zip": "Williwillswisse/AD_reverseToMarker",
}


class GitHubError(HubError):
    pass


def repository(value):
    value = value.strip()
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise GitHubError("Paste a public https://github.com/owner/repository URL.")
        parts = parsed.path.strip("/").split("/")
    else:
        parts = value.strip("/").split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", p) or p in {".", ".."} for p in parts[:2]):
        raise GitHubError("Use a GitHub repository URL or owner/repository.")
    return parts[0] + "/" + parts[1].removesuffix(".git")


def github_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
            and parsed.hostname in {"github.com", "api.github.com", "raw.githubusercontent.com", "release-assets.githubusercontent.com",
                                    "objects.githubusercontent.com", "github-releases.githubusercontent.com"})


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not github_url(newurl):
            raise GitHubError("The release redirected outside GitHub's download servers.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower())


def descriptor(data):
    root = read_xml(data)
    title = root.find("title")
    return {"version": (root.findtext("version") or "").strip(), "author": (root.findtext("author") or "").strip(),
            "title": ((title.findtext("en") or title.text or "").strip() if title is not None else "")}


def same_identity(mod, info, trusted=False):
    author, other = normalized(mod.get("author", "")), normalized(info.get("author", ""))
    authors_match = bool(author and other and (author == other or (min(len(author), len(other)) >= 6 and (author in other or other in author))))
    titles_match = SequenceMatcher(None, normalized(mod.get("title", "")), normalized(info.get("title", ""))).ratio() >= 0.75
    return authors_match and (trusted or titles_match)


def embedded_repositories(mod):
    try:
        source = Path(mod["path"])
        if source.is_dir():
            path = source / "modDesc.xml"
            if path.stat().st_size > MAX_XML:
                return []
            data = path.read_bytes()
        else:
            with zipfile.ZipFile(source) as archive:
                member = next(m for m in archive.infolist() if m.filename.lower() == "moddesc.xml")
                if member.file_size > MAX_XML:
                    return []
                data = archive.read(member)
        return list(dict.fromkeys(repository(u) for u in re.findall(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data.decode(errors="replace"))))
    except (OSError, ValueError, zipfile.BadZipFile, StopIteration, HubError):
        return []


class GitHubClient:
    def __init__(self):
        self.paused = {}
        self.lock = threading.RLock()

    def open(self, url):
        if not github_url(url):
            raise GitHubError("Use a public GitHub release download.")
        resource = "search" if "/search/" in url else "core"
        if urllib.parse.urlsplit(url).hostname == "api.github.com":
            with self.lock:
                reset = max(self.paused.get(resource, 0), self.paused.get("core", 0))
            if reset > time.time():
                raise GitHubError("GitHub's public request limit was reached. Try again after " + datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M UTC") + ". You can also open the repository in your browser.")
        headers = {"User-Agent": f"FS25ModYard/{__version__}", "Accept": "application/vnd.github+json" if url.startswith(API) else "*/*"}
        try:
            response = urllib.request.build_opener(GitHubRedirect()).open(urllib.request.Request(url, headers=headers), timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429}:
                if exc.headers.get("X-RateLimit-Remaining") == "0" or exc.code == 429:
                    reset = max(time.time() + 60, float(exc.headers.get("X-RateLimit-Reset", time.time() + 60)))
                    with self.lock:
                        self.paused[exc.headers.get("X-RateLimit-Resource", resource)] = reset
                    exc.close()
                    raise GitHubError("GitHub's public request limit was reached. Try again later.") from exc
            exc.close()
            raise
        if not github_url(response.geturl()):
            response.close()
            raise GitHubError("The release did not come from GitHub's servers.")
        return response

    def _read(self, url, limit=MAX_XML):
        with self.open(url) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise GitHubError("The GitHub response was larger than expected.")
        return data

    def _json(self, url):
        try:
            return json.loads(self._read(url))
        except (ValueError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                return None
            raise GitHubError("GitHub could not complete the request: " + str(exc)) from exc

    def _source_descriptor(self, repo, tag):
        url = "https://raw.githubusercontent.com/" + repo + "/" + urllib.parse.quote(tag, safe="") + "/modDesc.xml"
        try:
            return descriptor(self._read(url))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise

    def _package_descriptor(self, asset):
        data = self._read(asset["browser_download_url"], SMALL_PACKAGE)
        digest = asset.get("digest")
        if digest and digest.startswith("sha256:") and hashlib.sha256(data).hexdigest() != digest[7:]:
            raise GitHubError("The release ZIP did not match GitHub's SHA-256 checksum.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                member = next((m for m in archive.infolist() if m.filename.lower() == "moddesc.xml"), None)
                if member is None or member.file_size > MAX_XML:
                    raise GitHubError("This release ZIP is not a packaged mod with modDesc.xml at its root.")
                return descriptor(archive.read(member))
        except (zipfile.BadZipFile, RuntimeError, HubError) as exc:
            raise GitHubError("Cannot verify this GitHub mod package: " + str(exc)) from exc

    def check_repo(self, mod, repo, previous=None, trusted=False):
        repo = repository(repo)
        previous = previous or {}
        release = self._json(API + "repos/" + repo + "/releases/latest")
        base = {"source": "github", "repo": repo, "repo_url": "https://github.com/" + repo,
                "url": "https://github.com/" + repo + "/releases", "checked_at": datetime.now(timezone.utc).isoformat()}
        if not release or release.get("draft") or release.get("prerelease"):
            return {**base, "release_unavailable": True}
        asset = next((a for a in release.get("assets", []) if a.get("name") == mod["name"] and a.get("state", "uploaded") == "uploaded"), None)
        if not asset:
            return {**base, "release_unavailable": True}
        download = asset.get("browser_download_url", "")
        if not github_url(download) or urllib.parse.urlsplit(download).hostname != "github.com" or not urllib.parse.unquote(urllib.parse.urlsplit(download).path).casefold().startswith(("/" + repo + "/releases/download/").casefold()):
            raise GitHubError("The release asset does not belong to the selected repository.")
        stamp = [asset.get("id"), asset.get("updated_at"), asset.get("digest")]
        if previous.get("repo", "").casefold() == repo.casefold() and previous.get("asset_stamp") == stamp and previous.get("package_verified"):
            info = {k: previous.get(k, "") for k in ["version", "title", "author"]}
            packaged = True
        elif 0 < asset.get("size", 0) <= SMALL_PACKAGE:
            info = self._package_descriptor(asset)
            packaged = True
        else:
            info = self._source_descriptor(repo, release["tag_name"])
            tag = release["tag_name"].removeprefix("v").removeprefix("V")
            info["version"] = tag if version_number(tag) is not None else info.get("version", "")
            packaged = False
        if not same_identity(mod, info, trusted):
            raise GitHubError("The GitHub release's mod title or author does not match the installed mod.")
        return {**base, **info, "filename": asset["name"], "download_url": download,
                "url": release.get("html_url", base["url"]), "release_tag": release["tag_name"],
                "asset_stamp": stamp, "package_verified": packaged, "digest": asset.get("digest"),
                "size_bytes": asset.get("size", 0), "trusted_repo": trusted}

    def find(self, mod, previous=None):
        for repo in embedded_repositories(mod)[:3]:
            try:
                entry = self.check_repo(mod, repo, previous, trusted=True)
                if not entry.get("release_unavailable"):
                    return entry
            except GitHubError:
                continue
        stem = Path(mod["name"]).stem
        query = urllib.parse.urlencode({"q": stem + " in:name fork:false archived:false", "per_page": 3})
        found = self._json(API + "search/repositories?" + query) or {}
        matches = []
        for candidate in found.get("items", [])[:3]:
            if candidate.get("fork") or candidate.get("archived"):
                continue
            try:
                entry = self.check_repo(mod, candidate["full_name"], previous)
                if not entry.get("release_unavailable"):
                    matches.append(entry)
            except GitHubError:
                continue
        if len(matches) == 1:
            return matches[0]
        if matches:
            return {"unmatched": True, "candidates": [{"repo": e["repo"], "url": e["repo_url"]} for e in matches],
                    "checked_at": datetime.now(timezone.utc).isoformat()}
        return None
