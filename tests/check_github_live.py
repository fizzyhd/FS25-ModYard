"""Check all five AutoDrive release sources and replace one disposable fixture."""

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from farmsim_hub.modhub import ModHubClient
from tests import test_core as fixtures


def main():
    fixture = fixtures.CoreTests()
    fixture.setUp()
    try:
        specs = {
            "FS25_AutoDrive.zip": ("AutoDrive", "AutoDrive Team"),
            "FS25_AutoDrive_XT_Intersection.zip": ("AutoDrive Intersections AiO", "Willi Willswissen"),
            "FS25_AutoDrive_xCrossing.zip": ("AutoDrive-Crossroads", "Willi Willswissen"),
            "FS25_AutoDrive_T_intersection.zip": ("AutoDrive-T-intersection", "Willi Willswissen"),
            "FS25_AutoDrive_reverseToMarker.zip": ("AutoDrive-Reverse To Mapmarker", "Willi Willswissen"),
        }
        for name, (title, author) in specs.items():
            root = ET.Element("modDesc", descVersion="105")
            ET.SubElement(ET.SubElement(root, "title"), "en").text = title
            ET.SubElement(root, "author").text = author
            ET.SubElement(root, "version").text = "0.0.0.1"
            with zipfile.ZipFile(fixture.library / name, "w") as archive:
                archive.writestr("modDesc.xml", ET.tostring(root))
        hub = fixture.hub
        hub.scan()
        loadout = hub.new_loadout("GitHub release check", specs)
        client = ModHubClient(hub.data)
        results = client.check_mods([hub.mods[n] for n in specs])
        assert len(results) == 5
        assert all(r.get("source") == "github" and r["status"] == "Update available" and r.get("package_verified") for r in results), results
        Path("/tmp/farmsim-hub-github-live.json").write_text(json.dumps(client.cache))
        name = "FS25_AutoDrive.zip"
        before = (fixture.library / name).read_bytes()
        updated = client.install_update(hub, name)
        assert (updated["backup"] / name).read_bytes() == before
        assert hub.mods[name]["version"] == updated["version"]
        print(json.dumps({"repositories": [{"mod": r["name"], "repo": r["repo"], "version": r["version"]} for r in results],
                          "autodrive_checks_package_versions_checksum_backup_replacement": "passed",
                          "library": "temporary fixtures only; removed after check"}, indent=2))
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    main()
