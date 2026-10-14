#!/bin/sh
# Adds App Commander to your app menu (user-level, no root). Run ./install.sh --uninstall to undo.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ID=io.github.appcommander.AppCommander
BIN="$HOME/.local/bin/app-commander"
ENTRY="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_ID.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/$APP_ID.svg"

if [ "$1" = "--uninstall" ]; then
    rm -f "$BIN" "$ENTRY" "$ICON"
    echo "Removed $BIN, $ENTRY and $ICON"
    exit 0
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$ENTRY")" "$(dirname "$ICON")"
cp "$DIR/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ICON"
ln -sf "$DIR/app-commander" "$BIN"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=App Commander
Comment=Manage installed applications, their desktop entries and updates
Exec=$DIR/app-commander
Icon=$APP_ID
Terminal=false
Categories=GTK;System;PackageManager;
Keywords=uninstall;update;desktop entry;flatpak;appimage;apt;
StartupNotify=true
DESKTOP
echo "Installed: $ENTRY"
