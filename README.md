# FS25 ModYard

**A community-built mod manager and loadout organizer for Farming Simulator 25 on Linux.** Find, organize, update, and install mods from one desktop app, with tools for managing saves and preparing mod bundles for dedicated servers.

FS25 ModYard runs natively on Linux and works with the Steam version of Farming Simulator 25 through Proton. It is an independent project inspired by [FarmSim Hub by The FarmSim Guy](https://thefarmsimguy.com/game-creator). It does not use FarmSim Hub's source code or branding and is not affiliated with or endorsed by The FarmSim Guy or GIANTS Software.

## What it does

- **Manage your mod library:** Browse picture cards, search by title, creator, filename, or equipment category, and keep favourites and collections.
- **Build loadouts:** Organize mods for different saves or play sessions, choose DLCs, and export a loadout as a ZIP containing the selected mods and dependencies for a dedicated server.
- **Find mods:** Search the official [Farming Simulator ModHub](https://www.farming-simulator.com/mods.php?title=fs2025&lang=en), view mod details and pictures, and download mods into your library.
- **Check for updates:** Check ModHub and saved GitHub sources at startup (once per 24 hours by default), run manual checks, and install verified updates in a batch with backups.
- **Resolve dependencies:** Find missing loadout dependencies by exact filename on ModHub or from trusted, packaged GitHub releases.
- **Choose mod versions:** Select which source-folder copy each loadout uses when the same mod exists in multiple folders.
- **Import by dragging:** Drop ZIP mods onto ModYard to import them directly into the selected FS25 mod folder.
- **Manage saves:** Adjust supported game settings, clone a save, apply a loadout, and create or restore backups.
- **Keep control of your files:** The app works with your local FS25 files. It has no telemetry or self-updater; online searches and downloads run when you request them.

## Install

### Install or update with one command

Open Konsole and paste:

```sh
curl -fL https://raw.githubusercontent.com/fizzyhd/FS25-ModYard/main/install-latest.sh -o /tmp/fs25-modyard-install.sh && bash /tmp/fs25-modyard-install.sh
```

The installer finds the latest stable release, downloads its bundle and checksum, verifies the download, and installs the app and desktop shortcut in your home folder. It keeps your mods, loadouts, collections, settings, and backups when updating. You do not need to set a version number or uninstall first.

On SteamOS, it avoids trying to install system packages because the system partition is read-only. On supported desktop Linux distributions, it installs missing dependencies with the system package manager and may ask for your `sudo` password. The app itself does not need root access. To inspect the script before running it, [view install-latest.sh](https://github.com/fizzyhd/FS25-ModYard/blob/main/install-latest.sh).

The installer needs Python 3, GTK 3, and PyGObject. Steam Deck users should switch to Desktop Mode before installing, then launch **FS25 ModYard** from the application menu.

| Distribution family | Packages |
| --- | --- |
| Debian / Ubuntu | `python3 python3-gi gir1.2-gtk-3.0` |
| Fedora | `python3 python3-gobject gtk3` |
| Arch Linux | `python python-gobject gtk3` |
| openSUSE | `python3 python3-gobject-Gdk gtk3` |

The app and desktop shortcut install in your home directory; root access is not needed to install the app itself. After installation, open **FS25 ModYard** from your application menu. On first launch, check **Settings** if your FS25 Steam or Proton folders are not detected automatically.

To uninstall while keeping your settings, loadouts, collections, and backups, run `./uninstall.sh` from a downloaded bundle. Add `--purge-data` only if you also want to delete the app's data directory.

### Run from source

Install the GTK 3 and PyGObject packages for your distribution, then run:

```sh
python3 run.py
```

The Steam integration expects the Linux Steam installation of FS25 running through Proton. Other distributions can run the app if they provide Python 3.10+, PyGObject, GTK 3, and GTK/GdkPixbuf introspection data.

## Using the app

1. Open **Mod Manager** and choose a save/loadout at the top. Existing saves and FS25 Linux Mod Switcher profiles are detected automatically. Your most recently modified save is selected on first use.
2. Browse picture cards, with favourites shown in their own section. Use a card's checkbox to include it in the loadout and its star button to favourite it. If a mod exists in multiple source folders, use its version selector to choose which copy this loadout uses; the choice is saved separately for each loadout and is used when applying or exporting it. Double-click a picture for details. Search by name, filename, author, or shop category; use the **Show** menu to filter by a category such as **Telehandlers**, or sort by name, size, or modification time. Dependencies are added automatically; a linked save's map stays selected. Highlight one or more cards and choose **Delete highlighted mods and files** to permanently remove their files from scanned mod folders and clear their loadout, collection, and favourite references. Farm saves are kept.
3. To make a **collection**, highlight pictures with Ctrl-click or Shift-click, then choose **New collection** in the sidebar or **Make collection** in the mod-actions menu beside Rescan. To add highlighted mods to an existing collection, choose **Add to collection…** below the library filters and select the collection. Click a collection in the sidebar, highlight its cards, and choose **Remove from collection** to remove members. **Edit collection…**, or **Manage collections → Edit mods**, opens a searchable picture checklist: check mods to add them, uncheck mods to remove them, then choose **Save collection**. Search includes categories and preserves choices hidden by the current search. Collection membership changes keep all mod files and loadout selections. **Manage collections** also has buttons to add or remove an entire collection from the current loadout, or **Delete collection**. Deleting a collection keeps its mod files and all loadouts.
4. **Apply loadout** builds a folder of symbolic links, backs up the full linked save plus `gameSettings.xml`, sets FS25's mod-folder override using its actual Proton drive mapping, and updates that save's mod selections. Existing DLC selections and unrelated XML are preserved. Your original library and mod-switcher folders remain in place.
5. **Launch FS25** applies the loadout and asks Steam to launch FS25. A linked save passes `-autoStartSavegameId`; an independent loadout opens the game normally. Steam and Proton handle the actual game startup.
6. Use **Configure Save** to edit the supported game settings, create a manual backup, clone a farm into the next empty slot, or delete a save. Deleting a save creates a full backup first; linked loadouts are kept as independent setups. A clone retains the original farm's progress. Create fresh worlds in FS25 itself. **Loadouts** contains new/clone, rename, **preferred mod folder**, **DLC choices**, **Export ZIP**, and **Delete loadout** controls. Choosing a preferred folder adds its valid mods and dependencies to that loadout, then uses copies from that folder when available; other scanned folders are fallback, and individual card choices can override it. Use **Add folder mods to loadout** after rescanning to include ZIPs added later. Deleting a loadout keeps mod files, farm saves, and applied folders; deleted detected snapshots stay deleted after rescanning. A save's **Mods** button can create a fresh snapshot later. ZIP import is also available in the mod-actions menu.
7. Use **Backups → Restore** to return to an earlier full save and game mod-folder setting. The current state is backed up before restore.

Editing a draft loadout changes only this app's data. Game files change when you apply a loadout, save game settings, clone or delete a save, restore a backup, or replace a mod with a downloaded update. These operations are blocked while FS25 is running. New downloads and imports copy ZIPs into the selected FS25 user folder's `mods/` directory. For the default Steam/Proton profile this is `~/.local/share/Steam/steamapps/compatdata/2300320/pfx/drive_c/users/steamuser/Documents/My Games/FarmingSimulator2025/mods/`. You can also drop ZIP files onto the app to import them.

Use **Launch all mods** in Mod Manager to apply every installed mod to the selected save and launch FS25. ModYard backs up the save first; the saved loadout stays unchanged, but the linked save's active mod list is updated.

## Find and download mods

Open **Find mods** in the sidebar. Enter a search and choose **Search ModHub**, or choose **Latest mods**. Results come from the official FS25 ModHub and include mod pictures and creator names. Use **Previous page** and **Next page** to browse more results.

Search results also show their ModHub category. Choose a category from **All categories** to browse that type directly. **Gameplay & scripts** uses ModHub's official **Gameplay** category. Searching a category name such as `telehandler` uses ModHub's **Telehandlers** category, so model names do not need to include the search word. A mixed pack listed under several categories retains its primary category and the selected category. **Latest mods** resets the category filter.

Choose **Details and download** to view the version, filename, size, picture, and official page. **Download to library** downloads and checks the mod ZIP, then imports it into the selected FS25 mod folder. Add it to a loadout when ready. If a dependency is missing, ModYard can find an exact filename match on ModHub or use a previously trusted or built-in GitHub release source. Existing mods are kept; the details dialog offers **Check installed update** for those files.

## Categories in your library

Each installed mod card shows its shop category. Packs containing several types show the first category and a `+N` badge; hover the badge or open the details to see all categories. Categories are read from the store items registered by the mod, including vehicle/tool XML and accessible inherited base-game XML. Unregistered example files are excluded. Maps and scripts retain their broad types; equipment without readable category metadata keeps the **Equipment** label.

Type `telehandler` in Mod Manager to find matching categories as well as names and authors. The **Show → Telehandlers** filter selects the telehandler vehicle category specifically; **Telehandler tools** is available separately. Mixed packs can appear under every category they contain. Category metadata is cached and refreshed on rescan when its source changes. Existing metadata caches upgrade automatically on the first scan after updating.

## Dedicated-server exports

Open **Loadouts → Export ZIP** and choose an output file. The bundle contains the actual selected mod ZIPs, their dependencies, and any linked map mod. Unpacked mods are packed with `modDesc.xml` at the ZIP root. Missing dependencies block an incomplete export, and an unsuccessful export keeps any previous output file intact.

Extract the bundle and upload the individual ZIPs in **mods/** to the dedicated server's mods folder or mod-upload page. Enable them in the server's save configuration. `loadout.json` lists the mod names, versions, and selected DLCs. The included `README.txt` gives the upload steps. Players need the same mod versions as the server.

The export contains mods rather than the farm's savegame. Licensed DLC archives are not bundled; install and activate the selected DLC separately on the server. Exporting leaves the local library and loadout intact.

## DLC selections

Choose **Loadouts → DLC choices** to tick the DLCs for a setup and optionally link it to a save. Existing loadouts inherit their linked save's DLC selections until you explicitly save choices. The hub reads DLC names and versions from installed files, save metadata, and FS25's log. Start FS25 once and rescan if a newly installed DLC's metadata is unavailable.

**Apply loadout** writes the chosen DLC entries to the linked save after making a full backup. A DLC map stays selected for a save using that map. Deselecting DLC can remove its objects when the game is next saved. Independent loadouts retain their choices for export checklists; select their DLCs in-game or link them to a save to apply the choices. Steam and FS25 continue to manage ownership and activation.

## ModHub and GitHub updates

Open **Mod updates** and choose **Quick check current loadout** or **Quick check main mod folder**. Update checks and replacements are limited to ZIPs in FS25's main `mods/` folder; other scanned folders remain available for loadouts and version choices. Checks compare installed versions with the official [FS25 ModHub](https://www.farming-simulator.com/mods.php?title=fs2025&lang=en), then look for GitHub releases for unmatched mods. Automatic checks run in the background at startup, at most once every 24 hours by default; turn them off in **Preferences**. The first check can take a few minutes; results are cached locally. **Download all available updates** installs verified updates in sequence and keeps a backup of each replaced ZIP. Use **All checked mods** to inspect versions, sources, failed checks, and unmatched files. An exact release ZIP filename is required for a match. **Link update source** accepts an official ModHub page, a public GitHub repository URL, or `owner/repository`.

The [main AutoDrive repository](https://github.com/Stephan-S/FS25_AutoDrive) and the four companion repositories linked from its README are recognised automatically, including available right-hand-drive variants. Other GitHub sources can be discovered from links in an installed mod's descriptor or a repository-name search. GitHub matches check the mod's author and title as well as the asset filename. Multiple plausible repositories are shown for review rather than chosen automatically. A repository must publish a packaged ZIP for the installed filename; GitHub's generated source-code archives are not installed as mods. Drafts and prereleases are excluded.

For small GitHub release assets, the check reads the version from the packaged `modDesc.xml`, since release tags and source files can differ from the actual package. Verified metadata is reused when the release asset is unchanged. Larger assets use the release tag and source descriptor for the initial check and are verified again after downloading. GitHub's SHA-256 asset checksum is checked when provided. GitHub may pause public requests at its rate limit; results already checked are retained and you can retry later. No GitHub login or token is needed.

For a matched newer ZIP, **Download and replace** fetches the current source release, checks the ZIP and its `modDesc.xml` version, backs up the old ZIP, and replaces the installed file atomically. The confirmation names the source, including the GitHub repository when applicable. It checks that FS25 is closed and that the source file has not changed during preparation. Missing new dependencies block replacement; installed dependencies are added to affected loadouts. Every loadout sharing that mod file uses the updated version. Unpacked mods can be downloaded through their source page and imported as ZIPs.

**Open mod backups** opens the previous ZIPs. Each backup folder contains `update.json` and a `README.txt` with the original file path and restore steps. Download failures keep the installed file. Mods without a matching ModHub page or usable GitHub release, and unrecognized version formats, require review rather than an automatic replacement. The per-mod download limit is 8 GB.

## Current scope

- A responsive card grid, separate favourites section, coloured library statistics, sidebar collection filters, and a green/charcoal theme inspired by the original FarmSim Hub layout. The header uses locally installed FS25 Steam artwork when available.
- Mod pictures, shop-category labels and filters, metadata search, favourites, sorting, dependency resolution, and missing-mod checks. Artwork is read from ZIPs or unpacked mod folders, with support for PNG references that resolve to DDS textures. Pictures are cached locally; missing or unreadable artwork shows a generic icon. DDS display uses the system's GdkPixbuf DDS image loader.
- Per-save loadouts, independent loadouts, snapshots imported from an existing Linux Mod Switcher setup, deletion, and portable mod ZIP exports.
- Named collections with automatic colour assignment, editable membership, and deletion.
- Save name, economy difficulty, days per month, time speed, autosave, traffic, crop destruction, plowing, stones, weeds, lime, snow, engine start, braking, trailer limit, and worker purchasing settings. Unsupported and mod-specific settings are preserved.
- DLC choices per loadout, with save linking and protection for DLC maps. Paid content ownership and activation are handled by FS25.
- Official ModHub search, picture results, details, and validated downloads of new mods.
- ModHub version checks and GitHub fallback, AutoDrive source mappings, direct ZIP updates, integrity validation, and backups of replaced mod files.
- Full-save ZIP backups, restore, and save cloning.
- User-folder detection across ordinary and Flatpak Steam installations and Steam library paths. Preferences lists detected and added mod source folders; remove a source there to stop scanning it without deleting its files, and add it again to re-enable it. Mods available from another source remain available; otherwise selected loadout mods are marked missing until the source is restored or the mods are deselected.

The app does not generate new world/terrain data, upload to multiplayer servers, or reproduce every feature of the original Windows app. Searches and downloads run when requested; update checks run at startup when enabled and contact official Farming Simulator/GIANTS sites or GitHub using mod search terms, filenames, page IDs, and repository names. There is no telemetry or app self-updater. Actual in-game loading and dedicated-server uploads must be verified with your own Steam/Proton setup and server; automated checks use temporary fixture saves and mock the Steam launch request.

Older saves can reference mods no longer in your library. Those references appear as **missing**. The hub refuses to apply an incomplete loadout until you import the missing mods or intentionally deselect the missing entries. Removing a mod may remove its objects when the game is next saved; a backup is created before applying changes.

## Data and recovery

Default location: `~/.local/share/fs25-modyard/` (respects `XDG_DATA_HOME`). When upgrading from the earlier FarmSim Hub Linux version, the installer moves the existing data here and updates saved loadout paths. On the first ModYard launch with FS25 closed, the game mod-folder override is updated to the new location; a copy of the original `gameSettings.xml` is kept in `backups/` first.

- `app/`: installed application code
- `config.json`: favourites, collections, and draft loadouts
- `metadata-cache.json`: mod metadata cache
- `thumbnails/`: cached mod pictures, refreshed when the source artwork changes
- `modhub-cache.json`: ModHub/GitHub matches, versions, and check results
- `modhub-browse-pictures/`: cached pictures for official ModHub search results
- `modhub-browse-details.json`: cached category, filename, and version metadata for ModHub search results
- `mod-backups/`: original ZIPs kept before replacing mods, with restore instructions
- `<FS25 user folder>/mods/`: destination for new imports and ModHub downloads
- `imports/`: legacy folder from earlier versions; it is still scanned for existing mods
- `applied/`: applied loadout directories containing symbolic links
- `backups/`: full-save backups plus the previous game settings and a manifest

Keep this data directory while FS25 uses an applied loadout or while you need its backups. To return to your previous mod-folder configuration, restore the backup from before applying the loadout. The app keeps previous applied folders so older backup references continue to work.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 run.py --diagnose
```

Core tests cover applying a loadout, backup and full restore, preserving or explicitly changing DLC selections, protected DLC maps, deletion, dependency-complete exports, rollback after a write failure, interrupted or corrupt updates, missing dependencies, game-running guards, safe import, save cloning, XML validation, and launch arguments. All mutation tests use temporary fixture saves; they do not change real game data.

`python3 -m tests.check_interface` is an additional local integration check for a populated FS25 installation. It uses temporary app data and offscreen GTK windows to exercise card selection, favourites, filters, sorting, collection membership editing and cancellation, collection/loadout deletion, the DLC checkbox chooser, and navigation. It renders every page and checks the mod library at 2048, 1360, and 1180 pixels wide. `design-qa.md` records the original visual comparison against the supplied reference screenshot.

`python3 -m tests.check_modhub_live` is an optional network check. It checks, downloads, validates, backs up, replaces, and exports one small official mod inside a disposable test library. It never replaces a mod in your real library.

`python3 -m tests.check_github_live` checks the main AutoDrive release and four companion releases, then verifies a download, checksum, backup, and replacement entirely inside a temporary fixture library.

`python3 -m tests.check_categories_live` verifies category searches against the installed library and the official ModHub using temporary app data.

Licensed under MIT; see LICENSE.
