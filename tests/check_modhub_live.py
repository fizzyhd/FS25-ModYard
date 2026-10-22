"""Opt-in live ModHub check/download/replace/export using a disposable library."""

import json
import shutil
import zipfile
from pathlib import Path

from farmsim_hub.modhub import ModHubClient, version_number
from farmsim_hub.transfers import export_loadout
from tests import test_core as fixtures


def main():
    fixture = fixtures.CoreTests()
    fixture.setUp()
    try:
        hub = fixture.hub
        name = "FS25_MapObjectsHider.zip"
        client = ModHubClient(hub.data)
        search = client.search("Map Objects Hider")
        output = Path("/tmp/farmsim-hub-browse-live")
        output.mkdir(exist_ok=True)
        for item in search["items"]:
            if item.get("picture"):
                picture = output / (item["id"] + ".img")
                shutil.copy2(item["picture"], picture)
                item["picture"] = str(picture)
        (output / "results.json").write_text(json.dumps(search))
        item = next(i for i in search["items"] if i["id"] == "310506")
        assert item["picture"], "The official ModHub picture was not retrieved"
        downloaded = client.download_mod(hub, item["id"])
        source = downloaded["path"]
        assert source.is_file()
        # Re-create an older installed version inside this disposable imports folder.
        fixtures.make_mod(source, title="Map Objects Hider", version="1.0.0.0")
        before = source.read_bytes()
        hub.scan()
        loadout = hub.new_loadout("Live download check", [name])
        results = client.check_mods([hub.mods[name]])
        result = results[0]
        assert result["status"] == "Update available", result
        updated = client.install_update(hub, name)
        assert (updated["backup"] / name).read_bytes() == before
        assert version_number(hub.mods[name]["version"]) == version_number(result["version"])
        hub.scan()
        output = fixture.home / "dedicated-server-bundle.zip"
        exported = export_loadout(hub, loadout, output)
        with zipfile.ZipFile(output) as bundle:
            assert bundle.read("mods/" + name) == source.read_bytes()
            assert bundle.testzip() is None
        print(json.dumps({"official_page": result["url"], "download": result["download_url"],
                          "version": updated["version"], "download_bytes": source.stat().st_size,
                          "search_pictures_new_download_check_backup_replace_export": "passed", "exported_mods": exported["mods"],
                          "library": "temporary fixture only; removed after check"}, indent=2))
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    main()
