"""Read FS25 shop categories from the store items a mod actually registers."""

import re
import zipfile
from pathlib import Path, PurePosixPath

from .core import HubError, MAX_XML, read_xml

CATEGORY_SCHEMA = 1
MAX_FILES = 512
MAX_TOTAL_XML = 32 * 1024 * 1024

CATEGORY_LABELS = {
    "tractorsS": "Small tractors", "tractorsM": "Medium tractors", "tractorsL": "Large tractors",
    "trucks": "Trucks", "cars": "Cars and bikes", "miscDrivables": "Other vehicles",
    "frontLoaderVehicles": "Front loader vehicles", "frontLoaders": "Front loaders", "frontLoaderTools": "Front loader tools",
    "teleLoaderVehicles": "Telehandlers", "teleLoaders": "Telehandlers", "teleLoaderTools": "Telehandler tools",
    "wheelLoaderVehicles": "Wheel loaders", "wheelLoaders": "Wheel loaders", "wheelLoaderTools": "Wheel loader tools",
    "skidSteerVehicles": "Skid steer loaders", "skidSteerTools": "Skid steer tools", "forklifts": "Forklifts",
    "trailers": "Trailers", "augerWagons": "Auger wagons", "trailersChangingSystem": "Interchangeable trailers",
    "trailersFlatbed": "Flatbed trailers", "lowloaders": "Low loaders", "trailersSemi": "Semi trailers",
    "plows": "Plows", "cultivators": "Cultivators", "discHarrows": "Disc harrows", "powerHarrows": "Power harrows",
    "subsoilers": "Subsoilers", "spaders": "Spaders", "mulchers": "Mulchers", "rollers": "Rollers", "stonePickers": "Stone pickers",
    "seeders": "Seeders", "planters": "Planters", "ricePlanters": "Rice planters", "riceHarvesters": "Rice harvesters",
    "sprayers": "Sprayers", "fertilizerSpreaders": "Fertilizer spreaders", "manureSpreaders": "Manure spreaders",
    "slurryTanks": "Slurry tanks", "slurryTransport": "Slurry transport", "weeders": "Weeders", "grasslandCare": "Grassland care",
    "harvesters": "Combine harvesters", "cutters": "Grain headers", "cornHeaders": "Corn headers", "cutterTrailers": "Header trailers",
    "forageHarvesters": "Forage harvesters", "forageHarvesterCutters": "Forage harvester headers",
    "forageHarvesterCutterTrailers": "Forage header trailers", "potatoPlanting": "Potato planters", "potatoHarvesting": "Potato harvesters",
    "beetHarvesters": "Beet harvesters", "beetHarvesterCutters": "Beet headers", "vegetableHarvesters": "Vegetable harvesters",
    "cottonHarvesters": "Cotton harvesters", "cottonTransport": "Cotton transport", "sugarcaneHarvesters": "Sugarcane harvesters",
    "sugarcaneTransport": "Sugarcane transport", "grapeHarvesters": "Grape harvesters", "oliveHarvesters": "Olive harvesters", "grapeTools": "Grape tools",
    "mowers": "Mowers", "combineWindrower": "Self-propelled mowers", "tedders": "Tedders", "windrowers": "Windrowers",
    "loaderWagons": "Forage wagons", "balersSquare": "Square balers", "balersRound": "Round balers", "baleWrappers": "Bale wrappers",
    "baleLoaders": "Bale loaders", "forageMixers": "Feed mixers", "animalTransport": "Animal transport",
    "forestryHarvesters": "Forestry harvesters", "forestryForwarders": "Forestry forwarders", "forestryExcavators": "Forestry excavators",
    "forestryExcavatorTools": "Excavator tools", "woodTransport": "Timber transport", "forestryWinches": "Forestry winches",
    "forestryMulchers": "Forestry mulchers", "forestryMisc": "Forestry equipment", "woodChippers": "Wood chippers",
    "chainsaws": "Chainsaws", "stumpCutters": "Stump cutters", "treePlanters": "Tree planters",
    "weights": "Weights", "leveler": "Levelers", "silocompaction": "Silo compactors", "belts": "Conveyor belts",
    "barrels": "Barrels and tanks", "winterEquipment": "Winter equipment", "misc": "Miscellaneous equipment",
    "handtoolsMisc": "Hand tools", "pallets": "Pallets", "bigbags": "Big bags", "bigbagPallets": "Big bag pallets", "bales": "Bales",
    "ibc": "IBC tanks", "shippingContainers": "Shipping containers", "wood": "Wood",
    "objectMisc": "Miscellaneous objects", "palletAnimals": "Animal supply pallets", "palletSeeds": "Seed pallets",
    "palletSeedsRootcrops": "Root crop seed pallets", "palletFertilizerHerbicide": "Fertilizer and herbicide pallets",
    "palletSilage": "Silage supplies", "palletGrassland": "Grassland supplies", "palletBaling": "Baling supplies",
    "palletForestry": "Forestry supplies", "palletVegetables": "Vegetable pallets", "specialCropsPallets": "Special crop pallets",
    "sheds": "Sheds", "gardenSheds": "Garden sheds", "silos": "Silos", "siloExtensions": "Silo extensions", "storages": "Storage buildings",
    "farmhouses": "Farmhouses", "animalpens": "Animal pens", "beeHives": "Beehives", "fences": "Fences",
    "productionPoints": "Production sites", "sellingPoints": "Selling points", "generators": "Generators",
    "dieselTanks": "Diesel tanks", "waterTanks": "Water tanks", "fillableTanks": "Fillable tanks",
    "floodLighting": "Floodlights", "decoration": "Decorations", "placeableMisc": "Other placeables",
}
LABELS_BY_KEY = {key.casefold(): value for key, value in CATEGORY_LABELS.items()}
# These category IDs are shared with the official ModHub's category menu.
MODHUB_CATEGORIES = {label: key for key, label in CATEGORY_LABELS.items()
                     if key not in {"teleLoaders", "wheelLoaders", "combineWindrower", "objectMisc", "ibc", "wood", "silocompaction"}
                     and not key.startswith("pallet")}
