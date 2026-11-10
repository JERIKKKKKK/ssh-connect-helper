#!/bin/bash
# Сборка deb-пакета: ./build_deb.sh  ->  dist/ssh-connect_X.Y.Z_all.deb
set -e
cd "$(dirname "$0")"
VER=$(python3 get_version.py)
PKG=ssh-connect
ROOT=build/deb/${PKG}_${VER}
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/opt/ssh-connect" "$ROOT/usr/bin" "$ROOT/usr/share/applications" "$ROOT/usr/share/pixmaps"
cp ssh_connect.py "$ROOT/opt/ssh-connect/"
cp -r vendor_linux "$ROOT/opt/ssh-connect/vendor"
cp -r assets "$ROOT/opt/ssh-connect/assets"
find "$ROOT" -name __pycache__ -prune -exec rm -rf {} +
cp assets/ssh_connect.png "$ROOT/usr/share/pixmaps/ssh-connect.png"
cat > "$ROOT/usr/bin/ssh-connect" <<'L'
#!/bin/sh
exec python3 /opt/ssh-connect/ssh_connect.py "$@"
L
chmod 755 "$ROOT/usr/bin/ssh-connect"
cat > "$ROOT/usr/share/applications/ssh-connect.desktop" <<'D'
[Desktop Entry]
Type=Application
Name=SSH Connect
Comment=SSH-клиент: терминал, файлы, быстрые команды
Exec=/usr/bin/ssh-connect
Icon=ssh-connect
Terminal=false
Categories=Network;System;
D
SIZE=$(du -sk "$ROOT/opt" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<C
Package: $PKG
Version: $VER
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.7), python3-tk, python3-paramiko, iputils-ping
Installed-Size: $SIZE
Maintainer: SSH Connect
Description: SSH Connect - SSH client with terminal, file manager and bulk mode
 GUI SSH client (CustomTkinter + paramiko): terminal, file manager,
 quick commands, accounts and bulk operations.
C
cat > "$ROOT/DEBIAN/postinst" <<'P'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
python3 -m compileall -q /opt/ssh-connect >/dev/null 2>&1 || true
exit 0
P
cat > "$ROOT/DEBIAN/postrm" <<'P'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then rm -rf /opt/ssh-connect; fi
exit 0
P
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"
find "$ROOT" -type d -exec chmod 755 {} +
find "$ROOT/opt" "$ROOT/usr/share" -type f -exec chmod 644 {} +
mkdir -p dist
dpkg-deb --build --root-owner-group "$ROOT" "dist/${PKG}_${VER}_all.deb"
echo "Готово: dist/${PKG}_${VER}_all.deb"
