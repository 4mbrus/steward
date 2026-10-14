#!/bin/sh
# Build app-commander_<version>_all.deb into dist/. Needs only dpkg-deb and fakeroot.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ID=io.github.appcommander.AppCommander
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$DIR/appcommander/main.py")
DATE=$(date +%Y-%m-%d)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# application code (no caches or tests)
install -d "$STAGE/usr/share/app-commander/appcommander"
install -m 644 "$DIR"/appcommander/*.py "$DIR"/appcommander/style.css "$STAGE/usr/share/app-commander/appcommander/"
install -D -m 755 "$DIR/packaging/app-commander" "$STAGE/usr/bin/app-commander"

# desktop integration
install -D -m 644 "$DIR/packaging/$APP_ID.desktop" "$STAGE/usr/share/applications/$APP_ID.desktop"
install -D -m 644 "$DIR/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -d "$STAGE/usr/share/metainfo"
sed -e "s/@VERSION@/$VERSION/" -e "s/@DATE@/$DATE/" "$DIR/packaging/$APP_ID.metainfo.xml" \
    > "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"
chmod 644 "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"

# docs
install -D -m 644 "$DIR/packaging/copyright" "$STAGE/usr/share/doc/app-commander/copyright"
printf 'app-commander (%s) unstable; urgency=medium\n\n  * Release %s.\n\n -- Ambrus Fodor <ambrusfodor.online@gmail.com>  %s\n' \
    "$VERSION" "$VERSION" "$(date -R)" | gzip -9n > "$STAGE/usr/share/doc/app-commander/changelog.gz"
chmod 644 "$STAGE/usr/share/doc/app-commander/changelog.gz"

# control file
install -d "$STAGE/DEBIAN"
SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$DIR/packaging/control.in" > "$STAGE/DEBIAN/control"
find "$STAGE" -type d -exec chmod 755 {} +

mkdir -p "$DIR/dist"
OUT="$DIR/dist/app-commander_${VERSION}_all.deb"
fakeroot dpkg-deb --build -Zxz "$STAGE" "$OUT" >/dev/null
echo "$OUT"