MODHUB_CATEGORIES["Telehandlers"] = "teleLoaderVehicles"
MODHUB_CATEGORIES["Wheel loaders"] = "wheelLoaderVehicles"
MODHUB_CATEGORIES["Stump cutters"] = "forestryStumpCutters"
MODHUB_CATEGORIES["Tree planters"] = "forestryPlanters"
MODHUB_CATEGORIES["Shipping containers"] = "containers"


def modhub_category_for_query(query):
    compact = re.sub(r"[\W_]+", "", query.casefold())
    if not compact:
        return None
    for label, key in MODHUB_CATEGORIES.items():
        name = re.sub(r"[\W_]+", "", label.casefold())
        if compact in {name, name.removesuffix("s"), key.casefold()}:
            return key
    return None


def category_label(key):
    return LABELS_BY_KEY.get(key.casefold(), re.sub(r"(?<=[a-z0-9])(?=[A-Z])|[_-]+", " ", key).strip().capitalize())


def matches_search(mod, query):
    parts = query.casefold().split()
    values = [mod.get(k, "") for k in ["title", "name", "author", "kind"]]
    values += mod.get("categories", []) + mod.get("category_terms", [])
    haystack = " ".join(values).casefold()
    compact = re.sub(r"[\W_]+", "", haystack)
    return all(part in haystack or (bool(needle := re.sub(r"[\W_]+", "", part)) and needle in compact) for part in parts)


def category_cache_valid(info):
    if info.get("category_schema") != CATEGORY_SCHEMA:
        return False
    for filename, size, mtime in info.get("category_files", []):
        try:
            stat = Path(filename).stat()
            current = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            current = [None, None]
        if current != [size, mtime]:
            return False
    return True


