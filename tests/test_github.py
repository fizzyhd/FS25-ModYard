import hashlib
import io
import json
import shutil
import time
import unittest
import urllib.error
import zipfile
from unittest.mock import patch

from farmsim_hub.core import HubError
from farmsim_hub.github import GitHubClient, GitHubError, KNOWN_REPOSITORIES, github_url, repository
from farmsim_hub.modhub import ModHubClient
from tests.test_core import make_mod
from tests.test_workflows import TemporaryHub


class GitHubTests(TemporaryHub, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = GitHubClient()
        self.mod = self.hub.mods["FS25_Existing.zip"]
        self.repo = "FixtureAuthor/FS25_Existing"
        self.package = self.home / "new.zip"
        make_mod(self.package, title="Existing", version="2.0.0.0")
        self.data = self.package.read_bytes()
        self.asset = {"id": 25, "name": "FS25_Existing.zip", "state": "uploaded", "size": len(self.data),
                      "browser_download_url": "https://github.com/" + self.repo + "/releases/download/v2.0.0.0/FS25_Existing.zip",
                      "digest": "sha256:" + hashlib.sha256(self.data).hexdigest(), "updated_at": "2026-10-22T00:00:00Z"}
        self.release = {"id": 12, "tag_name": "v2.0.0.0", "draft": False, "prerelease": False,
                        "html_url": "https://github.com/" + self.repo + "/releases/tag/v2.0.0.0", "assets": [self.asset]}

    def checked(self):
        with patch.object(self.client, "_json", return_value=self.release), patch.object(self.client, "_read", return_value=self.data):
            return self.client.check_repo(self.mod, self.repo)

    def test_packaged_version_is_used_instead_of_outdated_source_or_tag(self):
        self.release["tag_name"] = "1.9.0.0"
        entry = self.checked()
        self.assertEqual(entry["version"], "2.0.0.0")
        self.assertTrue(entry["package_verified"])

    def test_unchanged_verified_asset_reuses_metadata(self):
        entry = self.checked()
        with patch.object(self.client, "_json", return_value=self.release), patch.object(self.client, "_read") as read:
            again = self.client.check_repo(self.mod, self.repo, previous=entry)
        read.assert_not_called()
        self.assertEqual(again["version"], "2.0.0.0")

    def test_wrong_author_and_a_source_code_zip_are_rejected(self):
        self.mod["author"] = "Different creator"
        with self.assertRaisesRegex(GitHubError, "title or author"):
            self.checked()
        self.mod["author"] = "Fixture author"
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("FS25_Existing-main/modDesc.xml", "<modDesc/>")
        self.data = content.getvalue()
        self.asset["digest"] = None
        with self.assertRaisesRegex(GitHubError, "modDesc.xml at its root"):
            self.checked()

    def test_prereleases_and_missing_asset_names_are_not_offered(self):
        self.release["prerelease"] = True
        self.assertTrue(self.checked()["release_unavailable"])
        self.release["prerelease"] = False
        self.asset["name"] = "FS22_Existing.zip"
        self.assertTrue(self.checked()["release_unavailable"])

    def test_checksum_mismatch_blocks_the_release(self):
        self.asset["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(GitHubError, "SHA-256"):
            self.checked()

    def test_external_or_other_repository_asset_is_rejected(self):
        for url in ["https://example.com/FS25_Existing.zip", "https://github.com/Other/Repo/releases/download/v2/FS25_Existing.zip"]:
            self.asset["browser_download_url"] = url
            with self.assertRaisesRegex(GitHubError, "selected repository"):
                self.checked()
        self.assertFalse(github_url("https://github.com.evil.example/file.zip"))
        self.assertFalse(github_url("http://github.com/author/repo"))
        self.assertTrue(github_url("https://release-assets.githubusercontent.com/asset.zip"))

    def test_repository_link_parsing(self):
        self.assertEqual(repository("https://github.com/Author/Mod/releases/latest"), "Author/Mod")
        self.assertEqual(repository("Author/Mod.git"), "Author/Mod")
        with self.assertRaises(GitHubError):
            repository("https://example.com/Author/Mod")

    def test_all_autodrive_mappings_bypass_modhub_lookup(self):
        engine = ModHubClient(self.hub.data)
        for name in ["FS25_AutoDrive.zip", "FS25_AutoDrive_XT_Intersection.zip", "FS25_AutoDrive_xCrossing.zip", "FS25_AutoDrive_T_intersection.zip", "FS25_AutoDrive_reverseToMarker.zip"]:
            with patch.object(engine.github, "check_repo", return_value={"source": "github", "repo": KNOWN_REPOSITORIES[name]}) as check, patch.object(engine, "_fetch") as fetch:
                result = engine._check_one({**self.mod, "name": name})
            self.assertEqual(result["repo"], KNOWN_REPOSITORIES[name])
            fetch.assert_not_called()
            check.assert_called_once()

    def test_unmatched_modhub_mod_falls_back_to_github_search(self):
        engine = ModHubClient(self.hub.data)
        entry = self.checked()
        with patch.object(engine, "_fetch", return_value="<html>No results</html>"), patch.object(engine.github, "find", return_value=entry):
            result = engine.check_mods([self.mod])[0]
        self.assertEqual(result["source"], "github")
        self.assertEqual(result["status"], "Update available")

    def test_ambiguous_repository_results_need_a_source_choice(self):
        entry = self.checked()
        other = {**entry, "repo": "SecondAuthor/FS25_Existing", "repo_url": "https://github.com/SecondAuthor/FS25_Existing"}
        found = {"items": [{"full_name": self.repo}, {"full_name": other["repo"]}]}
        with patch.object(self.client, "_json", return_value=found), patch.object(self.client, "check_repo", side_effect=[entry, other]):
            result = self.client.find(self.mod)
        self.assertTrue(result["unmatched"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_github_update_reuses_backups_and_atomic_replacement(self):
        engine = ModHubClient(self.hub.data)
        entry = self.checked()
        engine.cache["entries"][self.mod["name"]] = entry
        source = self.library / self.mod["name"]
        before = source.read_bytes()
        with patch.object(engine.github, "check_repo", return_value=entry), patch.object(engine, "_download", side_effect=lambda url, dest, progress=None: shutil.copy2(self.package, dest)), patch.object(engine, "detail") as detail:
            result = engine.install_update(self.hub, self.mod["name"])
        detail.assert_not_called()
        self.assertEqual(source.read_bytes(), self.data)
        self.assertEqual((result["backup"] / self.mod["name"]).read_bytes(), before)
        self.assertEqual(json.loads((result["backup"] / "update.json").read_text())["source"], "github")

    def test_downloaded_identity_is_rechecked_when_a_checksum_is_unavailable(self):
        engine = ModHubClient(self.hub.data)
        entry = self.checked()
        entry["digest"] = None
        engine.cache["entries"][self.mod["name"]] = entry
        source = self.library / self.mod["name"]
        before = source.read_bytes()
        make_mod(self.package, title="Completely unrelated machinery", version="2.0.0.0")
        with patch.object(engine.github, "check_repo", return_value=entry), patch.object(engine, "_download", side_effect=lambda url, dest, progress=None: shutil.copy2(self.package, dest)):
            with self.assertRaisesRegex(HubError, "identity"):
                engine.install_update(self.hub, self.mod["name"])
        self.assertEqual(source.read_bytes(), before)

    def test_public_search_limit_does_not_retry_until_reset(self):
        url = "https://api.github.com/search/repositories?q=fixture"
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(time.time() + 600), "X-RateLimit-Resource": "search"}
        error = urllib.error.HTTPError(url, 403, "Forbidden", headers, None)
        with patch("farmsim_hub.github.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            for _ in range(2):
                with self.assertRaisesRegex(GitHubError, "limit"):
                    self.client.open(url)
            opener.return_value.open.assert_called_once()

    def test_failed_github_search_keeps_an_unmatched_modhub_result_with_explanation(self):
        engine = ModHubClient(self.hub.data)
        with patch.object(engine, "_fetch", return_value="<html>No results</html>"), patch.object(engine.github, "find", side_effect=GitHubError("GitHub request limit reached")):
            result = engine.check_mods([self.mod])[0]
        self.assertFalse(result["can_update"])
        self.assertIn("limit", result["github_error"])


if __name__ == "__main__":
    unittest.main()
