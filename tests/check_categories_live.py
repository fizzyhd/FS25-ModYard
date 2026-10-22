"""Check installed shop categories and official ModHub category search."""

import json
import shutil
import tempfile
from pathlib import Path

from farmsim_hub.categories import matches_search
from farmsim_hub.core import Hub
from farmsim_hub.modhub import ModHubClient


def main():
    with tempfile.TemporaryDirectory() as directory:
        hub = Hub(data_dir=Path(directory))
        hub.scan()
        telehandlers = [m for m in hub.mods.values() if "Telehandlers" in m["categories"]]
        assert telehandlers and all(matches_search(m, "telehandler") for m in telehandlers)
        assert any("telehandler" not in (m["title"] + m["name"]).lower() for m in telehandlers)
        assert all(m.get("categories") for m in hub.mods.values())
        result = ModHubClient(hub.data).search("telehandler")
        assert result["category"] == "teleLoaderVehicles"
        assert result["items"] and all("Telehandlers" in m["categories"] for m in result["items"])
        assert any("telehandler" not in m["title"].lower() for m in result["items"])
        output = Path("/tmp/farmsim-hub-category-live")
        output.mkdir(exist_ok=True)
        for item in result["items"]:
            if item.get("picture"):
                picture = output / (item["id"] + ".img")
                shutil.copy2(item["picture"], picture)
                item["picture"] = str(picture)
        (output / "results.json").write_text(json.dumps(result))
        print(json.dumps({"installed_mods": len(hub.mods), "distinct_categories": len({c for m in hub.mods.values() for c in m["categories"]}),
                          "telehandlers": [m["name"] for m in telehandlers], "modhub_category": result["category"],
                          "modhub_results": len(result["items"]), "store_categories_local_search_official_category_search": "passed"}, indent=2))


if __name__ == "__main__":
    main()
