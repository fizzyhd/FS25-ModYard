#!/usr/bin/env bash
set -euo pipefail

purge_data=0
case "${1:-}" in
    "") ;;
    --purge-data) purge_data=1 ;;
    --help|-h)
        echo "Usage: ./uninstall.sh [--purge-data]"
        echo "Removes the app and launchers. User settings, mods, loadouts, and backups are kept unless --purge-data is given."
        exit 0
        ;;
    *) echo "Usage: ./uninstall.sh [--purge-data]" >&2; exit 2 ;;
esac

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf -- "$data_home/fs25-modyard/app" "$data_home/farmsim-hub-linux/app"
rm -f -- "$HOME/.local/bin/fs25-modyard" "$HOME/.local/bin/farmsim-hub-linux"
rm -f -- "$data_home/applications/org.community.FS25ModYard.desktop" "$data_home/applications/org.community.FarmSimHubLinux.desktop"
rm -f -- "$data_home/icons/hicolor/scalable/apps/fs25-modyard.svg" "$data_home/icons/hicolor/scalable/apps/farmsim-hub-linux.svg"
rm -f -- "$HOME/Desktop/FS25 ModYard.desktop" "$HOME/Desktop/FarmSim Hub Linux.desktop"

if [[ "$purge_data" -eq 1 ]]; then
    rm -rf -- "$data_home/fs25-modyard" "$data_home/farmsim-hub-linux"
    echo "Removed FS25 ModYard and its user data."
else
    echo "Removed FS25 ModYard. User data and backups remain in $data_home/fs25-modyard."
fi
