#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="prompt"

usage() {
    cat <<'EOF'
FS25 ModYard installer

Usage: ./install.sh [--install-deps | --no-deps]

By default, missing Python/GTK system packages are offered for installation
with the detected distro package manager. --no-deps only installs the app.
EOF
}

case "${1:-}" in
    "") ;;
    --install-deps) mode="install" ;;
    --no-deps) mode="skip" ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

python_cmd="$(command -v python3 || true)"
if [[ -z "$python_cmd" ]]; then
    echo "Python 3 was not found. Install Python 3.10 or newer, then run this installer again." >&2
    exit 1
fi

if ! "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "FS25 ModYard requires Python 3.10 or newer. Found: $($python_cmd --version 2>&1)" >&2
    exit 1
fi

runtime_ok() {
    "$python_cmd" - <<'PY'
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk
PY
}

if ! runtime_ok >/dev/null 2>&1; then
    if [[ "$mode" == "skip" ]]; then
        echo "GTK 3 and PyGObject are missing. Install your distro's Python GTK 3 packages, then run ./install.sh again." >&2
        exit 1
    fi

    distro_id=""
    distro_like=""
    if [[ -r /etc/os-release ]]; then
        # os-release is a shell-compatible file supplied by the operating system.
        . /etc/os-release
        distro_id="${ID:-}"
        distro_like="${ID_LIKE:-}"
    fi

    package_manager=""
    packages=()
    if command -v apt-get >/dev/null 2>&1 && [[ "$distro_id $distro_like" =~ (debian|ubuntu|linuxmint|pop) ]]; then
        package_manager="apt-get"
        packages=(python3 python3-gi gir1.2-gtk-3.0)
    elif command -v dnf >/dev/null 2>&1 && [[ "$distro_id $distro_like" =~ (fedora|rhel|centos) ]]; then
        package_manager="dnf"
        packages=(python3 python3-gobject gtk3)
    elif command -v pacman >/dev/null 2>&1 && [[ "$distro_id $distro_like" =~ (arch|manjaro|endeavouros) ]]; then
        package_manager="pacman"
        packages=(python python-gobject gtk3)
    elif command -v zypper >/dev/null 2>&1 && [[ "$distro_id $distro_like" =~ (opensuse|suse) ]]; then
        package_manager="zypper"
        packages=(python3 python3-gobject-Gdk gtk3)
    fi

    if [[ -z "$package_manager" ]]; then
        echo "Python GTK 3 packages are missing, and this distribution was not recognized." >&2
        echo "Install Python 3.10+, PyGObject, GTK 3, and their GObject introspection data, then run ./install.sh --no-deps." >&2
        exit 1
    fi

    echo "FS25 ModYard needs these system packages: ${packages[*]}"
    case "$mode" in
        install) answer="y" ;;
        *)
            if [[ ! -t 0 ]]; then
                echo "Run ./install.sh --install-deps to install them, or use --no-deps after installing them yourself." >&2
                exit 1
            fi
            read -r -p "Install them with $package_manager? [y/N] " answer
            ;;
    esac
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        echo "No changes made. Install the packages above, then run ./install.sh --no-deps." >&2
        exit 1
    fi

    if [[ "$EUID" -eq 0 ]]; then
        sudo_cmd=()
    else
        if ! command -v sudo >/dev/null 2>&1; then
            echo "sudo is required to install system packages. Install them yourself, then run ./install.sh --no-deps." >&2
            exit 1
        fi
        sudo_cmd=(sudo)
    fi
    case "$package_manager" in
        apt-get) "${sudo_cmd[@]}" apt-get update; "${sudo_cmd[@]}" apt-get install -y "${packages[@]}" ;;
        dnf) "${sudo_cmd[@]}" dnf install -y "${packages[@]}" ;;
        pacman) "${sudo_cmd[@]}" pacman -S --needed --noconfirm "${packages[@]}" ;;
        zypper) "${sudo_cmd[@]}" zypper --non-interactive install "${packages[@]}" ;;
    esac
fi

if ! runtime_ok; then
    echo "GTK 3 and PyGObject are still unavailable to $python_cmd. Check the distro package installation and Python paths." >&2
    exit 1
fi

exec "$python_cmd" "$source_dir/install.py"
