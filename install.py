#!/usr/bin/env python3
"""Install in the current user's home. No root access, downloads, or package changes."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from farmsim_hub.core import migrate_data_directory


def main():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk
    except (ImportError, ValueError) as exc:
        print("Python 3 with PyGObject and GTK3 is required. See README.md for your distribution.", file=sys.stderr)
        return 1
    source = Path(__file__).resolve().parent
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    destination = migrate_data_directory(data) / "app"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "farmsim_hub", destination / "farmsim_hub", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ["run.py", "README.md", "LICENSE", "icon.svg"]:
        shutil.copy2(source / name, destination / name)
    binary = home / ".local/bin/fs25-modyard"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nunset LD_PRELOAD\nexec " + shlex.quote(sys.executable) + " " +
                      shlex.quote(str(destination / "run.py")) + ' "$@"\n')
    binary.chmod(0o755)
    icon = data / "icons/hicolor/scalable/apps/fs25-modyard.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "icon.svg", icon)

    def desktop_quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'

    entry = ("[Desktop Entry]\nType=Application\nVersion=1.0\nName=FS25 ModYard\n"
             "Comment=Community mod manager for Farming Simulator 25 on Linux\n"
             f"Exec={desktop_quote(binary)}\nIcon={icon}\nTerminal=false\n"
             "Categories=Game;\nKeywords=FS25;Farming;Simulator;Mods;Loadouts;\n"
             "StartupNotify=true\nStartupWMClass=fs25-modyard\n")
    launcher = data / "applications/org.community.FS25ModYard.desktop"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(entry)
    launcher.chmod(0o755)
    desktop = home / "Desktop"
    if desktop.is_dir():
        shortcut = desktop / "FS25 ModYard.desktop"
        shortcut.write_text(entry)
        shortcut.chmod(0o755)
    update = shutil.which("update-desktop-database")
    if update:
        subprocess.run([update, str(launcher.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Remove the previous launchers after installing the renamed app.
    (home / ".local/bin/farmsim-hub-linux").unlink(missing_ok=True)
    (data / "applications/org.community.FarmSimHubLinux.desktop").unlink(missing_ok=True)
    (data / "icons/hicolor/scalable/apps/farmsim-hub-linux.svg").unlink(missing_ok=True)
    (desktop / "FarmSim Hub Linux.desktop").unlink(missing_ok=True)
    print(f"Installed: {binary}\nDesktop app: FS25 ModYard\nApp files: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