def read_mod_categories(path, root, kind, game_data_dirs=()):
    categories, raw_keys, watched = [], [], {}
    roots = [Path(p).resolve() for p in game_data_dirs]
    if path.is_dir():
        roots.append(path.resolve())
    archive = None if path.is_dir() else zipfile.ZipFile(path)
    members = {m.filename.replace("\\", "/").casefold(): m for m in archive.infolist()} if archive else {}
    visited, total = set(), 0
    custom = {}
    translations = {n.get("name"): (n.findtext("en") or n.text or "").strip() for n in root.findall("l10n/text")}
    for node in root.findall("storeCategories/storeCategory"):
        name, title = node.get("name", ""), node.get("title", "")
        if title.startswith("$l10n_"):
            title = translations.get(title[6:], "")
        if name and title:
            custom[name.casefold()] = title

    def add(key):
        for value in re.split(r"[\s,;]+", key.strip()):
            if not value:
                continue
            label = custom.get(value.casefold(), category_label(value))
            if label not in categories:
                categories.append(label)
            if value not in raw_keys:
                raw_keys.append(value)

    def local_file(candidate):
        candidate = candidate.resolve()
        if not any(candidate.is_relative_to(base) for base in roots):
            return None
        try:
            stat = candidate.stat()
            watched[str(candidate)] = [stat.st_size, stat.st_mtime_ns]
            if not candidate.is_file() or stat.st_size > MAX_XML:
                return None
            return candidate.read_bytes(), ("file", candidate)
        except OSError:
            watched[str(candidate)] = [None, None]
            return None

    def read(reference, parent=None):
        reference = reference.strip().replace("\\", "/")
        if reference.startswith("$data/"):
            for base in roots:
                if path.is_dir() and base == path.resolve():
                    continue
                result = local_file(base / reference[6:])
                if result:
                    return result
            return None
        prefix = "$moddir$" + path.stem + "/"
        if reference.casefold().startswith(prefix.casefold()):
            reference, parent = reference[len(prefix):], None
        if not reference or reference.startswith(("$", "/")) or ":" in reference:
            return None
        if parent and parent[0] == "file":
            return local_file(parent[1].parent / reference)
        if path.is_dir():
            return local_file(path / reference)
        parts = []
        base = str(PurePosixPath(parent[1]).parent) if parent else ""
        for part in (base + "/" + reference).split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        member = members.get("/".join(parts).casefold())
        if member is None or member.file_size > MAX_XML:
            return None
        return archive.read(member), ("zip", member.filename)

    def visit(reference, parent=None, depth=0):
        nonlocal total
        if len(visited) >= MAX_FILES or total >= MAX_TOTAL_XML or depth > 8:
            return
        try:
            result = read(reference, parent)
            if not result:
                return
            data, source = result
            if source in visited:
                return
            visited.add(source)
            total += len(data)
            document = read_xml(data)
            values = [n.text for n in document.findall("storeData/category") if n.text and n.text.strip()]
            if values:
                for value in values:
                    add(value)
            else:
                inherited = document.find("parentFile")
                if inherited is not None and inherited.get("xmlFilename"):
                    visit(inherited.get("xmlFilename"), source, depth + 1)
        except (OSError, ValueError, KeyError, RuntimeError, HubError, zipfile.BadZipFile):
            pass

    try:
        if kind in {"Maps", "Scripts"}:
            categories.append(kind)
        for node in root.findall("storeItems/storeItem"):
            reference = node.get("xmlFilename") or node.get("filename")
            if reference:
                visit(reference)
        if not categories:
            categories.append(kind)
    finally:
        if archive:
            archive.close()
    return {"categories": categories, "category_terms": raw_keys, "category_schema": CATEGORY_SCHEMA,
            "category_files": [[name, *stamp] for name, stamp in watched.items()]}
