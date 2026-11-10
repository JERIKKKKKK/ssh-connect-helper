# ssh-connect-helper
SSH Connect manager for windows/linux
**English** | [Русский]

# SSH Connect

**A program that makes working over SSH simpler.** A graphical client for Windows and Linux: connect to a PC and immediately get a terminal, file manager, ready-made commands and account management, with no command line and no hand-typed scripts.

## Features
- **Terminal** in browser-like tabs, with system info (motherboard, CPU, RAM, disks, Linux version, SMB mounting tool version).
- **Connection check** before opening: address, port, login and password, with a clear message about what is wrong.
- **Quick commands**: restart services, time sync, change IP and hostname, install .deb/.sh, auto-enable printers and more. You can add your own.
- **Files (SFTP)**: two panes, upload and download folders, drag to the Windows desktop, view and edit text files.
- **Accounts**: list, create, delete, groups, reset inactive sessions, unmount network disks.
- **Bulk mode**: an address range, one command for many PCs, SMB check with an Excel report.
- **Reboot** with automatic reconnect as soon as the PC answers again.
- **Update from GitHub** with one button, and a **help** window for every function (F1).

Passwords are never saved.

## Installation
Download the file for your system from [Releases](https://github.com/JERIKKKKKK/ssh-connect-helper/releases):
- Windows: `SSH_Connect_Setup_<version>.exe` (installer) or `SSH_Connect.exe` (portable)
- Linux (Debian, Astra): `ssh-connect_<version>_all.deb`
- Linux (RPM): `ssh-connect-<version>-1.noarch.rpm`

## Build
- Windows: `build_exe.bat`, `build_installer.bat` (needs Inno Setup 6)
- Linux: `./build_deb.sh`, `./build_rpm.sh`

## Publishing an update
The repository must be **public**: the program reads releases without a token.
1. Bump `APP_VERSION` in `ssh_connect.py` and commit.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` (the tag must equal `APP_VERSION`).
3. The `release` workflow builds the exe, installer, deb, rpm and `SHA256SUMS.txt` and attaches them to the release. Do not rename the files: the program picks the package by name.

[English] | **Русский**

# SSH Connect

**Программа для упрощения работы по SSH.** Графический клиент для Windows и Linux: подключился к ПК и сразу получил терминал, файлы, готовые команды и управление учётными записями — без командной строки и ручного набора скриптов.

## Что умеет
- **Терминал** во вкладках, как в браузере; сведения о системе (плата, процессор, память, диски, версия Linux, версия SMB-монтирования).
- **Проверка подключения** перед открытием: адрес, порт, логин, пароль — с понятным сообщением, что не так.
- **Быстрые команды**: перезапуск служб, синхронизация времени, смена IP и имени ПК, установка .deb/.sh, автовключение принтеров и другие. Свои команды можно добавлять.
- **Файлы (SFTP)**: два окна, загрузка и скачивание папок, перетаскивание на рабочий стол Windows, просмотр и правка текстовых файлов.
- **Учётные записи**: список, создание, удаление, группы, сброс неактивных сессий, отключение сетевых дисков.
- **Массовый режим**: диапазон адресов, одна команда на много ПК, проверка SMB с отчётом в Excel.
- **Перезагрузка** с автоматическим переподключением, когда ПК снова отвечает.
- **Обновление через GitHub** одной кнопкой, **справка** по всем функциям (F1).

Пароли нигде не сохраняются.

## Установка
Скачайте файл для своей системы со страницы [Releases](https://github.com/JERIKKKKKK/ssh-connect-helper/releases):
- Windows: `SSH_Connect_Setup_<версия>.exe` (установщик) или `SSH_Connect.exe` (без установки)
- Linux (Debian, Astra): `ssh-connect_<версия>_all.deb`
- Linux (RPM): `ssh-connect-<версия>-1.noarch.rpm`

## Сборка
- Windows: `build_exe.bat`, `build_installer.bat` (нужен Inno Setup 6)
- Linux: `./build_deb.sh`, `./build_rpm.sh`

## Выпуск обновления
Репозиторий должен быть **публичным**: программа читает релизы без токена.
1. Поднять `APP_VERSION` в `ssh_connect.py`, закоммитить.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` (тег = `APP_VERSION`).
3. Workflow `release` соберёт exe, установщик, deb, rpm и `SHA256SUMS.txt` и приложит к релизу. Имена файлов менять нельзя — по ним программа выбирает пакет.
