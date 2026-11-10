#!/bin/bash
# Сборка rpm-пакета: ./build_rpm.sh  ->  dist/ssh-connect-X.Y.Z-1.noarch.rpm
set -e
cd "$(dirname "$0")"
SRC=$(pwd)
VER=$(python3 get_version.py)
TOP=$SRC/build/rpm
rm -rf "$TOP"; mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
cat > "$TOP/SPECS/ssh-connect.spec" <<S
Name: ssh-connect
Version: $VER
Release: 1
Summary: SSH client with terminal, file manager and bulk mode
License: Proprietary
BuildArch: noarch
Requires: python3 >= 3.7, python3-tkinter, python3-paramiko, iputils
AutoReqProv: no
%global __os_install_post %{nil}
%global debug_package %{nil}

%description
GUI SSH client (CustomTkinter + paramiko): terminal, file manager,
quick commands, accounts and bulk operations.

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/ssh-connect %{buildroot}/usr/bin %{buildroot}/usr/share/applications %{buildroot}/usr/share/pixmaps
cp $SRC/ssh_connect.py %{buildroot}/opt/ssh-connect/
cp -r $SRC/vendor_linux %{buildroot}/opt/ssh-connect/vendor
cp -r $SRC/assets %{buildroot}/opt/ssh-connect/assets
find %{buildroot} -name __pycache__ -prune -exec rm -rf {} +
cp $SRC/assets/ssh_connect.png %{buildroot}/usr/share/pixmaps/ssh-connect.png
printf '#!/bin/sh\nexec python3 /opt/ssh-connect/ssh_connect.py "\$@"\n' > %{buildroot}/usr/bin/ssh-connect
chmod 755 %{buildroot}/usr/bin/ssh-connect
cat > %{buildroot}/usr/share/applications/ssh-connect.desktop <<D
[Desktop Entry]
Type=Application
Name=SSH Connect
Comment=SSH client: terminal, files, quick commands
Exec=/usr/bin/ssh-connect
Icon=ssh-connect
Terminal=false
Categories=Network;System;
D

%files
/opt/ssh-connect
/usr/bin/ssh-connect
/usr/share/applications/ssh-connect.desktop
/usr/share/pixmaps/ssh-connect.png

%postun
if [ "\$1" = "0" ]; then rm -rf /opt/ssh-connect; fi
S
rpmbuild --define "_topdir $TOP" --define "_build_id_links none" -bb "$TOP/SPECS/ssh-connect.spec"
mkdir -p dist
cp "$TOP"/RPMS/noarch/*.rpm dist/
echo "Готово: dist/ssh-connect-${VER}-1.noarch.rpm"
