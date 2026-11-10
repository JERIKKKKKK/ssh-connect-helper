# -*- coding: utf-8 -*-
"""
SSH Connect — современный SSH-клиент для Windows/Linux со встроенным терминалом.

  * IP-адрес -> логин/пароль -> терминал
  * Кнопки «Перезагрузить ПК» / «Выключить ПК»
  * «Быстрые команды»: список готовых действий (перезапуск CUPS и т.д.)
    + свои команды, которые сохраняются в конфиге
  * После перезагрузки скрыто пингуем ПК и подключаемся автоматически

Зависимости: customtkinter, paramiko, pyte. Сборка .exe: build_exe.bat
Пароли нигде не сохраняются.
"""

from collections import Counter
import gc
import ipaddress
import json
import os
import queue
import posixpath
import re
import secrets
import shutil
import shlex
import socket
import sys
import stat as pystat
import subprocess
import tempfile
import atexit
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import threading
import hashlib
import time
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog
from tkinter import ttk

# В deb-пакете библиотеки лежат в ./vendor рядом со скриптом (customtkinter, pyte, tkinterdnd2...).
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)
if sys.platform.startswith("linux"):
    try:   # customtkinter копирует свой шрифт в ~/.fonts; без этой папки значки рисуются криво
        os.makedirs(os.path.expanduser("~/.fonts"), exist_ok=True)
    except OSError:
        pass

import customtkinter as ctk  # noqa: E402
import paramiko  # noqa: E402
import pyte  # noqa: E402

APP_NAME = "SSH Connect"
APP_VERSION = "1.8.2"   # то же число попадает в имя установщика (build_installer.bat)

# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------
BG = "#0d1117"
SURFACE = "#151b23"
SURFACE2 = "#1d2530"
SURFACE_HOVER = "#27313f"
BORDER = "#2a3441"
TEXT = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#3b82f6"
ACCENT_H = "#2563eb"
WARN = "#f59e0b"
WARN_H = "#d97706"
DANGER = "#ef4444"
DANGER_H = "#dc2626"
OK = "#22c55e"
TERM_BG = "#0b0f14"
TERM_FG = "#d6deeb"

UI_FONT = "Segoe UI"


def ui_font(size=13, weight="normal"):
    return ctk.CTkFont(family=UI_FONT, size=size, weight=weight)


def pick_mono():
    fams = set(tkfont.families())
    for name in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"):
        if name in fams:
            return name
    return "Courier"


# --------------------------------------------------------------------------
# Быстрые команды по умолчанию (Astra Linux / Debian, systemd)
#   sudo   — выполнять через sudo (для root sudo не нужен)
#   output — показать вывод команды в окне
#   confirm— спросить подтверждение
# --------------------------------------------------------------------------
DEFAULT_COMMANDS = [
    {"group": "Учётные записи", "title": "Управление учётными записями…", "action": "accounts",
     "cmd": "список, группы, создание (в т.ч. из txt), удаление, отключение дисков и неактивных сессий"},
    {"group": "Система", "title": "Сменить имя компьютера…", "action": "set_hostname",
     "cmd": "откроется форма: новое имя (hostname)"},
    {"group": "Система", "title": "Исправить запуск браузера (блокировки)…", "action": "browser_fix",
     "cmd": "удаляет устаревшие блокировки Яндекс Браузера/Chromium после смены имени ПК"},
    {"group": "Система", "title": "Синхронизировать время", "action": "time_sync",
     "cmd": "по NTP с DNS-сервером этого ПК (DNS = сервер времени)"},
    {"group": "Печать", "title": "Автовключение принтеров", "action": "printers_auto",
     "cmd": "проверяет, установлено ли; если нет — предлагает установить"},
    {"group": "Печать", "title": "Перезапустить CUPS", "cmd": "systemctl restart cups", "sudo": True},
    {"group": "Печать", "title": "Статус CUPS", "cmd": "systemctl status cups --no-pager -l"},
    {"group": "Печать", "title": "Список принтеров", "cmd": "lpstat -p -d"},
    {"group": "Печать", "title": "Очистить очередь печати", "cmd": "cancel -a", "sudo": True, "confirm": True},
    {"group": "Сеть", "title": "Сменить IP на статический…", "action": "set_static_ip",
     "cmd": "откроется окно настройки адреса"},
    {"group": "Сеть", "title": "Перезапустить сеть", "cmd": "systemctl restart NetworkManager", "sudo": True, "confirm": True},
    {"group": "Сеть", "title": "IP-адреса", "cmd": "ip -br address"},
    {"group": "Сеть", "title": "Открытые порты", "cmd": "ss -tulpn", "sudo": True},
    {"group": "Система", "title": "Установить SH/DEB…", "action": "install_file",
     "cmd": "откроется выбор файла .sh или .deb на этом ПК"},
    {"group": "Система", "title": "Место на дисках", "cmd": "df -h"},
    {"group": "Система", "title": "Память", "cmd": "free -h"},
    {"group": "Система", "title": "Аптайм и нагрузка", "cmd": "uptime"},
    {"group": "Система", "title": "Время и NTP (статус)", "cmd": "timedatectl status"},
    {"group": "Система", "title": "Кто сейчас работает", "cmd": "who"},
    {"group": "Система", "title": "Обновить списки пакетов", "cmd": "apt-get update", "sudo": True},
    {"group": "Система", "title": "Ошибки журнала (последние 50)", "cmd": "journalctl -p 3 -n 50 --no-pager", "sudo": True},
]
CUSTOM_GROUP = "Мои команды"

# --------------------------------------------------------------------------
# Цвета терминала (эмуляция через pyte)
# --------------------------------------------------------------------------
PALETTE = {
    "black": "#1c2128", "red": "#f0616d", "green": "#4cc38a",
    "brown": "#e6c34a", "blue": "#5aa2ff", "magenta": "#c58af9",
    "cyan": "#4cc9d8", "white": "#d6deeb",
    "brightblack": "#6b7686", "brightred": "#ff7b86",
    "brightgreen": "#6ee7a8", "brightbrown": "#f5da6b",
    "brightblue": "#79b8ff", "brightmagenta": "#d8a6ff",
    "brightcyan": "#7ee0ec", "brightwhite": "#ffffff",
}
BASIC = {"black", "red", "green", "brown", "blue", "magenta", "cyan", "white"}

KEYMAP = {
    "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
    "Home": "\x1b[H", "End": "\x1b[F", "Insert": "\x1b[2~",
    "Delete": "\x1b[3~", "Prior": "\x1b[5~", "Next": "\x1b[6~",
    "BackSpace": "\x7f", "Return": "\r", "KP_Enter": "\r", "Tab": "\t",
    "Escape": "\x1b", "ISO_Left_Tab": "\x1b[Z",
}
MODIFIER_KEYS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Caps_Lock", "Num_Lock", "Win_L", "Win_R", "Meta_L", "Meta_R",
}


def to_color(value, default, bold=False):
    if value == "default":
        return default
    if bold and value in BASIC:
        value = "bright" + value
    if value in PALETTE:
        return PALETTE[value]
    if len(value) == 6:
        try:
            int(value, 16)
            return "#" + value
        except ValueError:
            pass
    return default


# --------------------------------------------------------------------------
# Конфиг (%APPDATA%\SSHConnect\config.json). Пароли НЕ сохраняются.
# --------------------------------------------------------------------------
class Config:
    def __init__(self):
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        self.dir = os.path.join(base, "SSHConnect")
        self.path = os.path.join(self.dir, "config.json")
        self.data = {"recent": [], "users": {}, "custom_commands": [], "auto_reconnect": True, "auto_update": True}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:  # -sig: переживает BOM из Блокнота
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    # удобные свойства
    @property
    def recent(self):
        return list(self.data.get("recent", []))

    def remember(self, host_str, user):
        rec = [h for h in self.data.get("recent", []) if h != host_str]
        rec.insert(0, host_str)
        self.data["recent"] = rec[:10]
        self.data.setdefault("users", {})[host_str] = user
        self.save()

    def last_user(self, host_str):
        return self.data.get("users", {}).get(host_str, "")

    @property
    def recent_ranges(self):
        return list(self.data.get("recent_ranges", []))

    def remember_range(self, raw, user):
        rec = [h for h in self.data.get("recent_ranges", []) if h != raw]
        rec.insert(0, raw)
        self.data["recent_ranges"] = rec[:10]
        self.data.setdefault("users", {})[raw] = user
        self.save()

    @property
    def custom_commands(self):
        return list(self.data.get("custom_commands", []))

    def add_custom(self, cmd):
        self.data.setdefault("custom_commands", []).append(cmd)
        self.save()

    def remove_custom(self, index):
        lst = self.data.get("custom_commands", [])
        if 0 <= index < len(lst):
            lst.pop(index)
            self.save()

    @property
    def auto_reconnect(self):
        return bool(self.data.get("auto_reconnect", True))

    @auto_reconnect.setter
    def auto_reconnect(self, value):
        self.data["auto_reconnect"] = bool(value)
        self.save()


# --------------------------------------------------------------------------
# Сеть: скрытый ping и проверка порта
# --------------------------------------------------------------------------
def _hidden_kwargs():
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return {"startupinfo": si, "creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def ping_once(host, timeout_ms=1000):
    """Один ICMP-запрос без видимого окна. Вывод читаем как байты (без проблем с кодировкой)."""
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
    try:
        r = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout_ms / 1000 + 3,
            **_hidden_kwargs(),
        )
    except Exception:  # noqa: BLE001
        return False
    if r.returncode != 0:
        return False
    if os.name == "nt":
        # Windows возвращает 0 и на «Destination host unreachable» — смотрим на TTL=
        return b"TTL=" in r.stdout.upper()
    return True


def is_ip_literal(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def tcp_open(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def clean_addr(raw):
    """Привести вставленный адрес в порядок: невидимые символы, переводы строк, маска /24
    (так адреса копируются из MikroTik/WinBox), префикс «ssh user@»."""
    t = (raw or "").replace("\u00a0", " ")
    for ch in "\u200b\u200c\u200d\ufeff\u2060\u202a\u202c":
        t = t.replace(ch, "")
    t = " ".join(t.split()).strip("\"'`")
    return re.sub(r"(\b\d{1,3}(?:\.\d{1,3}){3})/\d{1,2}\b", r"\1", t)


def parse_host(raw):
    raw = re.sub(r"\s*:\s*", ":", clean_addr(raw))   # «10.0.0.1: 2222» → «10.0.0.1:2222»
    if " " in raw:  # «ssh admin@10.0.0.1» и т.п. — берём последнее слово
        raw = raw.split()[-1]
    raw = re.sub(r"^[a-z]+://", "", raw, flags=re.I)
    if "@" in raw:
        raw = raw.rsplit("@", 1)[1]
    host, port = raw, 22
    if raw.count(":") == 1:
        host, p = raw.split(":")
        host = host.strip()
        port = int(p)  # ValueError обрабатывает вызывающий код
        if not 0 < port < 65536:
            raise ValueError("port")
    if not valid_host(host):
        raise ValueError("host")
    return host, port


def valid_host(host):
    """IPv4-адрес или имя ПК: буквы/цифры/точки/дефисы; «99999» и подобный мусор — не адрес."""
    if not host or len(host) > 253:
        return False
    if re.fullmatch(r"\d+(\.\d+){3}", host):
        return all(int(x) <= 255 for x in host.split("."))
    if host.isdigit():
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return bool(re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?", host))


def local_net_up(host):
    """Есть ли на ЭТОМ компьютере маршрут до адреса (кабель/Wi-Fi не отключены). Пакеты не отправляются."""
    try:
        infos = socket.getaddrinfo(host, 22, socket.AF_INET, socket.SOCK_DGRAM)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(1)
            s.connect(infos[0][4])
        finally:
            s.close()
        return True
    except (OSError, IndexError):
        return False


def exec_remote(client, user, password, cmd, use_sudo, timeout=180, stdin_data=None):
    """Выполнить команду на отдельном канале (терминал не затрагивается).
    Возвращает (код_выхода, текст). Код -1 — соединение оборвалось."""
    if use_sudo and user != "root":
        full = "sudo -S -p '' bash -c " + shlex.quote(cmd)
        feed = password + "\n"
    else:
        full = "bash -c " + shlex.quote(cmd)
        feed = None
    chan = client.get_transport().open_session(timeout=15)
    chan.set_combine_stderr(True)
    chan.settimeout(timeout)
    chan.exec_command(full)
    if feed:
        chan.sendall(feed.encode("utf-8"))
    if stdin_data:
        chan.sendall(stdin_data)
    try:
        chan.shutdown_write()
    except Exception:  # noqa: BLE001
        pass
    chunks, total = [], 0
    while True:
        data = chan.recv(65536)
        if not data:
            break
        if total < 400_000:
            chunks.append(data)
            total += len(data)
    code = chan.recv_exit_status()
    text = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n").strip()
    return code, text


# --------------------------------------------------------------------------
# Быстрые команды выполняются прямо в терминале (видно вывод, работает Ctrl+C)
# --------------------------------------------------------------------------
SUDO_PROMPT = "[sudo] password: "  # ASCII — не зависит от локали удалённого ПК
SHELL_META = set(";|&<>$`\\\"'(){}*?~#\n")


def sudo_prompt_pending(tail):
    """True, если поток кончается приглашением sudo, стоящим в начале строки.
    Эхо набранной команды содержит тот же текст («sudo -p '[sudo] password: '»), но не в начале
    строки и не в конце потока — на него пароль подставлять нельзя."""
    p = SUDO_PROMPT.encode()
    if not tail.endswith(p):
        return False
    head = tail[:-len(p)]
    return head == b"" or head[-1:] in (b"\r", b"\n")


def terminal_line(cmd, use_sudo):
    """Строка, которую набираем в терминале. Для sudo пароль подставит сама программа."""
    if not use_sudo:
        # несколько строк набираем одной конструкцией bash -c '…', чтобы они выполнились как один скрипт
        return f"bash -c {shlex.quote(cmd)}" if "\n" in cmd else cmd
    if any(ch in cmd for ch in SHELL_META):  # составная команда — sudo должен охватить всё
        return f"sudo -p '{SUDO_PROMPT}' bash -c {shlex.quote(cmd)}"
    return f"sudo -p '{SUDO_PROMPT}' {cmd}"


# --------------------------------------------------------------------------
# Информация о системе (опрашивается автоматически при подключении)
# --------------------------------------------------------------------------
SYSINFO_SCRIPT = r"""
LC_ALL=C; export LC_ALL
echo '##DMI'
for f in sys_vendor product_name board_vendor board_name; do
  printf '%s=%s\n' "$f" "$(cat /sys/class/dmi/id/$f 2>/dev/null)"
done
echo '##LSCPU'
lscpu 2>/dev/null
echo '##CPUINFO'
grep -m1 -i 'model name' /proc/cpuinfo 2>/dev/null
echo '##NPROC'
nproc 2>/dev/null
echo '##MEM'
grep MemTotal /proc/meminfo 2>/dev/null
echo '##OS'
( . /etc/os-release 2>/dev/null; echo "PRETTY=$PRETTY_NAME"; echo "VERSION_ID=$VERSION_ID" )
[ -r /etc/astra_version ] && echo "ASTRA=$(head -n1 /etc/astra_version)"
echo "KERNEL=$(uname -r)"
command -v mate-session >/dev/null 2>&1 && echo "MATE=1"
echo '##SMB'
[ -d /opt/astra-smb-mounter ] && echo "ASTRA=$(head -n1 /opt/astra-smb-mounter/VERSION 2>/dev/null)"
v=$(dpkg-query -W -f='${Version}' mate-smb-tools 2>/dev/null)
[ -n "$v" -o -d /opt/mate_smb_tools ] && echo "MATE=${v:-?}"
echo '##DISKS'
lsblk -d -b -n -P -o NAME,SIZE,ROTA,MODEL,TRAN,TYPE 2>/dev/null
"""
SYSINFO_DMI_MEM = "LC_ALL=C dmidecode -t memory 2>/dev/null"

_GENERIC = ("to be filled", "default string", "system product name", "system manufacturer",
            "not applicable", "not specified", "o.e.m.", "type2 -", "base board",
            "unknown", "none", "n/a", "undefined", "[empty]", "not provided", "unspecified")


def ru_plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def fmt_bytes(n):
    gb = n / 1e9
    if gb >= 995:
        tb = f"{gb / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{tb} ТБ"
    return f"{round(gb)} ГБ"


def _is_good(value):
    v = (value or "").strip().lower()
    return bool(v) and not any(g in v for g in _GENERIC) and not re.fullmatch(r"[0f\-\s]+", v)


def _clean_vendor(v):
    cleaned = re.sub(r"\b(inc|corporation|corp|co|ltd|technology|computer|international)\b\.?",
                     " ", v, flags=re.I)
    cleaned = re.sub(r"[,]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or v.strip()


def parse_sections(text):
    sec, cur = {}, None
    for line in text.splitlines():
        if line.startswith("##"):
            cur = line[2:].strip()
            sec[cur] = []
        elif cur is not None:
            sec[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in sec.items()}


def _kv(text):
    d = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def parse_board(dmi_text):
    d = _kv(dmi_text)
    bv, bn = d.get("board_vendor", ""), d.get("board_name", "")
    sv, pn = d.get("sys_vendor", ""), d.get("product_name", "")
    if _is_good(bn):
        name = " ".join(x for x in (_clean_vendor(bv) if _is_good(bv) else "", bn) if x)
        if _is_good(pn) and pn.lower() not in name.lower():
            name += f" ({pn})"  # у готовых ПК/ноутбуков полезна и модель устройства
        return name
    if _is_good(pn):
        return " ".join(x for x in (_clean_vendor(sv) if _is_good(sv) else "", pn) if x)
    return None


def parse_cpu(sec):
    ls = {}
    for line in sec.get("LSCPU", "").splitlines():
        k, _, v = line.partition(":")
        ls[k.strip()] = v.strip()
    name = ls.get("Model name", "") or sec.get("CPUINFO", "").partition(":")[2].strip()
    if not name:
        return None
    name = re.sub(r"\((R|TM)\)", "", name, flags=re.I)
    name = re.sub(r"\bCPU\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    threads = int(sec["NPROC"]) if sec.get("NPROC", "").strip().isdigit() else 0
    try:
        cores = int(ls["Socket(s)"]) * int(ls["Core(s) per socket"])
    except (KeyError, ValueError):
        cores = 0
    if cores and threads and cores != threads:
        tail = (f"{cores} {ru_plural(cores, 'ядро', 'ядра', 'ядер')} / "
                f"{threads} {ru_plural(threads, 'поток', 'потока', 'потоков')}")
    elif threads:
        tail = f"{threads} {ru_plural(threads, 'ядро', 'ядра', 'ядер')}"
    else:
        tail = ""
    return name + ("\n" + tail if tail else "")


def parse_dmi_memory(text):
    """Модули ОЗУ из `dmidecode -t memory`: список словарей."""
    devs, cur = [], None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():  # заголовок блока
            cur = {} if line.strip() == "Memory Device" else None
            if cur is not None:
                devs.append(cur)
            continue
        if cur is not None and ":" in line:
            k, _, v = line.partition(":")
            cur.setdefault(k.strip(), v.strip())
    modules = []
    for d in devs:
        m = re.match(r"(\d+)\s*(MB|GB|KB)", d.get("Size", ""), re.I)
        if not m:
            continue  # «No Module Installed»
        n, unit = int(m.group(1)), m.group(2).upper()
        mb = n * 1024 if unit == "GB" else n if unit == "MB" else n // 1024
        if mb <= 0:
            continue
        speed = 0
        for key in ("Configured Memory Speed", "Configured Clock Speed", "Speed"):
            sm = re.match(r"(\d+)", d.get(key, ""))
            if sm and int(sm.group(1)) > 0:
                speed = int(sm.group(1))
                break
        mtype = d.get("Type", "")
        if mtype.lower() in ("unknown", "other", "<out of spec>", ""):
            mtype = ""
        manuf = d.get("Manufacturer", "")
        part = d.get("Part Number", "")
        modules.append({
            "mb": mb, "type": mtype, "speed": speed,
            "manuf": manuf if _is_good(manuf) else "",
            "part": part if _is_good(part) else "",
        })
    return modules


def _fmt_mb(mb):
    if mb % 1024 == 0:
        return f"{mb // 1024} ГБ"
    return f"{mb} МБ"


def format_ram(modules, mem_kb):
    if modules:
        total = sum(m["mb"] for m in modules)
        types = Counter(m["type"] for m in modules if m["type"])
        speeds = Counter(m["speed"] for m in modules if m["speed"])
        head = _fmt_mb(total)
        if types:
            head += " " + types.most_common(1)[0][0]
        if speeds:
            head += f" {speeds.most_common(1)[0][0]} МГц"
        groups = Counter((m["mb"], m["manuf"], m["part"]) for m in modules)
        parts = []
        for (mb, manuf, part), n in groups.items():
            label = " ".join(x for x in (_fmt_mb(mb), manuf, part) if x)
            parts.append(f"{n} × {label}" if n > 1 else label)
        return head + "\n" + "; ".join(parts)
    if mem_kb:
        return f"≈{round(mem_kb / 1024 / 1024)} ГБ (по данным ОС)\nмодули не определены"
    return None


def parse_disks(text):
    disks = []
    for line in text.splitlines():
        try:
            pairs = dict(p.split("=", 1) for p in shlex.split(line) if "=" in p)
        except ValueError:
            continue
        name = pairs.get("NAME", "")
        if pairs.get("TYPE") != "disk" or name.startswith(("zram", "ram", "loop", "nbd", "dm-")):
            continue
        try:
            size = int(pairs.get("SIZE") or 0)
        except ValueError:
            size = 0
        if size <= 0:
            continue
        model = re.sub(r"\s+", " ", pairs.get("MODEL", "").replace("\\x20", " ")).strip()
        tran, rota = pairs.get("TRAN", "").lower(), pairs.get("ROTA", "")
        if tran == "nvme" or name.startswith("nvme"):
            kind = "NVMe SSD"
        elif tran == "usb":
            kind = "USB"
        elif rota == "0":
            kind = "SSD"
        elif rota == "1":
            kind = "HDD"
        else:
            kind = "Диск"
        disks.append((kind, model or "модель не определена", size))
    if not disks:
        return None
    lines = []
    for (kind, model, size), n in Counter(disks).items():
        text_line = f"{kind} · {model} · {fmt_bytes(size)}"
        lines.append(f"{n} × {text_line}" if n > 1 else text_line)
    return "\n".join(lines)


def parse_os(text):
    """##OS -> «Astra Linux 1.7.5» и вторая строка «ядро 5.15.0 · MATE»."""
    d = _kv(text)
    name = d.get("PRETTY") or "Linux"
    ver = d.get("ASTRA") or ""
    if not ver and "astra" in name.lower() and d.get("VERSION_ID"):
        ver = d["VERSION_ID"]
    if ver and ver not in name:
        name = f"{name} {ver}"
    extra = []
    if d.get("KERNEL"):
        extra.append(f"ядро {d['KERNEL']}")
    if d.get("MATE"):
        extra.append("MATE")
    return name + ("\n" + " · ".join(extra) if extra else "")


def parse_smb(text):
    """##SMB -> по строке на установленную программу SMB-дисков или «не установлено»."""
    d = _kv(text)
    lines = []
    if "ASTRA" in d:
        lines.append(f"Astra SMB Mounter {d['ASTRA'] or '(версия не определена)'}".strip())
    if "MATE" in d:
        lines.append("mate-smb-tools " + ("(версия не определена)" if d["MATE"] in ("", "?") else d["MATE"]))
    return "\n".join(lines) if lines else "не установлено"


def collect_sysinfo(client, user, password):
    sec = {}
    try:
        code, text = exec_remote(client, user, password, SYSINFO_SCRIPT, False, timeout=25)
        if code == 0:
            sec = parse_sections(text)
    except Exception:  # noqa: BLE001
        pass
    modules = []
    try:  # dmidecode требует root: для обычного пользователя идёт через sudo
        code2, text2 = exec_remote(client, user, password, SYSINFO_DMI_MEM, True, timeout=25)
        if code2 == 0:
            modules = parse_dmi_memory(text2)
    except Exception:  # noqa: BLE001
        pass
    mem_kb = 0
    mm = re.search(r"(\d+)", sec.get("MEM", ""))
    if mm:
        mem_kb = int(mm.group(1))
    return {
        "board": parse_board(sec.get("DMI", "")),
        "cpu": parse_cpu(sec),
        "ram": format_ram(modules, mem_kb),
        "disks": parse_disks(sec.get("DISKS", "")),
        "os": parse_os(sec.get("OS", "")),
        "smb": parse_smb(sec.get("SMB", "")),
    }



# --------------------------------------------------------------------------
# Смена IP на статический (через NetworkManager / nmcli)
# --------------------------------------------------------------------------
NETINFO_SCRIPT = r"""
LC_ALL=C; export LC_ALL
if ! command -v nmcli >/dev/null 2>&1; then echo '##NONM'; exit 0; fi
nmcli -t -f NAME,DEVICE,TYPE con show --active | while IFS= read -r line; do
  type=${line##*:}; rest=${line%:*}; dev=${rest##*:}; name=${rest%:*}
  name=$(printf '%s' "$name" | sed 's/\\:/:/g')
  case "$dev" in lo|--|'') continue;; esac
  case "$type" in loopback|tun|bridge) continue;; esac
  echo '##NMCLI'
  echo "name=$name"
  echo "uuid=$(nmcli -g connection.uuid con show "$name" 2>/dev/null | head -n1)"
  echo "dev=$dev"
  echo "method=$(nmcli -g ipv4.method con show "$name" 2>/dev/null)"
  nmcli -t -f IP4.ADDRESS,IP4.GATEWAY,IP4.DNS dev show "$dev" 2>/dev/null
done
"""


def parse_netinfo(text):
    """Активные соединения NetworkManager: {'conns': [...]} или {'error': ...}."""
    if "##NONM" in text:
        return {"error": "nonm"}
    conns = []
    for block in text.split("##NMCLI")[1:]:
        c = {"name": "", "uuid": "", "dev": "", "method": "", "addr": "", "prefix": "", "gw": "", "dns": []}
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith("name="):
                c["name"] = line[5:]
            elif line.startswith("uuid="):
                c["uuid"] = line[5:].strip()
            elif line.startswith("dev="):
                c["dev"] = line[4:]
            elif line.startswith("method="):
                c["method"] = line[7:]
            elif line.startswith("IP4.ADDRESS") and not c["addr"]:
                val = line.partition(":")[2]
                c["addr"], _, c["prefix"] = val.partition("/")
            elif line.startswith("IP4.GATEWAY"):
                gw = line.partition(":")[2].strip()
                c["gw"] = "" if gw in ("", "--") else gw
            elif line.startswith("IP4.DNS"):
                d = line.partition(":")[2].strip()
                if d:
                    c["dns"].append(d)
        if c["name"] and c["dev"]:
            conns.append(c)
    return {"conns": conns}


def collect_netinfo(client, user, password):
    code, text = exec_remote(client, user, password, NETINFO_SCRIPT, False, timeout=15)
    if code != 0:
        return {"error": f"код {code}: {text[:200]}"}
    return parse_netinfo(text)


def parse_prefix(text):
    """'24', '/24' или '255.255.255.0' -> 24; иначе None."""
    t = text.strip().lstrip("/")
    if not t:
        return None
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 30 else None
    try:
        v = int(ipaddress.IPv4Address(t))
    except ValueError:
        return None
    n = bin(v).count("1")
    if v != (0xFFFFFFFF << (32 - n)) & 0xFFFFFFFF:  # маска должна быть сплошной
        return None
    return n if 1 <= n <= 30 else None


def validate_static(ip, mask, gw, dns_text):
    """-> (текст_ошибки | None, данные | None)."""
    ip, gw = ip.strip(), gw.strip()
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return "Некорректный IP-адрес (пример: 192.168.1.60)", None
    if addr.is_multicast or addr.is_unspecified:
        return "Этот адрес нельзя назначить компьютеру", None
    prefix = parse_prefix(mask)
    if prefix is None:
        return "Некорректная маска: введите префикс (например, 24) или маску 255.255.255.0", None
    net = ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)
    if addr in (net.network_address, net.broadcast_address):
        return f"{addr} — адрес сети или широковещательный для {net}, выберите другой", None
    gw_s = ""
    if gw:
        try:
            g = ipaddress.IPv4Address(gw)
        except ValueError:
            return "Некорректный адрес шлюза", None
        if g not in net:
            return f"Шлюз {g} не входит в сеть {net}. Укажите шлюз из той же подсети", None
        if g == addr:
            return "Шлюз не может совпадать с IP-адресом", None
        gw_s = str(g)
    dns = []
    for tok in re.split(r"[,\s;]+", dns_text.strip()):
        if not tok:
            continue
        try:
            dns.append(str(ipaddress.IPv4Address(tok)))
        except ValueError:
            return f"Некорректный DNS-адрес: {tok}", None
    if len(dns) > 3:
        return "Укажите не более трёх DNS-серверов", None
    return None, {"ip": str(addr), "prefix": prefix, "gw": gw_s, "dns": dns}


IP_SCRIPT = r"""#!/bin/bash
export LC_ALL=C
REFK=@REFK@; REFV=@REFV@; IP=@IP@; PFX=@PFX@; GW=@GW@; DNS=@DNS@; T=@TOKEN@
SELF=$(readlink -f "$0")
mark() { printf 'SSHC-%s-%s\r\033[2K' "$1" "$T"; }
restore() {
  nmcli con mod "$REFK" "$REFV" ipv4.method "$OLD_M" ipv4.addresses "$OLD_A" \
    ipv4.gateway "$OLD_G" ipv4.dns "$OLD_D" >/dev/null 2>&1
}

if [ "$1" = "--apply" ]; then
  # Фоновая часть: переживает обрыв SSH, сама откатывает настройки при неудаче
  sleep 1
  nmcli con up "$REFK" "$REFV" >"$LOG" 2>&1; rc=$?
  if [ "$rc" -eq 0 ]; then
    rc=98
    for i in 1 2 3 4 5 6 7 8 9 10; do
      if ip -4 -o addr show 2>/dev/null | grep -qF " $IP/"; then rc=0; break; fi
      sleep 1
    done
    [ "$rc" -eq 0 ] || echo "Адрес $IP не появился на интерфейсе" >>"$LOG"
  fi
  if [ "$rc" -ne 0 ]; then
    echo "Откат прежних настроек..." >>"$LOG"
    restore
    nmcli con up "$REFK" "$REFV" >>"$LOG" 2>&1
  fi
  echo "exit=$rc" >>"$LOG"
  rm -f "$SELF"
  exit 0
fi

fail() { echo "$1"; mark FAIL; rm -f "$SELF" "$LOG"; exit 1; }

command -v nmcli >/dev/null 2>&1 || fail "Ошибка: nmcli не найден"
OLD_M=$(nmcli -g ipv4.method con show "$REFK" "$REFV" 2>/dev/null)
OLD_A=$(nmcli -g ipv4.addresses con show "$REFK" "$REFV" 2>/dev/null)
OLD_G=$(nmcli -g ipv4.gateway con show "$REFK" "$REFV" 2>/dev/null)
OLD_D=$(nmcli -g ipv4.dns con show "$REFK" "$REFV" 2>/dev/null)
export OLD_M OLD_A OLD_G OLD_D
echo "Меняю IP: $IP/$PFX, шлюз ${GW:--}, DNS ${DNS:--}"
nmcli con mod "$REFK" "$REFV" ipv4.method manual ipv4.addresses "$IP/$PFX" \
  ipv4.gateway "$GW" ipv4.dns "$DNS" || { restore; fail "Ошибка: nmcli не смог сохранить настройки"; }
SAVED=$(nmcli -g ipv4.addresses con show "$REFK" "$REFV" 2>/dev/null)
case "$SAVED" in
  *"$IP/$PFX"*) ;;
  *) restore; fail "Ошибка: адрес не сохранился в профиле (там: ${SAVED:-пусто})";;
esac
LOG=$(mktemp /tmp/sshc-iplog.XXXXXX); export LOG
mark APPLY
echo "Применяю настройки, связь может прерваться на несколько секунд..."
setsid -f bash "$SELF" --apply >/dev/null 2>&1 </dev/null
for i in $(seq 1 60); do grep -q '^exit=' "$LOG" 2>/dev/null && break; sleep 1; done
rc=$(sed -n 's/^exit=//p' "$LOG" 2>/dev/null | tail -n 1)
if [ "$rc" = 0 ]; then
  echo "Готово: IP применён"
  mark OK
else
  cat "$LOG" 2>/dev/null
  echo "Не удалось применить IP, прежние настройки возвращены"
  mark FAIL
fi
rm -f "$LOG"
"""


def build_ip_script(conn, ip, prefix, gw, dns, token):
    """Скрипт смены IP: сохранить профиль, проверить, применить в фоне, при неудаче откатить.
    conn — словарь соединения из parse_netinfo (используем uuid, иначе имя)."""
    if conn.get("uuid"):
        refk, refv = "uuid", conn["uuid"]
    else:
        refk, refv = "id", conn["name"]
    subst = {"@REFK@": refk, "@REFV@": refv, "@IP@": ip, "@PFX@": str(prefix), "@GW@": gw,
             "@DNS@": ",".join(dns), "@TOKEN@": token}
    text = IP_SCRIPT
    for key, val in subst.items():
        text = text.replace(key, shlex.quote(val))
    return text


def network_of(addr, prefix):
    try:
        return str(ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False))
    except ValueError:
        return ""


# --------------------------------------------------------------------------
# Быстрые команды администратора: учётные записи, время, сброс сессий
# (скрипты загружаются на ПК и запускаются в терминале — вывод виден сразу)
# --------------------------------------------------------------------------
KILL_SESSIONS_SCRIPT_PATH = "/opt/mate-session-tools/terminate_sessions_root.sh"
KILL_SESSIONS_LOG = "/var/log/kill-sessions.log"
LOGIN_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def validate_new_user(v):
    """Проверка формы создания учётной записи. -> текст ошибки или None."""
    login = v.get("login", "")
    if not LOGIN_RE.match(login):
        return "Логин: латиница в нижнем регистре, цифры, _ и -, начинать с буквы (до 32 знаков)"
    pw = v.get("password", "")
    if not pw:
        return "Введите пароль"
    if "\n" in pw or "\r" in pw:
        return "В пароле не должно быть переводов строки"
    if pw != v.get("password2", ""):
        return "Пароли не совпадают"
    if "\n" in v.get("name", "") or ":" in v.get("name", ""):
        return "В имени нельзя использовать двоеточие"
    return None


def build_create_user_script(login, fullname, password, admin):
    q = shlex.quote
    return f"""rm -f -- "$0"
login={q(login)}; fullname={q(fullname or login)}; pw={q(password)}; admin={1 if admin else 0}
echo "Создание учётной записи: $login"
if id "$login" >/dev/null 2>&1; then echo "ОШИБКА: пользователь $login уже существует"; exit 2; fi
if command -v adduser >/dev/null 2>&1; then
  adduser --disabled-password --gecos "$fullname" "$login" || {{ echo "ОШИБКА: не удалось создать пользователя"; exit 1; }}
else
  useradd -m -s /bin/bash -c "$fullname" "$login" || {{ echo "ОШИБКА: не удалось создать пользователя"; exit 1; }}
fi
if ! printf '%s:%s\\n' "$login" "$pw" | chpasswd; then
  echo "ОШИБКА: пароль не принят (проверьте требования к паролю). Учётная запись удалена."
  userdel -r "$login" >/dev/null 2>&1
  exit 1
fi
for g in plugdev audio video floppy dip tape cdrom scanner adm fax dialout; do
  if getent group "$g" >/dev/null 2>&1; then usermod -aG "$g" "$login" && echo "Привилегия: $g"; fi
done
if [ "$admin" = 1 ]; then
  for g in sudo astra-admin; do
    if getent group "$g" >/dev/null 2>&1; then usermod -aG "$g" "$login" && echo "Добавлен в группу: $g"; fi
  done
fi
echo "Готово. Учётная запись создана:"
id "$login" || true
exit 0
"""


TIMESYNC_PY = r"""import socket, struct, sys
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(b'\x1b' + 47 * b'\0', (sys.argv[1], int(sys.argv[2])))
    d = s.recvfrom(512)[0]
    sec = struct.unpack('!II', d[40:48])[0]
    if len(d) < 48 or sec == 0:
        raise SystemExit(1)
    print(sec - 2208988800)
except Exception:
    raise SystemExit(1)
"""


def build_timesync_script(resolv="/etc/resolv.conf", port=123, setter='date -u -s "@$t" >/dev/null'):
    """Синхронизация времени по NTP с DNS-серверами этого ПК: какой DNS, такой и сервер времени (SNTP на python3)."""
    return f"""rm -f -- "$0"
echo "Синхронизация времени"
echo "Сейчас на ПК:  $(date '+%F %T %Z')"
servers=""
for r in /run/systemd/resolve/resolv.conf {shlex.quote(resolv)}; do
  [ -r "$r" ] || continue
  for ip in $(awk '/^nameserver/ {{print $2}}' "$r"); do servers="$servers $ip"; done
done
if command -v nmcli >/dev/null 2>&1; then
  for ip in $(nmcli -g IP4.DNS dev show 2>/dev/null | tr '|' ' '); do servers="$servers $ip"; done
fi
list=""
for ip in $servers; do
  case "$ip" in ""|127.0.0.53|127.0.0.54|::*) continue;; esac
  case " $list " in *" $ip "*) continue;; esac
  list="$list $ip"
done
echo "Серверы времени (DNS этого ПК):${{list:- не найдены}}"
PYCODE=$(cat <<'PYEOF2'
{TIMESYNC_PY}PYEOF2
)
ok=""
for ip in $list; do
  printf '  %s ... ' "$ip"
  t=$(python3 -c "$PYCODE" "$ip" {int(port)} 2>/dev/null)
  if [ -n "$t" ]; then
    echo "ответил"
    {setter}
    hwclock --systohc >/dev/null 2>&1
    ok="$ip"
    break
  fi
  echo "нет ответа"
done
if [ -z "$ok" ]; then
  echo "ОШИБКА: DNS-сервер не ответил по NTP (UDP 123) или нет python3. Время не изменено."
  exit 1
fi
echo "Время синхронизировано с $ok"
echo "Теперь на ПК:  $(date '+%F %T %Z')"
"""


PRINTERS_SH = r'''#!/bin/bash
# ============================================================
#  Установщик автоактивации принтеров CUPS
#  Поддержка: Astra Linux, Linux MATE
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_PATH="/usr/local/bin/cups-printer-activate.sh"
SERVICE_PATH="/etc/systemd/system/cups-printer-activate.service"
TIMER_PATH="/etc/systemd/system/cups-printer-activate.timer"
LOG_PATH="/var/log/cups-activate.log"
INTERVAL="30s"

# ────────────────────────────────────────────────────────────
print_header() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║   Автоактивация принтеров CUPS — Установщик  ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo -e "${YELLOW}▶ $1${NC}"
}

print_ok() {
    echo -e "${GREEN}  ✔ $1${NC}"
}

print_err() {
    echo -e "${RED}  ✘ $1${NC}"
}

# ────────────────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_err "Скрипт нужно запускать с правами root: sudo $0"
        exit 1
    fi
}

check_cups() {
    print_step "Проверка CUPS..."
    if ! command -v lpstat &>/dev/null; then
        print_err "CUPS не установлен. Установите: sudo apt install cups"
        exit 1
    fi
    print_ok "CUPS найден"
}

uninstall() {
    print_step "Удаление компонентов..."
    systemctl stop cups-printer-activate.timer 2>/dev/null || true
    systemctl disable cups-printer-activate.timer 2>/dev/null || true
    rm -f "$SCRIPT_PATH" "$SERVICE_PATH" "$TIMER_PATH"
    systemctl daemon-reload
    print_ok "Всё удалено"
    echo ""
    echo -e "${GREEN}Автоактивация принтеров отключена.${NC}"
    exit 0
}

status() {
    echo ""
    echo -e "${BLUE}─── Статус таймера ───────────────────────────${NC}"
    systemctl status cups-printer-activate.timer --no-pager 2>/dev/null || \
        echo -e "${RED}Таймер не установлен${NC}"

    echo ""
    echo -e "${BLUE}─── Принтеры ─────────────────────────────────${NC}"
    lpstat -p 2>/dev/null || echo "Принтеры не найдены"

    echo ""
    echo -e "${BLUE}─── Последние записи лога ────────────────────${NC}"
    if [[ -f "$LOG_PATH" ]]; then
        tail -20 "$LOG_PATH"
    else
        echo "Лог пуст или отсутствует"
    fi
    exit 0
}

# ────────────────────────────────────────────────────────────
install() {
    # 1. Скрипт активации
    print_step "Создание скрипта активации..."
    cat > "$SCRIPT_PATH" << 'EOF'
#!/bin/bash
LOG="/var/log/cups-activate.log"

printers=$(lpstat -p 2>/dev/null | awk '{print $2}')

if [[ -z "$printers" ]]; then
    exit 0
fi

for printer in $printers; do
    status=$(lpstat -p "$printer" 2>/dev/null)
    if echo "$status" | grep -qiE "disabled|отключен"; then
        cupsenable "$printer" 2>/dev/null
        cups-accept "$printer" 2>/dev/null || true
        echo "$(date '+%Y-%m-%d %H:%M:%S') — Принтер '$printer' активирован автоматически" >> "$LOG"
    fi
done
EOF
    chmod +x "$SCRIPT_PATH"
    print_ok "Скрипт создан: $SCRIPT_PATH"

    # 2. Systemd-сервис
    print_step "Создание systemd-сервиса..."
    cat > "$SERVICE_PATH" << EOF
[Unit]
Description=Автоактивация принтеров CUPS
After=cups.service
Requires=cups.service

[Service]
Type=oneshot
ExecStart=$SCRIPT_PATH
EOF
    print_ok "Сервис создан: $SERVICE_PATH"

    # 3. Systemd-таймер
    print_step "Создание systemd-таймера (интервал: $INTERVAL)..."
    cat > "$TIMER_PATH" << EOF
[Unit]
Description=Таймер автоактивации принтеров CUPS

[Timer]
OnBootSec=30s
OnUnitActiveSec=$INTERVAL
Unit=cups-printer-activate.service

[Install]
WantedBy=timers.target
EOF
    print_ok "Таймер создан: $TIMER_PATH"

    # 4. Активация
    print_step "Активация таймера..."
    systemctl daemon-reload
    systemctl enable --now cups-printer-activate.timer
    print_ok "Таймер запущен и добавлен в автозагрузку"

    # 5. Итог
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Установка завершена!               ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Интервал проверки : ${YELLOW}$INTERVAL${NC}"
    echo -e "  Лог активаций     : ${YELLOW}$LOG_PATH${NC}"
    echo ""
    echo -e "  Полезные команды:"
    echo -e "  ${BLUE}sudo $0 status${NC}      — статус и лог"
    echo -e "  ${BLUE}sudo $0 uninstall${NC}   — удалить всё"
    echo -e "  ${BLUE}sudo $0 test${NC}        — тест прямо сейчас"
    echo ""
}

test_run() {
    print_step "Запуск проверки вручную..."
    systemctl start cups-printer-activate.service
    print_ok "Готово. Посмотреть результат:"
    echo "  tail $LOG_PATH"
    exit 0
}

# ────────────────────────────────────────────────────────────
print_header
check_root

case "${1:-install}" in
    install)   check_cups; install ;;
    uninstall) uninstall ;;
    status)    status ;;
    test)      test_run ;;
    *)
        echo "Использование: sudo $0 [install|uninstall|status|test]"
        exit 1
        ;;
esac
'''
PRINTERS_TIMER = "cups-printer-activate.timer"
PRINTERS_SCRIPT = "/usr/local/bin/cups-printer-activate.sh"
PRINTERS_ACTIONS = {
    "install": "Установить", "reinstall": "Переустановить", "status": "Показать статус",
    "test": "Проверить принтеры сейчас", "uninstall": "Удалить",
}


def build_printers_probe_script(timer=PRINTERS_TIMER, script=PRINTERS_SCRIPT, unit_dir="/etc/systemd/system"):
    """Проверка (без sudo): установлено ли автовключение принтеров и есть ли CUPS."""
    q = shlex.quote
    return f"""t={q(timer)}; s={q(script)}; d={q(unit_dir)}
if [ -x "$s" ] && [ -f "$d/$t" ]; then echo "installed=1"; else echo "installed=0"; fi
echo "enabled=$(systemctl is-enabled "$t" 2>/dev/null | head -n1)"
echo "active=$(systemctl is-active "$t" 2>/dev/null | head -n1)"
if command -v lpstat >/dev/null 2>&1; then echo "cups=1"; else echo "cups=0"; fi
"""


def parse_printers_probe(text):
    info = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    if "installed" not in info:
        return {"error": first_line(text, 120) or "нет ответа"}
    return {"installed": info["installed"] == "1", "cups": info.get("cups") == "1",
            "enabled": info.get("enabled", ""), "active": info.get("active", "")}


def build_printers_script(action="install"):
    """Встроенный установщик galochki_linux.sh; действие задаётся заранее (install|uninstall|status|test)."""
    if action not in ("install", "uninstall", "status", "test"):
        raise ValueError(action)
    body = (PRINTERS_SH.replace("sudo $0 status", "быстрая команда «Автовключение принтеров» → статус")
            .replace("sudo $0 uninstall", "быстрая команда «Автовключение принтеров» → удалить")
            .replace("sudo $0 test", "быстрая команда «Автовключение принтеров» → проверить сейчас")
            .replace("sudo $0", "sudo"))
    return f'rm -f -- "$0"\nset -- {action}\n' + body


def validate_hostname(name):
    """Имя компьютера по RFC 1123: латиница, цифры и дефис, до 63 символов, дефис не в начале/конце."""
    name = (name or "").strip()
    if not name:
        return "Введите имя компьютера"
    if len(name) > 63:
        return "Имя длиннее 63 символов"
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?", name):
        return "Только латиница, цифры и дефис (не в начале и не в конце; без подчёркивания и пробелов)"
    return None


def build_hostname_script(new, hosts="/etc/hosts", hostfile="/etc/hostname", clean_locks=False,
                          home_root="/home"):
    """Смена имени ПК: hostnamectl, /etc/hostname и строка 127.0.1.1 в /etc/hosts (с резервной копией).
    clean_locks — после смены удалить блокировки браузеров (иначе Яндекс Браузер/Chromium не открывается)."""
    q = shlex.quote
    script = _build_hostname_script(new, hosts, hostfile, q)
    if clean_locks:
        script += "\necho\n" + build_browser_locks_script(home_root, machine=False, self_delete=False)
    return script


def _build_hostname_script(new, hosts, hostfile, q):
    return f"""rm -f -- "$0"
new={q(new.strip())}; HOSTS={q(hosts)}; HF={q(hostfile)}
old="$(hostname)"
echo "Смена имени компьютера: $old -> $new"
if [ "$old" = "$new" ] && [ "$(cat "$HF" 2>/dev/null)" = "$new" ]; then echo "Имя уже такое — менять нечего."; exit 0; fi
if command -v hostnamectl >/dev/null 2>&1; then
  hostnamectl set-hostname "$new" || {{ echo "ОШИБКА: hostnamectl не смог сменить имя"; exit 1; }}
else
  hostname "$new" || {{ echo "ОШИБКА: не удалось сменить имя"; exit 1; }}
fi
printf '%s\\n' "$new" > "$HF" || {{ echo "ОШИБКА: не удалось записать $HF"; exit 1; }}
if [ -f "$HOSTS" ]; then
  cp -p "$HOSTS" "$HOSTS.sshc-bak" 2>/dev/null
  if grep -qE '^127\\.0\\.1\\.1[[:space:]]' "$HOSTS"; then
    sed -i -E "s/^(127\\.0\\.1\\.1[[:space:]]+).*/\\1$new/" "$HOSTS"
  else
    printf '127.0.1.1\\t%s\\n' "$new" >> "$HOSTS"
  fi
else
  printf '127.0.0.1\\tlocalhost\\n127.0.1.1\\t%s\\n' "$new" > "$HOSTS"
fi
echo "--- $HF ---"; cat "$HF"
echo "--- $HOSTS (127.0.1.1) ---"; grep -E '^127\\.0\\.1\\.1' "$HOSTS"
echo "Готово. Сейчас имя: $(hostname)"
echo "Приглашение в этом терминале обновится после нового входа; службам и домену может понадобиться перезагрузка."
"""


ACCOUNTS_LIST_SCRIPT = r"""now=" $(who 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ') $(loginctl list-users --no-legend 2>/dev/null | awk '{print $2}' | tr '\n' ' ') "
printf 'HOST\t%s\n' "$(hostname 2>/dev/null)"
t=""; [ -d /opt/astra-smb-mounter ] && t="astra"; [ -d /opt/mate_smb_tools ] && t="${t:+$t,}mate"
printf 'SMBTOOL\t%s\n' "$t"
awk '$3 ~ /^(cifs|smb3)$/ {mp=$2; src=$1; gsub(/\\040/," ",mp); gsub(/\\040/," ",src); u=""; n=split($4,o,","); for(i=1;i<=n;i++) if (o[i] ~ /^uid=/) u=substr(o[i],5); printf "MNT\t%s\t%s\t%s\n", u, mp, src}' /proc/mounts 2>/dev/null
isroot=0; [ "$(id -u 2>/dev/null)" = 0 ] && isroot=1
getent passwd | while IFS=: read -r name _ uid gid gecos home shell; do
  gecos=${gecos%%,*}; gecos=${gecos//$'\t'/ }
  grp=$(id -nG "$name" 2>/dev/null | tr ' ' ',')
  case "$now" in *" $name "*) on=1;; *) on=0;; esac
  case ",$grp," in *,sudo,*|*,astra-admin,*|*,wheel,*|*,admin,*) adm=1;; *) adm=0;; esac
  [ "$uid" = 0 ] && adm=1
  if [ "$isroot" = 1 ]; then st=$(passwd -S "$name" 2>/dev/null | awk '{print $2}'); st=${st:-?}; else st="?"; fi
  printf 'ACC\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$uid" "$gid" "$gecos" "$home" "$shell" "$grp" "$adm" "$on" "$st"
done
"""


def parse_accounts(text):
    """-> (имя ПК, [dict], инфо) из вывода ACCOUNTS_LIST_SCRIPT. Системные: UID < 1000 (кроме root) и nobody.
    инфо: {"tools": ["astra"|"mate"], "mounts": [(uid, точка, источник)]}. У записей: locked (True/False/None),
    disks — [(буква или имя, точка, источник)] сетевых дисков (CIFS), смонтированных этим пользователем."""
    host, rows, tools, mounts = "", [], [], []
    for line in (text or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if parts[0] == "HOST" and len(parts) >= 2:
            host = parts[1].strip()
        elif parts[0] == "SMBTOOL" and len(parts) >= 2:
            tools = [t for t in parts[1].strip().split(",") if t]
        elif parts[0] == "MNT" and len(parts) >= 4:
            mounts.append((parts[1].strip(), parts[2], parts[3]))
        elif parts[0] == "ACC" and len(parts) >= 10:
            try:
                uid, gid = int(parts[2]), int(parts[3])
            except ValueError:
                continue
            st = parts[10].strip() if len(parts) >= 11 else "?"
            locked = True if st in ("L", "LK") else (False if st in ("P", "PS", "NP") else None)
            rows.append({"login": parts[1], "uid": uid, "gid": gid, "name": parts[4], "home": parts[5],
                         "shell": parts[6], "groups": parts[7], "admin": parts[8] == "1",
                         "online": parts[9].strip() == "1", "locked": locked, "pwstate": st, "disks": [],
                         "system": not (uid == 0 or 1000 <= uid < 65534)})
    for muid, mp, src in mounts:
        owner = None
        for r in sorted(rows, key=lambda r: -len(r["home"])):      # сначала по домашней папке
            if r["home"] and r["home"] != "/" and (mp == r["home"] or mp.startswith(r["home"].rstrip("/") + "/")):
                owner = r
                break
        if owner is None and muid.isdigit() and int(muid) > 0:     # иначе по uid= из параметров монтирования
            owner = next((r for r in rows if r["uid"] == int(muid)), None)
        if owner is not None:
            name = posixpath.basename(mp.rstrip("/")) or mp
            owner["disks"].append((name, mp, src))
    return host, rows, {"tools": tools, "mounts": mounts}


def disks_text(acc):
    return ", ".join(d[0] for d in acc["disks"])


def lock_text(acc):
    return {True: "да", False: "нет"}.get(acc["locked"], "?")


def delete_block_reason(acc, me):
    """Почему эту учётную запись нельзя удалять из программы (или None)."""
    if acc["login"] == "root" or acc["uid"] == 0:
        return "Учётную запись root удалять нельзя"
    if acc["system"]:
        return "Это системная учётная запись (UID < 1000) — программа их не удаляет"
    if acc["login"] == me:
        return "Это учётная запись, под которой вы подключены, — удаление оборвёт соединение"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", acc["login"]):
        return "Необычное имя учётной записи — удалите её вручную"
    return None


def format_accounts_txt(host_label, pc_name, rows, when=None, info=None):
    when = when or time.strftime("%Y-%m-%d %H:%M:%S")
    users = [r for r in rows if not r["system"]]
    system = [r for r in rows if r["system"]]
    head = ("Логин", "UID", "Имя", "Домашняя папка", "Оболочка", "Админ", "Заблокирована", "Сейчас в системе",
            "Сетевые диски", "Группы")
    N = len(head) - 1

    def table(items):
        data = [[r["login"], str(r["uid"]), r["name"], r["home"], r["shell"], "да" if r["admin"] else "нет",
                 lock_text(r), "да" if r["online"] else "нет", disks_text(r) or "-", r["groups"]] for r in items]
        widths = [max(len(head[i]), *(len(d[i]) for d in data)) if data else len(head[i]) for i in range(N)]
        fmt = lambda row: "  ".join(row[i].ljust(widths[i]) for i in range(N)) + "  " + row[N]
        return "\n".join([fmt(head), fmt(["-" * w for w in widths] + ["-" * 6])] + [fmt(d) for d in data])

    tools = {"astra": "Astra SMB Mounter", "mate": "mate-smb-tools"}
    tool_line = ", ".join(tools.get(t, t) for t in (info or {}).get("tools", [])) or "не установлена"
    mounts = [(r["login"], d) for r in rows for d in r["disks"]]
    disks = "\n".join(f"  {login}: {d[0]}  {d[1]}  <- {d[2]}" for login, d in mounts) or "  нет"
    return (f"Учётные записи ПК {host_label}" + (f" ({pc_name})" if pc_name and pc_name != host_label else "") + "\n"
            f"Дата: {when}\n"
            f"Всего: {len(rows)} (пользователей: {len(users)}, системных: {len(system)})\n"
            f"Программа SMB-дисков: {tool_line}\n\n"
            f"== Пользователи ==\n{table(users)}\n\n== Системные ==\n{table(system)}\n\n"
            f"== Смонтированные сетевые диски (CIFS) ==\n{disks}\n")


def build_delete_user_script(login, purge, me=""):
    """Удаление учётной записи. purge=True — вместе с домашней папкой и почтой (userdel -r)."""
    q = shlex.quote
    flag = "-r" if purge else ""
    return f"""rm -f -- "$0"
login={q(login)}; me={q(me)}; purge={1 if purge else 0}
[ "$login" = root ] && {{ echo "ОШИБКА: root удалять нельзя"; exit 3; }}
[ -n "$me" ] && [ "$login" = "$me" ] && {{ echo "ОШИБКА: это учётная запись текущего подключения"; exit 3; }}
uid=$(id -u "$login" 2>/dev/null) || {{ echo "ОШИБКА: учётная запись $login не найдена"; exit 2; }}
[ "$uid" -ge 1000 ] 2>/dev/null || {{ echo "ОШИБКА: $login — системная учётная запись (UID $uid), не удаляю"; exit 3; }}
home=$(getent passwd "$login" | cut -d: -f6)
echo "Удаление учётной записи $login (UID $uid), домашняя папка: ${{home:-—}}"
if [ "$purge" = 1 ]; then
  case "$home" in
    /home/?*) ;;
    *) echo "ОШИБКА: домашняя папка '$home' нестандартная — вместе с файлами не удаляю. Удалите без файлов."; exit 3;;
  esac
  others=$(getent passwd | awk -F: -v h="$home" -v l="$login" '$6==h && $1!=l {{print $1}}')
  [ -n "$others" ] && {{ echo "ОШИБКА: эту папку использует и $others — вместе с файлами не удаляю."; exit 3; }}
fi
echo "Завершаю сессии и процессы пользователя…"
loginctl terminate-user "$login" >/dev/null 2>&1 || true
pkill -KILL -u "$login" >/dev/null 2>&1 || true
sleep 1
# Сетевые диски пользователя отключаем ДО удаления: иначе удаление папки могло бы зайти в сетевую шару.
mnts() {{
  awk -v h="$home/" -v u="$uid" '{{mp=$2; gsub(/\\\\040/," ",mp);
    n=split($4,o,","); mu=""; for(i=1;i<=n;i++) if (o[i] ~ /^uid=/) mu=substr(o[i],5);
    if (index(mp,h)==1 || ($3 ~ /^(cifs|smb3)$/ && mu==u)) print mp}}' /proc/mounts
}}
list="$(mnts)"
if [ -n "$list" ]; then
  echo "Отключаю сетевые диски пользователя:"
  while IFS= read -r mp; do [ -n "$mp" ] || continue; echo "  $mp"; umount -l "$mp" >/dev/null 2>&1 || true; done <<< "$list"
  sleep 1
  left="$(mnts)"
  if [ -n "$left" ] && [ "$purge" = 1 ]; then
    echo "ОШИБКА: не удалось отключить: $left — вместе с файлами не удаляю (можно удалить без файлов)."
    exit 3
  fi
fi
userdel {flag} "$login"
rc=$?
if id "$login" >/dev/null 2>&1; then echo "ОШИБКА: учётная запись не удалена (код $rc)"; exit 1; fi
[ "$rc" = 0 ] || echo "Предупреждение: userdel вернул код $rc (часть файлов, например почтовый ящик, могла не удалиться)"
if [ "$purge" = 1 ]; then
  [ -e "$home" ] && echo "Внимание: папка $home осталась" || echo "Домашняя папка удалена"
else
  echo "Файлы в $home сохранены (владелец теперь без имени, UID $uid)"
fi
echo "Готово: учётная запись $login удалена"
"""


def build_kill_sessions_script(script=KILL_SESSIONS_SCRIPT_PATH, log=KILL_SESSIONS_LOG):
    """Запуск установленного скрипта завершения сессий. Остаётся активная графическая сессия
    (за монитором) и это SSH-подключение — иначе скрипт оборвал бы и наш терминал."""
    q = shlex.quote
    return """rm -f -- "$0"
SCRIPT=@SCRIPT@; LOG=@LOG@
echo "Сброс неактивных учётных записей (сессий)"
if [ ! -x "$SCRIPT" ]; then
  echo "НЕ УСТАНОВЛЕНО: на этом ПК нет $SCRIPT"
  echo "Установите install_mate_vnc_improved.sh (Быстрые команды -> Установить SH/DEB)."
  exit 3
fi
echo "Скрипт найден: $SCRIPT"
REAL=$(command -v loginctl) || { echo "ОШИБКА: loginctl не найден"; exit 1; }
anc=" "; p=$$
while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
  anc="$anc$p "
  p=$(awk '{sub(/.*\\) /, ""); print $2}' /proc/$p/stat 2>/dev/null)
done
mine=""; keep=""
for s in $("$REAL" list-sessions --no-legend | awk '{print $1}'); do
  leader=$("$REAL" show-session "$s" -p Leader --value 2>/dev/null)
  case "$anc" in *" $leader "*) mine="$mine $s";; esac
  act=$("$REAL" show-session "$s" -p Active --value 2>/dev/null)
  typ=$("$REAL" show-session "$s" -p Type --value 2>/dev/null)
  if [ -z "$keep" ] && [ "$act" = yes ] && { [ "$typ" = x11 ] || [ "$typ" = wayland ]; }; then keep="$s"; fi
done
if [ -z "$keep" ]; then
  echo "ОШИБКА: нет активной графической сессии (за монитором никто не вошёл). Ничего не завершаю."
  exit 4
fi
echo "Остаётся: сессия $keep ($("$REAL" show-session "$keep" -p Name --value)), ваше SSH-подключение:${mine:- не найдено}"
others=""
for s in $("$REAL" list-sessions --no-legend | awk '{print $1}'); do
  [ "$s" = "$keep" ] && continue
  case " $mine " in *" $s "*) continue;; esac
  others="$others $s"
  echo "  будет завершена: $s  $("$REAL" show-session "$s" -p Name --value 2>/dev/null)  TTY=$("$REAL" show-session "$s" -p TTY --value 2>/dev/null)"
done
if [ -z "$others" ]; then echo "Других сессий нет — завершать нечего."; exit 0; fi
shim=$(mktemp -d)
cat > "$shim/loginctl" <<SHIM
#!/bin/bash
case " \\$SSHC_SKIP " in *" \\$2 "*) [ "\\$1" = terminate-session ] && { echo "(пропускаю ваше SSH-подключение \\$2)" >&2; exit 0; };; esac
exec $REAL "\\$@"
SHIM
chmod +x "$shim/loginctl"
SSHC_SKIP="$mine" PATH="$shim:$PATH" "$SCRIPT" "$keep" quiet
rc=$?
rm -rf "$shim"
echo "--- журнал $LOG ---"
tail -n 12 "$LOG" 2>/dev/null
echo "--- сессии сейчас ---"
"$REAL" list-sessions --no-legend
if [ "$rc" = 0 ]; then echo "Готово."; else echo "ОШИБКА: скрипт вернул код $rc"; fi
exit $rc
""".replace("@SCRIPT@", q(script)).replace("@LOG@", q(log))



# --------------------------------------------------------------------------
# Вспомогательные окна и виджеты
# --------------------------------------------------------------------------
def center_on(win, parent, w, h):
    """Разместить окно по центру родителя (с учётом масштабирования CustomTkinter)."""
    try:
        parent.update_idletasks()
        s = win._get_window_scaling()
        cx = parent.winfo_rootx() + parent.winfo_width() / 2
        cy = parent.winfo_rooty() + parent.winfo_height() / 2
        x = max(0, int(cx / s - w / 2))
        y = max(0, int(cy / s - h / 2))
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:  # noqa: BLE001
        win.geometry(f"{w}x{h}")


def center_screen(win, w, h):
    try:
        s = win._get_window_scaling()
        x = max(0, int((win.winfo_screenwidth() / s - w) / 2))
        y = max(0, int((win.winfo_screenheight() / s - h) / 3))
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:  # noqa: BLE001
        win.geometry(f"{w}x{h}")


def bring_front(win):
    """Вынести окно поверх остальных. Окно, созданное в обработчике клика, в Windows иногда
    оказывается под главным окном (то забирает фокус после клика) — поднимаем несколько раз."""
    def up(topmost=False):
        try:
            win.deiconify()
            win.lift()
            if topmost:
                win.attributes("-topmost", True)
            win.focus_force()
        except tk.TclError:
            pass

    def drop():
        try:
            win.attributes("-topmost", False)
        except tk.TclError:
            pass
    win.after(30, lambda: up(True))
    win.after(250, up)
    win.after(700, up)
    win.after(900, drop)


def primary_button(master, text, command, kind="accent", **kw):
    colors = {
        "accent": (ACCENT, ACCENT_H, "#ffffff"),
        "warn": (WARN, WARN_H, "#1a1204"),
        "danger": (DANGER, DANGER_H, "#ffffff"),
        "ghost": ("transparent", SURFACE_HOVER, TEXT),
        "soft": (SURFACE2, SURFACE_HOVER, TEXT),
    }
    fg, hover, tc = colors[kind]
    extra = {"border_width": 1, "border_color": BORDER} if kind in ("ghost", "soft") else {}
    return ctk.CTkButton(
        master, text=text, command=command, fg_color=fg, hover_color=hover,
        text_color=tc, text_color_disabled=MUTED,
        corner_radius=kw.pop("corner_radius", 10),
        font=kw.pop("font", ui_font(13, "bold")), **extra, **kw,
    )


class ModalDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, w, h):
        super().__init__(parent, fg_color=BG)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result = None
        center_on(self, parent, w, h)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass


class ConfirmDialog(ModalDialog):
    def __init__(self, parent, title, text, ok_text="Да", kind="accent"):
        n_lines = sum(max(1, -(-len(l) // 50)) for l in text.split("\n"))
        super().__init__(parent, title, 440, min(620, 170 + n_lines * 19))
        ctk.CTkLabel(self, text=title, font=ui_font(18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=24, pady=(22, 4))
        ctk.CTkLabel(self, text=text, font=ui_font(13), text_color=MUTED, anchor="w",
                     justify="left", wraplength=390).pack(fill="x", padx=24)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=24, pady=20)
        primary_button(row, ok_text, self._ok, kind, width=130, height=38).pack(side="right")
        primary_button(row, "Отмена", self.destroy, "ghost", width=110, height=38).pack(
            side="right", padx=(0, 10))
        self.bind("<Return>", lambda e: self._ok())

    def _ok(self):
        self.result = True
        self.destroy()


def ask_confirm(parent, title, text, ok_text="Да", kind="accent"):
    dlg = ConfirmDialog(parent, title, text, ok_text, kind)
    parent.wait_window(dlg)
    return bool(dlg.result)


class OutputDialog(ModalDialog):
    def __init__(self, parent, title, text, ok=True):
        super().__init__(parent, title, 720, 480)
        self.text = text
        self.resizable(True, True)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 8))
        ctk.CTkLabel(head, text="●", text_color=OK if ok else DANGER,
                     font=ui_font(14)).pack(side="left")
        ctk.CTkLabel(head, text=title, font=ui_font(16, "bold"), text_color=TEXT).pack(
            side="left", padx=8)
        box = ctk.CTkTextbox(self, fg_color=TERM_BG, text_color=TERM_FG, corner_radius=10,
                             border_width=1, border_color=BORDER, wrap="none",
                             font=ctk.CTkFont(family=pick_mono(), size=12))
        box.pack(fill="both", expand=True, padx=20)
        box.insert("1.0", text if text else "(пустой вывод)")
        box.configure(state="disabled")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=14)
        primary_button(row, "Закрыть", self.destroy, "accent", width=110, height=36).pack(side="right")
        primary_button(row, "Копировать", lambda: self._copy(text), "ghost", width=120,
                       height=36).pack(side="right", padx=(0, 10))

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)


class Toast(ctk.CTkFrame):
    COLORS = {"ok": OK, "err": DANGER, "warn": WARN, "info": ACCENT}

    def __init__(self, master):
        super().__init__(master, corner_radius=12, fg_color=SURFACE2,
                         border_width=1, border_color=BORDER)
        self.bar = ctk.CTkFrame(self, width=5, height=10, corner_radius=3, fg_color=ACCENT)
        self.bar.pack(side="left", fill="y", padx=(10, 0), pady=10)
        self.label = ctk.CTkLabel(self, text="", font=ui_font(13), text_color=TEXT,
                                  justify="left", wraplength=380, anchor="w")
        self.label.pack(side="left", padx=(12, 16), pady=12)
        self._job = None

    def show(self, text, kind="info", ms=4500):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.bar.configure(fg_color=self.COLORS.get(kind, ACCENT))
        self.label.configure(text=text)
        self.place(relx=1.0, rely=0.0, x=-22, y=78, anchor="ne")
        self.lift()
        if ms:
            self._job = self.after(ms, self.hide)

    def hide(self):
        self._job = None
        self.place_forget()


# --------------------------------------------------------------------------
# Добавление своей быстрой команды
# --------------------------------------------------------------------------
class AddCommandDialog(ModalDialog):
    def __init__(self, parent):
        super().__init__(parent, "Новая команда", 460, 400)
        ctk.CTkLabel(self, text="Своя команда", font=ui_font(20, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=26, pady=(22, 12))
        ent = dict(height=40, corner_radius=10, fg_color=SURFACE, border_color=BORDER,
                   text_color=TEXT, font=ui_font(14))
        self.title_e = ctk.CTkEntry(self, placeholder_text="Название, например: Перезапустить Samba", **ent)
        self.title_e.pack(fill="x", padx=26, pady=(0, 10))
        self.cmd_e = ctk.CTkEntry(self, placeholder_text="Команда, например: systemctl restart smbd", **ent)
        self.cmd_e.pack(fill="x", padx=26)
        self.sudo_v = ctk.BooleanVar(value=True)
        self.conf_v = ctk.BooleanVar(value=False)
        for text, var in (("Выполнять через sudo", self.sudo_v),
                          ("Спрашивать подтверждение", self.conf_v)):
            ctk.CTkCheckBox(self, text=text, variable=var, font=ui_font(13), text_color=TEXT,
                            fg_color=ACCENT, hover_color=ACCENT_H, border_color=BORDER,
                            checkbox_width=18, checkbox_height=18).pack(anchor="w", padx=26, pady=(12, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=26, pady=22)
        primary_button(row, "Сохранить", self._ok, "accent", width=130, height=40).pack(side="right")
        primary_button(row, "Отмена", self.destroy, "ghost", width=110, height=40).pack(
            side="right", padx=(0, 10))
        self.after(200, self.title_e.focus_set)

    def _ok(self):
        title, cmd = self.title_e.get().strip(), self.cmd_e.get().strip()
        if not title:
            self.title_e.configure(border_color=DANGER)
            return
        if not cmd:
            self.cmd_e.configure(border_color=DANGER)
            return
        self.result = {"group": CUSTOM_GROUP, "title": title, "cmd": cmd,
                       "sudo": self.sudo_v.get(), "confirm": self.conf_v.get()}
        self.destroy()


# --------------------------------------------------------------------------
# Окно «Статический IP»
# --------------------------------------------------------------------------
class IpChangeDialog(ModalDialog):
    def __init__(self, term, host):
        super().__init__(term, "Статический IP", 480, 600)
        self.term = term
        self.conns = []
        self.pending = None

        ctk.CTkLabel(self, text="Статический IP", font=ui_font(22, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=26, pady=(22, 0))
        self.status = ctk.CTkLabel(self, text="Читаю текущие настройки…", font=ui_font(12),
                                   text_color=MUTED, anchor="w", justify="left", wraplength=420)
        self.status.pack(fill="x", padx=26, pady=(2, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=26, pady=(4, 22))
        self.apply_btn = primary_button(row, "Применить", self._ok, "accent", width=130, height=40)
        self.apply_btn.pack(side="right")
        self.apply_btn.configure(state="disabled")
        primary_button(row, "Отмена", self.destroy, "ghost", width=110, height=40).pack(
            side="right", padx=(0, 10))
        self.err = ctk.CTkLabel(self, text="", font=ui_font(12), text_color=DANGER, anchor="w",
                                justify="left", wraplength=420)
        self.err.pack(side="bottom", fill="x", padx=26)

        self.menu_var = ctk.StringVar(value="")
        self.menu = ctk.CTkOptionMenu(
            self, variable=self.menu_var, values=[""], command=self._on_pick, height=38,
            corner_radius=10, fg_color=SURFACE2, button_color=SURFACE_HOVER,
            button_hover_color=BORDER, text_color=TEXT, font=ui_font(13),
            dropdown_fg_color=SURFACE2, dropdown_hover_color=SURFACE_HOVER,
            dropdown_text_color=TEXT, dropdown_font=ui_font(13))

        self.entries = {}
        for key, caption, ph in (("ip", "IP-АДРЕС", "например, 192.168.1.60"),
                                 ("mask", "МАСКА ИЛИ ПРЕФИКС", "24 или 255.255.255.0"),
                                 ("gw", "ШЛЮЗ", "например, 192.168.1.1"),
                                 ("dns", "DNS (ЧЕРЕЗ ЗАПЯТУЮ)", "например, 192.168.1.1, 8.8.8.8")):
            cap = ctk.CTkLabel(self, text=caption, font=ui_font(10, "bold"), text_color=MUTED,
                               anchor="w")
            cap.pack(fill="x", padx=26, pady=(10, 2))
            if key == "ip":
                self.first_caption = cap
            e = ctk.CTkEntry(self, placeholder_text=ph, height=40, corner_radius=10,
                             fg_color=SURFACE, border_color=BORDER, text_color=TEXT,
                             font=ui_font(14))
            e.pack(fill="x", padx=26)
            e.configure(state="disabled")
            self.entries[key] = e
        self.bind("<Return>", lambda e: self._ok())

    # -- данные с ПК ------------------------------------------------------
    def set_info(self, info):
        err = info.get("error")
        if err == "nonm":
            msg = "На этом ПК не найден NetworkManager (nmcli): автосмена IP работает только с ним."
        elif err:
            msg = f"Не удалось прочитать настройки сети: {err}"
        elif not info.get("conns"):
            msg = "Нет активных сетевых соединений."
        else:
            msg = ""
        if msg:
            self.status.configure(text=msg, text_color=DANGER)
            return
        self.conns = info["conns"]
        if len(self.conns) > 1:
            names = [f"{c['name']} ({c['dev']})" for c in self.conns]
            self.menu.configure(values=names)
            self.menu_var.set(names[0])
            self.menu.pack(fill="x", padx=26, pady=(0, 2), before=self.first_caption)
        for e in self.entries.values():
            e.configure(state="normal")
        self._fill(self.conns[0])
        self.apply_btn.configure(state="normal")
        self.entries["ip"].focus_set()

    def _on_pick(self, label):
        for c in self.conns:
            if label == f"{c['name']} ({c['dev']})":
                self._fill(c)
                return

    def _current(self):
        if len(self.conns) > 1:
            for c in self.conns:
                if self.menu_var.get() == f"{c['name']} ({c['dev']})":
                    return c
        return self.conns[0]

    def _fill(self, c):
        method = {"auto": "DHCP", "manual": "статический"}.get(c["method"], c["method"] or "?")
        now = f"{c['addr']}/{c['prefix']}" if c["addr"] else "нет адреса"
        self.status.configure(
            text=f"«{c['name']}» ({c['dev']}) · сейчас {now} · {method}\n"
                 f"Шлюз: {c['gw'] or '—'} · DNS: {', '.join(c['dns']) or '—'}",
            text_color=MUTED)
        for key, val in (("ip", c["addr"]), ("mask", c["prefix"]), ("gw", c["gw"]),
                         ("dns", ", ".join(c["dns"]))):
            e = self.entries[key]
            e.delete(0, "end")
            if val:
                e.insert(0, val)
            e.configure(border_color=BORDER)
        self.err.configure(text="")

    # -- применение -------------------------------------------------------
    def _ok(self):
        if not self.conns or self.apply_btn.cget("state") == "disabled":
            return
        c = self._current()
        error, data = validate_static(self.entries["ip"].get(), self.entries["mask"].get(),
                                      self.entries["gw"].get(), self.entries["dns"].get())
        if error:
            self.err.configure(text=error)
            return
        self.err.configure(text="")
        if data["ip"] == c["addr"]:
            self._confirm(c, data, occupied=False)
            return
        self.pending = (c, data)
        self.apply_btn.configure(state="disabled", text="Проверяю адрес…")
        self.term.check_ip(data["ip"])

    def set_checked(self, ip, occupied):
        if not self.pending or self.pending[1]["ip"] != ip:
            return
        c, data = self.pending
        self.pending = None
        self.apply_btn.configure(state="normal", text="Применить")
        self._confirm(c, data, occupied)

    def _confirm(self, c, data, occupied):
        old = f"{c['addr']}/{c['prefix']}" if c["addr"] else "—"
        lines = [f"Соединение «{c['name']}»:", f"{old}  →  {data['ip']}/{data['prefix']}",
                 f"Шлюз: {data['gw'] or '—'}   DNS: {', '.join(data['dns']) or '—'}", "",
                 "Связь прервётся на несколько секунд, затем программа сама подключится "
                 "к новому адресу."]
        if occupied:
            lines += ["", f"⚠ Адрес {data['ip']} уже отвечает на ping — возможен конфликт "
                          "с другим устройством."]
        cur_net = network_of(c["addr"], c["prefix"]) if c["addr"] else ""
        new_net = network_of(data["ip"], data["prefix"])
        if cur_net and cur_net != new_net:
            lines += ["", f"⚠ Новый адрес в другой подсети (сейчас {cur_net}): подключиться к нему "
                          "с этого ПК может не получиться."]
        if not ask_confirm(self, "Сменить IP?", "\n".join(lines), "Применить", "warn"):
            self._grab()
            return
        self.destroy()
        self.term.apply_static_ip(c, data)



# --------------------------------------------------------------------------
# Окно «Быстрые команды»
# --------------------------------------------------------------------------
class QuickCommandsWindow(ctk.CTkToplevel):
    def __init__(self, parent, cfg, on_run):
        super().__init__(parent, fg_color=BG)
        self.cfg = cfg
        self.on_run = on_run
        self.title("Быстрые команды")
        self.transient(parent.winfo_toplevel())
        center_on(self, parent, 460, 600)
        self.minsize(400, 420)
        self.bind("<Escape>", lambda e: self.destroy())

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(18, 8))
        ctk.CTkLabel(top, text="Быстрые команды", font=ui_font(20, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(top, text="Выберите действие — оно выполнится на подключённом ПК",
                     font=ui_font(12), text_color=MUTED).pack(anchor="w", pady=(2, 0))

        self.search_v = ctk.StringVar()
        self.search_v.trace_add("write", lambda *a: self._render())
        self.search = ctk.CTkEntry(self, textvariable=self.search_v, height=40, corner_radius=10,
                                   placeholder_text="Поиск команды…", fg_color=SURFACE,
                                   border_color=BORDER, text_color=TEXT, font=ui_font(14))
        self.search.pack(fill="x", padx=18, pady=(4, 8))

        self.listbox = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                              scrollbar_button_color=BORDER,
                                              scrollbar_button_hover_color=MUTED)
        self.listbox.pack(fill="both", expand=True, padx=(12, 8), pady=(0, 4))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=18, pady=(4, 14))
        foot.grid_columnconfigure(0, weight=1)
        self.note = ctk.CTkLabel(foot, text="", font=ui_font(12), text_color=OK, anchor="w")
        self.note.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._note_job = None
        primary_button(foot, "+  Своя команда", self._add, "soft", height=38).grid(
            row=1, column=0, sticky="ew")

        self._render()
        self.after(200, self.search.focus_set)

    # -- список -----------------------------------------------------------
    def _all(self):
        order, buckets = [], {}

        def add(c):
            name = (c.get("group") or CUSTOM_GROUP).strip() or CUSTOM_GROUP
            key = name.lower()
            if key not in buckets:  # регистр группы не важен: «печать» = «Печать»
                buckets[key] = (name, [])
                order.append(key)
            c["group"] = buckets[key][0]
            buckets[key][1].append(c)

        for c in DEFAULT_COMMANDS:
            add(dict(c))
        for i, c in enumerate(self.cfg.custom_commands):
            c = dict(c)
            c["_custom_index"] = i
            add(c)
        return [c for key in order for c in buckets[key][1]]

    FIRST_BATCH, BATCH = 24, 10  # строк за один проход: окно не «замирает» на длинных списках

    def _render(self):
        self._render_id = getattr(self, "_render_id", 0) + 1
        for w in self.listbox.winfo_children():
            w.destroy()
        q = self.search_v.get().strip().lower()
        todo = [c for c in self._all()
                if not q or q in (c["title"] + " " + (c.get("group") or "")).lower()]
        if not todo:
            ctk.CTkLabel(self.listbox, text="Ничего не найдено", font=ui_font(13),
                         text_color=MUTED).pack(pady=30)
            return
        self._draw(todo, 0, None, self._render_id, self.FIRST_BATCH)

    def _draw(self, todo, start, last_group, rid, count):
        if rid != self._render_id:
            return  # список уже перерисован (поиск, удаление)
        try:
            for c in todo[start:start + count]:
                if c["group"] != last_group:
                    last_group = c["group"]
                    ctk.CTkLabel(self.listbox, text=last_group.upper(), font=ui_font(11, "bold"),
                                 text_color=MUTED, anchor="w").pack(fill="x", padx=8, pady=(12, 2))
                self._row(c)
        except tk.TclError:
            return  # окно закрыли
        if start + count < len(todo):
            self.after(15, self._draw, todo, start + count, last_group, rid, self.BATCH)

    def _row(self, c):
        row = ctk.CTkFrame(self.listbox, fg_color=SURFACE2, corner_radius=10, cursor="hand2")
        row.pack(fill="x", padx=4, pady=3)
        title = ctk.CTkLabel(row, text=c["title"], font=ui_font(14, "bold"), text_color=TEXT,
                             anchor="w")
        title.pack(fill="x", padx=14, pady=(9, 0))
        tags = []
        if c.get("sudo"):
            tags.append("sudo")
        if c.get("confirm"):
            tags.append("с подтверждением")
        first = c["cmd"].split("\n")
        cmd_line = first[0] if len(first[0]) <= 90 else first[0][:89] + "…"
        if len(first) > 1:
            cmd_line += f"  (+{len(first) - 1} стр.)"
        desc_text = cmd_line + ("   ·   " + ", ".join(tags) if tags else "")
        desc = ctk.CTkLabel(row, text=desc_text, font=ctk.CTkFont(family=pick_mono(), size=11),
                            text_color=MUTED, anchor="w")
        desc.pack(fill="x", padx=14, pady=(0, 9))

        def enter(_e=None):
            row.configure(fg_color=SURFACE_HOVER)

        def leave(e):
            w = row.winfo_containing(e.x_root, e.y_root)
            if w is None or not str(w).startswith(str(row)):
                row.configure(fg_color=SURFACE2)

        for w in (row, title, desc):
            w.bind("<Enter>", enter, add="+")
            w.bind("<Leave>", leave, add="+")
            w.bind("<Button-1>", lambda e, c=c: self._pick(c), add="+")

        if "_custom_index" in c:
            ctk.CTkButton(row, text="✕", width=26, height=26, corner_radius=8,
                          fg_color="transparent", hover_color=DANGER, text_color=MUTED,
                          font=ui_font(12, "bold"),
                          command=lambda i=c["_custom_index"]: self._delete(i)
                          ).place(relx=1.0, x=-10, rely=0.5, anchor="e")

    # -- действия ---------------------------------------------------------
    def _pick(self, c):
        self.destroy()
        self.on_run(c)

    def _delete(self, index):
        self.cfg.remove_custom(index)
        self._render()

    def flash(self, text, color=OK, ms=5000):
        if self._note_job:
            self.after_cancel(self._note_job)
        self.note.configure(text=text, text_color=color)
        self._note_job = self.after(ms, lambda: self.note.configure(text=""))

    def _add(self):
        dlg = AddCommandDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.cfg.add_custom(dlg.result)
            self.search_v.set("")
            self._render()


# --------------------------------------------------------------------------
# Виджет терминала (эмуляция через pyte)
# --------------------------------------------------------------------------
class TerminalView(tk.Text):
    def __init__(self, master, on_input, on_resize):
        self.font = tkfont.Font(family=pick_mono(), size=11)
        super().__init__(
            master, font=self.font, bg=TERM_BG, fg=TERM_FG, insertwidth=0, wrap="none",
            padx=6, pady=4, borderwidth=0, highlightthickness=0, cursor="xterm",
            undo=False, exportselection=False, selectbackground="#264f78",
            width=10, height=4,
        )
        self.on_input = on_input
        self.on_resize = on_resize
        self.cols, self.rows = 100, 30

        self.screen = pyte.HistoryScreen(self.cols, self.rows, history=5000)
        self.screen.write_process_input = lambda d: self.on_input(d.encode("utf-8"))
        self.stream = pyte.ByteStream(self.screen)
        self._tags = set()
        self._resize_job = None

        self.configure(state="disabled")
        self.bind("<Key>", self._on_key)
        self.bind("<Button-1>", lambda e: self.focus_set(), add="+")
        self.bind("<ButtonRelease-1>", lambda e: self.copy_selection(), add="+")
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                            activeforeground="#ffffff", bd=0, font=(UI_FONT, 10))
        self.bind("<Button-3>", self._on_menu)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda e: self._scroll(+1))   # Linux
        self.bind("<Button-5>", lambda e: self._scroll(-1))
        self.bind("<Configure>", self._on_configure)
        self.bind("<<Paste>>", lambda e: (self.paste(), "break")[1])

    # -- вывод ------------------------------------------------------------
    def feed(self, data: bytes):
        self.stream.feed(data)

    def reset(self):
        self.screen.reset()
        self.render()

    def _tag(self, fg, bg):
        name = f"t_{fg}_{bg}"
        if name not in self._tags:
            self.tag_configure(name, foreground=fg, background=bg)
            self._tags.add(name)
        return name

    def render(self):
        scr = self.screen
        hist = getattr(scr, "history", None)
        at_bottom = hist is None or hist.position == hist.size
        show_cursor = at_bottom and not scr.cursor.hidden
        cy, cx = scr.cursor.y, scr.cursor.x

        self.configure(state="normal")
        self.delete("1.0", "end")
        for y in range(scr.lines):
            row = scr.buffer[y]
            run_tag, run_text = None, []
            for x in range(scr.columns):
                ch = row[x]
                if ch.data == "":  # вторая половина широкого символа
                    continue
                reverse = ch.reverse
                if show_cursor and y == cy and x == cx:
                    reverse = not reverse
                fg = to_color(ch.fg, TERM_FG, ch.bold)
                bg = to_color(ch.bg, TERM_BG)
                if reverse:
                    fg, bg = bg, fg
                tag = self._tag(fg, bg)
                if tag != run_tag and run_text:
                    self.insert("end", "".join(run_text), run_tag)
                    run_text = []
                run_tag = tag
                run_text.append(ch.data)
            if run_text:
                self.insert("end", "".join(run_text), run_tag)
            if y < scr.lines - 1:
                self.insert("end", "\n")
        self.configure(state="disabled")

    # -- ввод -------------------------------------------------------------
    def _on_key(self, e):
        ks = e.keysym
        if ks in MODIFIER_KEYS:
            return "break"
        ctrl = bool(e.state & 0x4)
        shift = bool(e.state & 0x1)
        low = ks.lower()
        if ctrl and (low == "insert" or (shift and low == "c")):
            self.copy_selection()
            return "break"
        if (ctrl and low == "v") or (shift and low == "insert"):
            self.paste()
            return "break"
        if ks in KEYMAP:
            self.on_input(KEYMAP[ks].encode("utf-8"))
        elif e.char:
            self.on_input(e.char.encode("utf-8"))
        return "break"

    def has_selection(self):
        try:
            return bool(self.get("sel.first", "sel.last"))
        except tk.TclError:
            return False

    def _on_menu(self, e):
        """Правая кнопка: меню «Копировать / Вставить» (раньше сразу вставляла текст)."""
        self.focus_set()
        try:
            has_clip = bool(self.clipboard_get())
        except tk.TclError:
            has_clip = False
        m = self.menu
        m.delete(0, "end")
        m.add_command(label="Копировать", command=self.copy_selection,
                      state="normal" if self.has_selection() else "disabled")
        m.add_command(label="Вставить", command=self.paste, state="normal" if has_clip else "disabled")
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        return "break"

    def copy_selection(self):
        try:
            text = self.get("sel.first", "sel.last")
        except tk.TclError:
            return
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        text = text.replace("\r\n", "\n").replace("\n", "\r")
        if text:
            self.on_input(text.encode("utf-8"))

    def _scroll(self, direction):
        if direction > 0:
            self.screen.prev_page()
        else:
            self.screen.next_page()
        self.render()

    def _on_wheel(self, e):
        self._scroll(+1 if e.delta > 0 else -1)

    # -- размер -----------------------------------------------------------
    def _on_configure(self, _e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self.apply_size)

    def apply_size(self):
        self._resize_job = None
        if not self.winfo_viewable():  # вкладка скрыта: размер не настоящий, PTY не трогаем
            return
        cw = max(1, self.font.measure("0"))
        lh = max(1, self.font.metrics("linespace"))
        cols = max(20, (self.winfo_width() - 14) // cw)
        rows = max(5, (self.winfo_height() - 10) // lh)
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.screen.resize(rows, cols)
            self.on_resize(cols, rows)
            self.render()


# --------------------------------------------------------------------------
# Передача файлов по SFTP
# --------------------------------------------------------------------------
class TransferCancelled(Exception):
    pass


def human_size(n):
    if n is None:
        return ""
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    v, i = float(n), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{int(v)} {units[i]}" if i == 0 else f"{v:.1f}".replace(".", ",") + f" {units[i]}"


def sort_entries(items):
    return sorted(items, key=lambda x: (not x[1], x[0].casefold()))


def local_drives():
    """Буквы дисков. В Windows — через GetLogicalDrives: не обращается к дискам, поэтому
    не зависает на отключённых сетевых дисках и пустых картридерах."""
    if os.name == "nt":
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]
        except Exception:  # noqa: BLE001
            pass
    return [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]


def list_local(path):
    """-> [(имя, папка?, размер, время)]; пустой путь в Windows — список дисков."""
    if path == "" and os.name == "nt":
        return [(d, True, None, None) for d in local_drives()]
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st = e.stat()
                isdir = e.is_dir()
                out.append((e.name, isdir, None if isdir else st.st_size, st.st_mtime))
            except OSError:
                continue
    return sort_entries(out)


def local_parent(path):
    """Родительская папка на этом ПК; в Windows выше корня диска — список дисков (пустой путь)."""
    if path == "":
        return ""
    parent = os.path.dirname(path.rstrip("\\/"))
    if parent == path.rstrip("\\/") or (os.name == "nt" and len(path.rstrip("\\/")) <= 2):
        return "" if os.name == "nt" else path
    return parent or path


def files_tree_style(widget):
    st = ttk.Style(widget)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    fam = UI_FONT
    st.configure("Files.Treeview", background=SURFACE, fieldbackground=SURFACE,
                 foreground=TEXT, rowheight=28, borderwidth=0, relief="flat", font=(fam, 11))
    st.configure("Files.Treeview.Heading", background=SURFACE2, foreground=MUTED,
                 relief="flat", borderwidth=0, font=(fam, 10, "bold"))
    st.configure("Files.Treeview", bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE)
    st.map("Files.Treeview", bordercolor=[("focus", SURFACE)], lightcolor=[("focus", SURFACE)],
           background=[("selected", "#1f3a66")], foreground=[("selected", "#ffffff")])
    st.map("Files.Treeview.Heading", background=[("active", SURFACE_HOVER)])


def list_remote(sftp, path):
    """-> [(имя, папка?, размер, время, ссылка?)]; ссылка на папку считается папкой."""
    out = []
    for a in sftp.listdir_attr(path):
        mode = a.st_mode or 0
        islink = pystat.S_ISLNK(mode)
        isdir = pystat.S_ISDIR(mode)
        if islink:
            try:
                isdir = pystat.S_ISDIR(sftp.stat(posixpath.join(path, a.filename)).st_mode)
            except OSError:
                pass
        out.append((a.filename, isdir, None if isdir else a.st_size, a.st_mtime, islink))
    return sorted(out, key=lambda x: (not x[1], x[0].casefold()))


def local_tree(root):
    """[(относительный путь с '/', папка?, размер)] — сама папка/файл первой, родители раньше детей."""
    root = root.rstrip("\\/") or root
    base = os.path.basename(root) or root
    if not os.path.isdir(root):
        return [(base, False, os.path.getsize(root))]
    out = [(base, True, 0)]
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        prefix = base if rel == "." else base + "/" + rel.replace(os.sep, "/")
        dns.sort()
        for d in dns:
            out.append((prefix + "/" + d, True, 0))
        for f in sorted(fns):
            try:
                size = os.path.getsize(os.path.join(dp, f))
            except OSError:
                size = 0
            out.append((prefix + "/" + f, False, size))
    return out


def remote_tree(sftp, root, links=None):
    """То же для удалённого пути. Символические ссылки на папки не раскрываем (защита от
    циклов и от удаления чужого): их полные пути складываем в links (если список передан)."""
    root = root.rstrip("/") or root
    base = posixpath.basename(root) or root
    st = sftp.stat(root)
    if not pystat.S_ISDIR(st.st_mode):
        return [(base, False, st.st_size or 0)]
    out = [(base, True, 0)]
    stack = [(root, base)]
    while stack:
        rp, rel = stack.pop()
        for name, isdir, size, _t, islink in list_remote(sftp, rp):
            r = rel + "/" + name
            full = posixpath.join(rp, name)
            if isdir and islink:
                if links is not None:
                    links.append(full)
            elif isdir:
                out.append((r, True, 0))
                stack.append((full, r))
            else:
                out.append((r, False, size or 0))
    return out


def remote_kind(sftp, path):
    """'dir' | 'file' | None"""
    try:
        return "dir" if pystat.S_ISDIR(sftp.stat(path).st_mode) else "file"
    except (IOError, OSError):
        return None


def _connection_alive(sftp):
    try:
        return sftp.get_channel().get_transport().is_active()
    except Exception:  # noqa: BLE001
        return False


def _new_result():
    return {"files": 0, "bytes": 0, "skipped": 0, "errors": [], "cancelled": False}


def run_upload(sftp, local_paths, remote_dir, overwrite, cancel, progress):
    """Загрузить файлы/папки в remote_dir. Файл сначала пишется во временное имя и переименовывается,
    поэтому оборванная передача не оставляет «половинчатых» файлов под настоящим именем."""
    items = []
    for lp in local_paths:
        parent = os.path.dirname(lp.rstrip("\\/"))
        for rel, isdir, size in local_tree(lp):
            items.append((os.path.join(parent, *rel.split("/")), rel, isdir, size))
    total = sum(s for _f, _r, d, s in items if not d)
    done, res, bad_dirs = 0, _new_result(), []
    for full, rel, isdir, size in items:
        if cancel.is_set():
            res["cancelled"] = True
            break
        if any(rel.startswith(b + "/") for b in bad_dirs):
            res["skipped"] += 1
            continue
        rpath = posixpath.join(remote_dir, rel)
        try:
            if isdir:
                if remote_kind(sftp, rpath) != "dir":
                    sftp.mkdir(rpath)
                continue
            if not overwrite and remote_kind(sftp, rpath) is not None:
                res["skipped"] += 1
                done += size
                progress(done, total, rel)
                continue
            part = rpath + ".sshc-part"

            def cb(x, _t, base=done, name=rel):
                if cancel.is_set():
                    raise TransferCancelled()
                progress(base + x, total, name)
            try:
                sftp.put(full, part, callback=cb)
                try:
                    sftp.posix_rename(part, rpath)
                except (IOError, OSError, AttributeError):
                    if remote_kind(sftp, rpath) is not None:
                        sftp.remove(rpath)
                    sftp.rename(part, rpath)
            except BaseException:
                try:
                    sftp.remove(part)
                except Exception:  # noqa: BLE001
                    pass
                raise
            done += size
            res["files"] += 1
            res["bytes"] += size
            progress(done, total, rel)
        except TransferCancelled:
            res["cancelled"] = True
            break
        except Exception as e:  # noqa: BLE001
            if not _connection_alive(sftp):
                raise ConnectionError("соединение потеряно") from e
            res["errors"].append(f"{rel}: {e}")
            if isdir:
                bad_dirs.append(rel)
    return res


def run_download(sftp, remote_paths, local_dir, overwrite, cancel, progress):
    items, links = [], []
    for rp in remote_paths:
        parent = posixpath.dirname(rp.rstrip("/"))
        for rel, isdir, size in remote_tree(sftp, rp, links):
            items.append((posixpath.join(parent, rel) if parent else rel, rel, isdir, size))
    total = sum(s for _f, _r, d, s in items if not d)
    done, res, bad_dirs = 0, _new_result(), []
    for ln in links:
        res["errors"].append(f"{ln}: ссылка на папку пропущена")
    for full, rel, isdir, size in items:
        if cancel.is_set():
            res["cancelled"] = True
            break
        if any(rel.startswith(b + "/") for b in bad_dirs):
            res["skipped"] += 1
            continue
        lpath = os.path.join(local_dir, *rel.split("/"))
        try:
            if isdir:
                os.makedirs(lpath, exist_ok=True)
                continue
            if not overwrite and os.path.exists(lpath):
                res["skipped"] += 1
                done += size
                progress(done, total, rel)
                continue
            part = lpath + ".sshc-part"

            def cb(x, _t, base=done, name=rel):
                if cancel.is_set():
                    raise TransferCancelled()
                progress(base + x, total, name)
            try:
                sftp.get(full, part, callback=cb)
                os.replace(part, lpath)
            except BaseException:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise
            done += size
            res["files"] += 1
            res["bytes"] += size
            progress(done, total, rel)
        except TransferCancelled:
            res["cancelled"] = True
            break
        except Exception as e:  # noqa: BLE001
            if not _connection_alive(sftp):
                raise ConnectionError("соединение потеряно") from e
            res["errors"].append(f"{rel}: {e}")
            if isdir:
                bad_dirs.append(rel)
    return res


def remote_delete(sftp, path):
    """Удалить файл, ссылку или папку целиком. Ссылки удаляются сами, цель не трогаем."""
    if pystat.S_ISLNK(sftp.lstat(path).st_mode):
        sftp.remove(path)
        return
    links = []
    tree = remote_tree(sftp, path, links)
    parent = posixpath.dirname(path.rstrip("/"))
    for ln in links:
        sftp.remove(ln)
    for rel, isdir, _s in reversed(tree):
        full = posixpath.join(parent, rel) if parent else rel
        if isdir:
            sftp.rmdir(full)
        else:
            sftp.remove(full)


def local_delete(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


class TextPromptDialog(ModalDialog):
    def __init__(self, parent, title, label, initial="", ok_text="ОК"):
        super().__init__(parent, title, 440, 210)
        ctk.CTkLabel(self, text=title, font=ui_font(18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=24, pady=(20, 2))
        ctk.CTkLabel(self, text=label, font=ui_font(12), text_color=MUTED,
                     anchor="w").pack(fill="x", padx=24)
        self.entry = ctk.CTkEntry(self, height=40, corner_radius=10, fg_color=SURFACE,
                                  border_color=BORDER, text_color=TEXT, font=ui_font(14))
        self.entry.pack(fill="x", padx=24, pady=(8, 0))
        self.entry.insert(0, initial)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=24, pady=18)
        primary_button(row, ok_text, self._ok, "accent", width=120, height=38).pack(side="right")
        primary_button(row, "Отмена", self.destroy, "ghost", width=110, height=38).pack(
            side="right", padx=(0, 10))
        self.bind("<Return>", lambda e: self._ok())
        self.after(200, lambda: (self.entry.focus_set(), self.entry.select_range(0, "end")))

    def _ok(self):
        text = self.entry.get().strip()
        if text:
            self.result = text
            self.destroy()


def ask_text(parent, title, label, initial="", ok_text="ОК"):
    dlg = TextPromptDialog(parent, title, label, initial, ok_text)
    parent.wait_window(dlg)
    return dlg.result


PLACE_LABELS = (("DESKTOP", "Рабочий стол"), ("DOCUMENTS", "Документы"), ("DOWNLOAD", "Загрузки"),
                ("PICTURES", "Изображения"), ("MUSIC", "Музыка"), ("VIDEOS", "Видео"))
PLACE_FALLBACKS = {"DESKTOP": ("Рабочий стол", "Desktop"), "DOCUMENTS": ("Документы", "Documents"),
                   "DOWNLOAD": ("Загрузки", "Downloads"), "PICTURES": ("Изображения", "Pictures"),
                   "MUSIC": ("Музыка", "Music"), "VIDEOS": ("Видео", "Videos")}
_KNOWN_FOLDER_IDS = {"DESKTOP": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "DOCUMENTS": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
                     "DOWNLOAD": "374DE290-123F-4565-9164-39C4925E467B", "PICTURES": "33E28130-4E1E-4676-835A-98395C3BC3BB",
                     "MUSIC": "4BD8D571-6D19-48D3-BE97-422220080E43", "VIDEOS": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}


def windows_known_folder(key):
    """Путь закреплённой папки Проводника Windows (учитывает перенос в OneDrive) или None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]
        u = uuid.UUID(_KNOWN_FOLDER_IDS[key])
        g = GUID.from_buffer_copy(u.bytes_le)
        buf = ctypes.c_wchar_p()
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = (ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p))
        if fn(ctypes.byref(g), 0, None, ctypes.byref(buf)) != 0:
            return None
        path = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return path
    except Exception:  # noqa: BLE001
        return None


def local_places():
    """Быстрый доступ на этом ПК: домашняя папка, Рабочий стол, Документы, Загрузки… (+ «Этот компьютер»/диски)."""
    home = os.path.expanduser("~")
    out = [("Домашняя папка", home)]
    for key, label in PLACE_LABELS:
        path = windows_known_folder(key)
        if not path or not os.path.isdir(path):
            path = next((os.path.join(home, n) for n in PLACE_FALLBACKS[key]
                         if os.path.isdir(os.path.join(home, n))), None)
        if path and os.path.normcase(path) != os.path.normcase(home):
            out.append((label, path))
    if os.name == "nt":
        out.append(("Этот компьютер", ""))
        for d in local_drives():
            out.append((f"Диск {d.rstrip(chr(92)).rstrip('/')}", d))
    else:
        out.append(("Файловая система /", "/"))
    return out


def remote_places(sftp, home):
    """Быстрый доступ на удалённом ПК: ~/.config/user-dirs.dirs (как в файловом менеджере), иначе обычные имена."""
    def isdir(path):
        try:
            return pystat.S_ISDIR(sftp.stat(path).st_mode)
        except Exception:  # noqa: BLE001
            return False
    xdg = {}
    try:
        with sftp.open(posixpath.join(home, ".config", "user-dirs.dirs")) as f:
            text = f.read().decode("utf-8", "replace")
        for m in re.finditer(r'^XDG_(\w+)_DIR="([^"]*)"', text, re.M):
            xdg[m.group(1)] = m.group(2).replace("$HOME", home)
    except Exception:  # noqa: BLE001
        pass
    out = [("Домашняя папка", home)]
    for key, label in PLACE_LABELS:
        cands = ([xdg[key]] if key in xdg else []) + [posixpath.join(home, n) for n in PLACE_FALLBACKS[key]]
        path = next((c for c in cands if c.rstrip("/") != home.rstrip("/") and isdir(c)), None)
        if path:
            out.append((label, path))
    out.append(("Файловая система /", "/"))
    return out


class PlacesList(ctk.CTkFrame):
    """Узкая колонка быстрых папок (как «Быстрый доступ» в Проводнике): щелчок открывает папку в соседней панели."""

    def __init__(self, master, title, window, side):
        super().__init__(master, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        self.window, self.side, self.places, self._silent, self._marked = window, side, [], False, None
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, font=ui_font(11, "bold"), text_color=MUTED, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        holder = tk.Frame(self, bg=SURFACE)
        holder.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, show="tree", selectmode="browse", style="Files.Treeview")
        self.tree.column("#0", width=170, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._select)

    def set_places(self, places):
        self.places = list(places)
        self._silent = True
        self.tree.delete(*self.tree.get_children())
        for i, (label, _path) in enumerate(self.places):
            self.tree.insert("", "end", iid=str(i), text="  " + label)
        self._silent = False

    def _select(self, _e=None):
        if self._silent:
            return
        sel = self.tree.selection()
        if sel and sel[0] != self._marked:          # _marked — подсветка программой, это не щелчок
            self._marked = None
            self.window.go(self.side, self.places[int(sel[0])][1])

    def mark(self, path):
        """Подсветить пункт, если открытая папка совпадает с ним (без перехода)."""
        self._silent = True
        self._marked = None
        if self.side == "remote" and not path:
            self._silent = False
            return
        try:
            norm = (lambda x: os.path.normcase(x.rstrip("\\/")) if self.side == "local" else x.rstrip("/"))
            hit = [str(i) for i, (_l, p) in enumerate(self.places) if p != "" and norm(p) == norm(path)] \
                or [str(i) for i, (_l, p) in enumerate(self.places) if p == path]
            if hit:
                self._marked = hit[0]
                self.tree.selection_set(hit[0])
            else:
                self.tree.selection_remove(self.tree.selection())
        except tk.TclError:
            pass
        self._silent = False


def enable_file_drop(root_widget, callback, on_enter=None, on_leave=None):
    """Приём файлов, брошенных из Проводника (tkdnd через пакет tkinterdnd2).
    Регистрируются окно и ВСЕ его вложенные виджеты — файл можно бросить на любое место окна.
    -> (словарь, "") при успехе или (None, причина). Без библиотеки программа работает, просто без этой функции."""
    try:
        import types
        from tkinterdnd2 import TkinterDnD, DND_FILES
    except Exception as e:  # noqa: BLE001
        return None, f"нет библиотеки tkinterdnd2 ({type(e).__name__})"
    try:
        TkinterDnD._require(root_widget)
    except Exception as e:  # noqa: BLE001
        return None, f"не загрузилась tkdnd: {e}"
    holder = {}

    def on_drop(event):
        try:
            paths = [os.path.normpath(p) for p in root_widget.tk.splitlist(event.data)]
            if on_leave:
                on_leave()
            if paths:
                callback(paths)
        except Exception:  # noqa: BLE001
            pass
        return getattr(event, "action", "copy")
    holder["on_drop"] = on_drop

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)
    ok, last_err = 0, ""
    for w in walk(root_widget):
        try:
            for name in ("drop_target_register", "dnd_bind", "_dnd_bind", "_subst_format_dnd",
                         "_subst_format_str_dnd", "_substitute_dnd"):
                attr = getattr(TkinterDnD.DnDWrapper, name)
                setattr(w, name, types.MethodType(attr, w) if callable(attr) else attr)
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", on_drop)
            if on_enter:
                w.dnd_bind("<<DropEnter>>", lambda e: (on_enter(), getattr(e, "action", "copy"))[1])
            if on_leave:
                w.dnd_bind("<<DropLeave>>", lambda e: (on_leave(), None)[1])
            ok += 1
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
    holder["count"] = ok
    if not ok:
        return None, f"не удалось зарегистрировать окно ({last_err})"
    return holder, ""


class FilePane(ctk.CTkFrame):
    """Одна панель: путь, кнопки навигации, список, кнопки операций."""

    def __init__(self, master, title, window, side, with_ops=True, tags_fn=None):
        super().__init__(master, fg_color=SURFACE, corner_radius=14, border_width=1,
                         border_color=BORDER)
        self.window, self.side, self.path = window, side, ""
        self.tags_fn = tags_fn
        self.entries = []
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.title_lbl = ctk.CTkLabel(self, text=title, font=ui_font(11, "bold"), text_color=MUTED,
                                      anchor="w")
        self.title_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 6))
        bar.grid_columnconfigure(2, weight=1)
        self.btn_up = primary_button(bar, "↑", self._up, "soft", width=36, height=34,
                                     font=ui_font(15, "bold"))
        self.btn_up.grid(row=0, column=0)
        self.btn_ref = primary_button(bar, "⟳", lambda: window.go(side, self.path), "soft",
                                      width=36, height=34, font=ui_font(15, "bold"))
        self.btn_ref.grid(row=0, column=1, padx=(6, 6))
        self.path_entry = ctk.CTkEntry(bar, height=34, corner_radius=8, fg_color=SURFACE2,
                                       border_color=BORDER, text_color=TEXT, font=ui_font(12))
        self.path_entry.grid(row=0, column=2, sticky="ew")
        self.path_entry.bind("<Return>", lambda e: window.go(side, self.path_entry.get().strip()))

        holder = tk.Frame(self, bg=SURFACE)
        holder.grid(row=2, column=0, sticky="nsew", padx=(10, 10))
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, columns=("name", "size", "date"), show="headings",
                                 selectmode="extended", style="Files.Treeview")
        for col, text, w, anchor in (("name", "Имя", 220, "w"), ("size", "Размер", 90, "e"),
                                     ("date", "Изменён", 150, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "name"),
                             minwidth=w if col != "name" else 100)
        self.tree.tag_configure("dir", foreground="#7cb2ff")
        sb = ctk.CTkScrollbar(holder, command=self.tree.yview, width=12)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)
        self.tree.bind("<BackSpace>", lambda e: self._up())
        self.tree.bind("<F5>", lambda e: window.go(side, self.path))
        self._drag = None
        if with_ops:
            self.tree.bind("<Delete>", lambda e: window.delete(side))
            self.tree.bind("<F2>", lambda e: window.rename(side))
            self.menu = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                                activeforeground="#ffffff", bd=0, font=(UI_FONT, 10))
            self.tree.bind("<Button-3>", self._on_menu)
            self.tree.bind("<ButtonPress-1>", self._drag_press)
            self.tree.bind("<B1-Motion>", self._drag_move)
            self.tree.bind("<ButtonRelease-1>", self._drag_release)

        ops = ctk.CTkFrame(self, fg_color="transparent")
        ops.grid(row=3, column=0, sticky="ew", padx=10, pady=(8, 10))
        self.ops = []
        for text, cmd in (("Новая папка", lambda: window.mkdir(side)),
                          ("Переименовать", lambda: window.rename(side)),
                          ("Удалить", lambda: window.delete(side))) if with_ops else ():
            b = primary_button(ops, text, cmd, "ghost", width=110, height=32,
                               font=ui_font(12, "bold"))
            b.pack(side="left", padx=(0, 6))
            self.ops.append(b)
        if with_ops and side == "local":
            primary_button(ops, "Открыть", self.open_in_explorer, "soft", width=90, height=32,
                           font=ui_font(12, "bold")).pack(side="left", padx=(0, 6))
        self.count_lbl = ctk.CTkLabel(ops, text="", font=ui_font(11), text_color=MUTED)
        self.count_lbl.pack(side="right")

    # -- содержимое ---------------------------------------------------------
    def show(self, path, entries):
        self.path = path
        self.entries = entries
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, path if path or self.side != "local" else "Мой компьютер")
        self.tree.delete(*self.tree.get_children())
        for e in entries:
            name, isdir, size, mtime = e[:4]
            try:
                date = time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime)) if mtime else ""
            except (OSError, OverflowError, ValueError):
                date = ""  # в Windows localtime падает на «странных» датах файлов
            self.tree.insert("", "end", iid=name, values=(("▸ " if isdir else "   ") + name,
                                                          human_size(size), date),
                             tags=(("dir",) if isdir else ()) + (
                                 self.tags_fn(name, isdir) if self.tags_fn else ()))
        nd = sum(1 for e in entries if e[1])
        self.count_lbl.configure(text=f"папок: {nd} · файлов: {len(entries) - nd}")

    def open_in_explorer(self):
        """Открыть показанную сейчас папку в Проводнике (на Linux — в файловом менеджере)."""
        path = self.path
        try:
            if os.name == "nt":
                if path and os.path.isdir(path):
                    os.startfile(path)                                    # noqa: S606
                else:                                                      # «Мой компьютер»
                    subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
            else:
                subprocess.Popen(["xdg-open", path or "/"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            self.window.set_status(f"Не удалось открыть папку: {e}", "err")

    def selected(self):
        return list(self.tree.selection())

    def is_dir(self, name):
        return any(e[0] == name and e[1] for e in self.entries)

    def names(self):
        return {e[0] for e in self.entries}

    def _on_double(self, _e=None):
        sel = self.selected()
        if len(sel) == 1 and self.is_dir(sel[0]):
            self.window.go(self.side, self.window.join(self.side, self.path, sel[0]))
        elif len(sel) == 1 and isinstance(self.window, FileManagerWindow):
            self.window.open_text(self.side, sel[0], False)
        elif len(sel) == 1 and hasattr(self.window, "on_file_open"):
            self.window.on_file_open(sel[0])
        return "break"

    def _up(self):
        self.window.go(self.side, self.window.parent_of(self.side, self.path))

    # -- правая кнопка: Копировать / Переименовать / Удалить ----------------------
    def _on_menu(self, e):
        row = self.tree.identify_row(e.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        if not self.tree.selection():
            return "break"
        w = self.window
        enabled = (not getattr(w, "transferring", False)) and (self.side == "local" or w.sftp is not None)
        n = len(self.tree.selection())
        target = "на ПК" if self.side == "local" else "на этот компьютер"
        m = self.menu
        m.delete(0, "end")
        m.add_command(label=f"Копировать {target}", command=lambda: w.copy_across(self.side),
                      state="normal" if enabled else "disabled")
        sel = list(self.tree.selection())
        if len(sel) == 1 and not self.is_dir(sel[0]) and sel[0].lower().endswith(INSTALL_EXTS):
            m.add_command(label="Установить" + (" (загрузить на ПК и установить)" if self.side == "local" else ""),
                          command=lambda: w.install_selected(self.side), state="normal" if enabled else "disabled")
            m.add_separator()
        can_open = (n == 1 and not self.is_dir(sel[0]) and not sel[0].lower().endswith(OPEN_EXCLUDE_EXTS)
                    and (self.side == "local" or w.sftp is not None))
        m.add_command(label="Просмотр", command=lambda: w.open_text(self.side, sel[0], False),
                      state="normal" if can_open else "disabled")
        m.add_command(label="Редактировать", command=lambda: w.open_text(self.side, sel[0], True),
                      state="normal" if can_open else "disabled")
        m.add_command(label="Переименовать", command=lambda: w.rename(self.side),
                      state="normal" if enabled and n == 1 else "disabled")
        m.add_command(label="Удалить", command=lambda: w.delete(self.side),
                      state="normal" if enabled else "disabled")
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        return "break"

    # -- перетаскивание мышью в другую панель ----------------------------------------
    def _drag_press(self, e):
        row = self.tree.identify_row(e.y)
        self._drag = None
        if not row or self.tree.identify_region(e.x, e.y) == "heading":
            return None
        if e.state & 0x0005:            # Shift / Ctrl — обычное выделение
            return None
        if row in self.tree.selection():
            # не сбрасываем многострочное выделение: возможно, это начало перетаскивания
            self._drag = {"x": e.x_root, "y": e.y_root, "row": row, "active": False, "over": None}
            return "break"
        return None

    def _other_pane(self):
        return self.window.right if self.side == "local" else self.window.left

    def _drag_move(self, e):
        d = self._drag
        if not d:
            return None
        if not d["active"]:
            if abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) < 8:
                return "break"
            d["active"] = True
            try:
                self.tree.configure(cursor="hand2")      # без плавающих окон: на Windows они могли подвешивать программу
            except tk.TclError:
                pass
            n = len(self.tree.selection())
            self.window.set_status(
                f"Перетащите {n} шт. на соседнюю панель, чтобы скопировать" if n > 1 else
                "Перетащите на соседнюю панель, чтобы скопировать" + (
                    " или за окно — на Рабочий стол" if self.side == "remote" else ""), "info")
        over = self._pane_at(e.x_root, e.y_root) is self._other_pane()
        out = self._outside_window(e.x_root, e.y_root)
        if out != d.get("out"):
            d["out"] = out
            if self.side == "remote":
                self.window.set_status("Отпустите здесь — файлы будут скачаны на Рабочий стол" if out else
                                       "Перетащите на левую панель или за окно (на Рабочий стол)", "info")
        if over != d["over"]:
            d["over"] = over
            try:
                self._other_pane().configure(border_color=ACCENT if over else BORDER)
            except tk.TclError:
                pass
        return "break"

    def _outside_window(self, x_root, y_root):
        """Курсор за пределами окон программы (например, над Рабочим столом Windows)."""
        try:
            return self.winfo_containing(x_root, y_root) is None
        except (tk.TclError, KeyError):
            return False

    def _pane_at(self, x_root, y_root):
        w = self.winfo_containing(x_root, y_root)
        while w is not None:
            if w in (self.window.left, self.window.right):
                return w
            w = getattr(w, "master", None)
        return None

    def _drag_release(self, e):
        d, self._drag = self._drag, None
        if not d:
            return None
        try:
            self.tree.configure(cursor="")
            self._other_pane().configure(border_color=BORDER)
        except tk.TclError:
            pass
        if not d["active"]:                 # обычный щелчок по выделенному — оставляем только его
            self.tree.selection_set(d["row"])
            return "break"
        if self._pane_at(e.x_root, e.y_root) is self._other_pane():
            # действие — уже после отпускания кнопки: диалог (замена файлов) внутри обработчика мыши
            # при захваченной мыши на Windows зависал
            self.after(80, lambda: self.window.copy_across(self.side))
        elif self.side == "remote" and self._outside_window(e.x_root, e.y_root):
            self.after(80, self.window.download_to_desktop)
        else:
            self.window.set_status("Готово", "ok")
        return "break"

    def set_enabled(self, enabled):
        for b in [self.btn_up, self.btn_ref, *self.ops]:
            b.configure(state="normal" if enabled else "disabled")


EDIT_MAX_BYTES = 3 * 1024 * 1024      # больше — открываем во внешней программе, а не в редакторе
EXTERNAL_MAX_BYTES = 500 * 1024 * 1024
OPEN_EXCLUDE_EXTS = (".deb",)         # пакеты не открываем: их устанавливают (меню → Установить)


class TooBigForEditor(Exception):
    pass


def launch_default(path):
    """Открыть файл программой по умолчанию (Windows — как двойной щелчок в Проводнике)."""
    if os.name == "nt":
        os.startfile(path)                                                  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



def decode_text_file(data):
    """Байты -> (текст с \\n, кодировка, перевод строки) или None, если файл не текстовый."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            enc = "utf-16"
            text = data.decode(enc)
        except UnicodeDecodeError:
            return None
    else:
        if b"\x00" in data[:8192]:
            return None
        enc = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            for enc in ("cp1251", "latin-1"):
                try:
                    text = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    nl = "\r\n" if b"\r\n" in data else "\n"
    return text.replace("\r\n", "\n"), enc, nl


def encode_text_file(text, enc, nl):
    return text.replace("\n", nl).encode(enc)


def read_file_limited(client, side, path, limit=EDIT_MAX_BYTES):
    """Прочитать файл целиком (отдельным каналом SFTP, не мешая передаче файлов)."""
    if side == "local":
        if os.path.getsize(path) > limit:
            raise TooBigForEditor()
        with open(path, "rb") as f:
            return f.read()
    sftp = client.open_sftp()
    try:
        if sftp.stat(path).st_size > limit:
            raise TooBigForEditor()
        with sftp.open(path, "rb") as f:
            return f.read()
    finally:
        try:
            sftp.close()
        except Exception:  # noqa: BLE001
            pass


def write_file_bytes(client, side, path, data):
    """Записать поверх существующего файла (владелец и права сохраняются)."""
    if side == "local":
        with open(path, "wb") as f:
            f.write(data)
        return
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "wb") as f:
            f.write(data)
    except OSError as e:
        if getattr(e, "errno", None) == 13 or "ermission" in str(e):
            raise PermissionError(str(e))
        raise
    finally:
        try:
            sftp.close()
        except Exception:  # noqa: BLE001
            pass


def write_file_sudo(client, user, password, path, data):
    """Записать с правами root: во временный файл, затем cat > файл (владелец и права не меняются)."""
    tmp = "/tmp/.sshc_edit_" + secrets.token_hex(6)
    sftp = client.open_sftp()
    try:
        with sftp.open(tmp, "wb") as f:
            f.write(data)
    finally:
        try:
            sftp.close()
        except Exception:  # noqa: BLE001
            pass
    q = shlex.quote
    code, text = exec_remote(client, user, password,
                             f"cat {q(tmp)} > {q(path)}; rc=$?; rm -f {q(tmp)}; exit $rc", True, timeout=30)
    if code != 0:
        raise OSError(text or f"код выхода {code}")


class TextEditorWindow(ctk.CTkToplevel):
    """Просмотр и правка текстового файла (на удалённом ПК или на этом компьютере)."""

    def __init__(self, fm, side, path, edit=False):
        super().__init__(fm, fg_color=BG)
        self.fm, self.side, self.path = fm, side, path
        self.term = fm.term
        self.enc, self.nl = "utf-8", "\n"
        self.editing = False
        self.loaded = False
        self.busy = False
        self.want_edit = edit
        self.q = queue.Queue()
        name = posixpath.basename(path) if side == "remote" else os.path.basename(path)
        self.name = name
        self.title(f"{name} — {'на ' + self.term.host if side == 'remote' else 'этот компьютер'}")
        self.minsize(560, 360)
        center_on(self, fm, 900, 640)
        self.protocol("WM_DELETE_WINDOW", self._close)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        self.title_lbl = ctk.CTkLabel(head, text=name, font=ui_font(16, "bold"), text_color=TEXT, anchor="w")
        self.title_lbl.pack(side="left")
        self.info = ctk.CTkLabel(head, text="Загрузка…", font=ui_font(11), text_color=MUTED)
        self.info.pack(side="right")
        ctk.CTkLabel(self, text=path, font=ui_font(11), text_color=MUTED, anchor="w").pack(
            fill="x", padx=16)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(8, 6))
        self.find_entry = ctk.CTkEntry(bar, placeholder_text="Найти (Ctrl+F)", width=240, height=30,
                                       fg_color=SURFACE2, border_color=BORDER, text_color=TEXT)
        self.find_entry.pack(side="left")
        self.find_entry.bind("<Return>", lambda e: self._find())
        primary_button(bar, "Найти", self._find, "ghost", width=80, height=30).pack(side="left", padx=6)

        self.box = ctk.CTkTextbox(self, fg_color=TERM_BG, text_color=TERM_FG, corner_radius=10,
                                  border_width=1, border_color=BORDER, wrap="none", undo=True,
                                  font=ctk.CTkFont(family=pick_mono(), size=12))
        self.box.pack(fill="both", expand=True, padx=16)
        self.box.tag_config("found", background=ACCENT, foreground="#ffffff")
        self.box.configure(state="disabled")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=12)
        self.status = ctk.CTkLabel(foot, text="", font=ui_font(12), text_color=MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        primary_button(foot, "Закрыть", self._close, "ghost", width=100, height=34).pack(side="right")
        self.btn_save = primary_button(foot, "Сохранить", self.save, "accent", width=120, height=34,
                                       state="disabled")
        self.btn_save.pack(side="right", padx=(0, 8))
        self.btn_edit = primary_button(foot, "Редактировать", lambda: self._set_editing(True), "soft",
                                       width=140, height=34, state="disabled")
        self.btn_edit.pack(side="right", padx=(0, 8))

        for seq in ("<Control-s>", "<Control-S>", "<Control-Cyrillic_yeru>", "<Control-Cyrillic_YERU>"):
            self.bind(seq, lambda e: (self.save(), "break")[1])
        for seq in ("<Control-f>", "<Control-F>", "<Control-Cyrillic_a>", "<Control-Cyrillic_A>"):
            self.bind(seq, lambda e: (self.find_entry.focus_set(), "break")[1])
        self.box._textbox.bind("<<Modified>>", self._on_modified)
        self.bind("<Escape>", lambda e: self._close())

        threading.Thread(target=self._load_worker, daemon=True).start()
        self.after(60, self._poll)
        self.after(200, self._focus)

    def _focus(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    # -- загрузка / сохранение в рабочем потоке ------------------------------------
    def _load_worker(self):
        try:
            self.q.put(("loaded", read_file_limited(self.term.client, self.side, self.path)))
        except TooBigForEditor:
            self.q.put(("external", None))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"Не удалось открыть: {e}"))

    def _save_worker(self, data, sudo):
        try:
            if sudo:
                write_file_sudo(self.term.client, self.term.user, self.term.password, self.path, data)
            else:
                write_file_bytes(self.term.client, self.side, self.path, data)
            self.q.put(("saved", len(data)))
        except PermissionError:
            self.q.put(("need_sudo", data))
        except Exception as e:  # noqa: BLE001
            self.q.put(("save_error", f"Не удалось сохранить: {e}"))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                getattr(self, "_on_" + kind)(val)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.after(60, self._poll)
        except tk.TclError:
            pass

    def _say(self, text, kind="info"):
        color = {"ok": OK, "err": DANGER, "warn": WARN}.get(kind, MUTED)
        self.status.configure(text=text, text_color=color)

    def _on_loaded(self, data):
        res = decode_text_file(data)
        if res is None:
            self._on_external(None)
            return
        text, self.enc, self.nl = res
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        self.box._textbox.edit_reset()
        self.box._textbox.edit_modified(False)
        self.loaded = True
        self.box.configure(state="disabled")
        n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        self.info.configure(text=f"{self.enc.upper().replace('-SIG', ' (BOM)')} · "
                                 f"{'CRLF' if self.nl == chr(13) + chr(10) else 'LF'} · "
                                 f"строк: {n} · {human_size(len(data))}")
        self.btn_edit.configure(state="normal")
        self._say("Режим просмотра. Нажмите «Редактировать», чтобы править." if not self.want_edit else "")
        if self.want_edit:
            self._set_editing(True)

    def _on_external(self, _v):
        """Не текст или слишком большой — открываем программой по умолчанию."""
        self.loaded = False
        fm, side, path = self.fm, self.side, self.path
        self.destroy()
        fm.open_external(side, path)

    def _on_error(self, msg):
        self.info.configure(text="")
        self._say(msg, "err")

    def _set_editing(self, on):
        if not self.loaded:
            return
        self.editing = on
        self.box.configure(state="normal" if on else "disabled")
        self.btn_edit.configure(state="disabled" if on else "normal")
        self._refresh_save()
        if on:
            self.box.focus_set()
            self._say("Режим правки. Ctrl+S — сохранить.")

    def _modified(self):
        return self.loaded and bool(self.box._textbox.edit_modified())

    def _on_modified(self, _e=None):
        try:
            self._refresh_save()
        except tk.TclError:
            pass

    def _refresh_save(self):
        mod = self._modified()
        self.btn_save.configure(state="normal" if (self.editing and mod and not self.busy) else "disabled")
        self.title_lbl.configure(text=self.name + (" •" if mod else ""))

    def save(self):
        if not (self.editing and self._modified()) or self.busy:
            return
        text = self.box.get("1.0", "end-1c")
        try:
            data = encode_text_file(text, self.enc, self.nl)
        except UnicodeEncodeError:
            if not ask_confirm(self, "Кодировка не подходит",
                               f"В тексте есть символы, которых нет в кодировке {self.enc.upper()}.\n\n"
                               "Сохранить файл в UTF-8?", "Сохранить в UTF-8"):
                return
            self.enc = "utf-8"
            data = encode_text_file(text, self.enc, self.nl)
        self._start_save(data, False)

    def _start_save(self, data, sudo):
        self.busy = True
        self._refresh_save()
        self._say("Сохраняю…")
        threading.Thread(target=self._save_worker, args=(data, sudo), daemon=True).start()

    def _on_saved(self, n):
        self.busy = False
        self.box._textbox.edit_modified(False)
        self._refresh_save()
        self._say(f"Сохранено ({human_size(n)})", "ok")
        try:
            self.fm.refresh_both()
        except Exception:  # noqa: BLE001
            pass

    def _on_save_error(self, msg):
        self.busy = False
        self._refresh_save()
        self._say(msg, "err")

    def _on_need_sudo(self, data):
        self.busy = False
        self._refresh_save()
        if self.side == "remote" and self.term.user != "root" and ask_confirm(
                self, "Нет прав на запись",
                f"Пользователь {self.term.user} не может записать этот файл.\n\n"
                "Сохранить с правами root (sudo)? Владелец и права файла не изменятся.",
                "Сохранить с sudo", "warn"):
            self._start_save(data, True)
        else:
            self._say("Нет прав на запись в этот файл", "err")

    # -- поиск ----------------------------------------------------------------------
    def _find(self):
        needle = self.find_entry.get()
        t = self.box._textbox
        t.tag_remove("found", "1.0", "end")
        if not needle or not self.loaded:
            return
        start = t.index("insert +1c") if t.tag_ranges("sel") else t.index("insert")
        pos = t.search(needle, start, "end", nocase=True) or t.search(needle, "1.0", "end", nocase=True)
        if not pos:
            self._say("Не найдено", "warn")
            return
        end = f"{pos}+{len(needle)}c"
        t.tag_add("found", pos, end)
        t.mark_set("insert", pos)
        t.see(pos)
        self._say("")

    def _close(self):
        if self._modified() and not ask_confirm(self, "Закрыть без сохранения?",
                                                "В файле есть несохранённые изменения.",
                                                "Закрыть без сохранения", "danger"):
            return
        self.destroy()


class FileManagerWindow(ctk.CTkToplevel):
    """Две панели (этот ПК / удалённый ПК) и передача файлов по SFTP отдельным каналом."""

    def __init__(self, term, local_start=None):
        super().__init__(term, fg_color=BG)
        self.term = term
        self.title(f"Файлы — {term.user}@{term.host}")
        center_on(self, term.winfo_toplevel(), 1480, 720)
        self.minsize(1180, 540)
        self.q = queue.Queue()
        self.jobs = queue.Queue()
        self.sftp = None
        self.closing = False
        self.transferring = False
        self.cancel = threading.Event()
        self.t_start = 0.0
        self._last_progress = 0.0
        self.remote_home = "/"
        self.local_start = local_start or os.path.expanduser("~")
        self._style()

        self.grid_columnconfigure((1, 3), weight=1)
        self.grid_columnconfigure((0, 2), weight=0, minsize=190)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=56)
        head.grid(row=0, column=0, columnspan=4, sticky="ew")
        head.pack_propagate(False)
        ctk.CTkLabel(head, text="Передача файлов", font=ui_font(16, "bold"),
                     text_color=TEXT).pack(side="left", padx=18)
        self.status = ctk.CTkLabel(head, text="Открываю SFTP…", font=ui_font(12),
                                   text_color=MUTED, anchor="e")
        self.status.pack(side="right", padx=18, fill="x", expand=True)

        # четыре колонки: папки Windows | содержимое | папки Linux | содержимое
        self.places_l = PlacesList(self, "ЭТОТ КОМПЬЮТЕР", self, "local")
        self.places_l.grid(row=1, column=0, sticky="nsew", padx=(14, 6), pady=14)
        self.left = FilePane(self, "ЭТОТ КОМПЬЮТЕР", self, "local")
        self.left.grid(row=1, column=1, sticky="nsew", padx=(0, 8), pady=14)
        self.places_r = PlacesList(self, f"{term.user}@{term.host}", self, "remote")
        self.places_r.grid(row=1, column=2, sticky="nsew", padx=(8, 6), pady=14)
        self.right = FilePane(self, f"{term.user}@{term.host}", self, "remote")
        self.right.grid(row=1, column=3, sticky="nsew", padx=(0, 14), pady=14)
        self.places_l.set_places(local_places())

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=4, pady=(0, 12))
        self.btn_up_files = primary_button(actions, "Загрузить на ПК  →", self.upload, "accent",
                                           width=170, height=40)
        self.btn_down_files = primary_button(actions, "←  Скачать на этот компьютер", self.download, "accent",
                                             width=230, height=40)
        self.btn_install = primary_button(actions, "Установить DEB / SH", self.install_selected, "warn",
                                          width=180, height=40, font=ui_font(12, "bold"))
        self.btn_up_files.pack(side="left", padx=6)
        self.btn_down_files.pack(side="left", padx=6)
        self.btn_install.pack(side="left", padx=6)
        self.pending_install = None

        self.bar_box = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1,
                                    border_color=BORDER)
        self.bar_lbl = ctk.CTkLabel(self.bar_box, text="", font=ui_font(12), text_color=TEXT,
                                    anchor="w")
        self.bar_lbl.pack(fill="x", padx=16, pady=(10, 4))
        row = ctk.CTkFrame(self.bar_box, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.bar = ctk.CTkProgressBar(row, height=8, progress_color=ACCENT, fg_color=BORDER)
        self.bar.pack(side="left", fill="x", expand=True)
        self.bar.set(0)
        self.btn_cancel = primary_button(row, "Отмена", self._cancel, "danger", width=100, height=32)
        self.btn_cancel.pack(side="left", padx=(14, 0))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.set_busy_ui(True)
        self._drop_hook = None
        self.after(300, self._enable_os_drop)
        self.left.show(self.local_start, [])
        threading.Thread(target=self._worker, daemon=True).start()
        self.jobs.put(lambda: self._do_open(self.term.client))
        self.after(40, self._pump)
        bring_front(self)

    def _style(self):
        files_tree_style(self)

    def _enable_os_drop(self):
        """Принимать файлы, брошенные в окно из Проводника."""
        if self.closing:
            return
        def enter():
            try:
                self.right.configure(border_color=ACCENT)
            except tk.TclError:
                pass

        def leave():
            try:
                self.right.configure(border_color=BORDER)
            except tk.TclError:
                pass
        self._drop_hook, why = enable_file_drop(
            self, lambda paths: self.after(150, lambda: self.upload_paths(paths)), enter, leave)
        if self._drop_hook is None:
            self.drop_note = f"Перетаскивание из Проводника недоступно: {why}"
            try:
                with open(self.term.app.error_log, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} drag-and-drop: {why}\n")
            except Exception:  # noqa: BLE001
                pass
        else:
            self.drop_note = "Файлы можно перетащить из Проводника прямо в это окно — они загрузятся на ПК"
        if self.sftp is not None and not self.transferring:
            self.set_status(self.drop_note, "info" if self._drop_hook else "err")

    # -- пути ---------------------------------------------------------------
    def join(self, side, base, name):
        return posixpath.join(base, name) if side == "remote" else os.path.join(base, name)

    def parent_of(self, side, path):
        if side == "remote":
            return posixpath.dirname(path.rstrip("/")) or "/"
        return local_parent(path)

    # -- поток с SFTP-операциями --------------------------------------------
    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception as e:  # noqa: BLE001
                self.q.put(("fatal", f"{type(e).__name__}: {e}"))

    def _do_open(self, client):
        if client is None:
            self.q.put(("fatal", "нет подключения"))
            return
        try:
            self.sftp = client.open_sftp()
            self.remote_home = self.sftp.normalize(".")
        except Exception as e:  # noqa: BLE001
            self.sftp = None
            self.q.put(("fatal", f"не удалось открыть SFTP: {e}"))
            return
        self.q.put(("opened", self.remote_home))
        try:
            self.q.put(("places", remote_places(self.sftp, self.remote_home)))
        except Exception:  # noqa: BLE001
            self.q.put(("places", [("Домашняя папка", self.remote_home), ("Файловая система /", "/")]))

    def _do_list(self, side, path):
        try:
            if side == "local":
                entries = list_local(path)
            else:
                path = self.sftp.normalize(path or ".")
                entries = list_remote(self.sftp, path)
        except Exception as e:  # noqa: BLE001
            if side == "remote" and not _connection_alive(self.sftp):
                raise ConnectionError("соединение потеряно") from e
            self.q.put(("list_err", (side, path, str(e))))
            return
        self.q.put(("listed", (side, path, entries)))

    def go(self, side, path):
        if self.sftp is None and side == "remote":
            return
        self.jobs.put(lambda: self._do_list(side, path))

    def refresh_both(self):
        self.go("local", self.left.path)
        self.go("remote", self.right.path)

    # -- события в окно -------------------------------------------------------
    def _pump(self):
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.after(40, self._pump)

    def _handle(self, kind, payload):
        if kind == "opened":
            note = getattr(self, "drop_note", "")
            self.set_status(note or "Готово", ("err" if "недоступно" in note else "info") if note else "ok")
            self.set_busy_ui(False)
            self.go("local", self.local_start)
            self.go("remote", payload)
        elif kind == "places":
            self.places_r.set_places(payload)
            self.places_r.mark(self.right.path)
        elif kind == "listed":
            side, path, entries = payload
            (self.left if side == "local" else self.right).show(path, entries)
            (self.places_l if side == "local" else self.places_r).mark(path)
        elif kind == "list_err":
            side, path, msg = payload
            self.set_status(f"Не открыть «{path}»: {msg}", "err")
            pane = self.left if side == "local" else self.right
            pane.path_entry.delete(0, "end")
            pane.path_entry.insert(0, pane.path)
        elif kind == "progress":
            done, total, name = payload
            self.bar.set(done / total if total else 1)
            rate = done / max(0.2, time.time() - self.t_start)
            self.bar_lbl.configure(
                text=f"{name}   ·   {human_size(done)} из {human_size(total)}   ·   {human_size(rate)}/с")
        elif kind == "done":
            self._transfer_done(payload)
        elif kind == "op_done":
            text, kind2 = payload
            self.set_status(text, kind2)
            self.set_busy_ui(False)
            self.refresh_both()
        elif kind == "fatal":
            self.transferring = False
            self.bar_box.grid_remove()
            self.set_status(f"Ошибка: {payload}", "err")
            self.set_busy_ui(True, keep_nav=False)

    def set_status(self, text, kind="info"):
        color = {"ok": OK, "err": DANGER, "info": MUTED}.get(kind, MUTED)
        self.status.configure(text=text, text_color=color)

    def set_busy_ui(self, busy, keep_nav=False):
        state = "disabled" if busy else "normal"
        for b in (self.btn_up_files, self.btn_down_files, self.btn_install):
            b.configure(state=state)
        for pane in (self.left, self.right):
            pane.set_enabled(not busy)

    # -- загрузка / скачивание ----------------------------------------------
    def _conflicts(self, names, pane):
        existing = pane.names()
        return [n for n in names if n in existing]

    def upload(self):
        self._transfer("up")

    def download(self):
        self._transfer("down")

    def copy_across(self, side):
        """Копировать выделенное в соседнюю панель (слева → на ПК, справа → на этот компьютер)."""
        self._transfer("up" if side == "local" else "down")

    def open_text(self, side, name, edit):
        """Открыть текстовый файл в окне просмотра/правки (по двойному щелчку или из меню)."""
        pane = self._pane(side)
        if pane.is_dir(name):
            return
        if name.lower().endswith(OPEN_EXCLUDE_EXTS):
            self.set_status("Пакеты .deb не открываются: для установки — правая кнопка → «Установить»", "info")
            return
        if side == "remote" and (self.sftp is None or not self.term.client):
            self.set_status("Нет подключения к ПК", "err")
            return
        TextEditorWindow(self, side, self.join(side, pane.path, name), edit)

    def open_external(self, side, path):
        """Нетекстовый файл: на этом ПК — открыть сразу, на удалённом — скачать во временную папку и открыть."""
        name = posixpath.basename(path) if side == "remote" else os.path.basename(path)
        if side == "local":
            try:
                launch_default(path)
                self.set_status(f"Открыт: {name}", "ok")
            except Exception as e:  # noqa: BLE001
                self.set_status(f"Не удалось открыть {name}: {e}", "err")
            return
        self.set_status(f"Скачиваю {name} для открытия…", "info")
        res = queue.Queue()

        def work():
            try:
                tmpdir = tempfile.mkdtemp(prefix="sshconnect_")
                atexit.register(shutil.rmtree, tmpdir, True)
                tmp = os.path.join(tmpdir, name)
                sftp = self.term.client.open_sftp()
                try:
                    if sftp.stat(path).st_size > EXTERNAL_MAX_BYTES:
                        raise ValueError(f"файл больше {human_size(EXTERNAL_MAX_BYTES)}")
                    sftp.get(path, tmp)
                finally:
                    try:
                        sftp.close()
                    except Exception:  # noqa: BLE001
                        pass
                res.put(("ok", tmp))
            except Exception as e:  # noqa: BLE001
                res.put(("err", str(e)))

        def poll():
            try:
                kind, val = res.get_nowait()
            except queue.Empty:
                try:
                    self.after(100, poll)
                except tk.TclError:
                    pass
                return
            if kind == "err":
                self.set_status(f"Не удалось скачать {name}: {val}", "err")
                return
            try:
                launch_default(val)
                self.set_status(f"Открыта копия {name}. Изменения в ней на ПК не передаются", "ok")
            except Exception as e:  # noqa: BLE001
                self.set_status(f"Не удалось открыть {name}: {e}", "err")
        threading.Thread(target=work, daemon=True).start()
        self.after(100, poll)

    def download_to_desktop(self):
        """Выделенное справа (на ПК) — на Рабочий стол этого компьютера (перетаскивание за пределы окна)."""
        desk = windows_known_folder("DESKTOP")
        if not desk or not os.path.isdir(desk):
            home = os.path.expanduser("~")
            desk = next((os.path.join(home, n) for n in PLACE_FALLBACKS["DESKTOP"]
                         if os.path.isdir(os.path.join(home, n))), None)
        if not desk:
            self.set_status("Не нашёл папку «Рабочий стол» на этом компьютере", "err")
            return
        self._transfer("down", dest_override=desk)

    def upload_paths(self, paths):
        """Файлы и папки, брошенные в окно из Проводника, — на ПК в открытую справа папку."""
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        if self.transferring or self.sftp is None:
            self.set_status("Сейчас нельзя передавать файлы: подождите или дождитесь подключения", "err")
            return
        self._transfer("up", paths)

    def install_selected(self, side=None):
        """Установить .deb/.sh: выбранный справа (на ПК) или слева (сначала загрузим на ПК).
        side — из какой панели (меню по правой кнопке); без него смотрим на обе."""
        if self.transferring or self.sftp is None:
            return
        rall = self.right.selected() if side != "local" else []
        lall = self.left.selected() if side != "remote" else []
        rsel = [n for n in rall if not self.right.is_dir(n)]
        lsel = [n for n in lall if not self.left.is_dir(n)]
        upload_first = False
        if len(rsel) == 1 and len(rall) == 1 and rsel[0].lower().endswith(INSTALL_EXTS):
            remote = posixpath.join(self.right.path, rsel[0])
        elif (not rall and len(lsel) == 1 and len(lall) == 1
              and lsel[0].lower().endswith(INSTALL_EXTS)):
            if not self.right.path:
                self.set_status("Сначала откройте папку на ПК справа", "err")
                return
            remote = posixpath.join(self.right.path, lsel[0])
            upload_first = True
        else:
            self.set_status("Выделите один файл .sh или .deb: справа — он уже на ПК, "
                            "слева — сначала будет загружен в открытую справа папку", "err")
            return
        cmd = install_command(remote)
        is_deb = remote.lower().endswith(".deb")
        root_note = " с правами root" if self.term.user != "root" else ""
        plan = ("Файл будет загружен в папку справа, затем выполнено в терминале" if upload_first
                else "Будет выполнено в терминале") + f" на {self.term.host}{root_note}:\n\n{cmd}"
        sudo = True
        if is_deb:
            if not ask_confirm(self, "Установить пакет?", plan, "Установить"):
                return
        else:
            form = ask_form(self, "Установить скрипт?", plan,
                            [{"key": "sudo", "label": "Запускать с правами root (sudo)", "kind": "check",
                              "initial": True}], "Установить")
            if not form:
                return
            sudo = bool(form["sudo"])
        if upload_first:
            self.pending_install = (remote, cmd, sudo)
            self._transfer("up")
            if not self.transferring:
                self.pending_install = None
        else:
            self._run_install(remote, cmd, sudo)

    def _run_install(self, remote, cmd, sudo):
        self.set_status(f"Установка {posixpath.basename(remote)} запущена в терминале", "ok")
        self.term._install_chosen(remote, cmd, sudo)

    def _transfer(self, kind, local_paths=None, dest_override=None):
        if self.transferring or self.sftp is None:
            return
        src, dst = (self.left, self.right) if kind == "up" else (self.right, self.left)
        names = [os.path.basename(p.rstrip("/\\")) or p for p in local_paths] if local_paths else src.selected()
        if not names:
            self.set_status("Выделите файлы или папки в " + (
                "левой" if kind == "up" else "правой") + " панели", "err")
            return
        if dest_override is None and (kind == "up" and not self.right.path or kind == "down" and self.left.path == ""):
            self.set_status("Сначала откройте папку назначения", "err")
            return
        sides = ("local", "remote") if kind == "up" else ("remote", "local")
        paths = list(local_paths) if local_paths else [self.join(sides[0], src.path, n) for n in names]
        dest = dest_override or dst.path
        overwrite = True
        if dest_override:
            clash = [n for n in names if os.path.exists(os.path.join(dest_override, n))]
        else:
            clash = self._conflicts(names, dst)
        if clash:
            shown = ", ".join(clash[:4]) + (f" и ещё {len(clash) - 4}" if len(clash) > 4 else "")
            if not ask_confirm(self, "Заменить существующие?",
                               f"В папке назначения уже есть: {shown}.\n\n"
                               "Файлы с такими именами будут перезаписаны.", "Заменить", "warn"):
                return
        self.transferring = True
        self.cancel = threading.Event()
        self.t_start = time.time()
        self.set_busy_ui(True)
        self.bar.set(0)
        self.bar_lbl.configure(text="Подготовка…")
        self.bar_box.grid(row=3, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 14))
        self.set_status("Загрузка на ПК…" if kind == "up" else "Скачивание…", "info")
        cancel = self.cancel
        self.jobs.put(lambda: self._do_transfer(kind, paths, dest, overwrite, cancel))

    def _progress(self, done, total, name):
        now = time.time()
        if now - self._last_progress > 0.08 or done >= total:
            self._last_progress = now
            self.q.put(("progress", (done, total, name)))

    def _do_transfer(self, kind, paths, dest, overwrite, cancel):
        fn = run_upload if kind == "up" else run_download
        res = fn(self.sftp, paths, dest, overwrite, cancel, self._progress)
        self.q.put(("done", (kind, res)))

    def _cancel(self):
        self.cancel.set()
        self.btn_cancel.configure(state="disabled")
        self.bar_lbl.configure(text="Отменяю…")

    def _transfer_done(self, payload):
        kind, res = payload
        pending, self.pending_install = self.pending_install, None
        self.transferring = False
        self.btn_cancel.configure(state="normal")
        self.bar_box.grid_remove()
        self.set_busy_ui(False)
        what = "Загружено" if kind == "up" else "Скачано"
        text = f"{what}: {res['files']} файл(ов), {human_size(res['bytes'])}"
        if res["skipped"]:
            text += f" · пропущено {res['skipped']}"
        if res["cancelled"]:
            text = "Отменено. " + text
        kind2 = "err" if res["errors"] else ("info" if res["cancelled"] else "ok")
        if res["errors"]:
            text += f" · ошибок: {len(res['errors'])}"
        self.set_status(text, kind2)
        self.refresh_both()
        if res["errors"]:
            OutputDialog(self, f"Не удалось передать: {len(res['errors'])}",
                         "\n".join(res["errors"]), ok=False)
        elif pending and not res["cancelled"] and not res["skipped"]:
            self._run_install(*pending)

    # -- операции над файлами ---------------------------------------------------
    def _pane(self, side):
        return self.left if side == "local" else self.right

    def _op(self, label, fn):
        """Выполнить короткую операцию в рабочем потоке и обновить панели."""
        self.set_busy_ui(True)

        def job():
            try:
                fn()
            except ConnectionError:
                raise
            except Exception as e:  # noqa: BLE001
                self.q.put(("op_done", (f"{label}: {e}", "err")))
                return
            self.q.put(("op_done", (f"{label}: готово", "ok")))
        self.jobs.put(job)

    def mkdir(self, side):
        pane = self._pane(side)
        if side == "local" and pane.path == "":
            return
        name = ask_text(self, "Новая папка", "Имя папки:", "", "Создать")
        if not name:
            return
        path = self.join(side, pane.path, name)
        if side == "remote":
            self._op("Создание папки", lambda: self.sftp.mkdir(path))
        else:
            self._op("Создание папки", lambda: os.mkdir(path))

    def rename(self, side):
        pane = self._pane(side)
        sel = pane.selected()
        if len(sel) != 1:
            self.set_status("Выделите один элемент для переименования", "err")
            return
        new = ask_text(self, "Переименовать", f"Новое имя для «{sel[0]}»:", sel[0], "Готово")
        if not new or new == sel[0]:
            return
        if "/" in new or (side == "local" and "\\" in new):
            self.set_status("В имени не должно быть разделителей пути", "err")
            return
        old_p, new_p = self.join(side, pane.path, sel[0]), self.join(side, pane.path, new)
        if new in pane.names():
            self.set_status(f"«{new}» уже существует", "err")
            return
        if side == "remote":
            self._op("Переименование", lambda: self.sftp.rename(old_p, new_p))
        else:
            self._op("Переименование", lambda: os.rename(old_p, new_p))

    def delete(self, side):
        pane = self._pane(side)
        sel = pane.selected()
        if not sel or (side == "local" and pane.path == ""):
            return
        shown = ", ".join(sel[:5]) + (f" и ещё {len(sel) - 5}" if len(sel) > 5 else "")
        where = "с удалённого ПК" if side == "remote" else "с этого компьютера"
        if not ask_confirm(self, "Удалить?", f"Будет удалено {where} безвозвратно:\n{shown}",
                           "Удалить", "danger"):
            return
        paths = [self.join(side, pane.path, n) for n in sel]

        def run():
            for p in paths:
                if side == "remote":
                    remote_delete(self.sftp, p)
                else:
                    local_delete(p)
        self._op("Удаление", run)

    # -- связь ------------------------------------------------------------------
    def on_disconnect(self):
        self.sftp = None
        self.transferring = False
        self.cancel.set()
        self.bar_box.grid_remove()
        self.set_status("Соединение потеряно. Файлы откроются снова после переподключения", "err")
        self.set_busy_ui(True)

    def attach(self, client):
        """Сессия восстановлена — открыть SFTP заново."""
        self.cancel = threading.Event()
        self.set_status("Открываю SFTP…", "info")
        self.jobs.put(lambda: self._do_open(client))

    def close(self):
        self.closing = True
        self.cancel.set()
        sftp, self.sftp = self.sftp, None
        self.jobs.put(None)
        if sftp is not None:
            threading.Thread(target=lambda: sftp.close(), daemon=True).start()
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Установка .sh / .deb с самого ПК (быстрая команда «Установить SH/DEB»)
# --------------------------------------------------------------------------
INSTALL_EXTS = (".sh", ".deb")


def install_command(path):
    """Команда установки для файла на удалённом ПК или None, если тип не поддерживается.
    .deb — через apt-get (сам подтянет зависимости), запасной путь — dpkg + «apt-get -f install»;
    .sh — запуск из его папки (скрипты часто берут соседние файлы по относительным путям)."""
    ext = posixpath.splitext(path)[1].lower()
    q = shlex.quote
    if ext == ".deb":
        return (f"apt-get install -y {q(path)} || "
                f"{{ dpkg -i {q(path)}; apt-get install -f -y; }}")
    if ext == ".sh":
        folder, name = posixpath.split(path)
        return f"cd {q(folder or '/')} && bash {q('./' + name)}"
    return None


UM_LOGIN_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
UM_SEP = "\x1f"     # разделитель полей в данных для скриптов (не пробельный — пустые поля сохраняются)


def um_validate_login(login):
    login = login or ""
    if not login:
        return "Введите логин"
    if not UM_LOGIN_RE.fullmatch(login):
        return "Логин: строчная латиница, цифры, _ и -, начинается с буквы или _, до 32 символов"
    return None


def um_validate_password(pw):
    if not pw:
        return "Введите пароль"
    if any(ord(c) < 32 or ord(c) == 127 for c in pw):
        return "В пароле есть управляющие символы"
    return None


def um_validate_fullname(name):
    if any(c in name for c in ":,\n\r\t" + UM_SEP):
        return "В имени нельзя двоеточие, запятая и переносы строк"
    return None


def um_validate_new_user(v):
    err = um_validate_login(v.get("login", ""))
    if err:
        return err
    err = um_validate_password(v.get("password", ""))
    if err:
        return err
    if v.get("password") != v.get("password2"):
        return "Пароли не совпадают"
    return um_validate_fullname(v.get("name", ""))


def um_decode_text(data):
    """Файл из Блокнота Windows бывает в UTF-8 (с BOM) или cp1251."""
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def um_parse_import(text, existing=()):
    """Разбор txt: «логин<TAB>пароль» или «логин пробел пароль», по одной учётной записи в строке.
    Пустые строки и строки с # в начале пропускаются. 3-й столбец (после табуляции) — имя.
    -> [{"line", "login", "password", "name", "error", "exists"}]"""
    existing = set(existing)
    out, seen = [], set()
    text = (text or "").lstrip("﻿")
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name = ""
        if "\t" in line:
            parts = line.split("\t")
            while parts and not parts[-1].strip():
                parts.pop()
            login = parts[0].strip()
            pw = parts[1] if len(parts) > 1 else ""
            name = parts[2].strip() if len(parts) > 2 else ""
        else:
            parts = line.strip().split(None, 1)
            login = parts[0] if parts else ""
            pw = parts[1].rstrip() if len(parts) > 1 else ""
        err = um_validate_login(login) or (None if pw else "нет пароля") or um_validate_password(pw) or um_validate_fullname(name)
        if not err and login in seen:
            err = "логин повторяется в файле"
        if not err:
            seen.add(login)
        out.append({"line": no, "login": login, "password": pw, "name": name, "error": err,
                    "exists": (not err) and login in existing})
    return out


def um_parse_results(text):
    """Строки RES/LOG из вывода скриптов -> ([(логин, статус, сообщение)], [строки журнала])."""
    res, log = [], []
    for line in (text or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if parts[0] == "RES" and len(parts) >= 4:
            res.append((parts[1], parts[2], parts[3]))
        elif parts[0] == "LOG" and len(parts) >= 2:
            log.append(parts[1])
        elif line.strip() and parts[0] not in ("HOST", "SMBTOOL", "MNT", "ACC", "GRP"):
            log.append(line.strip())
    return res, log


# --------------------------------------------------------------------------
# Скрипты (запускаются от root; секреты передаются только через stdin)
# --------------------------------------------------------------------------
UM_LIST_SCRIPT = r"""now=" $(who 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ') $(loginctl list-users --no-legend 2>/dev/null | awk '{print $2}' | tr '\n' ' ') "
printf 'HOST\t%s\n' "$(hostname 2>/dev/null)"
t=""; [ -d /opt/astra-smb-mounter ] && t="astra"; [ -d /opt/mate_smb_tools ] && t="${t:+$t,}mate"
printf 'SMBTOOL\t%s\n' "$t"
awk '$3 ~ /^(cifs|smb3)$/ {mp=$2; src=$1; gsub(/\\040/," ",mp); gsub(/\\040/," ",src); u=""; n=split($4,o,","); for(i=1;i<=n;i++) if (o[i] ~ /^uid=/) u=substr(o[i],5); printf "MNT\t%s\t%s\t%s\n", u, mp, src}' @MOUNTS@ 2>/dev/null
getent group | awk -F: '{printf "GRP\t%s\t%s\t%s\n", $1, $3, $4}'
isroot=0; [ "$(id -u 2>/dev/null)" = 0 ] && isroot=1
getent passwd | while IFS=: read -r name _ uid gid gecos home shell; do
  gecos=${gecos%%,*}; gecos=${gecos//$'\t'/ }
  grp=$(id -nG "$name" 2>/dev/null | tr ' ' ',')
  case "$now" in *" $name "*) on=1;; *) on=0;; esac
  case ",$grp," in *,sudo,*|*,astra-admin,*|*,wheel,*|*,admin,*) adm=1;; *) adm=0;; esac
  [ "$uid" = 0 ] && adm=1
  if [ "$isroot" = 1 ]; then st=$(passwd -S "$name" 2>/dev/null | awk '{print $2}'); st=${st:-?}; else st="?"; fi
  printf 'ACC\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$uid" "$gid" "$gecos" "$home" "$shell" "$grp" "$adm" "$on" "$st"
done
"""

# общие функции: отчёт, журнал, отключение сетевых дисков пользователя
_UM_PRELUDE = r"""res() { printf 'RES\t%s\t%s\t%s\n' "$1" "$2" "$3"; }
say() { printf 'LOG\t%s\n' "$*"; }
setpw() {
  # обычный путь — chpasswd (политика паролей PAM); если политика не принимает пароль (например A1234b),
  # задаём его заранее посчитанным хешем: chpasswd -e политику PAM не проверяет
  local l="$1" p="$2" h
  printf '%s:%s\n' "$l" "$p" | chpasswd >/dev/null 2>&1 && return 0
  h=$(printf '%s' "$p" | openssl passwd -6 -stdin 2>/dev/null)
  [ -n "$h" ] || h=$(PW="$p" python3 -c 'import crypt,os;print(crypt.crypt(os.environ["PW"],crypt.mksalt(crypt.METHOD_SHA512)))' 2>/dev/null)
  [ -n "$h" ] || return 1
  printf '%s:%s\n' "$l" "$h" | chpasswd -e >/dev/null 2>&1
}
mnts() {
  awk -v hh="$1" -v u="$2" '{mp=$2; gsub(/\\040/," ",mp);
    n=split($4,o,","); mu=""; for(i=1;i<=n;i++) if (o[i] ~ /^uid=/) mu=substr(o[i],5);
    if ((hh != "" && (mp==hh || index(mp,hh"/")==1)) || ($3 ~ /^(cifs|smb3)$/ && mu==u)) print mp}' @MOUNTS@
}
unmount_user() {
  local list mp
  list="$(mnts "$1" "$2")"
  [ -n "$list" ] || return 0
  say "Отключаю сетевые диски:"
  while IFS= read -r mp; do
    [ -n "$mp" ] || continue
    say "  $mp"
    umount "$mp" >/dev/null 2>&1 || umount -l "$mp" >/dev/null 2>&1 || true
  done <<< "$list"
  sleep 1
  [ -z "$(mnts "$1" "$2")" ]
}
"""

_UM_DELETE_BODY = r"""
del_one() {
  local login="$1" purge="$2" uid home others tries rc flag msg
  if [ "$login" = root ]; then res "$login" fail "root удалять нельзя"; return; fi
  if [ "$login" = "$me" ]; then res "$login" fail "это учётная запись, под которой запущена программа"; return; fi
  uid=$(id -u "$login" 2>/dev/null) || { res "$login" fail "учётная запись не найдена"; return; }
  if ! [ "$uid" -ge 1000 ] 2>/dev/null; then res "$login" fail "системная учётная запись (UID $uid)"; return; fi
  home=$(getent passwd "$login" | cut -d: -f6)
  flag=""
  if [ "$purge" = 1 ]; then
    flag="-r"
    case "$home" in
      /home/?*) ;;
      *) res "$login" fail "домашняя папка '$home' нестандартная — вместе с файлами не удаляю"; return;;
    esac
    others=$(getent passwd | awk -F: -v h="$home" -v l="$login" '$6==h && $1!=l {print $1}' | tr '\n' ' ')
    if [ -n "$others" ]; then res "$login" fail "эту папку использует и $others — вместе с файлами не удаляю"; return; fi
  fi
  say "[$login] завершаю сеансы и процессы"
  loginctl terminate-user "$login" >/dev/null 2>&1 || true
  pkill -KILL -u "$login" >/dev/null 2>&1 || true
  sleep 1
  if ! unmount_user "$home" "$uid"; then
    if [ "$purge" = 1 ]; then res "$login" fail "не удалось отключить сетевые диски — вместе с файлами не удаляю"; return; fi
    say "[$login] часть сетевых дисков не отключилась (файлы на них не затрагиваются)"
  fi
  tries=0
  while :; do
    userdel $flag "$login" >/dev/null 2>&1
    rc=$?
    # код 8 — пользователь ещё в системе; userdel при этом ничего не удаляет, поэтому повторяем
    [ "$rc" = 8 ] || break
    tries=$((tries+1))
    [ "$tries" -ge 4 ] && break
    say "[$login] код 8 (ещё в системе) — завершаю процессы и повторяю ($tries из 3)"
    loginctl kill-user "$login" >/dev/null 2>&1 || true
    pkill -KILL -u "$login" >/dev/null 2>&1 || true
    sleep 2
  done
  if id "$login" >/dev/null 2>&1; then
    if [ "$rc" = 8 ]; then res "$login" fail "код 8: пользователь не выходит из системы, ничего не удалено"
    else res "$login" fail "userdel вернул код $rc"; fi
    return
  fi
  if [ "$purge" = 1 ]; then
    if [ -e "$home" ]; then msg="учётная запись удалена, папка $home осталась"; else msg="удалена вместе с домашней папкой"; fi
  else
    msg="удалена, файлы в $home сохранены"
  fi
  res "$login" ok "$msg"
}
mapfile -t lines
for ln in "${lines[@]}"; do
  IFS=$'\x1f' read -r login purge <<< "$ln"
  [ -n "$login" ] && del_one "$login" "$purge"
done
exit 0
"""

_UM_UNMOUNT_BODY = r"""
mapfile -t lines
for login in "${lines[@]}"; do
  [ -n "$login" ] || continue
  uid=$(id -u "$login" 2>/dev/null) || { res "$login" fail "учётная запись не найдена"; continue; }
  home=$(getent passwd "$login" | cut -d: -f6)
  if [ -z "$(mnts "$home" "$uid")" ]; then res "$login" skip "сетевых дисков нет"; continue; fi
  if unmount_user "$home" "$uid"; then res "$login" ok "диски отключены"
  else res "$login" fail "часть дисков не отключилась (заняты процессами пользователя)"; fi
done
exit 0
"""

_UM_CREATE_BODY = r"""
mapfile -t lines
for ln in "${lines[@]}"; do
  IFS=$'\x1f' read -r login pw name adm upd <<< "$ln"
  [ -n "$login" ] || continue
  if id "$login" >/dev/null 2>&1; then
    if [ "$upd" = 1 ]; then
      if setpw "$login" "$pw"; then res "$login" ok "пароль изменён"
      else res "$login" fail "пароль не принят системой"; fi
    else
      res "$login" skip "уже существует"
    fi
    continue
  fi
  if ! adduser --disabled-password --gecos "${name:-$login}" "$login" >/dev/null 2>&1; then
    res "$login" fail "adduser не смог создать учётную запись"; continue
  fi
  if ! setpw "$login" "$pw"; then
    userdel -r "$login" >/dev/null 2>&1
    res "$login" fail "не удалось задать пароль — учётная запись удалена"; continue
  fi
  # привилегии как в «Параметры пользователей → Привилегии» (только существующие группы)
  for g in plugdev audio video floppy dip tape cdrom scanner adm fax dialout; do
    getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$login" >/dev/null 2>&1
  done
  if [ "$adm" = 1 ]; then
    usermod -aG sudo "$login" >/dev/null 2>&1
    getent group astra-admin >/dev/null 2>&1 && usermod -aG astra-admin "$login" >/dev/null 2>&1
    res "$login" ok "создана, администратор"
  else
    res "$login" ok "создана"
  fi
done
exit 0
"""

_UM_GROUP_DELETE_BODY = r"""
mapfile -t lines
for g in "${lines[@]}"; do
  [ -n "$g" ] || continue
  gid=$(getent group "$g" | cut -d: -f3)
  if [ -z "$gid" ]; then res "$g" fail "группа не найдена"; continue; fi
  if ! { [ "$gid" -ge 1000 ] && [ "$gid" -lt 65534 ]; } 2>/dev/null; then res "$g" fail "системная группа (GID $gid)"; continue; fi
  prim=$(getent passwd | awk -F: -v g="$gid" '$4==g {print $1}' | tr '\n' ' ')
  if [ -n "$prim" ]; then res "$g" fail "это основная группа пользователей: $prim"; continue; fi
  if out=$(groupdel "$g" 2>&1); then res "$g" ok "группа удалена"; else res "$g" fail "groupdel: $out"; fi
done
exit 0
"""

_UM_GROUP_CREATE_BODY = r"""
mapfile -t lines
for g in "${lines[@]}"; do
  [ -n "$g" ] || continue
  if getent group "$g" >/dev/null 2>&1; then res "$g" skip "уже существует"; continue; fi
  if out=$(groupadd "$g" 2>&1); then res "$g" ok "группа создана"; else res "$g" fail "groupadd: $out"; fi
done
exit 0
"""


UM_PROC_MOUNTS = "/proc/mounts"   # в тестах подменяется


def _um_script(body, me="", mounts=None):
    text = "me=" + shlex.quote(me) + "\n" + _UM_PRELUDE + body
    return text.replace("@MOUNTS@", shlex.quote(mounts or UM_PROC_MOUNTS))


def um_list_script():
    return UM_LIST_SCRIPT.replace("@MOUNTS@", shlex.quote(UM_PROC_MOUNTS))


def um_delete_script(me):
    return _um_script(_UM_DELETE_BODY, me)


def um_unmount_script():
    return _um_script(_UM_UNMOUNT_BODY)


def um_create_script():
    return _um_script(_UM_CREATE_BODY)


def um_group_delete_script():
    return _um_script(_UM_GROUP_DELETE_BODY)


def um_group_create_script():
    return _um_script(_UM_GROUP_CREATE_BODY)


# --------------------------------------------------------------------------
# Разбор списка учётных записей и групп
# --------------------------------------------------------------------------
def um_parse_inventory(text):
    """-> {"host", "accounts", "groups", "tools", "mounts"}"""
    host, rows, groups, tools, mounts = "", [], [], [], []
    for line in (text or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if parts[0] == "HOST" and len(parts) >= 2:
            host = parts[1].strip()
        elif parts[0] == "SMBTOOL" and len(parts) >= 2:
            tools = [t for t in parts[1].strip().split(",") if t]
        elif parts[0] == "MNT" and len(parts) >= 4:
            mounts.append((parts[1].strip(), parts[2], parts[3]))
        elif parts[0] == "GRP" and len(parts) >= 3:
            try:
                gid = int(parts[2])
            except ValueError:
                continue
            members = [m for m in (parts[3] if len(parts) > 3 else "").split(",") if m]
            groups.append({"name": parts[1], "gid": gid, "members": members, "primary": [],
                           "system": not (1000 <= gid < 65534)})
        elif parts[0] == "ACC" and len(parts) >= 10:
            try:
                uid, gid = int(parts[2]), int(parts[3])
            except ValueError:
                continue
            st = parts[10].strip() if len(parts) >= 11 else "?"
            locked = True if st in ("L", "LK") else (False if st in ("P", "PS", "NP") else None)
            rows.append({"login": parts[1], "uid": uid, "gid": gid, "name": parts[4], "home": parts[5],
                         "shell": parts[6], "groups": parts[7], "admin": parts[8] == "1",
                         "online": parts[9].strip() == "1", "locked": locked, "pwstate": st, "disks": [],
                         "system": not (uid == 0 or 1000 <= uid < 65534)})
    for muid, mp, src in mounts:
        owner = None
        for r in sorted(rows, key=lambda r: -len(r["home"])):      # сначала по домашней папке
            if r["home"] and r["home"] != "/" and (mp == r["home"] or mp.startswith(r["home"].rstrip("/") + "/")):
                owner = r
                break
        if owner is None and muid.isdigit() and int(muid) > 0:     # иначе по uid= из параметров монтирования
            owner = next((r for r in rows if r["uid"] == int(muid)), None)
        if owner is not None:
            owner["disks"].append((posixpath.basename(mp.rstrip("/")) or mp, mp, src))
    by_gid = {g["gid"]: g for g in groups}
    for r in rows:
        g = by_gid.get(r["gid"])
        if g is not None:
            g["primary"].append(r["login"])
    return {"host": host, "accounts": rows, "groups": groups, "tools": tools, "mounts": mounts}


def um_group_block_reason(g):
    if g["system"]:
        return "Системная группа (GID %d) — программа их не удаляет" % g["gid"]
    if g["primary"]:
        return "Основная группа пользователей: " + ", ".join(g["primary"])
    return None




class AcctPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.app = app

    def refresh_view(self):
        """Данные приложения обновились — перерисовать."""

    def make_tree(self, parent, cols, selectmode="extended", stretch="groups"):
        holder = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=[c[0] for c in cols], show="headings",
                            selectmode=selectmode, style="Files.Treeview")
        for key, text, w, anchor in cols:
            tree.heading(key, text=text, anchor=anchor)
            tree.column(key, width=w, anchor=anchor, stretch=(key == stretch), minwidth=50)
        sb = ctk.CTkScrollbar(holder, command=tree.yview, width=12)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        tree.bind("<Control-a>", lambda e: (tree.selection_set(tree.get_children()), "break")[1])
        return holder, tree

    def make_log(self, parent, height=96):
        box = ctk.CTkTextbox(parent, height=height, fg_color=TERM_BG, text_color=TERM_FG, corner_radius=10,
                             border_width=1, border_color=BORDER, wrap="word",
                             font=ctk.CTkFont(family=pick_mono(), size=12))
        box.configure(state="disabled")
        return box

    @staticmethod
    def say(box, text, replace=False):
        try:
            box.configure(state="normal")
            if replace:
                box.delete("1.0", "end")
            box.insert("end", text.rstrip("\n") + "\n")
            box.see("end")
            box.configure(state="disabled")
        except tk.TclError:
            pass


class AcctAccountsTab(AcctPage):
    COLS = (("login", "Логин", 130, "w"), ("uid", "UID", 56, "e"), ("name", "Имя", 150, "w"),
            ("home", "Домашняя папка", 160, "w"), ("groups", "Группы", 200, "w"),
            ("admin", "Админ", 60, "center"), ("lock", "Заблок.", 70, "center"),
            ("online", "В системе", 80, "center"), ("disks", "Сетевые диски", 120, "w"))

    def __init__(self, master, app):
        super().__init__(master, app)
        self.saved_path = ""
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="Учётные записи", font=ui_font(20, "bold"), text_color=TEXT,
                     anchor="w").pack(side="left")
        self.count_lbl = ctk.CTkLabel(top, text="Загрузка…", font=ui_font(12), text_color=MUTED)
        self.count_lbl.pack(side="right")
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=20)
        self.show_sys = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Показывать системные", variable=self.show_sys, command=self.refresh_view,
                        font=ui_font(12), text_color=MUTED, fg_color=ACCENT, hover_color=ACCENT_H,
                        border_color=BORDER, checkbox_width=18, checkbox_height=18).pack(side="left")
        ctk.CTkLabel(opts, text="Выделение нескольких — Ctrl/Shift · в txt всегда попадают все записи",
                     font=ui_font(11), text_color=MUTED).pack(side="right")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        self.log = self.make_log(foot)
        self.log.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        primary_button(row, "Обновить", self.app.refresh, "soft", width=110, height=38).pack(side="left")
        self.btn_save = primary_button(row, "Сохранить в TXT…", self.save_txt, "soft", width=160, height=38)
        self.btn_save.pack(side="left", padx=(10, 0))
        self.btn_unmount = primary_button(row, "Отмонтировать диски", self.unmount_selected, "soft",
                                          width=190, height=38)
        self.btn_unmount.pack(side="left", padx=(10, 0))
        self.btn_kill = primary_button(row, "Сбросить неактивные…", self.kill_inactive, "soft", width=210, height=38)
        self.btn_kill.pack(side="left", padx=(10, 0))
        self.btn_del = primary_button(row, "Удалить…", self.delete_selected, "danger", width=140, height=38)
        self.btn_del.pack(side="right")

        holder, self.tree = self.make_tree(self, self.COLS)
        holder.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        self.tree.tag_configure("t_on", foreground="#56d364")
        self.tree.tag_configure("t_sys", foreground=MUTED)
        self.tree.tag_configure("t_adm", foreground=WARN)
        self.tree.tag_configure("t_lock", foreground="#ff7b72")

    def refresh_view(self):
        d = self.app.data
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for r in d["accounts"]:
            if r["system"] and not self.show_sys.get():
                continue
            tags = (("t_sys",) if r["system"] else (("t_lock",) if r["locked"] else (
                ("t_on",) if r["online"] else (("t_adm",) if r["admin"] else ()))))
            self.tree.insert("", "end", iid=r["login"], tags=tags, values=(
                r["login"], r["uid"], r["name"], r["home"], r["groups"], "да" if r["admin"] else "",
                lock_text(r), "да" if r["online"] else "", disks_text(r)))
            shown += 1
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        users = sum(1 for r in d["accounts"] if not r["system"])
        names = {"astra": "Astra SMB Mounter", "mate": "mate-smb-tools"}
        tool = ", ".join(names.get(t, t) for t in d["tools"]) or "не установлена"
        mounted = sum(len(r["disks"]) for r in d["accounts"])
        self.count_lbl.configure(text="Пользователей: %d · системных: %d · показано: %d · сетевых дисков: %d · "
                                      "программа SMB: %s" % (users, len(d["accounts"]) - users, shown, mounted, tool))

    def selected(self):
        by = {r["login"]: r for r in self.app.data["accounts"]}
        return [by[i] for i in self.tree.selection() if i in by]

    # -- txt -----------------------------------------------------------------
    def save_txt(self):
        d = self.app.data
        if not d["accounts"]:
            self.say(self.log, "Список пуст — сначала нажмите «Обновить»", True)
            return
        host = "".join(ch for ch in (d["host"] or "pc") if ch.isalnum() or ch in ".-_") or "pc"
        start = os.path.expanduser("~")
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Сохранить список учётных записей", initialdir=start,
            initialfile="accounts_%s_%s.txt" % (host, time.strftime("%Y%m%d-%H%M%S")),
            defaultextension=".txt", filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:   # BOM: Блокнот Windows видит UTF-8
                f.write(format_accounts_txt(self.app.term.host, d["host"], d["accounts"], info={"tools": d["tools"]}))
        except OSError as e:
            self.say(self.log, "Не удалось сохранить файл: %s" % e, True)
            return
        self.saved_path = path
        self.say(self.log, "Сохранено: %s" % path, True)
        self.app.toast.show("Список учётных записей сохранён в txt", "ok", 3500)

    # -- удаление ------------------------------------------------------------
    def delete_selected(self):
        sel = self.selected()
        if not sel:
            self.say(self.log, "Выберите учётные записи в списке", True)
            return
        if not self.app.need_admin():
            return
        ok_list, blocked = [], []
        for a in sel:
            why = delete_block_reason(a, self.app.me)
            (blocked if why else ok_list).append((a, why))
        self.note = ("Не удаляются:\n" + "\n".join("  %s — %s" % (a["login"], w) for a, w in blocked) + "\n") if blocked else ""
        if blocked:
            self.say(self.log, self.note.rstrip("\n"), True)
        if not ok_list:
            return
        accs = [a for a, _ in ok_list]
        names = ", ".join(a["login"] for a in accs[:6]) + (" и ещё %d" % (len(accs) - 6) if len(accs) > 6 else "")
        form = ask_form(
            self, "Удалить учётные записи" if len(accs) > 1 else "Удалить учётную запись",
            "Выбрано: %s (%d)" % (names, len(accs)),
            [{"key": "mode", "label": "Что удалить", "kind": "choice", "initial": "keep",
              "options": [("keep", "Только учётную запись (файлы в домашней папке останутся)"),
                          ("purge", "Учётную запись и домашнюю папку со всеми файлами")]}], "Далее")
        if not form:
            return
        purge = form["mode"] == "purge"
        online = [a["login"] for a in accs if a["online"]]
        with_disks = [a for a in accs if a["disks"]]
        text = ("Удалить %s?\n\n" % (("«%s»" % accs[0]["login"]) if len(accs) == 1 else "%d учётных записей" % len(accs)) +
                ("ВМЕСТЕ С ДОМАШНИМИ ПАПКАМИ И ВСЕМИ ФАЙЛАМИ В НИХ. Восстановить их будет нельзя."
                 if purge else "Файлы в домашних папках останутся на диске."))
        if online:
            text += "\n\nСейчас в системе: %s — их сеансы и процессы будут завершены." % ", ".join(online)
        if with_disks:
            text += ("\n\nСетевые диски (%s) будут отключены до удаления; файлы на сетевых дисках "
                     "не затрагиваются." % ", ".join("%s: %s" % (a["login"], disks_text(a)) for a in with_disks))
        if not ask_confirm(self, "Удалить?", text, "Удалить", "danger"):
            return
        data = "".join("%s%s%d\n" % (a["login"], UM_SEP, 1 if purge else 0) for a in accs)
        self.say(self.log, "Удаляю…", True)
        self.app.run_job(um_delete_script(self.app.me), data, self._done)

    def kill_inactive(self):
        if not self.app.need_admin():
            return
        if ask_confirm(self, "Сбросить неактивные учётки?",
                       "Будут завершены сессии всех других пользователей этого ПК, кроме активной "
                       "за монитором и вашего SSH-подключения. Несохранённая работа в них пропадёт.\n\n"
                       "Нужен установленный скрипт terminate_sessions_root.sh — наличие проверю. "
                       "Скрипт выполнится в терминале, это окно закроется.", "Сбросить", "warn"):
            self.app.kill_inactive()

    def unmount_selected(self):
        sel = self.selected()
        if not sel:
            self.say(self.log, "Выберите учётные записи в списке", True)
            return
        if not self.app.need_admin():
            return
        with_disks = [a for a in sel if a["disks"]]
        if not with_disks:
            self.say(self.log, "У выбранных учётных записей нет смонтированных сетевых дисков", True)
            return
        text = ("Отключить сетевые диски у: %s?\n\nФайлы на сетевых дисках не затрагиваются. Если у пользователя "
                "включено автомонтирование (smb_mount), диски могут подключиться снова при следующем входе."
                % ", ".join("%s (%s)" % (a["login"], disks_text(a)) for a in with_disks))
        if not ask_confirm(self, "Отмонтировать диски?", text, "Отключить"):
            return
        self.say(self.log, "Отключаю диски…", True)
        self.app.run_job(um_unmount_script(), "".join(a["login"] + "\n" for a in with_disks), self._done)

    def _done(self, code, out):
        res, log = um_parse_results(out)
        lines = list(log)
        for login, st, msg in res:
            lines.append("%s %s: %s" % ({"ok": "✔", "skip": "•", "fail": "✘"}.get(st, "?"), login, msg))
        self.say(self.log, getattr(self, "note", "") + ("\n".join(lines) or ("(пустой вывод, код %d)" % code)), True)
        self.note = ""
        okn = sum(1 for r in res if r[1] == "ok")
        bad = sum(1 for r in res if r[1] == "fail")
        self.app.toast.show("Готово: успешно %d%s" % (okn, (", с ошибками %d" % bad) if bad else ""),
                            "err" if bad else "ok", 4500)
        self.app.refresh()


class AcctGroupsTab(AcctPage):
    COLS = (("name", "Группа", 170, "w"), ("gid", "GID", 70, "e"), ("members", "Участники", 330, "w"),
            ("primary", "Основная у", 220, "w"), ("kind", "Тип", 90, "center"))

    def __init__(self, master, app):
        super().__init__(master, app)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="Группы", font=ui_font(20, "bold"), text_color=TEXT, anchor="w").pack(side="left")
        self.count_lbl = ctk.CTkLabel(top, text="Загрузка…", font=ui_font(12), text_color=MUTED)
        self.count_lbl.pack(side="right")
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=20)
        self.show_sys = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Показывать системные", variable=self.show_sys, command=self.refresh_view,
                        font=ui_font(12), text_color=MUTED, fg_color=ACCENT, hover_color=ACCENT_H,
                        border_color=BORDER, checkbox_width=18, checkbox_height=18).pack(side="left")
        ctk.CTkLabel(opts, text="Удаляются только обычные группы (GID 1000+), не являющиеся основными для пользователей",
                     font=ui_font(11), text_color=MUTED).pack(side="right")
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        self.log = self.make_log(foot)
        self.log.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        primary_button(row, "Обновить", self.app.refresh, "soft", width=110, height=38).pack(side="left")
        primary_button(row, "Создать группу…", self.create_group, "soft", width=170, height=38).pack(
            side="left", padx=(10, 0))
        primary_button(row, "Удалить…", self.delete_selected, "danger", width=140, height=38).pack(side="right")
        holder, self.tree = self.make_tree(self, self.COLS, stretch="members")
        holder.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        self.tree.tag_configure("t_sys", foreground=MUTED)
        self.tree.tag_configure("t_used", foreground=WARN)

    def refresh_view(self):
        groups = self.app.data["groups"]
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for g in sorted(groups, key=lambda g: (g["system"], g["gid"])):
            if g["system"] and not self.show_sys.get():
                continue
            tags = ("t_sys",) if g["system"] else (("t_used",) if g["primary"] else ())
            self.tree.insert("", "end", iid=g["name"], tags=tags, values=(
                g["name"], g["gid"], ", ".join(g["members"]), ", ".join(g["primary"]),
                "системная" if g["system"] else "обычная"))
            shown += 1
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        own = sum(1 for g in groups if not g["system"])
        self.count_lbl.configure(text="Обычных: %d · системных: %d · показано: %d" % (own, len(groups) - own, shown))

    def selected(self):
        by = {g["name"]: g for g in self.app.data["groups"]}
        return [by[i] for i in self.tree.selection() if i in by]

    def create_group(self):
        if not self.app.need_admin():
            return
        names = {g["name"] for g in self.app.data["groups"]}

        def check(v):
            err = um_validate_login(v["name"])
            return err.replace("Логин", "Имя группы") if err else ("Такая группа уже есть" if v["name"] in names else None)
        form = ask_form(self, "Создать группу", "Новая группа без участников.",
                        [{"key": "name", "label": "Имя группы", "kind": "text",
                          "hint": "строчная латиница, цифры, _ и -"}], "Создать", validate=check)
        if form:
            self.say(self.log, "Создаю группу…", True)
            self.app.run_job(um_group_create_script(), form["name"] + "\n", self._done)

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            self.say(self.log, "Выберите группы в списке", True)
            return
        if not self.app.need_admin():
            return
        ok_list, blocked = [], []
        for g in sel:
            why = um_group_block_reason(g)
            (blocked if why else ok_list).append((g, why))
        self.note = ("Не удаляются:\n" + "\n".join("  %s — %s" % (g["name"], w) for g, w in blocked) + "\n") if blocked else ""
        if blocked:
            self.say(self.log, self.note.rstrip("\n"), True)
        if not ok_list:
            return
        gs = [g for g, _ in ok_list]
        members = sum(len(g["members"]) for g in gs)
        text = "Удалить %s?" % (("группу «%s»" % gs[0]["name"]) if len(gs) == 1 else "%d групп (%s)" % (
            len(gs), ", ".join(g["name"] for g in gs[:6]) + ("…" if len(gs) > 6 else "")))
        if members:
            text += "\n\nУчастники (%d) просто потеряют членство в удаляемых группах." % members
        text += "\n\nФайлы, принадлежащие этим группам, останутся — у них будет числовой GID без имени."
        if not ask_confirm(self, "Удалить группы?", text, "Удалить", "danger"):
            return
        self.say(self.log, "Удаляю…", True)
        self.app.run_job(um_group_delete_script(), "".join(g["name"] + "\n" for g in gs), self._done)

    def _done(self, code, out):
        res, log = um_parse_results(out)
        lines = list(log) + ["%s %s: %s" % ({"ok": "✔", "skip": "•", "fail": "✘"}.get(s, "?"), n, m) for n, s, m in res]
        self.say(self.log, getattr(self, "note", "") + ("\n".join(lines) or ("(пустой вывод, код %d)" % code)), True)
        self.note = ""
        bad = sum(1 for r in res if r[1] == "fail")
        self.app.toast.show("Готово: успешно %d%s" % (sum(1 for r in res if r[1] == "ok"),
                                                       (", с ошибками %d" % bad) if bad else ""),
                            "err" if bad else "ok", 4000)
        self.app.refresh()


class AcctCreateTab(AcctPage):
    COLS = (("line", "Строка", 64, "e"), ("login", "Логин", 160, "w"), ("password", "Пароль", 150, "w"),
            ("name", "Имя", 180, "w"), ("status", "Статус", 360, "w"))

    def __init__(self, master, app):
        super().__init__(master, app)
        self.items = []
        self.path = ""
        ctk.CTkLabel(self, text="Создание учётных записей", font=ui_font(20, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(16, 8))
        self.mode_btn = ctk.CTkSegmentedButton(
            self, values=["Одна учётная запись", "Импорт из txt"], command=self._set_mode, height=36,
            corner_radius=10, font=ui_font(13, "bold"), fg_color=SURFACE2, selected_color=ACCENT,
            selected_hover_color=ACCENT_H, unselected_color=SURFACE2, unselected_hover_color=SURFACE_HOVER,
            text_color=TEXT)
        self.mode_btn.set("Одна учётная запись")
        self.mode_btn.pack(fill="x", padx=20)
        self.log = self.make_log(self, 88)
        self.log.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        self.single = self._build_single()
        self.bulk = self._build_bulk()
        self.single.pack(fill="both", expand=True, padx=20, pady=(12, 0))

    def _set_mode(self, value):
        self.single.pack_forget()
        self.bulk.pack_forget()
        (self.single if value.startswith("Одна") else self.bulk).pack(fill="both", expand=True, padx=20, pady=(12, 0))

    # -- одна учётная запись -------------------------------------------------
    def _build_single(self):
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        kw = dict(height=38, corner_radius=10, fg_color=BG, border_color=BORDER, text_color=TEXT, font=ui_font(13))
        self.ent = {}
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=22, pady=(18, 0))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="c")
        spec = (("login", "Логин", "", 0, 0), ("name", "Имя (необязательно)", "", 0, 1),
                ("password", "Пароль", "•", 2, 0), ("password2", "Повторите пароль", "•", 2, 1))
        for key, label, show, row, col in spec:
            ctk.CTkLabel(grid, text=label.upper(), font=ui_font(11, "bold"), text_color=MUTED,
                         anchor="w").grid(row=row, column=col, sticky="w", padx=(0 if col == 0 else 10, 0), pady=(12, 4))
            e = ctk.CTkEntry(grid, show=show, **kw)
            e.grid(row=row + 1, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            self.ent[key] = e
        self.admin_v = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(card, text="Администратор (группа sudo)", variable=self.admin_v, font=ui_font(13),
                        text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_H, border_color=BORDER,
                        checkbox_width=18, checkbox_height=18).pack(anchor="w", padx=22, pady=(16, 0))
        self.msg = ctk.CTkLabel(card, text="", font=ui_font(12), text_color=DANGER, anchor="w")
        self.msg.pack(fill="x", padx=22, pady=(10, 0))
        primary_button(card, "Создать учётную запись", self.create_single, "accent", width=230, height=40).pack(
            anchor="e", padx=22, pady=(6, 20))
        return card

    def create_single(self):
        v = {k: e.get() for k, e in self.ent.items()}
        v["login"], v["name"] = v["login"].strip(), v["name"].strip()
        err = um_validate_new_user(v)
        if err:
            self.msg.configure(text=err)
            return
        if v["login"] in {a["login"] for a in self.app.data["accounts"]}:
            self.msg.configure(text="Учётная запись «%s» уже существует" % v["login"])
            return
        if not self.app.need_admin():
            return
        self.msg.configure(text="")
        data = UM_SEP.join((v["login"], v["password"], v["name"], "1" if self.admin_v.get() else "0", "0")) + "\n"
        self.say(self.log, "Создаю…", True)

        def done(code, out):
            res, log = um_parse_results(out)
            self._report(res, log, code)
            if res and res[0][1] == "ok":
                for e in self.ent.values():
                    e.delete(0, "end")
                self.admin_v.set(False)
            self.app.refresh()
        self.app.run_job(um_create_script(), data, done)

    def _report(self, res, log, code):
        lines = list(log) + ["%s %s: %s" % ({"ok": "✔", "skip": "•", "fail": "✘"}.get(s, "?"), n, m) for n, s, m in res]
        self.say(self.log, "\n".join(lines) or ("(пустой вывод, код %d)" % code), True)
        bad = sum(1 for r in res if r[1] == "fail")
        self.app.toast.show("Создано: %d%s" % (sum(1 for r in res if r[1] == "ok"),
                                               (", с ошибками %d" % bad) if bad else ""),
                            "err" if bad else "ok", 4000)

    # -- импорт из txt ---------------------------------------------------------
    def _build_bulk(self):
        card = ctk.CTkFrame(self, fg_color="transparent")
        top = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        top.pack(fill="x")
        line = ctk.CTkFrame(top, fg_color="transparent")
        line.pack(fill="x", padx=18, pady=(16, 0))
        self.path_ent = ctk.CTkEntry(line, height=38, corner_radius=10, fg_color=BG, border_color=BORDER,
                                     text_color=TEXT, font=ui_font(13), placeholder_text="Файл txt не выбран")
        self.path_ent.pack(side="left", fill="x", expand=True)
        self.path_ent.configure(state="disabled")
        primary_button(line, "Выбрать файл…", self.pick_file, "soft", width=150, height=38).pack(
            side="left", padx=(10, 0))
        ctk.CTkLabel(top, text="Формат: по одной учётной записи в строке — «логин<Tab>пароль» или «логин пробел пароль». "
                               "Пустые строки и строки с # пропускаются; 3-й столбец через Tab — имя.",
                     font=ui_font(11), text_color=MUTED, anchor="w", justify="left", wraplength=900).pack(
            fill="x", padx=18, pady=(8, 0))
        opts = ctk.CTkFrame(top, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(8, 14))
        self.b_admin = ctk.BooleanVar(value=False)
        self.b_update = ctk.BooleanVar(value=False)
        self.b_show = ctk.BooleanVar(value=False)
        for text, var, cmd in (("Сделать всех администраторами (sudo)", self.b_admin, None),
                               ("Если учётная запись уже есть — сменить пароль", self.b_update, self._refill),
                               ("Показывать пароли", self.b_show, self._refill)):
            ctk.CTkCheckBox(opts, text=text, variable=var, command=cmd, font=ui_font(12), text_color=TEXT,
                            fg_color=ACCENT, hover_color=ACCENT_H, border_color=BORDER,
                            checkbox_width=18, checkbox_height=18).pack(side="left", padx=(0, 20))
        self.btn_go = primary_button(card, "Создать", self.create_bulk, "accent", width=240, height=40)
        self.btn_go.pack(side="bottom", anchor="e", pady=(8, 0))
        self.btn_go.configure(state="disabled")
        holder, self.tree = self.make_tree(card, self.COLS, selectmode="browse", stretch="status")
        holder.pack(fill="both", expand=True, pady=(10, 0))
        self.tree.tag_configure("t_ok", foreground="#56d364")
        self.tree.tag_configure("t_bad", foreground="#ff7b72")
        self.tree.tag_configure("t_skip", foreground=WARN)
        return card

    def pick_file(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="Файл со списком учётных записей",
            initialdir=os.path.expanduser("~"), filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*")])
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            with open(path, "rb") as f:
                text = um_decode_text(f.read())
        except OSError as e:
            self.say(self.log, "Не удалось прочитать файл: %s" % e, True)
            return
        self.path = path
        self.path_ent.configure(state="normal")
        self.path_ent.delete(0, "end")
        self.path_ent.insert(0, path)
        self.path_ent.configure(state="disabled")
        self.items = um_parse_import(text, {a["login"] for a in self.app.data["accounts"]})
        for it in self.items:
            it["result"] = None
        bad = sum(1 for it in self.items if it["error"])
        self.say(self.log, "Прочитано строк с данными: %d, с ошибками: %d" % (len(self.items), bad), True)
        self._refill()

    def _status(self, it):
        if it.get("result"):
            return it["result"]
        if it["error"]:
            return ("✘ " + it["error"], "t_bad")
        if it["exists"]:
            return (("уже есть — пароль будет изменён", "t_skip") if self.b_update.get()
                    else ("уже есть — будет пропущена", "t_skip"))
        return ("готова к созданию", "")

    def _refill(self):
        existing = {a["login"] for a in self.app.data["accounts"]}
        self.tree.delete(*self.tree.get_children())
        ready = 0
        for i, it in enumerate(self.items):
            if not it["error"]:
                it["exists"] = it["login"] in existing and not it.get("result")
            text, tag = self._status(it)
            if not it["error"] and not it.get("result") and (not it["exists"] or self.b_update.get()):
                ready += 1
            pw = it["password"] if self.b_show.get() else ("•" * min(len(it["password"]), 10) if it["password"] else "")
            self.tree.insert("", "end", iid=str(i), tags=(tag,) if tag else (), values=(
                it["line"], it["login"], pw, it["name"], text))
        self.btn_go.configure(text="Создать (%d)" % ready if ready else "Создать",
                              state="normal" if ready else "disabled")

    def refresh_view(self):
        if self.items:
            self._refill()

    def create_bulk(self):
        todo = [it for it in self.items if not it["error"] and not it.get("result")
                and (not it["exists"] or self.b_update.get())]
        if not todo:
            return
        if not self.app.need_admin():
            return
        adm = "1" if self.b_admin.get() else "0"
        upd = "1" if self.b_update.get() else "0"
        text = "Создать учётных записей: %d.%s" % (len(todo), "\n\nВсе получат права администратора (sudo)." if adm == "1" else "")
        if not ask_confirm(self, "Создать учётные записи?", text, "Создать"):
            return
        data = "".join(UM_SEP.join((it["login"], it["password"], it["name"], adm, upd)) + "\n" for it in todo)
        self.say(self.log, "Создаю %d…" % len(todo), True)
        by_login = {it["login"]: it for it in todo}

        def done(code, out):
            res, log = um_parse_results(out)
            for login, st, msg in res:
                it = by_login.get(login)
                if it is not None:
                    it["result"] = (("✔ " if st == "ok" else ("• " if st == "skip" else "✘ ")) + msg,
                                    {"ok": "t_ok", "skip": "t_skip"}.get(st, "t_bad"))
            self._report(res, log, code)
            self.app.refresh()
        self.app.run_job(um_create_script(), data, done)


class AccountsWindow(ctk.CTkToplevel):
    """Управление учётными записями ПК: учётные записи, группы, создание (по одной и импортом из txt).
    Скрипты идут отдельным каналом (терминал не затрагивается), пароли — только через stdin."""
    TABS = (("Учётные записи", AcctAccountsTab), ("Группы", AcctGroupsTab), ("Создание", AcctCreateTab))

    def __init__(self, term, tab=None):
        super().__init__(term, fg_color=BG)
        self.term = term
        self.me = term.user
        self.toast = term.toast
        self.title(f"Управление учётными записями — {term.user}@{term.host}")
        self.geometry("1200x740")
        self.minsize(960, 600)
        self.transient(term.winfo_toplevel())
        self.data = {"host": "", "accounts": [], "groups": [], "tools": [], "mounts": []}
        self.is_admin = (term.user == "root")
        self.busy = False
        self.closing = False
        self.pending = []
        self.q = queue.Queue()
        self.cur = None
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<F5>", lambda e: self.refresh())
        files_tree_style(self)

        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="●", font=ui_font(14), text_color=ACCENT).pack(side="left", padx=(16, 4))
        ctk.CTkLabel(bar, text=f"{term.host}", font=ui_font(14, "bold"), text_color=TEXT).pack(
            side="left", padx=(0, 18))
        tab_row = ctk.CTkFrame(bar, fg_color="transparent")
        tab_row.pack(side="left", fill="y", pady=7)
        foot = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=38)
        foot.pack(side="bottom", fill="x")
        foot.pack_propagate(False)
        self.status_dot = ctk.CTkLabel(foot, text="●", width=16, font=ui_font(12), text_color=MUTED)
        self.status_dot.pack(side="left", padx=(18, 4))
        self.status = ctk.CTkLabel(foot, text="", font=ui_font(13), text_color=TEXT, anchor="w")
        self.status.pack(side="left")
        self.rights = ctk.CTkLabel(foot, text="", font=ui_font(12), text_color=MUTED)
        self.rights.pack(side="right", padx=18)
        content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        content.pack(fill="both", expand=True)

        self.pages, self.tab_btns = {}, {}
        for name, cls in self.TABS:
            self.pages[name] = cls(content, self)
            b = ctk.CTkButton(tab_row, text=name, width=150, height=34, corner_radius=8,
                              font=ui_font(13, "bold"), fg_color="transparent", hover_color=SURFACE_HOVER,
                              text_color=MUTED, command=lambda n=name: self.show_tab(n))
            b.pack(side="left", padx=(0, 6))
            self.tab_btns[name] = b
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_tab(tab if tab in self.pages else self.TABS[0][0])
        self.set_status("Загружаю список…", "busy")
        self.after(80, self._pump)
        self.refresh()
        bring_front(self)

    # -- оформление ---------------------------------------------------------------
    def show_tab(self, name):
        if self.cur:
            self.pages[self.cur].pack_forget()
        self.cur = name
        for n, b in self.tab_btns.items():
            b.configure(fg_color=SURFACE2 if n == name else "transparent",
                        text_color=TEXT if n == name else MUTED)
        self.pages[name].pack(fill="both", expand=True)
        self.pages[name].refresh_view()

    def set_status(self, text, kind="ok"):
        try:
            self.status.configure(text=text)
            self.status_dot.configure(text_color={"ok": OK, "busy": WARN, "err": DANGER}.get(kind, MUTED))
        except tk.TclError:
            pass

    def _update_rights(self):
        try:
            self.rights.configure(
                text=("Права: root" if self.term.user == "root" else "Права: sudo") if self.is_admin
                else "Только просмотр (нет прав sudo)", text_color=MUTED if self.is_admin else WARN)
        except tk.TclError:
            pass

    def need_admin(self):
        if not self.is_admin:
            self.toast.show("Нужны права sudo: у этой учётной записи их нет", "warn", 4500)
        return self.is_admin

    # -- фоновые задачи (по одной, остальные ждут в очереди) -------------------------
    def run_job(self, script, stdin_text, callback):
        self.pending.append(("job", script, stdin_text, callback))
        self._kick()

    def refresh(self):
        if self.pending and self.pending[-1][0] == "refresh":
            return
        self.pending.append(("refresh",))
        self._kick()

    def _kick(self):
        if self.busy or not self.pending or self.closing:
            return
        item = self.pending.pop(0)
        self.busy = True
        t = self.term
        if item[0] == "refresh":
            self.set_status("Загружаю список…", "busy")

            def work():
                try:
                    inv, ok, text = None, False, ""
                    for use_sudo in (True, False):    # статус блокировки видит только root
                        try:
                            code, text = exec_remote(t.client, t.user, t.password, um_list_script(), use_sudo, timeout=60)
                        except (EOFError, OSError, paramiko.SSHException):
                            if not use_sudo:
                                raise
                            continue
                        inv = um_parse_inventory(text)
                        if inv["accounts"]:
                            ok = use_sudo or t.user == "root"
                            break
                    self.q.put(("inventory", (inv, ok, text)))
                except Exception as e:  # noqa: BLE001
                    self.q.put(("inventory", (None, False, f"{type(e).__name__}: {e}")))
        else:
            _, script, stdin_text, callback = item
            self.set_status("Выполняется…", "busy")

            def work():
                try:
                    code, out = exec_remote(t.client, t.user, t.password, script, True, timeout=300,
                                            stdin_data=stdin_text.encode("utf-8") if stdin_text else None)
                except Exception as e:  # noqa: BLE001
                    code, out = -1, f"{type(e).__name__}: {e}"
                self.q.put(("job", (callback, code, out)))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self.busy = False
                if kind == "inventory":
                    inv, ok, text = payload
                    if inv and inv["accounts"]:
                        self.data, self.is_admin = inv, ok
                        self._update_rights()
                        self.set_status(f"Список обновлён: учётных записей {len(inv['accounts'])}, "
                                        f"групп {len(inv['groups'])}", "ok")
                        for p in self.pages.values():
                            p.refresh_view()
                    else:
                        self.set_status("Не удалось получить список", "err")
                        self.toast.show(f"Не удалось получить список: {first_line(text, 120) or 'пустой ответ'}",
                                        "err", 6000)
                elif kind == "job":
                    callback, code, out = payload
                    if code != 0 and "RES\t" not in out and self.term.user != "root":
                        out = (out + "\n(команда не выполнена — проверьте, что у пользователя "
                                     f"{self.term.user} есть права sudo)").strip()
                    self.set_status("Готово", "ok")
                    callback(code, out)
                self._kick()
        except queue.Empty:
            pass
        self.after(80, self._pump)

    # -- сброс неактивных: выполняется в терминале (там видно вывод, и сам терминал не будет сброшен) ----
    def kill_inactive(self):
        term = self.term
        self.close()
        term.run_script(build_kill_sessions_script(), True, "Сброс неактивных учёток")

    def close(self):
        self.closing = True
        try:
            self.destroy()
        except tk.TclError:
            pass



class InstallPickerWindow(ctk.CTkToplevel):
    """Выбор .sh/.deb на подключённом ПК (только его файлы). «Установить» запускает установку в терминале."""

    def __init__(self, term, on_install):
        super().__init__(term, fg_color=BG)
        self.term, self.on_install = term, on_install
        self.title(f"Установить SH / DEB — {term.user}@{term.host}")
        self.geometry("760x640")
        self.minsize(600, 460)
        self.transient(term.winfo_toplevel())
        self.q = queue.Queue()
        self.jobs = queue.Queue()
        self.sftp = None
        self.closing = False
        self.chosen = None
        self.bind("<Escape>", lambda e: self.close())
        FileManagerWindow._style(self)

        ctk.CTkLabel(self, text="Установить SH / DEB", font=ui_font(20, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(18, 0))
        self.hint = ctk.CTkLabel(
            self, text="Файлы на подключённом ПК. Выберите скрипт .sh или пакет .deb — установка "
                       "пойдёт в окне терминала.", font=ui_font(12), text_color=MUTED, anchor="w",
            justify="left", wraplength=700)
        self.hint.pack(fill="x", padx=20, pady=(2, 8))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=20, pady=(6, 18))
        self.sel_lbl = ctk.CTkLabel(foot, text="Файл не выбран", font=ui_font(12), text_color=MUTED,
                                    anchor="w", justify="left", wraplength=700)
        self.sel_lbl.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        self.sudo_v = ctk.BooleanVar(value=True)
        self.sudo_chk = ctk.CTkCheckBox(row, text="Запускать .sh с правами root (sudo)",
                                        variable=self.sudo_v, font=ui_font(12), text_color=TEXT,
                                        fg_color=ACCENT, border_color=BORDER)
        self.sudo_chk.pack(side="left")
        self.btn_ok = primary_button(row, "Установить", self._install, "accent", width=140, height=40)
        self.btn_ok.pack(side="right")
        self.btn_ok.configure(state="disabled")
        primary_button(row, "Отмена", self.close, "ghost", width=110, height=40).pack(
            side="right", padx=(0, 10))

        self.pane = FilePane(self, f"{term.user}@{term.host}", self, "remote", with_ops=False,
                             tags_fn=lambda n, d: ("inst",) if (not d and n.lower().endswith(INSTALL_EXTS)) else ())
        self.pane.tree.tag_configure("inst", foreground=OK)
        self.pane.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.pane.tree.configure(selectmode="browse")
        self.pane.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())
        self.pane.tree.bind("<Double-1>", self._double, add="+")
        self.pane.set_enabled(False)
        self.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self._worker, daemon=True).start()
        self.jobs.put(self._do_open)
        self.after(40, self._pump)
        bring_front(self)

    # FilePane ожидает от окна эти три метода
    def join(self, side, base, name):
        return posixpath.join(base, name)

    def parent_of(self, side, path):
        return posixpath.dirname(path.rstrip("/")) or "/"

    def go(self, side, path):
        if self.sftp is not None:
            self.jobs.put(lambda: self._do_list(path))

    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception as e:  # noqa: BLE001
                self.q.put(("fatal", f"{type(e).__name__}: {e}"))

    def _do_open(self):
        try:
            self.sftp = self.term.client.open_sftp()
            home = self.sftp.normalize(".")
        except Exception as e:  # noqa: BLE001
            self.sftp = None
            self.q.put(("fatal", f"не удалось открыть SFTP: {e}"))
            return
        self.q.put(("opened", home))
        self._do_list(home)

    def _do_list(self, path):
        try:
            path = self.sftp.normalize(path or ".")
            entries = list_remote(self.sftp, path)
        except Exception as e:  # noqa: BLE001
            self.q.put(("list_err", (path, str(e))))
            return
        self.q.put(("listed", (path, entries)))

    def _pump(self):
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "opened":
                    self.pane.set_enabled(True)
                elif kind == "listed":
                    self.pane.show(*payload)
                    self._selected()
                elif kind == "list_err":
                    self.hint.configure(text=f"Не открыть «{payload[0]}»: {payload[1]}", text_color=DANGER)
                    self.pane.path_entry.delete(0, "end")
                    self.pane.path_entry.insert(0, self.pane.path)
                elif kind == "fatal":
                    self.hint.configure(text=f"Ошибка: {payload}", text_color=DANGER)
        except queue.Empty:
            pass
        self.after(40, self._pump)

    def _double(self, _e=None):
        # двойной щелчок по подходящему файлу — то же, что «Установить»
        sel = self.pane.selected()
        if len(sel) == 1 and not self.pane.is_dir(sel[0]) and self.chosen:
            self._install()

    def _selected(self):
        sel = self.pane.selected()
        self.chosen = None
        if len(sel) == 1 and not self.pane.is_dir(sel[0]):
            name = sel[0]
            if name.lower().endswith(INSTALL_EXTS):
                self.chosen = posixpath.join(self.pane.path, name)
        if self.chosen:
            kind = "пакет .deb" if self.chosen.lower().endswith(".deb") else "скрипт .sh"
            self.sel_lbl.configure(text=f"Выбран {kind}: {self.chosen}", text_color=TEXT)
            self.sudo_chk.configure(state="normal" if self.chosen.lower().endswith(".sh") else "disabled")
        else:
            self.sel_lbl.configure(text="Выберите файл .sh или .deb (они подсвечены зелёным)",
                                   text_color=MUTED)
        self.btn_ok.configure(state="normal" if self.chosen else "disabled")

    def _install(self):
        path = self.chosen
        if not path:
            return
        cmd = install_command(path)
        is_deb = path.lower().endswith(".deb")
        sudo = True if is_deb else bool(self.sudo_v.get())
        if not ask_confirm(self, "Установить?",
                           f"На {self.term.host} будет выполнено в терминале"
                           f"{' с правами root' if sudo and self.term.user != 'root' else ''}:\n\n{cmd}",
                           "Установить"):
            return
        self.close()
        self.on_install(path, cmd, sudo)

    def close(self):
        self.closing = True
        sftp, self.sftp = self.sftp, None
        self.jobs.put(None)
        if sftp is not None:
            threading.Thread(target=lambda: sftp.close(), daemon=True).start()
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Массовый режим: диапазон адресов, определение системы, скрипты
# --------------------------------------------------------------------------
MAX_BULK = 512
BULK_CONFIRM_OVER = 100
BULK_WORKERS = 24
DASHES = "-‐‑‒–—−"


def parse_range(raw):
    """«192.168.1.10-192.168.1.50» или «192.168.1.10-50» (+ «:порт») -> (список IP, порт).
    Ошибку возвращаем как ValueError с понятным текстом."""
    text = clean_addr(raw)
    for d in DASHES[1:]:
        text = text.replace(d, "-")
    port = 22
    m = re.search(r":(\d+)$", text)
    if m:
        port = int(m.group(1))
        text = text[:m.start()].strip()
        if not 0 < port < 65536:
            raise ValueError("Некорректный порт")
    if "-" not in text:
        raise ValueError("Укажите первый и последний адрес через тире: 192.168.1.10-192.168.1.50")
    left, _, right = text.partition("-")
    left, right = left.strip(), right.strip()
    try:
        a = ipaddress.IPv4Address(left)
    except ValueError:
        raise ValueError("Первый адрес указан неверно") from None
    if re.fullmatch(r"\d{1,3}", right):  # сокращённая запись: только последний октет
        if int(right) > 255:
            raise ValueError("Последний адрес указан неверно")
        right = ".".join(left.split(".")[:3] + [right])
    try:
        b = ipaddress.IPv4Address(right)
    except ValueError:
        raise ValueError("Последний адрес указан неверно") from None
    if int(b) < int(a):
        raise ValueError("Последний адрес меньше первого")
    n = int(b) - int(a) + 1
    if n > MAX_BULK:
        raise ValueError(f"Слишком большой диапазон: {n} адресов (максимум {MAX_BULK})")
    return [str(ipaddress.IPv4Address(int(a) + i)) for i in range(n)], port


OSINFO_SCRIPT = r"""
. /etc/os-release 2>/dev/null
echo "##ID=${ID:-}"
echo "##NAME=${PRETTY_NAME:-${NAME:-}}"
echo "##VERSION=${VERSION_ID:-}"
if [ -r /etc/astra_version ]; then echo "##ASTRA=$(head -n1 /etc/astra_version 2>/dev/null)"
elif [ -r /etc/astra/build_version ]; then echo "##ASTRA=$(head -n1 /etc/astra/build_version 2>/dev/null)"
fi
if command -v mate-session >/dev/null 2>&1 || [ -e /usr/share/xsessions/mate.desktop ]; then echo "##MATE=1"; fi
echo "##HOME=$HOME"
echo "##HOST=$(hostname 2>/dev/null)"
"""


def parse_osinfo(text):
    info = {}
    for line in text.splitlines():
        if line.startswith("##") and "=" in line:
            k, _, v = line[2:].partition("=")
            info[k.strip().lower()] = v.strip()
    return info


def classify_os(info):
    """-> (вид, подпись). Виды: astra, mate, linux, windows, unknown."""
    if info.get("windows"):
        return "windows", "Windows"
    ident = info.get("id", "").lower()
    name = info.get("name", "")
    if "astra" in info or ident.startswith("astra") or "astra" in name.lower():
        ver = info.get("astra") or info.get("version") or ""
        return "astra", ("Astra Linux " + ver).strip()
    if "mate" in info or "mate" in name.lower():
        return "mate", "Linux MATE"
    if ident or name:
        return "linux", (name or ident)[:28]
    return "unknown", "Неизвестно"


def exec_raw(client, cmd, timeout=15):
    """Выполнить команду как есть (без bash -c — подходит и для Windows). -> текст вывода."""
    chan = client.get_transport().open_session(timeout=15)
    chan.set_combine_stderr(True)
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    try:
        chan.shutdown_write()
    except Exception:  # noqa: BLE001
        pass
    buf = b""
    while True:
        data = chan.recv(65536)
        if not data:
            break
        buf += data
        if len(buf) > 100_000:
            break
    chan.recv_exit_status()
    return buf.decode("utf-8", "replace").replace("\r\n", "\n").strip()


def detect_system(client, user, password):
    """Определить систему на подключённом хосте. -> dict(kind, label, name, home)."""
    raw = lambda cmd, timeout=15: exec_raw(client, cmd, timeout)

    def windows_host():
        try:
            name = raw("hostname").splitlines()[0].strip()
        except Exception:  # noqa: BLE001
            name = ""
        return {"kind": "windows", "label": "Windows", "name": name, "home": ""}

    if "Windows_NT" in raw("echo %OS%"):
        return windows_host()
    code, out = exec_remote(client, user, password, OSINFO_SCRIPT, False, timeout=20)
    info = parse_osinfo(out)
    if code != 0 or "id" not in info:
        if "Windows_NT" in raw("echo $env:OS"):
            return windows_host()
        return {"kind": "unknown", "label": "Неизвестно", "name": "", "home": ""}
    kind, label = classify_os(info)
    return {"kind": kind, "label": label, "name": info.get("host", ""), "home": info.get("home", "")}


def safe_remote_name(path):
    """Имя файла для удалённого ПК: только basename, без управляющих символов и слэшей."""
    name = os.path.basename(path.replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch >= " " and ch not in '/\\:*?"<>|')
    return name or "file"


def expand_remote_dir(path, home):
    path = path.strip()
    if path == "~" or path.startswith("~/"):
        path = (home.rstrip("/") or "") + path[1:]
    return path


SHORTCUT_MODES = {
    "desktop": "Рабочий стол пользователя, под которым подключились",
    "menu": "Меню приложений для всех пользователей (нужен sudo)",
    "all": "Рабочие столы всех пользователей /home/* (нужен sudo)",
}


DSS_VERSION = "1.2.4"
DSS_SYNC = "/usr/local/sbin/desktop-shortcuts-sync.sh"
DSS_CONF = "/etc/desktop-shortcuts-sync/desktop-shortcuts-sync.conf"
DSS_DEFAULT_SRC = "/usr/share/desktop-shortcuts-sync/source"
# пакет desktop-shortcuts-sync (.deb) — встроен, ставится на Linux MATE, если его там ещё нет
DSS_DEB_B64 = """
ITxhcmNoPgpkZWJpYW4tYmluYXJ5ICAgMTc4NzEyMTgxOSAgMCAgICAgMCAgICAgMTAwNjQ0ICA0ICAgICAgICAgYAoyLjAKY29u
dHJvbC50YXIuenN0IDE3ODcxMjE4MTkgIDAgICAgIDAgICAgIDEwMDY0NCAgMTM0MCAgICAgIGAKKLUv/WQAJ3UpAOa+wDoAc5sO
/P9qgEEoxoz+7iWAoIFNNx3bBGpSqW13R739TbqZyHJfXelHO8Pgx398P73j72xHVdVSKIwNsgCeALcAjwOUc61fKwgSUcDgUqyg
eVrxUTMuQPCOV3hwzmpL+CK4DZmEMYqws1c4ztkRyAN7SviKxa/zSnVpM4hf2XO4koppAxejbtbQorbDxYfNc0ANd8KV4CS8qteU
UnAZC8YynSMn5FULjpIgjrgzqndaHGGzPVirc/TzPKgn4TJtTNNlssFgLhrMlJkwmymT4YhzW05Kskx6zp38+5qzieYTzWWzmS6Z
jQ0ODEYzaTSby0SyBHegKOd5u0CzeqB81xnewqAYX0utIEW9v53NrBbVD+STY8edhRvK7GF/8OMsH+xUyq8TOajQEeE7pV4asQve
VT4Pm4tRMGLYYwh/29zhWmp8HzDT4OAWpTabfLVa4773s5r1Y166LjpX2k+XOiiCqHwl3z8SNsMNeqtn59FXqzg9Dc4EVjm7A61+
6GcftLMpXVg9vm2p1N+vuZ6z5awuLDQS2ysjJBoJEc3E9hZSVI9W6SEYL7SSkFZiRAudhiaifdhCIW2kRCPRVmggtg9biO0ytBKt
RUiroV1E87Cl0B7AOYoAyrHoCyuPy1EkSfdZLC1nEiXBhAcNMjCYQME5jd7KTpKwIwEIwLGh4chwTBsYTPPEnGVhZ5Pl5Nl3TKD1
s6+xa8+55/cXvcDdanvePVG5oWcegxavSpB51kj3rsIy2+9mlJzUmyWktCMPasZXDLB+XkMYzHC6XDKbaWMzuVYXejrQs57o1+pn
M4zyBKdMAigZjgmj2WQ0lylz7cRd3nAD5bPK+JzQihqainMiFzNQaBTLDMqnRalzISNiiNhIHkJiEwngVi/YIFNGkVLwY/UaO09q
9WbUjeS89FV3LjZlMUt/NS+LslhUNtt9ZpT2fUgOepxm2+/Q4ZJFetEx6v+dPIDhFAMxZlL6UIB5R3SOBTgMGHAMr6JfuAc6EW1k
RghJyYrQiQ3EFrKVmNgCgOqoAaWEhmhERGQkSQrDAUCEkDFm4xyyyUQGYsgoQogACYxgCA2JxEgMBYVFTa4By4dGQQ1oFWF71PEf
9Ijhr8guc5hSpNVyqi4lf9ZELDa77e4m0BigpK5hDEBk+nNQCCQaJkl6uDJDxfCp7KBy6lTOHGypxO5jxU2OVFdvu/eV5Zi/Vz9g
iSUPQNoNQLIxBimsulx+BJ/M+RdghnGmugBbT3yXqR2O4NIu4CKJ19HJB4JR2Yd+DB1+Yk2DfdkQVDXhd/a0nciLheqjvES8fMe+
xBaDuSqezM8+VW6gHgNqbvawbssykSatImdywhp7EemN2MHUqGlviIzRlUh7RIg5SCXAnacXKTdFKkDgiD4R26tsXIynJ9FiRWHl
sNGmDxbQyhWRgdusGj506yUmrAHYrNN3WC5UL3LnvwvFITX8Uq9UX97rVqFVhKOrmcrH1RlcCRL6jkSkv2PsOua0rcB1sgOQlnBQ
B6TonQt+NjPfHW5L1lMYz/1P8ebcfXOxmMiFPtFu1jd5x3WWzbG/VZL5ABCKZpJzCNJV5iXGHTOnbJZS7oJEOKxog2ZkRaQAGO8E
Gad/zXXEWsEHHvPMJYY6yjeuILFLsbQ4oFCfRwXAmXmysoTH8Z+gQtaU/qI9idgQtZ9EzZ9SlrWjdeCDjLWqAKdys50RSn+KnYNg
rlZTL1Tf3Ub9kYSCWcfdMeWrzQMePSeBgYUAIAnAQ6vgZx/g0P2+haPvErn2wJZkYXRhLnRhci56c3QgICAgMTc4NzEyMTgxOSAg
MCAgICAgMCAgICAgMTAwNjQ0ICA2OTA3ICAgICAgYAootS/9ZACfbdcAOoEUG0Og0rEOAK/Dq1/kp8Ra8Dme7mpmZmYcdZEiy352
0T+H2VgznCrbu+PZ6/zFoy3ot+nw0D/WtRXMPXbXpcnxcqzluDACqAGUAaUBIUkiTWPMXz7XXmPW1ULJIEh53J4PdAvLtiYcXSvA
vKM0KuWruqYuim5pwKJ15y/tyUIfdtWXgd0Y5fCa/j8Ljc8HqNZd43eFUd0Sr60DFhJpCkes+HzD0ogESXEE0xaPu8zHoUfUiiS1
KlMkKQ5UEKRKdK02E5LhcJg7BMm/P1Yzd9xkW8zmx6vZzN3HgGYnVlRUVCAQd8Ex6iTFYFWSpERYBdF2ZhmsFoPB5hraTPYw/4b5
RSUgy2H658y0G84MA8IIikaVKSYo2jvhuI+A7bm68JLvkZ/c4t/k7oVsyLOenNvjeJhlX2Li4INu0GQSLy4LwmeF1IlJglKByomO
DQHJP4PjKOxyO4dPVgfh7gI5lMqkpCRRrbSH2YYsJMf++5s5xFwzZIvJH5N/ZNhck19UG5I3zDeb7WNDjsk3Wv/OTJM1zDG5ZobN
PjJNzjAPyTdZhfwd4Zc4RmSJHMdwJJFItXXRr0QaQbSV6bvHLjSGXVbfrxvDdJ6WTKRQDDlMomk0GpEjGnPwi10X5viHz7i7OErP
QRJNHKSRJJVCkTSWxFLJFHKAXyg3/ZBnvbSmIfAt6/fD/OV8t9v+771d8ZuQL1z4kPTsesY0nvJ1G/F9H42MFqyqO7NNRsEruC0O
l60L/tONBG+dLe88wE/dFgt80RZ2xT8cAM6uaonC/QSrybCZR+b+hfenJ4fT9BR5Z7kH6/45zmNZVwtWEBmf435kf2c7C9uDVkOm
KeO+slrT6FUVP15jOBqSlBkPz01d/YW6/9jv3Mw6shgyzfP9Lgvlr1vOij+k5cPn7X4uxfLq+v+vZ97rSg6vLgoc1iQp5EgEktEo
RHygcnKSijCmpFIqBRqkJY5CQpbRKJQAgipJqVYamKhIOJp9mvHQAQJ8y3Me3Kv7WTTHoRKkqJDEfhaDzb+/43QB0DEKSVIcSTZG
TaNxJMXRqPR1mfPFnp2rOPu21v5pC/OUNZHkq5d4hTVGR9q1JaJQvkLQquiWFc/l/A6Dcha6OfwiGZfnrBt3Dnf/7MlZW2V5X2He
gDSSRjBvrTGrXV06GK9QHpPSMW9dFH9lVnTLikV3dbMn6rIomMfqiO7/bvy/IkuloLu7R8HDE0OmIX8K2YXMAoZMSIPo5MKEDEIW
AZ779/4Q8vegGJ8xL3iAS3Xd5y4vc+K+GttMFnmrBCkuCr3sbDIOs4uTqAf8F+kA/cT6+tKu77vC9D91n3IRzuMDUSEKdmGYl+n5
5Yu3/yMvwRSKygIPCWDbQcNJ+JjhvD2qGyTdIkgDlRUGq1RJqaAEwgSQT+1/OfzS6dcJc+KXrNu/m7eR1dB4y2V5hPj9HsdxHNDX
N/iyghh0Yjsv+oO0EtQPkFfjdoXfmFN8Vvzy1k7bVe1cXduzVcYth2EXqr1u6GerqjmdHeQjsplltJyTSkWlSnEh+a/9qrpqkef6
C4r1/K37F2pfsA0S333N7YZc2zc6836RLQaNiMhzosd7IKuPE8rHacHHpx+79bEjfAwAeR6fWrNQlxt73bsfXlOYloZBGZC4rKmV
kZV9IvdJq/xd33dd+2PeP6EgW8zGcbb9Q7SYjVvsS3zgmcmfN5TTM4N1P7VZzVQymUjhaCqVRpLUeeumGj077+lpISAMCfn1uX5y
PyJC2ZM8JZtZiDSijTVP/EehAt9r8/u+d71Apjd+9zqIJv+Ab+43zd8iWjD473vKtP5FT7MeLTJNFjR9E9IzIU9ld/yeLjz9WPYs
w0/8xHLd/wLzpjEZ5msCBEax9Kjva1l+X1e1aZpan7gfTptsjJo6Z0jr/w7Ri4JMc+f1zDPN7msz8wvyET3Ra27GfQ1WwjFf+7w2
YaYZI4Q5Q/7PxGCapqdj6/61aZowTPNPKOTf+qPd4Mw1WeTFnLyOdT8KSyE5SltfCqVkJKUqhSYSdUefSHP6aRzQyedHDODGLRdW
S9F+Cvvj9XzJqGvstjScoKEbMVmHmYlwxNne4XUFPt7v2XqYtxxgmfE61IDudahexihnWRe1gR5E+220UwvLuE80T6hEEGlkMpFG
kjgqfVzKe+eo6/XO421xGtTco57TOn31fr7egO86j3SzLyzLy3R1fXmKW5eLupzP+Wp9UeBa5HuIhKeZEQDSrqoNcq0B5BmJgDz/
FKQxcNr7VbhAgQIJYihwV7vGLR6vj1olMOng+YAhvqCbZ+tmOR5m2PgyWQl5hYsQcwcR7WUHiNCoJPWri1AiIhEAAAACs2AAGBgQ
zOaiAWEY5wDk5udnpsFy4CRCBNIQwwEMA0EoRgxQAABAADADQyA0XQK3w6/DnuQjt1KG2K+ObOcBl7fh3XeKKY/nTpxXbpP8Eyx/
W0Kaz0LEIXmbXpgjByGSYzhVlWppns1GKihIOBZXYwGshiNLqsSNlTnhirJlUXIYSLn1GAMK5EZHgi4JHR0rbSDKRmTiHGDvphN7
c31R/lYvSarOSyD2dn2meT8aKW/Ay5vQ58ZQG1cRyVzf0nk2MIhYzBP5vi6dfBt0eLBvHH2hKq7oTvUTp5sByUrqB9jAvgkSoOc0
YT9VgRU3oM/pyQGCk/Y9W0yijeC+0/zpH91H8LZR6trUpn7jLV5FBzSogLEHg3aDhSikhVSzh/wyNiMZCd0VvYsOs0URHIkpSHLQ
RshDW7caE/I/dDJz86pmmvZAG2MOJDkMINZNWKcdNi3W/FP8KY9BFk2EyZ1FMi2fRXdJYOrAjBREWtBQaDVoNXwOIx9/gEjiSLit
HE2yVQmXB8ZcoM5BR3CqS788TqxJ3emFZOLLpXGew486SWMozmftkUtKUusvRy/qx64mBcBl/1Jaq0/Zh3uxLNHCyAG/1sVWtO6A
JCBlMIlVn/DzMgVVVNiBEVArWcEwJ45dHKwWcTv5Yxb4A+8g0K4egeVegs5SzglPTtRZCAsNKgdUfEhM/AUWem9tXahhbzKQ3bvu
ifjJKlDtTsMz0Qkj6dFxD40hDmB0bRR7BIr45Ff2mm30d8HnEKzl2uW0KNR2d0WssI3GgPa1d9yJcmU1qQsgFnFobLGjRYAXgb2b
AqavYO+PGZabOadS14JFSdmkX3XxWr3h5AoYJzQOO/iRCcp1tN71l/mm2hZ8B2h126C95Z8wbfb2was02JDkz6OlWi5yelMbY2AD
IyJkkd/kL4vnat1sGc6LsGbPP8vYf2Y5dah85wbgbhZPBbHQMF00d5nRsCY5KKMw3r7zeMmmzTRR2OPWx0XnhqE8/jWii1G/Hbs+
r8Ukm4lXGDagU8rmGgfSMJ4tbwlY/wSZmS57Hleizl6WuYsheiNFNhCPq2o+ODBys8MLYsMxzz4qZ06FmlBK2yafFku4yxmRsmMv
KpORAZJ6wEflWREjy+DZ03mmxhKbBVWHU7mNySlfo+uLuXgRlGlBgjvXq+NYNJog74B+Ximm4yWv+QBDIa8mu+eu4FMoW+Owi+pN
5TUMq6aHtlniF1Hyeqz4Lm4DP2p4wTveDvgAMSuSY+v5brC8wfWpydsXcE0UgTuuj3Edb5kIoD16T3dxRJ2UkIs0NwpP/rY1thsr
cuPdtoYhSfoNqVpj8wzhoMh5kes/fqmslMbzDg6nDZg5i1Jz6+YN6GX5v75/ye3bJ6aEOuLsX0C9SrtkGQdK0Bdct1nbiVrVYSLd
eQiTHNUB57Bw49HG44In2W60yg5cQm+wFwKtM6Iy+pVJ+BxhSOWGITqosWOJ+QmmHPY9ZzWzlRpHS6h7epkVKGCiKDqaV7MTCmbu
sRjl6CdpsXxjYytzWRTIKTmIreLewEpSLjGYqeATiPrIgvGuB+qtUmI2YEEIqBDgS+30AUtDZwxcQA/UBqArqPs4tCS6EjIlXMwG
2HsB6zgABcUvhReOF9SAG8WA8i2vq+xU0d0KYH5YKQwBwLpHhlvdDNUPvl5MJY6QCte6fAMV0q89idkbGgzMHYo8MBzRkpzq+/OQ
Q8NFqhbkFiCR2S4dA/LVV8Cz+8wUPAXE6VZsmx8KCnB2Z1uZ/VH1F+hnvbdltrXokaD7tFyaKiHtKU9djGp3y0pswgdyTyMiEr4Z
CagJm6CD2pqJfbwUaSx5FvehWrPOom2k/pTs5LQglh+kO0kezA+0Kku2NnkI88Ljpd2Kmb3PYhR3RPrYZerjImaUcVAycEJUiO8Q
BipFNHsJs3IekidNGgFiMv/gLW5EjbqBZ70pcoR/PmIX9J/mkE3QlwLWIpYvAABGqRQXYEGZO63STCdnpWnri+80lr7zYT423eEw
77TYK3h0WRR133MjXYBPaeKv0Z1+56yvYuxNq/3GYDG5AP2AYc5+FObd8EwUTNQz0N8KrtCvIBSmguK6eBsP9mi+zIGlk7CMLQ/P
Pq0wDfD/v/NAVGnyX7knXMi/TfFMEj4y7iZ42lkSJfn1ntVYpLdel//iWExU5KoXvJLfdoajQFHbY9owZysFFkCBugNG+OyGBJaG
eKBNCBlLO2SmP/D+y0ike6L9tLh2g3tTkeCySiZaKkfFlDgGzgUpro9Vc7klnrB607aQUM/CpHsoMsnRKtzZIwyt7NN911sQrBiV
U28gUBUaRwslU7Ao3Q7/9B2SseC9Ipx+jym2V5SMp0Bb1QQU7hRWCaDCABHBAQt86kYH9h34UuC5okodGw/Jt2K3HoWvkYRqiYcc
pA83A9JNvh5clFD4qs2kUA8p3g8d3dzYpQHmWsgMq3Ah+cZScTuyYnaLRCuGYoqlETYdHaUeMMX+3ixq1BAUzDnGHfu3zjGJpqxH
UV81x1ZC/cUOLON+S/gM0ShF9uWt1aiXCtI++ZLx4GXjfpTEMSvpWmsP66BXAwcW76BlEYJ1PAMeG6LlXG67BGKchabik3/I+QQx
hRHTiSd41JcnnZDzbqMD5WUqoeIxCnH2HgJMjrQyGeGVMrGYVOpMCJYpWpOQS9C6weEk1SLXapzmJWA/yErD1HG9MEte2eXy6PPP
LPBAAs2y1PYB50qGnC8WMXyzqVBmu4NHSTy5JuC678s0uwOLH116YQGiYvw5PW7OXYp+sKlrZKW+RZkJNou4LUIHVeuUg7VBzQU9
ctvcL7jOA7ZoWuDr32ZgJQt9/A0Sv/FGnZcHQnl0pxDB7x0gIDDs+/L9kuwWGfgWjsARPI9IgDKb7Ddy9mPFs7cQM+ENZIK3Y5Ut
lSqwFStBuyUyzVshMDWS0vh0TB8yb2HG92ZYLu51GcSwCxXbK8cBD3A5bFOFngeCAoLyt6gZUE0ZYLOL8hr6NRESSKNQ8IeaVs/A
SS//c52RowyjCV728cpTiqS9X49UzENOFkN4zyQaryQj0/MP0JzgurFQ3oHCNJWw6i4Gwy/fR49ufTC631fMagGQIhT+l60l8iFP
KdJ+FsVkfSPBDx0GlWN4nnR6GbAz5iLbVgFqQBRovV3bPneobE0dgKZpXCxwLsgr4858pZ+C/HIIrHIvzFQOsRB7MCfiog9pNXCX
naLArEkrJZ1LhvRdf+QyGi0PdBacJyVYX/y66plSuhY+xQsDI0mi92JY5hjq3+tri2jpshaGybTks3xN76oFEiXJMWdD3Bq+d6Ko
VbOAVubqw0BW1v3s1DXRYowoYBznRD+b8yHy1Io6lJG1Wxf52OOlBrNWDORq9yOAvIOBs9eWJhXDPLaIYwHcKNwaXOGxMxBMQM2z
Uze6QNGMns2gQdURpLfoDECWax8ku/CFVX0CxczHYMytM8mr1H3ztEZpnpnjMKZ35F391qjIhRIMKCG8MxtRpe5lSpZiZ9yCIEgz
JtyBuD4Vwu+ikTHPlxxLBdzQVpdTJ3ZzkTnvH3dsuPMcgoTyVcWVakoQoQsxgJvkkF8I9fy4S62TIKevOpZSNaqEyDP4PLb+r3DL
cQa4h/JBEtbLzJVrt1xKLFinG+2H5NwBEBz6twq47Q+smp2fP/qo5ol1C56tMSshvJIZpOAmhKrc6M2TnscLIBlb9rQ3pNPKwgCr
V1LhM278ZSS/TQA+ShqDVNMV8/qZxlNd8qtaVfnyEI+PwHNtLecKylnGZkXeVccaYeOHT0MvvAnasPBhiXC7kkJF1EoYGKzrBuRL
Iqi95NC80GnYEWYTNyN47ZbIhEbPWI5EzWhRVHkEB9n0CCxYi/EmhgpYOnI5Af19A7/OLSLs2uQE3INdVsEJzbgsiCpbZl8wLvpp
IO4YEqs8ypzJb9o6j8pDV1FyMvYzyhAu9TJfUmaG+5PVdCF4ObhAyIErWit7vWmWyjkeN7Yebenh3j3eWflJ9+UY5Dq1g8Fkg0vM
SVqltDPLgZRE+FHvoOffRYXptix4d1o5WhpAl3by6CIHLgpj7A3NWpKBxvyimhcqjg4h15fMMQMewIOiA+Z/aKpcPd92Wubr4BN+
eNSQpJo8v3bhuOyABTbvoKGeEKUnKjIxW7vCCHjPwsn1Jlo94ChnO4RGTRXP0eOAVMeb41bLCLTuXQFwOnLlSlGc3yldicz7poK8
jvJPwGjBaBEeDspE4TTIU5tcJZe+duZslQem53kLVIfOsb+ZnxaBkiJ0D3zLZJYieRBXl4NpzD/KpjQMK2/g6imtPRV9zV+I23aH
pdI28NWDlFbk6Ha7swx+/cc9HjEJUUqM7nCnwEBf8KfP4P5LkHC+Ds1Az/tuf3Yp6YvJiZPP4bnd2fr5CD4og+ZIFV5xR1NaCXtD
rgHJ/mMzsntl9mIduOarvpkZbs6QWai3QwXlwqzcrWMejYz+BtqWjAtHFLfSAiccWPfuPLvV8GRUh3Jc7xXQY9e8k3DPkKtZBcRS
wZdxezEHMQRFN8dX/URhCY+bmeAAinY5OH8Dk3IZBbWuAiJ3Z9PDrdxE4eMfiUbrHP6zZFSOggAEio1hg9xrKGv5mNC2gOs8VMs2
lpQMzfEe2EDZxZMHlrO2RKXZr1gKixaBCkwc7t86K+BpsAErXC3EAt4B+IKxKnPoe0sq42BYPaJ9xv5oomxx3trdqe23Ru34esI4
DRTgLnDHdDXDzRTuEsb9hk8I2/TTFfL0nlm9pjLsT1QZP8EJgo7fbzenUdRGzsqx+ikp8AVfqo4EKMDMDM/bxl/t/V/Q4w2U4rF8
b03HnqVNf+Ziv8+1T6QDzFZVWxMiLwoil7Jr5hQYX0NLXrzqEdjOVUW+et/WvDnAAmv9pUyuzmo0eMb05VNklCgheFDoQbZamR90
JVQ/osmpdy+hay1OFpp71wPy6N8JPLW4VJRPnjRDuMkJLkkLQ/EUe2u8sFVQocQzYbL8FgoakfX6BGik5QSkR/avzwWOBkPEc+Cy
mE/+mvqVXZ9ReGdqe3JBXc7pH6I3wkc7GPiSKBHttX0UiiHkWyQVv9uBTRF9wBEXEZTvWAifwwMvYB0pOItiBTBy7CMbAKfUjaX6
5jjhy2sqjwue0g0XlaFhox1AbAwIBMVyB2DWFmwcVCpNeDhRxEAMvHolrKJgyw7KMSkScoLFp295Rlj9kqRAY0p4VL217I0b4T9T
LSi6l8APgko9g3SS7F2SHnINdTpXDdk3vXPe16lvlXEfffbtzKXOxSmPxEZLbKpDX6mkndDSWj0hMudjzNa82rx3/CKoVD7Bll9O
edmTC44SdujaVlyKT8tcDJO3IUnShJ3mFKjxx6fxW6PEL8mDc+fG4XpqCRFuMIkani/oeCYgn5qfT4IoecF8nk1L7arsqwoaxqjE
oA/u7FL0ZE3DOak6DNpYjQiT8t6gHRkoDYYkETBI7WSWmuDjGib6CRMy3UOodGiABRFBoR9t241Xb8CVdoEFJKRtC0ju1iLla42i
kJLeon4qlcSNDARAFbAUm3+6fmUotvhlxQbtTLc83kBuuZMj2FhLKaLkyPZuQ572J1IrehMBAJ8yBsyRNLMAyzAJiI9fa5LEoYZC
eFFDHI9Xiie2rbm1fHMPMZE/wqCBZeS55fywJklR1QWOnNEe7kmj61Q6rJJnWQps+lvDWyUgMOcxq6By6+iba8TK5f4CKbKEUMQo
MFlX3MxjnqyZsxwg9kxC6auDF0ViupB8RMmHMZlaKfXmMs5Wdrx63t9fDLHHlx66RsCXUbbxJNVAxV4DZwclnCyGr9tQ/3IGPWz9
EjYkGDHBIPmDPTAqf2JQaUsXgTxagfX41wH3p5e//kSBJdPpb40B92HkjCZTx7FUP+oqWWby7Y8JxhrMAD8g+8CgSYREp+8GowWJ
Kf1VazbZZihx8aI+cs0oNllBj48kMF9H+jcibtcvUQSLsdfSAMyBwAMJsBH/5vyrtvkEh/MGNWD/5p+H5QR9duOOOWepolUCKTFg
mQv4vABuAMGdux4FzPmwmNmA7eYBaKiwCoEpsGXS6zEAPAaWkIYGfxQYfmEV68MlEM4fXOB7GGDvf2cvTNz/QcSJ/bzsiQl8P0By
6W4AKaI4YGBHl2Tr4kIYGqA+L2Rgb52xXXCIFAHRF5pANJo8qJQqv/hEOIisHMrugv5in7igYeXARk+hWg8wJ+c7rLsBuIt+Db48
3FJexsgAm9y1/263zYQW7YHjPhn9AO4mRrFCqbwKTny/BvYm78J7Atlu1DFUQXQ+aQkGA2yPN7iQBGzZZFBsqpjlvAbH+s8D3NnN
0ODexRDz1wKp/sv9b5xKJmgQ7DOABnDUdIAomiCdOGIXODK1AYAD+M/w9zp6AQNSz9RA/wCNRsa8GAQVuZQ630FT0EawfJL2u3KW
Nro3kFSGm8hsdFveYOUXNImbgp4s4lIkmW/+i0NzaThauUO/P0gnqky2g3eH2TwLmNKAGm7y0LQzj25MP8jkBG0jTeN7X1veEAtL
05T34T4n8ZjwJAybHYSYG+bbyVMhEww282cz3RlfpUr3gOpxaRGQoqTqiWcwNCl1oPXx/5wZ2WKFH/4BECmBLPYxtpDgkUo6sL59
NqBFbdjRpViMIFYzjD2VEsUNlOmndjeU+Pm8E7IooN/Qh2vOG8bnuPIiw+6TuF7X779mlRCMtuFU8NXmHxljYql+CYDSAB31gzjf
jVSmIms0GgSb/xCtnJXfi7MUrBJPwcghOb4oDMKDgr7U8IkFuTC+sbdAvbXu5HMAEbJg1YjBm+sTurD/IjmlSkmAlUWOAwDlaMr7
adA/IHVgD7zcPt0OsOCvgHF/wM9NHPfGfUm6vkDf9A+jRQL04VmYCg==
"""


def build_mate_shortcut_script(src, name, deb_b64=None, sync=DSS_SYNC, conf=DSS_CONF, default_src=DSS_DEFAULT_SRC):
    """Linux MATE: ярлык кладётся в папку-источник службы desktop-shortcuts-sync (её создаёт пакет),
    дальше служба сама раздаёт его всем пользователям. Нет службы — сначала ставится пакет."""
    q = shlex.quote
    deb = DSS_DEB_B64 if deb_b64 is None else deb_b64
    return f"""src={q(src)}; name={q(name)}; SYNC={q(sync)}; CONF={q(conf)}; SRC_DIR={q(default_src)}
trap 'rm -f "$src" "$deb"' EXIT
deb=""
if [ ! -x "$SYNC" ]; then
  echo "Служба синхронизации ярлыков не найдена, устанавливаю desktop-shortcuts-sync {DSS_VERSION}"
  deb="$(mktemp /tmp/sshc-dss.XXXXXX)" || {{ echo "Не удалось создать временный файл"; exit 1; }}
  base64 -d > "$deb" <<'B64EOF'
{deb.strip()}
B64EOF
  chmod 644 "$deb"
  export DEBIAN_FRONTEND=noninteractive
  if ! {{ apt-get install -y "$deb" || dpkg -i "$deb"; }} >/tmp/sshc-dss.log 2>&1; then
    tail -n 15 /tmp/sshc-dss.log
    echo "Не удалось установить пакет desktop-shortcuts-sync (нужны rsync и inotify-tools)"
    exit 4
  fi
  [ -x "$SYNC" ] || {{ echo "После установки скрипт синхронизации не найден"; exit 4; }}
  echo "Пакет desktop-shortcuts-sync установлен"
fi
[ -f "$CONF" ] && SRC_DIR="$(. "$CONF" >/dev/null 2>&1; echo "${{SRC_DIR:-$SRC_DIR}}")"
mkdir -p "$SRC_DIR" || {{ echo "Не удалось создать $SRC_DIR"; exit 1; }}
install -m 755 "$src" "$SRC_DIR/$name" || {{ echo "Не удалось записать $SRC_DIR/$name"; exit 1; }}
echo "Ярлык положен в $SRC_DIR/$name"
if "$SYNC"; then
  echo "Ярлык разослан всем пользователям (и в /etc/skel для новых)"
else
  echo "Синхронизация завершилась с ошибкой, см. /var/log/desktop-shortcuts-sync.log"; exit 3
fi
"""


def build_shortcut_script(src, name, mode, home_root="/home", menu_dir="/usr/share/applications"):
    """bash-скрипт установки ярлыка (.desktop). Всё, что приходит снаружи, экранируется."""
    q = shlex.quote
    head = f"src={q(src)}; name={q(name)}\ntrap 'rm -f \"$src\"' EXIT\n"
    find_desk = (
        'desk_of() { local h="$1" d=""\n'
        '  d="$(HOME="$h" xdg-user-dir DESKTOP 2>/dev/null)"\n'
        '  if [ -z "$d" ] || [ ! -d "$d" ] || [ "$d" = "$h" ]; then d="$h/Рабочий стол"; fi\n'
        '  [ -d "$d" ] || d="$h/Desktop"\n'
        '  [ -d "$d" ] && printf "%s" "$d"; }\n')
    if mode == "menu":
        return head + (
            f'menu={q(menu_dir)}\n'
            'mkdir -p "$menu" && install -m 644 "$src" "$menu/$name" || { echo "Не удалось записать в $menu"; exit 1; }\n'
            'update-desktop-database "$menu" >/dev/null 2>&1 || true\n'
            'echo "Ярлык добавлен в меню: $menu/$name"\n')
    if mode == "all":
        return head + find_desk + (
            f'root={q(home_root)}; n=0; bad=0\n'
            'for h in "$root"/*; do\n'
            '  [ -d "$h" ] || continue\n'
            '  u="$(stat -c %U "$h")"; g="$(stat -c %G "$h")"\n'
            '  [ "$u" = root ] && continue\n'
            '  d="$(desk_of "$h")"\n'
            '  if [ -z "$d" ]; then echo "$u: нет папки рабочего стола"; bad=$((bad+1)); continue; fi\n'
            '  if install -o "$u" -g "$g" -m 755 "$src" "$d/$name"; then echo "$u: $d/$name"; n=$((n+1)); '
            'else echo "$u: ошибка записи"; bad=$((bad+1)); fi\n'
            'done\n'
            'echo "Ярлык установлен пользователям: $n"\n'
            '[ "$n" -gt 0 ] || { echo "Подходящих пользователей не найдено"; exit 2; }\n'
            '[ "$bad" -eq 0 ] || exit 3\n')
    return head + find_desk + (
        'd="$(desk_of "$HOME")"\n'
        '[ -n "$d" ] || { echo "Папка рабочего стола не найдена"; exit 2; }\n'
        'install -m 755 "$src" "$d/$name" || { echo "Не удалось записать в $d"; exit 1; }\n'
        'gio set "$d/$name" metadata::trusted true >/dev/null 2>&1 || true\n'
        'echo "Ярлык установлен: $d/$name"\n')


def build_sendfile_script(src, dest_dir, name, make_exec):
    q = shlex.quote
    mode = "755" if make_exec else "644"
    return (f"src={q(src)}; dir={q(dest_dir)}; name={q(name)}\n"
            "trap 'rm -f \"$src\"' EXIT\n"
            'mkdir -p "$dir" || { echo "Не удалось создать папку $dir"; exit 1; }\n'
            f'install -m {mode} "$src" "$dir/$name" || {{ echo "Не удалось записать $dir/$name"; exit 1; }}\n'
            'echo "Файл сохранён: $dir/$name"\n')


# --------------------------------------------------------------------------
# Проверка SMB-программ (mate-smb-tools / Astra SMB Mounter), «PC» -> «PC1», блокировки браузеров
# --------------------------------------------------------------------------
SMB_NEW_VERSION = (1, 6, 1)      # эта версия mate-smb-tools и выше — НОВАЯ, ниже — СТАРАЯ
SMB_RENAME_FROM, SMB_RENAME_TO = "PC", "PC1"

# Только чтение: версия mate-smb-tools, система, имя хоста, Astra SMB Mounter и признаки его старой версии.
SMB_AUDIT_SCRIPT = r'''
export LC_ALL=C
D=/opt/mate_smb_tools
PKG=mate-smb-tools
echo "PKG_STATUS=$(dpkg-query -W -f='${db:Status-Abbrev}' $PKG 2>/dev/null | tr -d ' ')"
echo "PKG_VERSION=$(dpkg-query -W -f='${Version}' $PKG 2>/dev/null)"
if [ -d "$D" ]; then echo "DIR=1"; else echo "DIR=0"; fi
OSN=""
[ -f /etc/os-release ] && OSN=$(. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME")
echo "OS=$OSN"
echo "ASTRA=$(head -n1 /etc/astra_version 2>/dev/null)"
echo "ASTRA_BUILD=$(head -n1 /etc/astra/build_version 2>/dev/null)"
echo "KERNEL=$(uname -r)"
echo "HOST=$(hostname)"
echo "UID_NUM=$(id -u)"
AD=/opt/astra-smb-mounter
if [ -d "$AD" ]; then echo "AS_DIR=1"; else echo "AS_DIR=0"; fi
if [ -f "$AD/VERSION" ]; then AV=$(head -n1 "$AD/VERSION")
elif [ -e "$AD/lib/smb-helper" ]; then AV="7.0"
elif [ -e "$AD/lib/check_admin.sh" ]; then AV="6.7.0"
elif [ -e "$AD/lib/read_admin_pass.sh" ]; then AV="6.5.6"
elif [ -d "$AD" ]; then AV="неизвестная"
else AV=""; fi
echo "AS_VERSION=$AV"
echo "AS_OLD_ADDR=$(grep -rhE '192\.168\.5\.40|10\.35\.[^ ]*\.253' $AD 2>/dev/null | grep -vE '^[[:space:]]*#' | wc -l)"
echo "AS_OLD_SHARE=$(grep -rhE '//(\$SERVER|%s|\$\{?SERVER\}?)/Диск' $AD 2>/dev/null | grep -vE '^[[:space:]]*#' | wc -l)"
'''


def smb_rename_script(old, new):
    """Смена имени «old» -> «new» (hostnamectl, /etc/hostname, строка 127.0.1.1 в /etc/hosts). Нужен root."""
    return f'''
hostnamectl set-hostname {new} 2>/dev/null || hostname {new}
echo {new} > /etc/hostname
sed -i 's/^\\(127\\.0\\.1\\.1[[:space:]]\\+\\){old}\\b/\\1{new}/' /etc/hosts
echo "NEWHOST=$(hostname)"
'''


def build_browser_locks_script(home_root="/home", machine=False, self_delete=True):
    """Удалить устаревшие блокировки браузеров (Singleton*) в ~/.config/*/ всех пользователей.
    Яндекс Браузер и Chromium после смены имени ПК считают, что он открыт на другом компьютере, и не
    запускаются. У пользователя с запущенным браузером блокировки НЕ трогаются.
    machine=True — вывод для разбора программой (LOCK_DEL=/LOCK_SKIP=), иначе — для чтения в терминале."""
    if machine:
        on_run, on_del, on_fail = ('echo "LOCK_SKIP=$F|браузер запущен"', 'echo "LOCK_DEL=$F"',
                                   'echo "LOCK_SKIP=$F|ошибка удаления"')
        tail = 'echo "CLEAN_DONE=1"'
    else:
        on_run = 'echo "  пропущено (у $U запущен браузер): $F"'
        on_del = 'echo "  удалено: $F"'
        on_fail = 'echo "  не удалось удалить: $F"'
        tail = ('echo "Готово. Удалено блокировок: $del, пропущено: $skip"\n'
                'if [ "$del" = 0 ] && [ "$skip" = 0 ]; then echo "Блокировок браузеров не найдено."; fi\n'
                'if [ "$skip" != 0 ]; then echo "Закройте браузер у этих пользователей и запустите команду ещё раз."; fi')
    head = 'rm -f -- "$0"\n' if self_delete else ""
    return f"""{head}ROOT={shlex.quote(home_root)}
RUNRE='^([^ ]*/)?(yandex_browser|yandex-browser|chrome|chromium|chromium-browser|brave|brave-browser)( |$)'
del=0; skip=0
{'' if machine else 'echo "Проверяю блокировки браузеров (Singleton*) в домашних папках…"'}
for H in "$ROOT"/*; do
  [ -d "$H/.config" ] || continue
  U=${{H##*/}}
  RUN=0
  pgrep -u "$U" -f "$RUNRE" >/dev/null 2>&1 && RUN=1
  while IFS= read -r F; do
    if [ "$RUN" = 1 ]; then skip=$((skip+1)); {on_run}
    elif rm -f -- "$F" 2>/dev/null; then del=$((del+1)); {on_del}
    else skip=$((skip+1)); {on_fail}
    fi
  done < <(find "$H/.config" -maxdepth 2 -name 'Singleton*' 2>/dev/null)
done
{tail}
"""


def smb_parse_version(v):
    """'1.6.1-2astra1' -> (1, 6, 1). Пусто/мусор -> (1, 0)."""
    m = re.match(r"\s*(?:\d+:)?(\d+(?:\.\d+)*)", v or "")
    if not m:
        return (1, 0)
    return tuple(int(x) for x in m.group(1).split("."))


def smb_classify(d):
    """mate-smb-tools: NEW (версия >= 1.6.1) / OLD (ниже, в т.ч. 1.0 без записи в dpkg) / NONE."""
    if not (d.get("PKG_STATUS", "").startswith("i") or d.get("DIR") == "1"):
        return "NONE"
    ver = smb_parse_version(d.get("PKG_VERSION", ""))
    n = max(len(ver), len(SMB_NEW_VERSION))
    a = ver + (0,) * (n - len(ver))
    b = SMB_NEW_VERSION + (0,) * (n - len(SMB_NEW_VERSION))
    return "NEW" if a >= b else "OLD"


def _smb_int(d, k):
    try:
        return int(d.get(k, "0") or 0)
    except ValueError:
        return 0


def smb_classify_astra(d):
    """Astra SMB Mounter: NONE / OLD (старые адреса 10.35.*.253, 192.168.5.40, ресурсы «Диск K/U») / OTHER."""
    if d.get("AS_DIR") != "1":
        return "NONE"
    if _smb_int(d, "AS_OLD_ADDR") > 0 or _smb_int(d, "AS_OLD_SHARE") > 0:
        return "OLD"
    return "OTHER"


def smb_is_exact_new(d):
    """Ровно версия 1.6.1 (суффикс сборки не учитывается)."""
    v = smb_parse_version(d.get("PKG_VERSION", ""))
    return len(v) <= 3 and v + (0,) * (3 - len(v)) == SMB_NEW_VERSION


_NV = ".".join(map(str, SMB_NEW_VERSION))
SMB_LABEL = {"OLD": f"СТАРАЯ (ниже {_NV})", "NEW": f"новая ({_NV})", "NONE": "не установлена"}
SMB_ASTRA_LABEL = {"OLD": "СТАРАЯ (10.35.*.253 / 192.168.5.40, Диск K/U)",
                   "OTHER": "установлена, старых адресов нет", "NONE": "не установлена"}


def smb_version_text(r):
    v = r.get("PKG_VERSION", "")
    if not v and r.get("state") in ("OLD", "NEW"):
        v = "1.0"
    return v


def smb_system_text(r):
    parts = []
    if r.get("OS"):
        parts.append(r["OS"])
    if r.get("ASTRA"):
        parts.append("astra_version " + r["ASTRA"])
    if r.get("ASTRA_BUILD"):
        parts.append("build " + r["ASTRA_BUILD"])
    if r.get("KERNEL"):
        parts.append("ядро " + r["KERNEL"])
    return "; ".join(parts)


def smb_parse_kv(text, keys=None):
    d = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if re.fullmatch(r"[A-Z_]+", k):
                d[k] = v.strip()
    return d


def smb_audit_host(client, user, password, old=SMB_RENAME_FROM, new=SMB_RENAME_TO, do_rename=False, do_clean=False):
    """Проверить один ПК; при do_rename (old -> new) и do_clean ещё и выполнить действия
    (только на ПК с mate-smb-tools ровно 1.6.1 и именем old/new).
    -> dict: данные скрипта + state, astra, log[(время, действие, объект, результат)], renamed, locks, error."""
    res = {"error": "", "log": []}

    def log(action, obj, result):
        res["log"].append((datetime.now().strftime("%Y-%m-%d %H:%M:%S"), action, obj, result))

    code, text = exec_remote(client, user, password, SMB_AUDIT_SCRIPT, False, timeout=30)
    d = smb_parse_kv(text)
    if code != 0 or "HOST" not in d:
        res.update(state="ERROR", error=(text or f"код {code}")[:120])
        log("Проверка", "", "ошибка: " + res["error"])
        return res
    res.update(d)
    res["state"] = smb_classify(d)
    log("Проверка", f"версия {smb_version_text(res) or '1.0'}", SMB_LABEL[res["state"]])
    res["astra"] = smb_classify_astra(d)
    log("Проверка Astra SMB Mounter", f"версия {res.get('AS_VERSION') or '-'}", SMB_ASTRA_LABEL[res["astra"]])
    host_up = d.get("HOST", "").upper()
    if res["state"] == "NEW" and smb_is_exact_new(d) and host_up in (old.upper(), new.upper()):
        if host_up == old.upper() and old.upper() != new.upper():
            if do_rename:
                try:
                    c2, out2 = exec_remote(client, user, password, smb_rename_script(old, new), True, timeout=30)
                    ok = any(ln.strip() == "NEWHOST=" + new for ln in out2.splitlines())
                    msg = "" if ok else (out2 or "не удалось сменить имя (нет прав sudo?)")[:80]
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, (str(e) or type(e).__name__)[:80]
                if ok:
                    log("Переименование", f"{old} -> {new}", "OK")
                    res["HOST"] = new
                    res["renamed"] = f"{old} -> {new}"
                else:
                    log("Переименование", f"{old} -> {new}", "ошибка: " + msg)
                    res["renamed"] = "ошибка переименования"
                    res["error"] = msg
            else:
                res["renamed"] = "нужно переименовать (отключено)"
                log("Переименование", f"{old} -> {new}", "пропущено (отключено)")
        if do_clean and res.get("HOST", "").upper() == new.upper():
            try:
                c3, out3 = exec_remote(client, user, password, build_browser_locks_script(machine=True, self_delete=False),
                                       True, timeout=60)
            except Exception as e:  # noqa: BLE001
                c3, out3 = -1, "ERR " + (str(e) or type(e).__name__)
            deleted, skipped, done = [], [], False
            for line in out3.splitlines():
                if line.startswith("LOCK_DEL="):
                    deleted.append(line.split("=", 1)[1].strip())
                elif line.startswith("LOCK_SKIP="):
                    path, _, why = line.split("=", 1)[1].partition("|")
                    skipped.append((path.strip(), why.strip()))
                elif line.startswith("CLEAN_DONE="):
                    done = True
            for p in deleted:
                log("Блокировка браузера", p, "удалена")
            for p, why in skipped:
                log("Блокировка браузера", p, "пропущена: " + why)
            if not done:
                cerr = (out3 or "нет прав sudo?")[:80]
                log("Блокировка браузера", "", "ошибка: " + cerr)
                res["error"] = cerr
            res["locks"] = f"удалено {len(deleted)}" + (f", пропущено {len(skipped)}" if skipped else "") + (
                "" if done else " (ошибка)")
            if done and not deleted and not skipped:
                log("Блокировка браузера", "", "блокировок не найдено")
                res["locks"] = "блокировок нет"
    return res


def smb_audit_summary(r):
    """Короткая строка для таблицы + признак «есть старая версия» (подсветка)."""
    st = r.get("state")
    if st == "ERROR":
        return "Ошибка проверки: " + r.get("error", ""), False
    parts = ["mate-smb-tools " + (smb_version_text(r) or "—") if st != "NONE" else "mate-smb-tools нет"]
    ast = r.get("astra")
    if ast and ast != "NONE":
        parts.append(f"Astra SMB {r.get('AS_VERSION') or '?'}" + (" (старая)" if ast == "OLD" else ""))
    if r.get("renamed"):
        parts.append(r["renamed"])
    if r.get("locks"):
        parts.append("блокировки: " + r["locks"])
    return " · ".join(parts), st == "OLD" or ast == "OLD"


def smb_audit_details(ip, r):
    lines = [f"Адрес: {ip}", f"Хост: {r.get('HOST', '')}", f"mate-smb-tools: {SMB_LABEL.get(r.get('state'), r.get('state'))}"
             + (f"  v{smb_version_text(r) or '?'}" if r.get("state") in ("OLD", "NEW") else "")]
    ast = r.get("astra")
    if ast:
        lines.append(f"Astra SMB Mounter: {SMB_ASTRA_LABEL.get(ast, ast)}" + (
            f"  v{r.get('AS_VERSION') or '?'}" if ast != "NONE" else ""))
    sysx = smb_system_text(r)
    if sysx:
        lines.append("Система: " + sysx)
    if r.get("log"):
        lines += ["", "Действия:"] + [f"  {t}  {a}  {o}  → {res}" for t, a, o, res in r["log"]]
    return "\n".join(lines)


def save_smb_audit_xlsx(path, rows):
    """rows: [(ip, результат smb_audit_host)] -> Excel: лист «mate-smb-tools» и лист «Лог»."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    fills = {"OLD": "F8CBAD", "NEW": "C6EFCE", "NONE": "EDEDED", "ERROR": "FFEB9C"}
    afills = {"OLD": "F8CBAD", "OTHER": "C6EFCE", "NONE": "EDEDED"}

    def style_header(ws):
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="305496")
            c.alignment = Alignment(horizontal="center", vertical="center")
    wb = Workbook()
    ws = wb.active
    ws.title = "mate-smb-tools"
    ws.append(["IP", "Статус", "Версия пакета", "Astra SMB Mounter", "Версия Astra SMB Mounter", "Хост",
               "Переименование", "Блокировки браузера", "Система", "astra_version", "Сборка Astra", "Ядро", "Ошибка"])
    style_header(ws)
    for ip, r in rows:
        st, ast = r.get("state", ""), r.get("astra", "")
        ws.append([ip, SMB_LABEL.get(st, st), smb_version_text(r),
                   SMB_ASTRA_LABEL.get(ast, ast) if ast else "",
                   r.get("AS_VERSION", "") if ast and ast != "NONE" else "",
                   r.get("HOST", ""), r.get("renamed", ""), r.get("locks", ""), r.get("OS", ""),
                   r.get("ASTRA", ""), r.get("ASTRA_BUILD", ""), r.get("KERNEL", ""), r.get("error", "")])
        if fills.get(st):
            for c in ws[ws.max_row]:
                c.fill = PatternFill("solid", fgColor=fills[st])
        if afills.get(ast):
            for col in (4, 5):
                ws.cell(row=ws.max_row, column=col).fill = PatternFill("solid", fgColor=afills[ast])
    for i, w in enumerate([16, 30, 14, 32, 16, 14, 26, 28, 30, 16, 14, 22, 28], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    lg = wb.create_sheet("Лог")
    lg.append(["Время", "IP", "Хост", "Действие", "Объект", "Результат"])
    style_header(lg)
    for ip, r in rows:
        for t, action, obj, result in r.get("log", []):
            lg.append([t, ip, r.get("HOST", ""), action, obj, result])
            color = ("C6EFCE" if result in ("OK", "удалена") else
                     "FFEB9C" if result.startswith(("ошибка", "пропущен")) else None)
            if color:
                lg.cell(row=lg.max_row, column=6).fill = PatternFill("solid", fgColor=color)
    for i, w in enumerate([20, 16, 14, 28, 70, 36], 1):
        lg.column_dimensions[get_column_letter(i)].width = w
    lg.freeze_panes = "A2"
    lg.auto_filter.ref = lg.dimensions
    wb.save(path)


class BulkHost:
    """Один ПК из диапазона: соединение, система, состояние и результат последней команды."""

    def __init__(self, ip, port):
        self.ip, self.port = ip, port
        self.conn = "wait"      # wait | probing | ok | off | auth | err
        self.conn_text = "Ожидание"
        self.kind, self.label, self.name, self.home = "", "", "", ""
        self.client = None
        self.result = ""        # короткий статус последней команды
        self.result_kind = ""   # run | ok | fail | skip
        self.output = ""        # подробный вывод для окна «Подробности»
        self.audit = None       # результат «Проверки SMB» (smb_audit_host) или None

    def alive(self):
        try:
            tr = self.client.get_transport() if self.client else None
            return bool(tr and tr.is_active())
        except Exception:  # noqa: BLE001
            return False

    def close(self):
        c, self.client = self.client, None
        if c is not None:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


def open_client(ip, port, user, password, timeout=8):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, port=port, username=user, password=password, timeout=timeout,
                   banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
    tr = client.get_transport()
    if tr is not None:
        tr.set_keepalive(30)
    return client


def friendly_error(e):
    text = str(e) or type(e).__name__
    low = text.lower()
    if "banner" in low or isinstance(e, EOFError):
        return "SSH-сервер не ответил"
    if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in low:
        return "Превышено время ожидания"
    if "refused" in low:
        return "Соединение отклонено"
    if "no existing session" in low or "unreachable" in low:
        return "Нет маршрута до ПК"
    return text[:60]


def probe_host(h, user, password):
    """Подключиться к ПК и определить систему. Результат записывается в h."""
    h.close()
    h.conn, h.conn_text = "probing", "Подключение…"
    if not tcp_open(h.ip, h.port, 2.0):
        h.conn, h.conn_text = "off", "Нет связи"
        return
    try:
        h.client = open_client(h.ip, h.port, user, password)
    except paramiko.AuthenticationException:
        h.conn, h.conn_text = "auth", "Неверный логин/пароль"
        return
    except Exception as e:  # noqa: BLE001
        h.conn, h.conn_text = "err", "Ошибка: " + friendly_error(e)
        return
    try:
        info = detect_system(h.client, user, password)
    except Exception as e:  # noqa: BLE001
        info = {"kind": "unknown", "label": "Неизвестно", "name": "", "home": ""}
        h.output = "Не удалось определить систему: " + (str(e) or type(e).__name__)
    h.kind, h.label, h.name, h.home = info["kind"], info["label"], info["name"], info["home"]
    h.conn, h.conn_text = "ok", "Подключён"


def ensure_client(h, user, password):
    if not h.alive():
        h.close()
        h.client = open_client(h.ip, h.port, user, password)
    return h.client


def upload_tmp(client, local_path, cancel):
    """Загрузить файл во временное имя на удалённом ПК, вернуть путь."""
    tmp = f"/tmp/sshc-{secrets.token_hex(4)}-{safe_remote_name(local_path)}"
    sftp = client.open_sftp()
    try:
        def cb(done, total):
            if cancel.is_set():
                raise TransferCancelled()
        sftp.put(local_path, tmp, callback=cb)
    finally:
        try:
            sftp.close()
        except Exception:  # noqa: BLE001
            pass
    return tmp


def job_shortcut(h, ctx):
    if h.kind == "windows":
        return "skip", "Пропущен (Windows)"
    client = ensure_client(h, ctx["user"], ctx["password"])
    name = ctx["name"]
    tmp = upload_tmp(client, ctx["file"], ctx["cancel"])
    mode = ctx["mode"]
    if mode == "all" and h.kind == "mate":   # Linux MATE: через службу desktop-shortcuts-sync
        script = build_mate_shortcut_script(tmp, name)
    else:   # Astra и остальные — как обычно
        script = build_shortcut_script(tmp, name, mode, ctx.get("home_root", "/home"),
                                       ctx.get("menu_dir", "/usr/share/applications"))
    code, out = exec_remote(client, ctx["user"], ctx["password"], script, mode != "desktop", timeout=120)
    return ("ok" if code == 0 else "fail"), (out or f"код {code}")


def job_sendfile(h, ctx):
    if h.kind == "windows":
        return "skip", "Пропущен (Windows)"
    client = ensure_client(h, ctx["user"], ctx["password"])
    tmp = upload_tmp(client, ctx["file"], ctx["cancel"])
    dest = expand_remote_dir(ctx["dest"], h.home)
    script = build_sendfile_script(tmp, dest, ctx["name"], ctx["chmod"])
    code, out = exec_remote(client, ctx["user"], ctx["password"], script, ctx["sudo"], timeout=120)
    return ("ok" if code == 0 else "fail"), (out or f"код {code}")


def job_command(h, ctx):
    if h.kind == "windows":
        return "skip", "Пропущен (Windows)"
    client = ensure_client(h, ctx["user"], ctx["password"])
    code, out = exec_remote(client, ctx["user"], ctx["password"], ctx["cmd"], ctx["sudo"], timeout=300)
    return ("ok" if code == 0 else "fail"), (out or "(пустой вывод)") + (f"\n[код выхода {code}]" if code else "")


def job_power(h, ctx):
    client = ensure_client(h, ctx["user"], ctx["password"])
    if h.kind == "windows":
        exec_raw(client, "shutdown /r /t 3" if ctx["action"] == "reboot" else "shutdown /s /t 3", 20)
        return "ok", "Команда отправлена"
    verb = "reboot" if ctx["action"] == "reboot" else "poweroff"
    try:
        code, out = exec_remote(client, ctx["user"], ctx["password"],
                                f"(sleep 1; systemctl {verb}) >/dev/null 2>&1 &", True, timeout=60)
    except (EOFError, OSError, paramiko.SSHException):
        return "ok", "Команда отправлена"
    return ("ok" if code == 0 else "fail"), (out or "Команда отправлена")


def job_smb_audit(h, ctx):
    """Проверка mate-smb-tools / Astra SMB Mounter (+ по желанию «PC» -> «PC1» и блокировки браузеров)."""
    if h.kind == "windows":
        return "skip", "Пропущен (Windows)"
    client = ensure_client(h, ctx["user"], ctx["password"])
    r = smb_audit_host(client, ctx["user"], ctx["password"], ctx["old"], ctx["new"], ctx["rename"], ctx["clean"])
    h.audit = r
    if r.get("HOST"):
        h.name = r["HOST"]
    text, _old = smb_audit_summary(r)
    return ("fail" if r.get("state") == "ERROR" else "ok"), text + "\n\n" + smb_audit_details(h.ip, r)


def job_refresh(h, ctx):
    probe_host(h, ctx["user"], ctx["password"])
    return ("ok", "Обновлено") if h.conn == "ok" else ("fail", h.conn_text)



# --------------------------------------------------------------------------
# Массовый режим: диалоги и вкладка
# --------------------------------------------------------------------------
class FilePickerPanel(ctk.CTkFrame):
    """Выбор файла на этом ПК прямо внутри окна формы (без второго окна и вложенных grab —
    системный проводник и окно поверх окна в Windows подвисали)."""

    def __init__(self, master, title, exts, start, on_pick, on_cancel):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.exts = tuple(e.lower() for e in exts) if exts else ()
        self.on_pick, self.on_cancel = on_pick, on_cancel
        self.result = None
        files_tree_style(self)
        ctk.CTkLabel(self, text=title, font=ui_font(18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(16, 0))
        hint = ("Файлы: " + ", ".join(self.exts)) if self.exts else "Двойной щелчок — открыть папку или выбрать файл"
        ctk.CTkLabel(self, text=hint, font=ui_font(12), text_color=MUTED, anchor="w").pack(
            fill="x", padx=20, pady=(0, 8))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=20, pady=14)
        self.msg = ctk.CTkLabel(row, text="", font=ui_font(12), text_color=DANGER, anchor="w")
        self.msg.pack(side="left")
        primary_button(row, "Выбрать", self._ok, "accent", width=120, height=38).pack(side="right")
        primary_button(row, "Назад", self.on_cancel, "ghost", width=110, height=38).pack(
            side="right", padx=(0, 10))
        self.pane = FilePane(self, "ЭТОТ ПК", self, "local", with_ops=False)
        self.pane.pack(fill="both", expand=True, padx=20)
        self.gen = 0
        self.box = queue.Queue()
        self.after(60, self._poll)
        self.go("local", start or os.path.expanduser("~"))

    # протокол окна для FilePane
    def join(self, side, base, name):
        return os.path.join(base, name) if base else name

    def parent_of(self, side, path):
        return local_parent(path)

    def go(self, side, path):
        """Папка читается в фоновом потоке: медленный диск или сетевая папка не вешают окно."""
        if path in ("Мой компьютер",):
            path = ""
        self.gen += 1
        gen = self.gen
        self.msg.configure(text="Загрузка…", text_color=MUTED)

        def work():
            try:
                self.box.put((gen, path, list_local(path), None))
            except Exception as e:  # noqa: BLE001
                self.box.put((gen, path, None, getattr(e, "strerror", None) or str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                gen, path, entries, err = self.box.get_nowait()
                if gen != self.gen:
                    continue
                if err:
                    self.msg.configure(text=f"Не открыть папку: {err}", text_color=DANGER)
                else:
                    self.msg.configure(text="")
                    self.pane.show(path, entries)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        except Exception as e:  # noqa: BLE001
            try:
                self.msg.configure(text=f"Ошибка списка: {e}", text_color=DANGER)
            except tk.TclError:
                return
        self.after(60, self._poll)

    def on_file_open(self, name):
        self._pick(name)

    def _ok(self):
        sel = self.pane.selected()
        if len(sel) == 1 and not self.pane.is_dir(sel[0]):
            self._pick(sel[0])
        elif len(sel) == 1:
            self.go("local", self.join("local", self.pane.path, sel[0]))
        else:
            self.msg.configure(text="Выберите файл", text_color=DANGER)

    def _pick(self, name):
        if self.exts and not name.lower().endswith(self.exts):
            self.msg.configure(text="Нужен файл: " + ", ".join(self.exts), text_color=DANGER)
            return
        self.result = os.path.join(self.pane.path, name)
        self.on_pick(self.result)


class FormDialog(ModalDialog):
    """Небольшая форма. fields: {key, label, kind: text|multiline|file|check|choice, initial, hint, options, exts}."""
    PICK_W, PICK_H = 780, 540

    def __init__(self, parent, title, intro, fields, ok_text="Далее", validate=None):
        heights = {"text": 82, "password": 82, "multiline": 150, "file": 82, "check": 38}
        h = 170 + (24 if intro else 0)
        for f in fields:
            h += heights.get(f["kind"], 0) if f["kind"] != "choice" else 34 + 30 * len(f["options"])
            h += 20 if f.get("hint") else 0
        self.form_w, self.form_h = 520, min(720, h)
        super().__init__(parent, title, self.form_w, self.form_h)
        self._parent = parent
        self.validate, self.fields, self.vars, self.entries = validate, fields, {}, {}
        self.panel = None
        self.body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.body.pack(fill="both", expand=True)
        body = self.body
        ctk.CTkLabel(body, text=title, font=ui_font(18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=24, pady=(20, 2))
        if intro:
            ctk.CTkLabel(body, text=intro, font=ui_font(12), text_color=MUTED, anchor="w",
                         justify="left", wraplength=470).pack(fill="x", padx=24)
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=24, pady=18)
        self.msg = ctk.CTkLabel(row, text="", font=ui_font(12), text_color=DANGER, anchor="w",
                                wraplength=210, justify="left")
        self.msg.pack(side="left")
        primary_button(row, ok_text, self._ok, "accent", width=120, height=38).pack(side="right")
        primary_button(row, "Отмена", self.destroy, "ghost", width=100, height=38).pack(
            side="right", padx=(0, 10))
        kw = dict(height=38, corner_radius=10, fg_color=SURFACE, border_color=BORDER,
                  text_color=TEXT, font=ui_font(13))
        first = None
        for f in fields:
            k, kind = f["key"], f["kind"]
            if kind == "check":
                var = ctk.BooleanVar(value=bool(f.get("initial", False)))
                ctk.CTkCheckBox(body, text=f["label"], variable=var, font=ui_font(13), text_color=TEXT,
                                fg_color=ACCENT, hover_color=ACCENT_H, border_color=BORDER,
                                checkbox_width=18, checkbox_height=18).pack(anchor="w", padx=24, pady=(14, 0))
                self.vars[k] = var.get
                continue
            ctk.CTkLabel(body, text=f["label"].upper(), font=ui_font(11, "bold"), text_color=MUTED,
                         anchor="w").pack(fill="x", padx=24, pady=(14, 4))
            if kind == "choice":
                var = ctk.StringVar(value=f.get("initial") or f["options"][0][0])
                for val, text in f["options"]:
                    ctk.CTkRadioButton(body, text=text, variable=var, value=val, font=ui_font(13),
                                       text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_H,
                                       border_color=BORDER).pack(anchor="w", padx=24, pady=3)
                self.vars[k] = var.get
            elif kind == "multiline":
                box = ctk.CTkTextbox(body, height=96, corner_radius=10, fg_color=SURFACE, border_width=1,
                                     border_color=BORDER, text_color=TEXT,
                                     font=ctk.CTkFont(family=pick_mono(), size=12))
                box.pack(fill="x", padx=24)
                box.insert("1.0", f.get("initial", ""))
                self.vars[k] = lambda b=box: b.get("1.0", "end").strip()
                first = first or box
            else:
                line = ctk.CTkFrame(body, fg_color="transparent")
                line.pack(fill="x", padx=24)
                ent = ctk.CTkEntry(line, show="•" if kind == "password" else "", **kw)
                ent.pack(side="left", fill="x", expand=True)
                ent.insert(0, f.get("initial", ""))
                self.entries[k] = ent
                if kind == "file":
                    primary_button(line, "Обзор…", lambda e=ent, f=f: self._browse(e, f), "soft",
                                   width=96, height=38).pack(side="left", padx=(8, 0))
                self.vars[k] = (lambda e=ent: e.get()) if kind == "password" else (lambda e=ent: e.get().strip())
                first = first or ent
            if f.get("hint"):
                ctk.CTkLabel(body, text=f["hint"], font=ui_font(11), text_color=MUTED,
                             anchor="w").pack(fill="x", padx=24, pady=(4, 0))
        if first is not None:
            self.after(200, first.focus_set)
        self.bind("<Escape>", self._escape)

    def _escape(self, _e=None):
        if self.panel is not None:
            self._close_panel()
        else:
            self.destroy()

    def _browse(self, entry, f):
        if self.panel is not None:
            return
        cur = entry.get().strip()
        start = os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur)) else None

        def picked(path):
            entry.delete(0, "end")
            entry.insert(0, path)
            self._close_panel()

        self.body.pack_forget()
        self.panel = FilePickerPanel(self, f["label"], f.get("exts"), start, picked, self._close_panel)
        self.panel.pack(fill="both", expand=True)
        self.resizable(True, True)
        center_on(self, self._parent, self.PICK_W, self.PICK_H)

    def _close_panel(self):
        panel, self.panel = self.panel, None
        if panel is not None:
            panel.destroy()
        self.body.pack(fill="both", expand=True)
        self.resizable(False, False)
        center_on(self, self._parent, self.form_w, self.form_h)

    def _ok(self):
        values = {k: get() for k, get in self.vars.items()}
        for f in self.fields:
            if f.get("required", True) and f["kind"] not in ("check", "choice") and not values[f["key"]]:
                self.msg.configure(text=f"Заполните: {f['label']}")
                return
        err = self.validate(values) if self.validate else None
        if err:
            self.msg.configure(text=err)
            return
        self.result = values
        self.destroy()


def ask_form(parent, title, intro, fields, ok_text="Далее", validate=None):
    dlg = FormDialog(parent, title, intro, fields, ok_text, validate)
    parent.wait_window(dlg)
    return dlg.result


def first_line(text, limit=64):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


class BulkTab(ctk.CTkFrame):
    """Массовый режим: таблица ПК из диапазона, кнопки массовых команд и строка состояния."""
    STATE_COLORS = {"ok": OK, "busy": WARN, "off": MUTED, "err": DANGER}
    KIND_NAMES = (("astra", "Astra"), ("mate", "MATE"), ("linux", "Linux"), ("windows", "Windows"))

    def __init__(self, master, app, ips, port, user, password, label):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.app, self.user, self.password, self.range_label = app, user, password, label
        self.hosts = [BulkHost(ip, port) for ip in ips]
        self.by_ip = {h.ip: h for h in self.hosts}
        self.state_kind = "busy"
        self.closing = False
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.dirty = set()
        self.work = queue.Queue()
        self.job = None            # текущая задача: {"title","total","done","ok","fail","skip","silent"}
        self.summary = ("", MUTED)  # итог последней задачи
        self.visible = set()
        files_tree_style(self)

        # --- шапка
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(14, 8))
        ctk.CTkLabel(top, text="Массовое подключение", font=ui_font(18, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(top, text=f"  {label}", font=ui_font(13), text_color=MUTED).pack(side="left")
        primary_button(top, "Отключиться", self.on_close, "ghost", width=120, height=34,
                       font=ui_font(12, "bold")).pack(side="right")
        self.btn_refresh = primary_button(top, "⟳ Обновить", self._cmd_refresh, "soft", width=120, height=34,
                                          font=ui_font(12, "bold"))
        self.btn_refresh.pack(side="right", padx=(0, 8))
        self.pill = ctk.CTkLabel(top, text="", font=ui_font(12, "bold"), text_color=TEXT, fg_color=SURFACE,
                                 corner_radius=14, height=30, padx=14)
        self.pill.pack(side="right", padx=(0, 14))

        # --- кнопки быстрых команд
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        bar.pack(fill="x", padx=18)
        ctk.CTkLabel(bar, text="БЫСТРЫЕ КОМАНДЫ", font=ui_font(11, "bold"), text_color=MUTED).pack(
            side="left", padx=(16, 12), pady=14)
        self.cmd_buttons = []
        for text, cmd, kind in (("Установить ярлык", self._cmd_shortcut, "accent"),
                                ("Отправить файл", self._cmd_sendfile, "soft"),
                                ("Выполнить команду", self._cmd_command, "soft"),
                                ("Проверка SMB", self._cmd_smb_audit, "soft"),
                                ("Перезагрузить", lambda: self._cmd_power("reboot"), "warn"),
                                ("Выключить", lambda: self._cmd_power("poweroff"), "danger")):
            b = primary_button(bar, text, cmd, kind, height=36, font=ui_font(12, "bold"))
            b.pack(side="left", padx=(0, 8), pady=10)
            self.cmd_buttons.append(b)
        self.scope_lbl = ctk.CTkLabel(bar, text="", font=ui_font(12), text_color=MUTED)
        self.scope_lbl.pack(side="right", padx=16)

        # --- таблица
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=18, pady=(10, 0))
        opts = ctk.CTkFrame(mid, fg_color="transparent")
        opts.pack(fill="x", pady=(0, 6))
        self.only_ok = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Только подключённые", variable=self.only_ok, command=self._refill,
                        font=ui_font(12), text_color=MUTED, fg_color=ACCENT, hover_color=ACCENT_H,
                        border_color=BORDER, checkbox_width=18, checkbox_height=18).pack(side="left")
        self.btn_report = primary_button(opts, "Отчёт в Excel…", self._cmd_report, "ghost", width=130,
                                         height=26, font=ui_font(11, "bold"))
        self.btn_report.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(opts, text="Двойной щелчок — терминал · правая кнопка — меню · без выделения команды идут всем подключённым",
                     font=ui_font(11), text_color=MUTED).pack(side="right")
        holder = tk.Frame(mid, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        holder.pack(fill="both", expand=True)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        cols = (("ip", "Адрес", 130, "w"), ("name", "Имя ПК", 130, "w"), ("sys", "Система", 150, "w"),
                ("conn", "Подключение", 150, "w"), ("smb", "mate-smb-tools", 130, "w"),
                ("asmb", "Astra SMB Mounter", 150, "w"), ("res", "Результат", 260, "w"))
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in cols], show="headings",
                                 selectmode="extended", style="Files.Treeview")
        for key, text, w, anchor in cols:
            self.tree.heading(key, text=text, anchor=anchor)
            self.tree.column(key, width=w, anchor=anchor, stretch=(key == "res"), minwidth=90)
        self.tree.tag_configure("t_off", foreground=MUTED)
        self.tree.tag_configure("t_fail", foreground="#ff7b72")
        self.tree.tag_configure("t_ok", foreground="#56d364")
        self.tree.tag_configure("t_skip", foreground=WARN)
        self.tree.tag_configure("t_run", foreground="#7cb2ff")
        sb = ctk.CTkScrollbar(holder, command=self.tree.yview, width=12)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._on_open)
        self.tree.bind("<Return>", self._on_open)
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                            activeforeground="#ffffff", bd=0, font=(UI_FONT, 10))
        self.menu.add_command(label="Открыть терминал", command=self._open_terminal)
        self.menu.add_command(label="Подробности", command=self._on_details)
        self.menu.add_command(label="Копировать адрес", command=self._copy_addr)
        self.tree.bind("<Button-3>", self._on_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_scope())
        self.tree.bind("<Control-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), "break")[1])

        # --- строка состояния
        st = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=46)
        st.pack(fill="x", pady=(10, 0))
        st.pack_propagate(False)
        self.status_dot = ctk.CTkLabel(st, text="●", width=16, font=ui_font(12), text_color=MUTED)
        self.status_dot.pack(side="left", padx=(18, 4))
        self.status = ctk.CTkLabel(st, text="", font=ui_font(13), text_color=TEXT, anchor="w")
        self.status.pack(side="left")
        self.btn_stop = primary_button(st, "Остановить", self._stop, "ghost", width=110, height=30,
                                       font=ui_font(12, "bold"))
        self.btn_stop.pack(side="right", padx=(0, 14))
        self.bar = ctk.CTkProgressBar(st, width=220, height=8, progress_color=ACCENT, fg_color=SURFACE2)
        self.bar.pack(side="right", padx=(0, 14))
        self.bar.set(0)

        self._refill()
        self._update_scope()
        for _ in range(min(BULK_WORKERS, len(self.hosts))):
            threading.Thread(target=self._worker, daemon=True).start()
        self._start_job("Подключение", job_refresh, self.hosts, silent=True)
        self.after(60, self._pump)

    # -- протокол вкладки ---------------------------------------------------
    def tab_label(self):
        return f"Массовый · {len(self.hosts)} ПК"

    def title(self):
        return f"{APP_NAME} — массовый режим: {self.range_label}"

    def on_show(self):
        self.after(100, self.tree.focus_set)

    def is_alive(self):
        return (not self.closing) and (self.job is not None or any(h.conn == "ok" for h in self.hosts))

    def on_close(self):
        self.on_close_quiet()
        self.app.remove_tab(self)

    def on_close_quiet(self):
        self.closing = True
        self.cancel.set()
        hosts = list(self.hosts)

        def run():
            for h in hosts:
                h.close()
        threading.Thread(target=run, daemon=True).start()

    # -- потоки ------------------------------------------------------------
    def _worker(self):
        while True:
            fn = self.work.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def _start_job(self, title, fn, targets, silent=False, **extra):
        self.cancel.clear()
        ctx = dict(extra, user=self.user, password=self.password, cancel=self.cancel)
        job = {"title": title, "total": len(targets), "done": 0, "ok": 0, "fail": 0, "skip": 0,
               "silent": silent, "show_ok": extra.get("show_ok", False)}
        self.job = job
        self.summary = ("", MUTED)
        for h in targets:
            if not silent:
                h.result, h.result_kind, h.output = "В очереди", "run", ""
            self.dirty.add(h.ip)
            self.work.put(lambda h=h: self._task(h, fn, ctx, job))
        self._set_buttons(False)
        self._update_status()

    def _task(self, h, fn, ctx, job):
        if self.closing:
            return
        if self.cancel.is_set() and not job["silent"]:
            status, text = "skip", "Отменено"
        else:
            if not job["silent"]:
                h.result, h.result_kind = "Выполняется…", "run"
                self.dirty.add(h.ip)
            try:
                status, text = fn(h, ctx)
            except TransferCancelled:
                status, text = "skip", "Отменено"
            except Exception as e:  # noqa: BLE001
                status, text = "fail", (str(e) or type(e).__name__)
        if job["silent"]:
            status = "ok" if h.conn == "ok" else "fail"
        else:
            h.result_kind = status
            h.output = text
            h.result = text if status == "skip" else (first_line(text) if status == "fail" or job["show_ok"]
                                                      else "Готово") or ("Готово" if status == "ok" else "Ошибка")
        self.dirty.add(h.ip)
        with self.lock:
            job[status] += 1
            job["done"] += 1

    # -- отображение --------------------------------------------------------
    def _row(self, h):
        if h.result_kind == "fail":
            tag = "t_fail"
        elif h.result_kind == "ok":
            tag = "t_ok"
        elif h.result_kind == "skip":
            tag = "t_skip"
        elif h.result_kind == "run":
            tag = "t_run"
        elif h.conn in ("auth", "err"):
            tag = "t_fail"
        elif h.conn in ("off", "wait", "probing"):
            tag = "t_off"
        else:
            tag = ""
        a = h.audit or {}
        if a and a.get("state") != "ERROR":
            smb = smb_version_text(a) + (" (старая)" if a.get("state") == "OLD" else "") if a["state"] != "NONE" else "нет"
            ast = a.get("astra", "")
            asmb = ("" if not ast else "нет" if ast == "NONE" else
                    f"{a.get('AS_VERSION') or '?'}" + (" (старая)" if ast == "OLD" else ""))
        else:
            smb = asmb = ""
        if h.result_kind == "ok" and a and (a.get("state") == "OLD" or a.get("astra") == "OLD"):
            tag = "t_fail"       # найдена старая версия — подсветка красным
        vals = (h.ip, h.name or "—" if h.conn == "ok" else "", h.label if h.conn == "ok" else "",
                h.conn_text, smb, asmb, h.result)
        return vals, ((tag,) if tag else ())

    def _show(self, h):
        return h.conn == "ok" or not self.only_ok.get()

    def _refill(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.visible = set()
        for h in self.hosts:
            if self._show(h):
                vals, tags = self._row(h)
                self.tree.insert("", "end", iid=h.ip, values=vals, tags=tags)
                self.visible.add(h.ip)
        keep = [i for i in sel if i in self.visible]
        if keep:
            self.tree.selection_set(keep)
        self._update_scope()

    def _flush(self):
        if not self.dirty:
            return
        ips, self.dirty = self.dirty, set()
        need_refill = False
        for ip in ips:
            h = self.by_ip[ip]
            shown = ip in self.visible
            if self._show(h) != shown:
                need_refill = True
                break
            if shown:
                vals, tags = self._row(h)
                self.tree.item(ip, values=vals, tags=tags)
        if need_refill:
            self._refill()
        self._update_header()
        self._update_scope()

    def _update_header(self):
        n_ok = sum(1 for h in self.hosts if h.conn == "ok")
        parts = [f"Подключено {n_ok} из {len(self.hosts)}"]
        counts = Counter(h.kind for h in self.hosts if h.conn == "ok")
        for kind, name in self.KIND_NAMES:
            if counts.get(kind):
                parts.append(f"{name} {counts[kind]}")
        if counts.get("unknown"):
            parts.append(f"? {counts['unknown']}")
        self.pill.configure(text="  ·  ".join(parts))

    def _set_buttons(self, enabled):
        for b in [*self.cmd_buttons, self.btn_refresh]:
            b.configure(state="normal" if enabled else "disabled")
        self.btn_stop.configure(state="disabled" if enabled or (self.job or {}).get("silent") else "normal")

    def _update_status(self):
        job = self.job
        n_ok = sum(1 for h in self.hosts if h.conn == "ok")
        if job:
            d, t = job["done"], job["total"]
            if job["silent"]:
                text = f"{job['title']}: {d} из {t}…  подключено {n_ok}"
            else:
                text = f"{job['title']}: {d} из {t}  ·  успешно {job['ok']}  ·  ошибок {job['fail']}" + (
                    f"  ·  пропущено {job['skip']}" if job["skip"] else "")
            self.status.configure(text=text, text_color=TEXT)
            self.status_dot.configure(text_color=WARN)
            self.bar.set(d / t if t else 1)
            self.state_kind = "busy"
        else:
            text, color = self.summary
            self.status.configure(text=text, text_color=TEXT)
            self.status_dot.configure(text_color=color)
            self.bar.set(0)
            self.state_kind = "ok" if n_ok else "err"
        self.app.tab_changed(self)

    def _finish_job(self):
        job, self.job = self.job, None
        n_ok = sum(1 for h in self.hosts if h.conn == "ok")
        if job["silent"]:
            text = f"Подключено {n_ok} из {len(self.hosts)}" + (
                "" if n_ok else " — проверьте диапазон, логин и пароль")
            color = OK if n_ok else DANGER
        else:
            text = f"{job['title']}: готово  ·  успешно {job['ok']}  ·  ошибок {job['fail']}" + (
                f"  ·  пропущено {job['skip']}" if job["skip"] else "")
            color = DANGER if job["fail"] and not job["ok"] else (WARN if job["fail"] else OK)
        self.summary = (text, color)
        self._set_buttons(True)
        self._refill() if self.only_ok.get() else None
        self._update_header()
        self._update_status()

    def _pump(self):
        if self.closing:
            return
        try:
            self._flush()
            if self.job:
                with self.lock:
                    done = self.job["done"] >= self.job["total"]
                if done:
                    self._flush()
                    self._finish_job()
                else:
                    self._update_status()
        except tk.TclError:
            return
        self.after(80, self._pump)

    # -- выбор целей --------------------------------------------------------
    def _selected_hosts(self):
        return [self.by_ip[i] for i in self.tree.selection() if i in self.by_ip]

    def _targets(self):
        sel = self._selected_hosts()
        pool = sel if sel else self.hosts
        return [h for h in pool if h.conn == "ok"], bool(sel)

    def _update_scope(self):
        sel = self._selected_hosts()
        n = len([h for h in (sel or self.hosts) if h.conn == "ok"])
        self.scope_lbl.configure(text=(f"Выбрано: {len(sel)} · подключено: {n}" if sel
                                       else f"Цель: всем подключённым: {n}"))

    def _prepare(self, what):
        """Вернуть (цели, текст «на N ПК») или None, если выполнять нечего."""
        targets, chosen = self._targets()
        if self.job:
            return None
        if not targets:
            self.summary = ("Нет подключённых ПК для выполнения команды", WARN)
            self._update_status()
            return None
        scope = "выбранных" if chosen else "всех подключённых"
        return targets, f"{what}\n\nБудет выполнено на {len(targets)} ПК ({scope})."

    # -- команды ------------------------------------------------------------
    def _cmd_shortcut(self):
        form = ask_form(
            self, "Установить ярлык",
            "Файл ярлыка (.desktop) берётся с этого ПК и копируется на выбранные ПК.",
            [{"key": "file", "label": "Файл ярлыка", "kind": "file", "exts": (".desktop",)},
             {"key": "mode", "label": "Куда установить", "kind": "choice", "initial": "desktop",
              "options": [(k, v) for k, v in SHORTCUT_MODES.items()]}],
            "Далее", validate=lambda v: None if os.path.isfile(v["file"]) else "Файл не найден")
        if not form:
            return
        name = safe_remote_name(form["file"])
        note = ("\nНа Linux MATE ярлык попадёт в папку службы desktop-shortcuts-sync (если службы нет, "
                "она будет установлена) и разойдётся всем пользователям. На Astra — как обычно."
                if form["mode"] == "all" else "")
        prep = self._prepare(f"Ярлык «{name}»: {SHORTCUT_MODES[form['mode']].lower()}.{note}")
        if prep and ask_confirm(self, "Установить ярлык?", prep[1], "Установить"):
            self._start_job("Установка ярлыка", job_shortcut, prep[0], file=form["file"], name=name,
                            mode=form["mode"])

    def _cmd_sendfile(self):
        form = ask_form(
            self, "Отправить файл",
            "Файл с этого ПК копируется в указанную папку на выбранных ПК (Linux).",
            [{"key": "file", "label": "Файл", "kind": "file"},
             {"key": "dest", "label": "Папка на удалённых ПК", "kind": "text", "initial": "~/",
              "hint": "~ — домашняя папка пользователя, например ~/Документы или /opt/tools"},
             {"key": "chmod", "label": "Сделать исполняемым (chmod +x)", "kind": "check"},
             {"key": "sudo", "label": "Записать от имени администратора (sudo)", "kind": "check"}],
            "Далее", validate=lambda v: None if os.path.isfile(v["file"]) else "Файл не найден")
        if not form:
            return
        name = safe_remote_name(form["file"])
        prep = self._prepare(f"Файл «{name}» → {form['dest']}")
        if prep and ask_confirm(self, "Отправить файл?", prep[1], "Отправить"):
            self._start_job("Отправка файла", job_sendfile, prep[0], file=form["file"], name=name,
                            dest=form["dest"], chmod=form["chmod"], sudo=form["sudo"])

    def _cmd_command(self):
        form = ask_form(
            self, "Выполнить команду",
            "Команда выполняется на каждом выбранном ПК (Linux), вывод можно посмотреть двойным щелчком по строке.",
            [{"key": "cmd", "label": "Команда (bash)", "kind": "multiline"},
             {"key": "sudo", "label": "От имени администратора (sudo)", "kind": "check"}], "Далее")
        if not form:
            return
        prep = self._prepare("Команда: " + first_line(form["cmd"], 80))
        if prep and ask_confirm(self, "Выполнить команду?", prep[1], "Выполнить"):
            self._start_job("Команда", job_command, prep[0], cmd=form["cmd"], sudo=form["sudo"], show_ok=False)

    def _cmd_power(self, action):
        word, ok, kind = (("Перезагрузить", "Перезагрузить", "warn") if action == "reboot"
                          else ("Выключить", "Выключить", "danger"))
        prep = self._prepare(f"{word} ПК.")
        if prep and ask_confirm(self, f"{word} ПК?", prep[1], ok, kind):
            self._start_job("Перезагрузка" if action == "reboot" else "Выключение", job_power, prep[0],
                            action=action)

    def _cmd_smb_audit(self):
        form = ask_form(
            self, "Проверка SMB",
            "Проверяет версию mate-smb-tools и Astra SMB Mounter, систему и имя ПК (только чтение). "
            "Старые версии подсвечиваются красным. Ниже — действия для ПК с mate-smb-tools ровно 1.6.1.",
            [{"key": "rename", "label": "Переименовать ПК (имя ниже «было» → «стало»)", "kind": "check",
              "initial": True},
             {"key": "old", "label": "Было", "kind": "text", "initial": SMB_RENAME_FROM},
             {"key": "new", "label": "Стало", "kind": "text", "initial": SMB_RENAME_TO},
             {"key": "clean", "label": "Удалить блокировки браузеров (Singleton*) на ПК с именем «Стало»",
              "kind": "check", "initial": True}],
            "Далее", validate=lambda v: validate_hostname(v["old"]) or validate_hostname(v["new"]))
        if not form:
            return
        acts = []
        if form["rename"]:
            acts.append(f"переименовать «{form['old']}» → «{form['new']}»")
        if form["clean"]:
            acts.append("удалить блокировки браузеров (у кого браузер запущен — пропустить)")
        what = "Проверка SMB-программ" + (". На ПК с mate-smb-tools 1.6.1 будет: " + "; ".join(acts) if acts else
                                          " (без изменений на ПК)")
        prep = self._prepare(what)
        if prep and ask_confirm(self, "Запустить проверку SMB?", prep[1], "Запустить"):
            self._start_job("Проверка SMB", job_smb_audit, prep[0], show_ok=True, old=form["old"].strip(),
                            new=form["new"].strip(), rename=bool(form["rename"]), clean=bool(form["clean"]))

    def _cmd_report(self):
        rows = [(h.ip, h.audit) for h in self.hosts if h.audit]
        if not rows:
            self.summary = ("Сначала выполните «Проверка SMB» — отчёта пока нет", WARN)
            self._update_status()
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Сохранить отчёт", defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
            initialfile=f"smb_audit_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        if not path:
            return
        try:
            save_smb_audit_xlsx(path, sorted(rows, key=lambda r: int(ipaddress.ip_address(r[0]))))
        except ImportError:
            self.summary = ("Для отчёта нужна библиотека openpyxl (pip install openpyxl)", DANGER)
        except Exception as e:  # noqa: BLE001
            self.summary = (f"Не удалось сохранить отчёт: {e}", DANGER)
        else:
            self.summary = (f"Отчёт сохранён: {path}", OK)
        self._update_status()

    def _cmd_refresh(self):
        if self.job:
            return
        sel = self._selected_hosts()
        targets = sel if sel else [h for h in self.hosts if h.conn != "ok"] or self.hosts
        self._start_job("Обновление", job_refresh, targets, show_ok=True)

    def _stop(self):
        self.cancel.set()
        self.btn_stop.configure(state="disabled")

    def _on_open(self, _e=None):
        """Двойной щелчок: у подключённого ПК — терминал в новой вкладке, у остальных — подробности."""
        sel = self._selected_hosts()
        if len(sel) == 1 and sel[0].conn == "ok":
            self._open_terminal()
        else:
            self._on_details()
        return "break"

    def _open_terminal(self):
        sel = self._selected_hosts()
        if len(sel) != 1 or sel[0].conn != "ok":
            return
        h = sel[0]
        self.app.open_session(h.ip, h.port, self.user, self.password)

    def _on_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set([row])
        h = self.by_ip[row]
        multi = len(self.tree.selection()) != 1
        self.menu.entryconfigure(0, state="normal" if (h.conn == "ok" and not multi) else "disabled")
        self.menu.entryconfigure(1, state="disabled" if multi else "normal")
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _copy_addr(self):
        ips = [h.ip for h in self._selected_hosts()]
        if ips:
            self.clipboard_clear()
            self.clipboard_append("\n".join(ips))

    def _on_details(self, _e=None):
        sel = self._selected_hosts()
        if len(sel) != 1:
            return "break"
        h = sel[0]
        lines = [f"Адрес: {h.ip}:{h.port}", f"Подключение: {h.conn_text}"]
        if h.conn == "ok":
            lines += [f"Имя ПК: {h.name or '—'}", f"Система: {h.label}"]
        if h.result:
            lines += [f"Результат: {h.result}"]
        if h.output:
            lines += ["", h.output]
        OutputDialog(self, f"{h.ip} · {h.label or h.conn_text}", "\n".join(lines),
                     ok=h.result_kind != "fail" and h.conn in ("ok", "wait", "probing"))
        return "break"



# --------------------------------------------------------------------------
# Окно терминала с SSH-сессией
# --------------------------------------------------------------------------
CONN_KINDS = {"connected", "data", "closed", "error", "status_text", "sysinfo", "netinfo", "ip_checked",
              "ip_script", "run_script", "printers_probe"}
IPCHANGE_TIMEOUT = 120  # сколько ждать ответа с нового адреса (после запуска скрипта), сек
IP_APPLY_WAIT = 25      # сколько ждать, пока скрипт смены IP начнёт выполняться в терминале, сек


class HostKeyMismatch(Exception):
    pass


def key_fingerprint(key):
    return "SHA256:" + hashlib.sha256(key.asbytes()).hexdigest()[:32]


class PinPolicy(paramiko.MissingHostKeyPolicy):
    """Принимает любой ключ, но если задан «отпечаток» — только такой же (пароль чужому не уходит)."""

    def __init__(self, pin=""):
        self.pin, self.fp = pin, ""

    def missing_host_key(self, client, hostname, key):
        self.fp = key_fingerprint(key)
        if self.pin and self.fp != self.pin:
            raise HostKeyMismatch(hostname)
WATCH_KINDS = {"watch_status", "watch_end"}


def fmt_elapsed(sec):
    return f"{sec // 60}:{sec % 60:02d}"


class TerminalTab(ctk.CTkFrame):
    """Одна SSH-сессия — вкладка главного окна."""
    STATE_COLORS = {"ok": OK, "busy": WARN, "off": MUTED, "err": DANGER}

    def __init__(self, master, cfg, host, port, user, password, app=None):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.app = app
        self.cfg = cfg
        self.host, self.port, self.user, self.password = host, port, user, password
        self._title = f"SSH — {user}@{host}"
        self.state_kind = "busy"

        self.q = queue.Queue()
        self.client = None
        self.channel = None
        self.closing = False
        self.gen = 0
        self.watch_id = 0
        self.watch_cancel = None
        self.watching = False
        self.suppress_auto = False
        self.quick_win = None
        self.sudo_fill_until = 0.0
        self._tail = b""
        self.ip_dialog = None
        self.watch_kind = ""
        self.ip_old_host = None
        self.files_win = None
        self.ip_job = None          # идущая смена IP: token, follow, same, apply_seen...
        self.ip_apply_evt = threading.Event()
        self._mtail = b""
        self.host_fp = ""
        self.pin_fp = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- верхняя панель ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=66)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.pack_propagate(False)

        pill = ctk.CTkFrame(header, fg_color=SURFACE2, corner_radius=18,
                            border_width=1, border_color=BORDER)
        pill.pack(side="left", padx=(18, 0), pady=14)
        self.dot = ctk.CTkLabel(pill, text="●", font=ui_font(14), text_color=MUTED, width=18)
        self.dot.pack(side="left", padx=(14, 4), pady=6)
        self.state_lbl = ctk.CTkLabel(pill, text="Подключение…", font=ui_font(13, "bold"),
                                      text_color=TEXT)
        self.state_lbl.pack(side="left", padx=(0, 16))

        self.btn_disc = primary_button(header, "Отключиться", self.on_close, "ghost", width=120, height=38)
        self.btn_disc.pack(side="right", padx=(8, 18), pady=14)
        self.btn_re = primary_button(header, "Переподключить", self.manual_reconnect, "ghost",
                                     width=140, height=38)
        self.btn_re.pack(side="right", padx=4, pady=14)
        self.btn_off = primary_button(header, "Выключить", lambda: self.power("poweroff"),
                                      "danger", width=110, height=38)
        self.btn_off.pack(side="right", padx=4, pady=14)
        self.btn_reboot = primary_button(header, "Перезагрузить", lambda: self.power("reboot"),
                                         "warn", width=130, height=38)
        self.btn_reboot.pack(side="right", padx=4, pady=14)
        self.btn_quick = primary_button(header, "Быстрые команды", self.open_quick, "accent",
                                        width=170, height=38)
        self.btn_quick.pack(side="right", padx=4, pady=14)
        self.btn_files = primary_button(header, "Файлы", self.open_files, "soft", width=90, height=38)
        self.btn_files.pack(side="right", padx=4, pady=14)

        # --- полоса ожидания (после перезагрузки) ---
        self.strip = ctk.CTkFrame(self, fg_color="#1a2232", corner_radius=12,
                                  border_width=1, border_color="#2b3a57")
        self.progress = ctk.CTkProgressBar(self.strip, mode="indeterminate", width=150, height=6,
                                           progress_color=WARN, fg_color=BORDER)
        self.progress.pack(side="left", padx=(18, 12), pady=14)
        self.strip_lbl = ctk.CTkLabel(self.strip, text="", font=ui_font(13), text_color=TEXT,
                                      anchor="w")
        self.strip_lbl.pack(side="left", fill="x", expand=True)
        primary_button(self.strip, "Отмена", self.cancel_watch, "ghost", width=90, height=30,
                       font=ui_font(12, "bold")).pack(side="right", padx=14, pady=8)

        # --- терминал ---
        term_frame = ctk.CTkFrame(self, fg_color=TERM_BG, corner_radius=14,
                                  border_width=1, border_color=BORDER)
        term_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(10, 6))
        self.view = TerminalView(term_frame, self.send, self.resize_pty)
        self.view.pack(fill="both", expand=True, padx=8, pady=8)

        # --- нижняя панель: логин@адрес + информация о системе ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(footer, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        self.addr_lbl = ctk.CTkLabel(top, text=f"{user}@{host}:{port}", font=ui_font(13, "bold"),
                                     text_color=TEXT)
        self.addr_lbl.pack(side="left")
        self.auto_v = ctk.BooleanVar(value=cfg.auto_reconnect)
        ctk.CTkSwitch(top, text="Автопереподключение при обрыве", variable=self.auto_v,
                      command=self._auto_changed, font=ui_font(12), text_color=MUTED,
                      progress_color=ACCENT, button_color="#c9d4e3", fg_color=BORDER,
                      switch_width=38, switch_height=18).pack(side="right")

        ctk.CTkLabel(footer, text="Система", font=ui_font(11, "bold"), text_color=MUTED,
                     anchor="w").grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.info_box = ctk.CTkFrame(footer, fg_color=SURFACE, corner_radius=12,
                                     border_width=1, border_color=BORDER)
        self.info_box.grid(row=2, column=0, sticky="ew")
        self.info_weights = (3, 3, 3, 4)
        self.info_labels = {}
        self.info_span = {}      # ключ -> (первая колонка, сколько колонок занимает)
        for i, (key, title, row, col, span) in enumerate((
                ("board", "МАТ-ПЛАТА", 0, 0, 1), ("cpu", "ПРОЦЕССОР", 0, 1, 1),
                ("ram", "ОПЕРАТИВНАЯ ПАМЯТЬ", 0, 2, 1), ("disks", "ДИСКИ", 0, 3, 1),
                ("os", "ОПЕРАЦИОННАЯ СИСТЕМА", 1, 0, 2), ("smb", "ПРОГРАММА SMB-ДИСКОВ", 1, 2, 2))):
            self.info_span[key] = (col, span)
            if row == 0:
                self.info_box.grid_columnconfigure(col, weight=self.info_weights[col], uniform="info")
            cell = ctk.CTkFrame(self.info_box, fg_color="transparent")
            cell.grid(row=row, column=col, columnspan=span, sticky="nsew", padx=(14, 6),
                      pady=(9, 9) if row == 0 else (0, 9))
            ctk.CTkLabel(cell, text=title, font=ui_font(10, "bold"), text_color=MUTED,
                         anchor="w").pack(fill="x")
            lbl = ctk.CTkLabel(cell, text="…", font=ui_font(12), text_color=TEXT, anchor="nw",
                               justify="left", wraplength=200)
            lbl.pack(fill="x", pady=(2, 0))
            self.info_labels[key] = lbl
        self.info_box.bind("<Configure>", self._info_resize)

        self.toast = Toast(self)

        self.set_state("busy", "Подключение…")
        self._update_buttons()
        self.after(30, self.pump)
        self.after(250, self.connect)
        self.after(300, self.view.focus_set)

    # -- состояние --------------------------------------------------------
    def title(self, text=None):
        """Без аргумента — заголовок сессии; с аргументом — задать (обновит вкладку и окно)."""
        if text is None:
            return self._title
        self._title = text
        if self.app is not None:
            self.app.tab_changed(self)
        return None

    def tab_label(self):
        return f"{self.user}@{self.host}"

    def on_show(self):
        """Вкладку открыли: фокус в терминал, размер PTY подгоняем под видимую область."""
        self.after(50, self.view.focus_set)
        self.after(80, self.view.apply_size)

    def is_alive(self):
        return self.channel is not None or self.watching

    def set_state(self, kind, text):
        self.state_kind = kind
        self.dot.configure(text_color=self.STATE_COLORS.get(kind, MUTED))
        self.state_lbl.configure(text=text)
        if self.app is not None:
            self.app.tab_changed(self)

    def _refresh_state(self):
        if self.channel is not None:
            self.set_state("ok", f"Подключено · {self.host}")
        else:
            self.set_state("off", "Нет подключения")

    def _update_buttons(self):
        online = self.channel is not None
        busy = self.watching
        for b in (self.btn_reboot, self.btn_off, self.btn_quick, self.btn_files):
            b.configure(state="normal" if online else "disabled")
        self.btn_re.configure(state="disabled" if (online or busy) else "normal")

    def _auto_changed(self):
        self.cfg.auto_reconnect = self.auto_v.get()

    # -- подключение ------------------------------------------------------
    def connect(self, retries=0):
        if self.closing:
            return
        self._drop_connection()
        self.gen += 1
        gen = self.gen
        self.suppress_auto = False
        self.set_state("busy", "Подключение…")
        self.view.reset()
        self._update_buttons()
        cols, rows = self.view.cols, self.view.rows
        threading.Thread(target=self._connect_worker, args=(gen, cols, rows, retries),
                         daemon=True).start()

    def manual_reconnect(self):
        self.stop_watch()
        self.connect()

    def _connect_worker(self, gen, cols, rows, retries):
        attempt = 0
        while True:
            attempt += 1
            client = paramiko.SSHClient()
            policy = PinPolicy(self.pin_fp)
            client.set_missing_host_key_policy(policy)
            try:
                client.connect(
                    self.host, port=self.port, username=self.user, password=self.password,
                    timeout=10, banner_timeout=15, auth_timeout=15,
                    look_for_keys=False, allow_agent=False,
                )
                chan = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
                break
            except paramiko.AuthenticationException:
                client.close()
                self.q.put(("error", gen, "Неверный логин или пароль"))
                return
            except Exception as e:  # noqa: BLE001
                client.close()
                if isinstance(e, HostKeyMismatch) or isinstance(getattr(e, "__cause__", None), HostKeyMismatch):
                    self.q.put(("error", gen, f"По адресу {self.host} отвечает другое устройство "
                                              "(ключ SSH не совпадает с прежним). Пароль не отправлен"))
                    return
                if attempt > retries or gen != self.gen or self.closing:
                    self.q.put(("error", gen, f"Не удалось подключиться: {e}"))
                    return
                self.q.put(("status_text", gen, f"Подключение… попытка {attempt + 1} из {retries + 1}"))
                time.sleep(3)
        if gen != self.gen or self.closing:
            client.close()
            return
        try:
            client.get_transport().set_keepalive(30)
        except Exception:  # noqa: BLE001
            pass
        self.q.put(("connected", gen, (client, chan, policy.fp)))
        while True:
            try:
                data = chan.recv(65536)
            except Exception:  # noqa: BLE001
                data = b""
            if not data:
                break
            self.q.put(("data", gen, data))
        # Штатный выход (exit/logout) присылает код возврата, при обрыве связи его нет (-1).
        # exit_status_ready()/status_event тут не годятся: их выставляет и просто закрытие канала.
        clean = getattr(chan, "exit_status", -1) != -1
        self.q.put(("closed", gen, clean))

    def _drop_connection(self):
        client, self.client, self.channel = self.client, None, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    # -- события из потоков ----------------------------------------------
    def pump(self):
        if self.closing:
            return
        dirty = False
        try:
            while True:
                kind, ident, payload = self.q.get_nowait()
                if kind in CONN_KINDS and ident != self.gen:
                    continue
                if kind in WATCH_KINDS and ident != self.watch_id:
                    continue
                if kind == "data":
                    self.view.feed(payload)
                    dirty = True
                    if self.sudo_fill_until:
                        self._sudo_autofill(payload)
                    if self.ip_job is not None:
                        self._scan_markers(payload)
                else:
                    self._handle(kind, payload)
        except queue.Empty:
            pass
        if dirty:
            self.view.render()
        self.after(30, self.pump)

    def _handle(self, kind, payload):
        if kind == "connected":
            self.client, self.channel, fp = payload
            was_watching, kind = self.watching, self.watch_kind
            self.stop_watch()
            if was_watching and kind == "ipchange" and self.ip_job:
                self.cfg.remember(self.host if self.port == 22 else f"{self.host}:{self.port}",
                                  self.user)
            self.ip_old_host = None
            self.ip_job = None
            self.host_fp, self.pin_fp = fp or self.host_fp, ""
            self.resize_pty(self.view.cols, self.view.rows)
            self._refresh_state()
            self._update_buttons()
            if was_watching and kind == "ipchange":
                self.toast.show(f"IP изменён. Подключено к {self.host}", "ok", 5000)
            else:
                self.toast.show("Подключение восстановлено" if was_watching else "Подключено",
                                "ok", 3000)
            self.view.focus_set()
            self._start_sysinfo()
            if self._files_alive():
                self.files_win.attach(self.client)
        elif kind == "status_text":
            self.set_state("busy", payload)
            if self.watching:
                self.strip_lbl.configure(text=payload)
        elif kind == "closed":
            self.channel = None
            self._drop_connection()
            if self._files_alive():
                self.files_win.on_disconnect()
            if self.watching:
                pass  # перезагрузка уже отслеживается
            elif (not payload) and self.auto_v.get() and not self.suppress_auto:
                self.start_watch("drop")
            else:
                self._refresh_state()
                self.toast.show("Сессия завершена", "info", 3000)
            self._update_buttons()
        elif kind == "error":
            self._refresh_state()
            self._update_buttons()
            self.stop_watch()
            self.set_state("err", payload)
            self.toast.show(payload, "err", 7000)
        elif kind == "watch_status":
            text, elapsed = payload
            self.strip_lbl.configure(text=f"{text}   ·   {fmt_elapsed(elapsed)}")
            self.set_state("busy", text.split("…")[0] + "…")
        elif kind == "watch_end":
            self._watch_finished(payload)
        elif kind == "sysinfo":
            self._show_sysinfo(payload)
        elif kind == "netinfo":
            if self.ip_dialog is not None and self.ip_dialog.winfo_exists():
                self.ip_dialog.set_info(payload)
        elif kind == "ip_checked":
            if self.ip_dialog is not None and self.ip_dialog.winfo_exists():
                self.ip_dialog.set_checked(*payload)
        elif kind == "ip_script":
            self._ip_script_ready(payload)
        elif kind == "run_script":
            self._run_script_ready(payload)
        elif kind == "printers_probe":
            self._printers_probed(payload)
        elif kind == "power_sent":
            self.toast.show(f"{payload}: команда отправлена", "ok", 3500)
        elif kind == "power_failed":
            action, code, text = payload
            self.stop_watch()
            self.suppress_auto = False
            self._refresh_state()
            self._update_buttons()
            self.toast.show("Не удалось выполнить команду", "err", 6000)
            OutputDialog(self, f"Ошибка (код {code})", text, ok=False)

    # -- ввод/размер ------------------------------------------------------
    def send(self, data: bytes):
        chan = self.channel
        if chan is not None:
            try:
                chan.send(data)
            except Exception:  # noqa: BLE001
                pass

    def resize_pty(self, cols, rows):
        chan = self.channel
        if chan is not None:
            try:
                chan.resize_pty(width=cols, height=rows)
            except Exception:  # noqa: BLE001
                pass

    # -- файлы ---------------------------------------------------------------
    def open_files(self):
        if self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        if self.files_win is not None and self.files_win.winfo_exists():
            bring_front(self.files_win)
            return
        self.files_win = FileManagerWindow(self)

    def _files_alive(self):
        return self.files_win is not None and self.files_win.winfo_exists()

    # -- быстрые команды --------------------------------------------------
    def open_quick(self):
        if self.quick_win is not None and self.quick_win.winfo_exists():
            self.quick_win.focus_force()
            return
        self.quick_win = QuickCommandsWindow(self, self.cfg, self.run_quick)
        bring_front(self.quick_win)

    def run_quick(self, c):
        """Команда набирается в открытом терминале: виден вывод, работает Ctrl+C."""
        if self.channel is None:
            self.toast.show("Нет активного подключения", "err")
            return
        if c.get("action") == "set_static_ip":
            self.open_ip_dialog()
            return
        if c.get("action") == "install_file":
            self.open_install_picker()
            return
        if c.get("action") == "create_user":
            self.open_accounts("Создание")
            return
        if c.get("action") == "accounts":
            self.open_accounts()
            return
        if c.get("action") == "set_hostname":
            self.open_hostname()
            return
        if c.get("action") == "printers_auto":
            self.open_printers_auto()
            return
        if c.get("action") == "browser_fix":
            if ask_confirm(self, "Исправить запуск браузера?",
                           "После смены имени ПК Яндекс Браузер и Chromium считают, что профиль открыт на другом "
                           "компьютере, и не запускаются. Будут удалены файлы блокировки Singleton* в "
                           "~/.config/*/ у всех пользователей.\n\nУ кого браузер сейчас запущен — блокировки "
                           "не трогаются (закройте браузер и повторите).", "Исправить"):
                self.run_script(build_browser_locks_script(), True, c["title"])
            return
        if c.get("action") == "time_sync":
            self.run_script(build_timesync_script(), True, c["title"])
            return
        if c.get("action") == "kill_sessions":
            if ask_confirm(self, "Сбросить неактивные учётки?",
                           "Будут завершены сессии всех других пользователей этого ПК, кроме активной "
                           "за монитором и вашего SSH-подключения. Несохранённая работа в них пропадёт.\n\n"
                           "Нужен установленный скрипт terminate_sessions_root.sh — наличие проверю.",
                           "Сбросить", "warn"):
                self.run_script(build_kill_sessions_script(), True, c["title"])
            return
        if c.get("confirm") and not ask_confirm(
                self, c["title"], f"Выполнить в терминале на {self.host}?\n\n{c['cmd']}",
                "Выполнить"):
            return
        self.type_command(c["cmd"], bool(c.get("sudo")), c["title"])

    def type_command(self, cmd, sudo, title):
        """Набрать команду в терминале (с sudo — подставив пароль, когда его спросят)."""
        use_sudo = sudo and self.user != "root"
        line = terminal_line(cmd, use_sudo)
        if use_sudo:  # пароль подставим сами, когда sudo его запросит
            self.sudo_fill_until = time.time() + 20
            self._tail = b""
        # Ctrl+E, Ctrl+U — очистить недописанную строку; затем команда и Enter
        self.send(b"\x05\x15" + line.encode("utf-8") + b"\r")
        self.toast.show(f"Отправлено в терминал: {title}", "info", 2500)
        self.view.focus_set()

    def open_create_user(self):
        form = ask_form(
            self, "Создать учётную запись",
            f"Пользователь создаётся на {self.host}. Пароль не показывается в терминале.",
            [{"key": "login", "label": "Логин", "kind": "text", "hint": "латиница, цифры, _ и -"},
             {"key": "name", "label": "Имя (необязательно)", "kind": "text", "required": False},
             {"key": "password", "label": "Пароль", "kind": "password"},
             {"key": "password2", "label": "Повторите пароль", "kind": "password"},
             {"key": "admin", "label": "Администратор (группа sudo)", "kind": "check"}],
            "Создать", validate=validate_new_user)
        if form:
            self.run_script(build_create_user_script(form["login"], form["name"], form["password"],
                                                     form["admin"]), True, f"создание {form['login']}")

    def open_accounts(self, tab=None):
        if self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        win = getattr(self, "accounts_win", None)
        if win is not None and win.winfo_exists():
            if tab in win.pages:
                win.show_tab(tab)
            bring_front(win)
            return
        self.accounts_win = AccountsWindow(self, tab)

    def open_hostname(self):
        form = ask_form(
            self, "Сменить имя компьютера",
            f"Новое имя (hostname) для {self.host}. Латиница, цифры и дефис.",
            [{"key": "name", "label": "Новое имя", "kind": "text", "hint": "например: pc-10-35-21-15"},
             {"key": "clean", "label": "Удалить блокировки браузеров (иначе Яндекс Браузер/Chromium может не открыться)",
              "kind": "check", "initial": True}],
            "Сменить", validate=lambda v: validate_hostname(v["name"]))
        if form:
            new = form["name"].strip()
            extra = ("\n\nПосле смены будут удалены блокировки браузеров (Singleton*) у пользователей, "
                     "у которых браузер не запущен." if form.get("clean") else "")
            if ask_confirm(self, "Сменить имя компьютера?", f"{self.host}: имя станет «{new}».\n\n"
                           "Изменятся hostname, /etc/hostname и строка 127.0.1.1 в /etc/hosts "
                           f"(копия — /etc/hosts.sshc-bak).{extra}", "Сменить"):
                self.run_script(build_hostname_script(new, clean_locks=bool(form.get("clean"))), True,
                                f"имя компьютера → {new}")

    def open_printers_auto(self):
        """Сначала проверяем на ПК, установлено ли автовключение принтеров, затем предлагаем действие."""
        if self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        self.toast.show("Проверяю автовключение принтеров…", "info", 2000)
        threading.Thread(target=self._printers_probe_worker, args=(self.gen, self.client), daemon=True).start()

    def _printers_probe_worker(self, gen, client):
        try:
            code, text = exec_remote(client, self.user, self.password, build_printers_probe_script(), False, timeout=20)
            info = parse_printers_probe(text)
        except Exception as e:  # noqa: BLE001
            info = {"error": f"{type(e).__name__}: {e}"}
        self.q.put(("printers_probe", gen, info))

    def _printers_probed(self, info):
        title = "Автовключение принтеров"
        if self.channel is None:
            return
        if "error" in info:
            self.toast.show(f"{title}: не удалось проверить — {info['error']}", "err", 6000)
            return
        if not info["installed"]:
            if not info["cups"]:
                ask_confirm(self, title, "Автовключение принтеров не установлено, а CUPS на этом ПК не найден — "
                            "сначала нужно установить CUPS (sudo apt install cups).", "Понятно")
                return
            if ask_confirm(self, title,
                           "Автовключение принтеров на этом ПК НЕ установлено.\n\nУстановить? Будет создан таймер "
                           "systemd, который каждые 30 секунд включает отключившиеся принтеры CUPS "
                           "(журнал /var/log/cups-activate.log).", "Установить"):
                self.run_script(build_printers_script("install"), True, title + ": установка")
            return
        state = "таймер включён и работает" if info["active"] == "active" else (
            f"таймер установлен, но не запущен ({info['active'] or 'неизвестно'})")
        form = ask_form(self, title, f"Установлено: {state}.\nЧто сделать?",
                        [{"key": "do", "label": "Действие", "kind": "choice", "initial": "status",
                          "options": [(k, PRINTERS_ACTIONS[k]) for k in ("status", "test", "reinstall", "uninstall")]}],
                        "Выполнить")
        if not form:
            return
        do = form["do"]
        if do == "uninstall" and not ask_confirm(self, title, "Удалить автовключение принтеров с этого ПК?",
                                                 "Удалить", "warn"):
            return
        self.run_script(build_printers_script("install" if do == "reinstall" else do), True,
                        f"{title}: {PRINTERS_ACTIONS[do].lower()}")

    def run_script(self, script, sudo, title):
        """Скрипт загружается на ПК отдельным каналом (так пароли не попадают в терминал),
        запускается в терминале — вывод виден сразу."""
        if self.channel is None or self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        self.toast.show(f"Готовлю: {title}", "info", 2000)
        threading.Thread(target=self._script_upload_worker, args=(self.gen, self.client, script, sudo, title),
                         daemon=True).start()

    def _script_upload_worker(self, gen, client, script, sudo, title):
        try:
            code, text = exec_remote(
                client, self.user, self.password,
                'f=$(mktemp /tmp/sshc-run.XXXXXX) && cat > "$f" && printf %s "$f"',
                False, timeout=20, stdin_data=script.encode("utf-8"))
            if code != 0 or not text.startswith("/tmp/sshc-run."):
                self.q.put(("run_script", gen, ("error", text or f"код {code}", sudo, title)))
                return
            self.q.put(("run_script", gen, ("ok", text.strip(), sudo, title)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("run_script", gen, ("error", f"{type(e).__name__}: {e}", sudo, title)))

    def _run_script_ready(self, payload):
        status, text, sudo, title = payload
        if status != "ok" or self.channel is None:
            self.toast.show(f"Не удалось подготовить «{title}»: {text}", "err", 7000)
            return
        self.type_command(f"bash {text}", sudo, title)

    def open_install_picker(self):
        if self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        if getattr(self, "install_win", None) is not None and self.install_win.winfo_exists():
            self.install_win.focus_force()
            return
        self.install_win = InstallPickerWindow(self, self._install_chosen)

    def _install_chosen(self, path, cmd, sudo):
        """Окно выбора закрыто — возвращаемся к терминалу, установка идёт в нём."""
        top = self.winfo_toplevel()
        top.deiconify()
        top.lift()
        top.focus_force()
        self.type_command(cmd, sudo, f"установка {posixpath.basename(path)}")

    # -- смена IP на статический --------------------------------------------
    def open_ip_dialog(self):
        if self.ip_dialog is not None and self.ip_dialog.winfo_exists():
            self.ip_dialog.focus_force()
            return
        self.ip_dialog = IpChangeDialog(self, self.host)
        threading.Thread(target=self._netinfo_worker, args=(self.gen, self.client),
                         daemon=True).start()

    def _netinfo_worker(self, gen, client):
        try:
            info = collect_netinfo(client, self.user, self.password)
        except Exception as e:  # noqa: BLE001
            info = {"error": f"{type(e).__name__}: {e}"}
        self.q.put(("netinfo", gen, info))

    def check_ip(self, ip):
        """Занят ли новый адрес (отвечает ли на ping) — в фоне, интерфейс не блокируется."""
        threading.Thread(target=self._check_ip_worker, args=(self.gen, ip), daemon=True).start()

    def _check_ip_worker(self, gen, ip):
        self.q.put(("ip_checked", gen, (ip, ping_once(ip, 800))))

    def _set_addr_label(self):
        self.addr_lbl.configure(text=f"{self.user}@{self.host}:{self.port}")
        self.title(f"SSH — {self.user}@{self.host}")

    def apply_static_ip(self, c, data):
        """Загружаем скрипт на ПК (отдельным каналом), затем запускаем его в терминале."""
        if self.channel is None or self.client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        ip = data["ip"]
        token = secrets.token_hex(4)
        script = build_ip_script(c, ip, data["prefix"], data["gw"], data["dns"], token)
        follows = (not is_ip_literal(self.host)) or self.host == c["addr"]
        self.ip_job = {"token": token, "follow": follows and ip != self.host, "ip": ip,
                       "same": ip == self.host, "apply_seen": False, "gen": self.gen}
        self.ip_apply_evt = threading.Event()
        self._mtail = b""
        self.toast.show("Готовлю смену IP…", "info", 2500)
        threading.Thread(target=self._ip_upload_worker, args=(self.gen, self.client, script),
                         daemon=True).start()

    def _ip_upload_worker(self, gen, client, script):
        try:
            code, text = exec_remote(
                client, self.user, self.password,
                'f=$(mktemp /tmp/sshc-ip.XXXXXX) && cat > "$f" && printf %s "$f"',
                False, timeout=20, stdin_data=script.encode("utf-8"))
            if code != 0 or not text.startswith("/tmp/sshc-ip."):
                self.q.put(("ip_script", gen, ("error", text or f"код {code}")))
                return
            self.q.put(("ip_script", gen, ("ok", text.strip())))
        except Exception as e:  # noqa: BLE001
            self.q.put(("ip_script", gen, ("error", f"{type(e).__name__}: {e}")))

    def _ip_script_ready(self, payload):
        job = self.ip_job
        status, text = payload
        if job is None:
            return
        if status != "ok" or self.channel is None:
            self.ip_job = None
            self.toast.show(f"Не удалось подготовить смену IP: {text}", "err", 8000)
            return
        use_sudo = self.user != "root"
        line = terminal_line(f"bash {text}", use_sudo)
        if job["follow"]:
            self.ip_old_host = self.host
            self.pin_fp = self.host_fp  # на новом адресе должен отвечать тот же ПК
            self.host = job["ip"]
            self._set_addr_label()
            self.start_watch("ipchange")
        if use_sudo:  # пароль подставим сами, когда sudo его запросит
            self.sudo_fill_until = time.time() + 20
            self._tail = b""
        self.send(b"\x05\x15" + line.encode("utf-8") + b"\r")
        self.toast.show("Смена IP запущена в терминале", "info", 3000)
        self.after(IP_APPLY_WAIT * 1000, self._ip_noapply_check, job["token"])

    def _ip_noapply_check(self, token):
        job = self.ip_job
        if self.closing or not job or job["token"] != token or job["apply_seen"]:
            return
        self._ip_failed("Скрипт смены IP не запустился (неверный пароль sudo или команда не "
                        "выполнена). Смотрите терминал")

    def _scan_markers(self, data):
        job = self.ip_job
        self._mtail = (self._mtail + data)[-300:]
        tok = job["token"].encode()
        for name in ("APPLY", "OK", "FAIL"):
            m = b"SSHC-" + name.encode() + b"-" + tok
            pos = self._mtail.find(m)
            if pos < 0:
                continue
            self._mtail = self._mtail[:pos] + self._mtail[pos + len(m):]
            if name == "APPLY":
                job["apply_seen"] = True
                self.ip_apply_evt.set()
            elif name == "OK":
                self._ip_ok()
            else:
                self._ip_failed("Не удалось сменить IP — прежние настройки возвращены. "
                                "Подробности в терминале")
            if self.ip_job is None:
                return

    def _ip_ok(self):
        job = self.ip_job
        if job and not job["follow"]:  # сессия пережила смену: адрес тот же либо не наш интерфейс
            self.ip_job = None
            self.toast.show("Настройки сети применены", "ok", 4000)

    def _ip_failed(self, msg):
        self.ip_job = None
        self.pin_fp = ""
        was_ip = self.watch_kind == "ipchange" and self.watching
        if was_ip:
            self.stop_watch()
        self._restore_old_host()
        self._refresh_state()
        self._update_buttons()
        self.toast.show(msg, "err", 10000)

    def _restore_old_host(self):
        if self.ip_old_host:
            self.host, self.ip_old_host = self.ip_old_host, None
            self._set_addr_label()

    def _sudo_autofill(self, data):
        if time.time() > self.sudo_fill_until:
            self.sudo_fill_until, self._tail = 0.0, b""
            return
        self._tail = (self._tail + data)[-200:]
        if sudo_prompt_pending(self._tail):
            self.sudo_fill_until, self._tail = 0.0, b""
            self.send(self.password.encode("utf-8") + b"\r")

    # -- информация о системе -----------------------------------------------
    def _start_sysinfo(self):
        for lbl in self.info_labels.values():
            lbl.configure(text="…")
        gen, client = self.gen, self.client
        threading.Thread(target=self._sysinfo_worker, args=(gen, client), daemon=True).start()
        self.after(40000, self._sysinfo_timeout, gen)

    def _sysinfo_worker(self, gen, client):
        try:
            info = collect_sysinfo(client, self.user, self.password)
        except Exception:  # noqa: BLE001
            info = {}
        self.q.put(("sysinfo", gen, info))

    def _sysinfo_timeout(self, gen):
        if self.closing or gen != self.gen:
            return
        for lbl in self.info_labels.values():
            if lbl.cget("text") == "…":
                lbl.configure(text="н/д")

    def _show_sysinfo(self, info):
        for key, lbl in self.info_labels.items():
            lbl.configure(text=(info or {}).get(key) or "н/д")

    def _info_resize(self, _e=None):
        try:
            scale = self.winfo_toplevel()._get_window_scaling()
            total = sum(self.info_weights)
            width = self.info_box.winfo_width() / scale
            for key, lbl in self.info_labels.items():
                col, span = self.info_span[key]
                w = sum(self.info_weights[col:col + span])
                lbl.configure(wraplength=max(110, int(width * w / total) - 26))
        except Exception:  # noqa: BLE001
            pass

    # -- перезагрузка / выключение ---------------------------------------
    def power(self, action):
        client = self.client
        if client is None:
            self.toast.show("Нет активного подключения", "err")
            return
        if action == "reboot":
            ok = ask_confirm(
                self, "Перезагрузить ПК?",
                f"{self.host} будет перезагружен. Соединение прервётся, а когда ПК снова "
                "появится в сети, я подключусь автоматически.",
                "Перезагрузить", "warn")
        else:
            ok = ask_confirm(self, "Выключить ПК?",
                             f"{self.host} будет выключен. Включить его удалённо не получится.",
                             "Выключить", "danger")
        if not ok:
            return
        if action == "reboot":
            self.start_watch("reboot")
        else:
            self.suppress_auto = True
            self.set_state("busy", "Выключение…")
        cmd = f"systemctl {action}"
        threading.Thread(target=self._power_worker, args=(client, action, cmd),
                         daemon=True).start()

    def _power_worker(self, client, action, cmd):
        try:
            code, text = exec_remote(client, self.user, self.password, cmd, True, timeout=30)
        except Exception:  # noqa: BLE001
            code, text = -1, ""  # обрыв связи на выключении — это нормально
        if code in (0, -1):
            self.q.put(("power_sent", 0, "Перезагрузка" if action == "reboot" else "Выключение"))
        else:
            self.q.put(("power_failed", 0, (action, code, text)))

    # -- ожидание ПК и автоподключение -----------------------------------
    def start_watch(self, kind):
        self.stop_watch()
        self.watch_id += 1
        self.watch_cancel = threading.Event()
        self.watching = True
        self.strip.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.progress.start()
        self.strip_lbl.configure(text="Подготовка…")
        self.watch_kind = kind
        self.set_state("busy", {"reboot": "Перезагрузка…", "ipchange": "Смена IP…"}.get(
            kind, "Связь потеряна…"))
        self._update_buttons()
        threading.Thread(target=self._watch_worker,
                         args=(self.watch_id, kind, self.watch_cancel), daemon=True).start()

    def stop_watch(self):
        if self.watch_cancel is not None:
            self.watch_cancel.set()
        self.watch_id += 1
        self.watching = False
        try:
            self.progress.stop()
        except Exception:  # noqa: BLE001
            pass
        self.strip.grid_remove()

    def cancel_watch(self):
        was_ipchange = self.watch_kind == "ipchange"
        self.ip_job = None
        self.pin_fp = ""
        self.stop_watch()
        if was_ipchange and self.channel is not None:
            self._restore_old_host()  # старое соединение живо — адрес не поменялся
        self._refresh_state()
        self._update_buttons()
        self.toast.show("Ожидание отменено", "info", 3000)

    def _watch_worker(self, wid, kind, cancel):
        host, port = self.host, self.port
        t0 = time.time()

        def say(text):
            self.q.put(("watch_status", wid, (text, int(time.time() - t0))))

        def end(result):
            self.q.put(("watch_end", wid, result))

        # Если ICMP на целевом ПК закрыт — вместо ping смотрим на порт SSH
        use_icmp = False if kind == "ipchange" else ping_once(host)

        def alive():
            return ping_once(host) if use_icmp else tcp_open(host, port, 1.5)

        if kind == "ipchange":
            say("Смена IP… жду запуска скрипта на ПК")
            evt = self.ip_apply_evt
            while not cancel.is_set() and not evt.wait(0.3):
                pass
            if cancel.is_set():
                return
            say(f"Применяю новый IP… жду ответа с {host}")
            deadline = time.time() + IPCHANGE_TIMEOUT
            ok_count = 0
            while not cancel.is_set():
                ok_count = ok_count + 1 if tcp_open(host, port, 1.5) else 0
                if ok_count >= 2:
                    break
                if time.time() > deadline:
                    end("timeout_ipchange")
                    return
                cancel.wait(1.0)
            if not cancel.is_set():
                cancel.wait(1.0)
                end("ok")
            return

        if kind == "reboot":
            say("Перезагрузка… жду, пока ПК отключится")
            fails, deadline = 0, time.time() + 150
            while not cancel.is_set():
                fails = 0 if alive() else fails + 1
                if fails >= 2:
                    break
                if time.time() > deadline:
                    end("timeout_down")
                    return
                cancel.wait(1.0)
        else:  # обрыв связи: возможно, ПК и не перезагружался — или пропала сеть на ЭТОМ компьютере
            say("Связь потеряна… проверяю доступность ПК")
            if not local_net_up(host):
                say("Нет сети на этом компьютере… жду, пока она появится")
                deadline = time.time() + 900
                while not cancel.is_set() and not local_net_up(host):
                    if time.time() > deadline:
                        end("timeout_localnet")
                        return
                    cancel.wait(1.0)
                if cancel.is_set():
                    return
                say("Сеть на этом компьютере появилась… проверяю ПК")
                cancel.wait(2.0)
            if alive() and tcp_open(host, port, 2.0):
                end("ok")
                return

        say("ПК перезагружается… жду появления в сети")
        ok_count, deadline = 0, time.time() + 900
        while not cancel.is_set():
            ok_count = ok_count + 1 if alive() else 0
            if ok_count >= 2:
                break
            if time.time() > deadline:
                end("timeout_up")
                return
            cancel.wait(1.0)

        say("ПК в сети… жду запуска SSH")
        deadline = time.time() + 300
        while not cancel.is_set():
            if tcp_open(host, port, 2.0):
                break
            if time.time() > deadline:
                end("timeout_ssh")
                return
            cancel.wait(1.5)
        if cancel.is_set():
            return
        cancel.wait(2.0)  # дать sshd полностью подняться
        if not cancel.is_set():
            end("ok")

    def _watch_finished(self, result):
        if result == "ok":
            self.strip_lbl.configure(text="ПК в сети — подключаюсь…")
            self.connect(retries=6)
            return
        if result == "timeout_ipchange":
            self.stop_watch()
            self.ip_job = None
            self.pin_fp = ""
            if self.channel is not None:  # старое соединение живо: команда, видимо, не выполнилась
                bad = self.host
                self._restore_old_host()
                msg = (f"IP не изменился: {bad} не отвечает, а прежнее соединение живо. "
                       "Посмотрите вывод команды в терминале")
            elif self.ip_old_host:  # скрипт мог откатить настройки — пробуем прежний адрес
                bad = self.host
                self._restore_old_host()
                self.ip_job = None
                self.pin_fp = ""
                self.toast.show(f"{bad} не отвечает. Проверяю прежний адрес {self.host} — "
                                "возможно, настройки были возвращены", "err", 10000)
                self.start_watch("drop")
                return
            else:
                msg = (f"ПК не отвечает на новом адресе {self.host}. Возможно, он в другой "
                       "подсети. Когда адрес станет доступен, нажмите «Переподключить»")
            self._refresh_state()
            self._update_buttons()
            self.toast.show(msg, "err", 10000)
            return
        self.stop_watch()
        msgs = {
            "timeout_down": "ПК не отключился за 2,5 минуты — перезагрузка, похоже, не началась",
            "timeout_up": "ПК не появился в сети за 15 минут",
            "timeout_localnet": "На этом компьютере не появилась сеть за 15 минут",
            "timeout_ssh": "ПК в сети, но SSH не запустился за 5 минут",
        }
        self._refresh_state()
        self._update_buttons()
        self.toast.show(msgs.get(result, "Ожидание прервано"), "err", 8000)

    # -- закрытие ---------------------------------------------------------
    def on_close_quiet(self):
        """Закрытие программы: просто оборвать связь и фоновые задачи."""
        self.closing = True
        if self.watch_cancel is not None:
            self.watch_cancel.set()
        self._drop_connection()

    def on_close(self):
        self.closing = True
        if self.watch_cancel is not None:
            self.watch_cancel.set()
        if self._files_alive():
            self.files_win.close()
        iw = getattr(self, "install_win", None)
        if iw is not None and iw.winfo_exists():
            iw.close()
        self._drop_connection()
        if self.app is not None:
            self.app.remove_tab(self)
        else:
            self.destroy()


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------
class ConnectPage(ctk.CTkFrame):
    """Форма подключения: адрес, логин, пароль. После «Подключиться» вкладка становится сессией."""
    tab_label_text = "Новое подключение"
    HINT_SINGLE = "IP или имя хоста. Другой порт: 192.168.1.50:2222"
    HINT_BULK = ("Первый и последний адрес через тире: 192.168.1.10-192.168.1.50 "
                 "(или коротко 192.168.1.10-50). Логин и пароль — общие для всех ПК.")

    def __init__(self, master, app):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.app, self.cfg = app, app.cfg
        self.state_kind = "off"

        box = ctk.CTkFrame(self, fg_color="transparent", width=460)
        box.place(relx=0.5, rely=0.44, anchor="center")
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=">_", width=52, height=52, corner_radius=14, fg_color=ACCENT,
                     text_color="#ffffff",
                     font=ctk.CTkFont(family=pick_mono(), size=20, weight="bold")).pack(side="left")
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left", padx=14)
        ctk.CTkLabel(titles, text=APP_NAME, font=ui_font(24, "bold"), text_color=TEXT,
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(titles, text="Удалённое управление ПК по SSH", font=ui_font(13),
                     text_color=MUTED, anchor="w").pack(anchor="w")

        card = ctk.CTkFrame(box, fg_color=SURFACE, corner_radius=18, border_width=1,
                            border_color=BORDER, width=460)
        card.pack(fill="x", pady=(24, 0))
        entry_kw = dict(height=44, corner_radius=12, font=ui_font(14), fg_color=SURFACE2,
                        border_color=BORDER, text_color=TEXT)
        self.mode = "single"
        self.mode_btn = ctk.CTkSegmentedButton(
            card, values=["Один ПК", "Массовый"], command=self._set_mode, height=36, corner_radius=10,
            font=ui_font(13, "bold"), fg_color=SURFACE2, selected_color=ACCENT,
            selected_hover_color=ACCENT_H, unselected_color=SURFACE2,
            unselected_hover_color=SURFACE_HOVER, text_color=TEXT)
        self.mode_btn.set("Один ПК")
        self.mode_btn.pack(fill="x", padx=22, pady=(20, 0))
        self.addr_lbl = ctk.CTkLabel(card, text="АДРЕС ПК", font=ui_font(11, "bold"), text_color=MUTED,
                                     anchor="w")
        self.addr_lbl.pack(fill="x", padx=22, pady=(16, 6))
        self.combo = ctk.CTkComboBox(
            card, values=self.cfg.recent or [""], height=46, corner_radius=12, font=ui_font(15),
            fg_color=SURFACE2, border_color=BORDER, button_color=SURFACE_HOVER,
            button_hover_color=BORDER, text_color=TEXT, dropdown_fg_color=SURFACE2,
            dropdown_hover_color=SURFACE_HOVER, dropdown_text_color=TEXT,
            dropdown_font=ui_font(14), command=lambda _v: self._fill_user())
        self.combo.pack(fill="x", padx=22)
        self.combo.set(self.cfg.recent[0] if self.cfg.recent else "")
        self.addr_hint = ctk.CTkLabel(card, text=self.HINT_SINGLE, font=ui_font(12), text_color=MUTED,
                                      anchor="w", justify="left", wraplength=410)
        self.addr_hint.pack(fill="x", padx=22, pady=(8, 10))

        ctk.CTkLabel(card, text="ЛОГИН", font=ui_font(11, "bold"), text_color=MUTED,
                     anchor="w").pack(fill="x", padx=22, pady=(4, 6))
        self.user = ctk.CTkEntry(card, placeholder_text="Логин", **entry_kw)
        self.user.pack(fill="x", padx=22)
        ctk.CTkLabel(card, text="ПАРОЛЬ", font=ui_font(11, "bold"), text_color=MUTED,
                     anchor="w").pack(fill="x", padx=22, pady=(12, 6))
        self.pwd = ctk.CTkEntry(card, placeholder_text="Пароль", show="•", **entry_kw)
        self.pwd.pack(fill="x", padx=22)
        self.show_v = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(card, text="Показать пароль", variable=self.show_v, command=self._toggle,
                        font=ui_font(12), text_color=MUTED, fg_color=ACCENT, hover_color=ACCENT_H,
                        border_color=BORDER, checkbox_width=18, checkbox_height=18).pack(
            anchor="w", padx=22, pady=(12, 16))
        self.btn = primary_button(card, "Подключиться", self.on_connect, "accent", height=48,
                                  font=ui_font(15, "bold"), corner_radius=12)
        self.btn.pack(fill="x", padx=22, pady=(0, 22))

        self.err = ctk.CTkLabel(box, text="", font=ui_font(12), text_color=DANGER, wraplength=440)
        self.err.pack(pady=(12, 0))
        ctk.CTkLabel(self, text="Пароли не сохраняются · список адресов хранится локально",
                     font=ui_font(11), text_color=MUTED).pack(side="bottom", pady=14)

        self._fill_user()
        self.combo.bind("<Return>", lambda e: self._next(self.combo))
        self.user.bind("<Return>", lambda e: self._next(self.user))
        self.pwd.bind("<Return>", lambda e: self.on_connect())

    # -- вкладка ------------------------------------------------------------
    def tab_label(self):
        return self.tab_label_text

    def title(self):
        return APP_NAME

    def on_show(self):
        target = self.pwd if self.user.get().strip() else (self.user if self.combo.get().strip() else self.combo)
        self.after(120, target.focus_set)

    def is_alive(self):
        return False

    # -- поля -------------------------------------------------------------
    def _toggle(self):
        self.pwd.configure(show="" if self.show_v.get() else "•")

    def _set_mode(self, value):
        self.mode = "bulk" if value == "Массовый" else "single"
        bulk = self.mode == "bulk"
        self.addr_lbl.configure(text="ДИАПАЗОН АДРЕСОВ" if bulk else "АДРЕС ПК")
        self.addr_hint.configure(text=self.HINT_BULK if bulk else self.HINT_SINGLE)
        values = self.cfg.recent_ranges if bulk else self.cfg.recent
        self.combo.configure(values=values or [""])
        self.combo.set(values[0] if values else "")
        self.err.configure(text="")
        self.user.delete(0, "end")
        self._fill_user()
        self.combo.focus_set()

    def _fill_user(self):
        """Подставить последний логин для выбранного адреса (если поле логина пустое)."""
        raw = self.combo.get().strip()
        last = self.cfg.last_user(raw) if raw else ""
        if last and not self.user.get().strip():
            self.user.insert(0, last)

    def _next(self, current):
        self._fill_user()
        self.err.configure(text="")
        if current is self.combo:
            (self.pwd if self.user.get().strip() else self.user).focus_set()
        else:
            self.pwd.focus_set()

    def on_connect(self):
        raw = self.combo.get().strip()
        user = self.user.get().strip()
        if not raw:
            self.err.configure(text="Введите диапазон адресов" if self.mode == "bulk" else "Введите IP-адрес")
            self.combo.focus_set()
            return
        if self.mode == "bulk":
            return self._connect_bulk(raw, user)
        try:
            host, port = parse_host(raw)
        except ValueError as e:
            self.err.configure(text="Некорректный адрес" if str(e) == "host" else "Некорректный порт", text_color=DANGER)
            return
        if not user:
            self.err.configure(text="Введите логин")
            self.user.focus_set()
            return
        if getattr(self, "_checking", False):
            return
        password = self.pwd.get()
        self._checking = True
        self.btn.configure(state="disabled", text="Проверяю подключение…")
        self.err.configure(text="Проверяю подключение…", text_color=MUTED)
        self._check_q = queue.Queue()
        threading.Thread(target=self._precheck_worker, args=(host, port, user, password), daemon=True).start()
        self.after(100, lambda: self._precheck_poll(raw, host, port, user, password))

    def _precheck_worker(self, host, port, user, password):
        """Проверка входа до открытия терминала: адрес, порт, логин и пароль."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, port=port, username=user, password=password, timeout=10, banner_timeout=15,
                           auth_timeout=15, look_for_keys=False, allow_agent=False)
            self._check_q.put(None)
        except paramiko.AuthenticationException:
            self._check_q.put("Неверный логин или пароль")
        except Exception as e:  # noqa: BLE001
            self._check_q.put(f"Не удалось подключиться: {e}")
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def _precheck_poll(self, raw, host, port, user, password):
        try:
            res = self._check_q.get_nowait()
        except queue.Empty:
            try:
                if self.winfo_exists():
                    self.after(100, lambda: self._precheck_poll(raw, host, port, user, password))
            except tk.TclError:
                pass
            return
        self._checking = False
        try:
            self.btn.configure(state="normal", text="Подключиться")
            if res is not None:
                self.err.configure(text=res, text_color=DANGER)
                return
            self.err.configure(text="", text_color=DANGER)
        except tk.TclError:
            return
        self.cfg.remember(raw, user)
        self.combo.configure(values=self.cfg.recent)
        self.app.open_session(host, port, user, password, from_page=self)


    def _connect_bulk(self, raw, user):
        try:
            ips, port = parse_range(raw)
        except ValueError as e:
            self.err.configure(text=str(e))
            self.combo.focus_set()
            return
        if not user:
            self.err.configure(text="Введите логин")
            self.user.focus_set()
            return
        if len(ips) > BULK_CONFIRM_OVER and not ask_confirm(
                self, "Большой диапазон",
                f"Будет выполнена попытка подключиться к {len(ips)} адресам. Продолжить?", "Подключиться"):
            return
        self.err.configure(text="")
        self.cfg.remember_range(raw, user)
        self.combo.configure(values=self.cfg.recent_ranges)
        self.app.open_bulk(ips, port, user, self.pwd.get(), raw, from_page=self)


# --------------------------------------------------------------------------
# Обновление через GitHub (Windows и Linux)
# --------------------------------------------------------------------------
GITHUB_REPO = "JERIKKKKKK/ssh-connect-helper"      # откуда берутся обновления (раздел Releases)
GITHUB_API = "https://api.github.com"
UPDATE_CHECK_EVERY = 12 * 3600                      # автопроверка при запуске — не чаще раза в 12 часов
UPDATE_HOST_SUFFIXES = ("github.com", "githubusercontent.com")
UPDATE_TEST_HOSTS = ()                              # только для автотестов (локальный «GitHub»)
LINUX_INSTALL_DIR = "/opt/ssh-connect"


class UpdateError(Exception):
    """Понятная пользователю ошибка проверки/загрузки обновления."""


def version_tuple(text):
    """'v1.8.0' -> (1, 8, 0); без цифр -> ()."""
    m = re.search(r"(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(remote, local):
    a, b = version_tuple(remote), version_tuple(local)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def update_url_allowed(url):
    """Только HTTPS и только GitHub (или тестовый узел)."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if host and host in UPDATE_TEST_HOSTS:
        return True
    return u.scheme == "https" and any(host == s or host.endswith("." + s) for s in UPDATE_HOST_SUFFIXES)


def _update_open(url, timeout=15, accept="application/vnd.github+json"):
    if not update_url_allowed(url):
        raise UpdateError("Адрес обновления не принадлежит GitHub — отказ")
    req = urllib.request.Request(url, headers={"User-Agent": f"SSHConnect/{APP_VERSION}", "Accept": accept})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("Релизов в репозитории пока нет (или репозиторий закрыт)")
        if e.code in (403, 429):
            raise UpdateError("GitHub временно ограничил запросы — повторите позже")
        raise UpdateError(f"GitHub вернул ошибку {e.code}")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", e)
        raise UpdateError(f"Нет связи с GitHub: {reason}")
    if not update_url_allowed(resp.geturl()):
        resp.close()
        raise UpdateError("Загрузка перенаправлена не на GitHub — отказ")
    return resp


def fetch_latest_release(repo=None, timeout=15):
    """Последний (не черновик и не пре-релиз) релиз репозитория."""
    url = f"{GITHUB_API}/repos/{repo or GITHUB_REPO}/releases/latest"
    resp = _update_open(url, timeout)
    try:
        data = json.loads(resp.read().decode("utf-8"))
    except ValueError:
        raise UpdateError("GitHub вернул непонятный ответ")
    finally:
        resp.close()
    tag = str(data.get("tag_name") or "")
    ver = ".".join(str(x) for x in version_tuple(tag))
    if not ver:
        raise UpdateError(f"Не удалось определить версию релиза «{tag}»")
    assets = []
    for a in data.get("assets") or []:
        if a.get("name") and a.get("browser_download_url"):
            assets.append({"name": str(a["name"]), "url": str(a["browser_download_url"]),
                           "size": int(a.get("size") or 0), "digest": str(a.get("digest") or "")})
    return {"tag": tag, "version": ver, "name": str(data.get("name") or tag), "body": str(data.get("body") or ""),
            "url": str(data.get("html_url") or f"https://github.com/{repo or GITHUB_REPO}/releases"), "assets": assets}


def detect_install_mode(frozen=None, platform=None, script=None, exe=None, exists=os.path.exists, which=shutil.which):
    """Как программа установлена -> {"kind": win_installer|win_exe|deb|rpm|source, ...}."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    script = os.path.abspath(script or __file__)
    if platform == "win32":
        if frozen:
            exe = exe or sys.executable
            kind = "win_installer" if exists(os.path.join(os.path.dirname(exe), "unins000.exe")) else "win_exe"
            return {"kind": kind, "exe": exe}
        return {"kind": "source"}
    if script.startswith(LINUX_INSTALL_DIR + "/"):
        if which("dpkg"):
            return {"kind": "deb", "script": script}
        if which("rpm"):
            return {"kind": "rpm", "script": script}
    return {"kind": "source"}


UPDATE_ASSET_PATTERNS = {
    "win_installer": r"^SSH_Connect_Setup.*\.exe$",
    "win_exe": r"^SSH_Connect\.exe$",
    "deb": r".*\.deb$",
    "rpm": r".*\.rpm$",
}


def pick_asset(release, kind):
    pat = UPDATE_ASSET_PATTERNS.get(kind)
    if not pat:
        return None
    for a in release.get("assets", []):
        if re.match(pat, a["name"], re.I):
            return a
    return None


def expected_sha256(release, asset):
    """Контрольная сумма файла: из поля digest релиза или из файла SHA256SUMS(.txt) среди файлов релиза."""
    d = asset.get("digest", "")
    if d.lower().startswith("sha256:"):
        return d.split(":", 1)[1].strip().lower()
    for a in release.get("assets", []):
        if re.match(r"^SHA256SUMS(\.txt)?$", a["name"], re.I):
            try:
                resp = _update_open(a["url"], 15, "application/octet-stream")
                try:
                    text = resp.read(1 << 20).decode("utf-8", "replace")
                finally:
                    resp.close()
            except UpdateError:
                return None
            for line in text.splitlines():
                m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
                if m and os.path.basename(m.group(2)) == asset["name"]:
                    return m.group(1).lower()
    return None


def download_asset(release, asset, dest_dir, progress=None, cancel=None):
    """Скачать файл релиза в dest_dir и проверить размер и SHA-256. -> (путь, проверена ли сумма)."""
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(asset["name"])
    path = os.path.join(dest_dir, name)
    part = path + ".part"
    expected = expected_sha256(release, asset)
    h = hashlib.sha256()
    got = 0
    resp = _update_open(asset["url"], 30, "application/octet-stream")
    try:
        total = int(resp.headers.get("Content-Length") or asset.get("size") or 0)
        with open(part, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Загрузка отменена")
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    except UpdateError:
        _silent_remove(part)
        raise
    except (OSError, socket.timeout) as e:
        _silent_remove(part)
        raise UpdateError(f"Ошибка загрузки: {e}")
    finally:
        resp.close()
    if asset.get("size") and got != asset["size"]:
        _silent_remove(part)
        raise UpdateError("Файл скачан не полностью — попробуйте ещё раз")
    if expected and h.hexdigest().lower() != expected.lower():
        _silent_remove(part)
        raise UpdateError("Контрольная сумма файла не совпала — он не будет установлен")
    os.replace(part, path)
    return path, bool(expected)


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _bat_quote(path):
    return '"' + path.replace("%", "%%") + '"'


def build_win_update_bat(kind, new_path, exe_path, tries=60):
    """Скрипт, который запускается после выхода программы: подменяет exe (или запускает установщик) и стартует её."""
    q = _bat_quote
    head = ["@echo off", "chcp 65001 >nul", "setlocal"]
    if kind == "win_installer":
        body = [f"{q(new_path)} /SILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS /NOCANCEL",
                f"del /F /Q {q(new_path)} >nul 2>&1",
                f"start \"\" {q(exe_path)}"]
    else:
        body = ["set /a n=0",
                ":retry",
                f"move /Y {q(new_path)} {q(exe_path)} >nul 2>&1",
                "if not errorlevel 1 goto done",
                "set /a n+=1",
                f"if %n% geq {tries} goto done",
                "ping -n 2 127.0.0.1 >nul",
                "goto retry",
                ":done",
                f"start \"\" {q(exe_path)}"]
    tail = ['(goto) 2>nul & del "%~f0"']
    return "\r\n".join(head + body + tail) + "\r\n"


def linux_install_command(path, kind, which=shutil.which):
    q = shlex.quote(path)
    if kind == "deb":
        return f"dpkg -i {q} || (apt-get install -f -y && dpkg -i {q})"
    if which("dnf"):
        return f"dnf install -y {q}"
    if which("zypper"):
        return f"zypper --non-interactive --no-gpg-checks install {q}"
    if which("yum"):
        return f"yum install -y {q}"
    return f"rpm -U {q}"


def linux_privileged_argv(cmd, uid=None, which=shutil.which, has_display=None, prefer="pkexec"):
    """Как получить root: напрямую, через pkexec (графический запрос пароля) или sudo -S (пароль с stdin)."""
    uid = os.geteuid() if uid is None and hasattr(os, "geteuid") else (uid or 0)
    if uid == 0:
        return ["bash", "-c", cmd], "root"
    if has_display is None:
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if prefer == "pkexec" and which("pkexec") and has_display:
        return ["pkexec", "bash", "-c", cmd], "pkexec"
    return ["sudo", "-S", "-p", "", "bash", "-c", cmd], "sudo"


def update_how_text(kind):
    return {
        "win_installer": "Будет скачан и запущен установщик, затем программа перезапустится.",
        "win_exe": "Файл программы будет заменён новым, затем программа перезапустится.",
        "deb": "Пакет .deb будет установлен системой — потребуется пароль администратора.",
        "rpm": "Пакет .rpm будет установлен системой — потребуется пароль администратора.",
        "source": "Программа запущена не из установленного пакета: скачайте новую версию вручную со страницы релиза.",
    }.get(kind, "")


class UpdateWindow(ctk.CTkToplevel):
    """Окно «Обновление»: проверка релиза на GitHub, описание, загрузка и установка."""
    instance = None

    def __init__(self, app, release=None):
        super().__init__(app, fg_color=BG)
        UpdateWindow.instance = self
        self.app = app
        self.release = release
        self.mode = detect_install_mode()
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.pw_event = None
        self.pw_value = None
        self.installed = False
        self.title("Обновление программы")
        self.minsize(560, 460)
        center_on(self, app, 640, 540)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

        ctk.CTkLabel(self, text="Обновление программы", font=ui_font(18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text=f"Источник: github.com/{GITHUB_REPO}", font=ui_font(11), text_color=MUTED,
                     anchor="w").pack(fill="x", padx=20)
        self.versions = ctk.CTkLabel(self, text="", font=ui_font(13), text_color=TEXT, anchor="w", justify="left")
        self.versions.pack(fill="x", padx=20, pady=(12, 2))
        self.how = ctk.CTkLabel(self, text="", font=ui_font(12), text_color=MUTED, anchor="w", justify="left",
                                wraplength=560)
        self.how.pack(fill="x", padx=20)
        self.box = ctk.CTkTextbox(self, fg_color=SURFACE, text_color=TEXT, corner_radius=10, border_width=1,
                                  border_color=BORDER, wrap="word", font=ui_font(12))
        self.box.pack(fill="both", expand=True, padx=20, pady=(10, 8))
        self.box.configure(state="disabled")
        self.bar = ctk.CTkProgressBar(self, height=8, progress_color=ACCENT, fg_color=SURFACE2)
        self.bar.set(0)
        self.status = ctk.CTkLabel(self, text="", font=ui_font(12), text_color=MUTED, anchor="w", justify="left",
                                   wraplength=560)
        self.status.pack(fill="x", padx=20)
        self.auto_var = tk.BooleanVar(value=bool(app.cfg.data.get("auto_update", True)))
        ctk.CTkCheckBox(self, text="Проверять обновления при запуске", variable=self.auto_var,
                        command=self._save_auto, font=ui_font(12), text_color=MUTED).pack(anchor="w", padx=20,
                                                                                         pady=(8, 0))
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=14)
        self.btn_close = primary_button(foot, "Закрыть", self._close, "ghost", width=100, height=34)
        self.btn_close.pack(side="right")
        self.btn_main = primary_button(foot, "Обновить", self._on_main, "accent", width=150, height=34)
        self.btn_main.pack(side="right", padx=(0, 8))
        self.btn_check = primary_button(foot, "Проверить снова", self.check, "soft", width=150, height=34)
        self.btn_check.pack(side="left")
        self.btn_skip = primary_button(foot, "Пропустить версию", self._skip, "ghost", width=150, height=34)
        self.after(60, self._poll)
        self.after(200, lambda: (self.lift(), self.focus_force()))
        if release is not None:
            self._show_release()
        else:
            self.check()

    # -- вспомогательное ----------------------------------------------------------
    def _say(self, text, kind="info"):
        self.status.configure(text=text, text_color={"ok": OK, "err": DANGER, "warn": WARN}.get(kind, MUTED))

    def _set_box(self, text):
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        self.box.configure(state="disabled")

    def _append_box(self, text):
        self.box.configure(state="normal")
        self.box.insert("end", text)
        self.box.see("end")
        self.box.configure(state="disabled")

    def _save_auto(self):
        self.app.cfg.data["auto_update"] = bool(self.auto_var.get())
        self.app.cfg.save()

    def _skip(self):
        if self.release:
            self.app.cfg.data["update_skip"] = self.release["version"]
            self.app.cfg.save()
            self.app.set_update_available(None)
        self._close()

    def _close(self):
        if self.busy and not self.installed:
            self.cancel.set()
            if self.pw_event is not None:
                self.pw_event.set()
        self.destroy()

    def destroy(self):
        if UpdateWindow.instance is self:
            UpdateWindow.instance = None
        super().destroy()

    # -- проверка ----------------------------------------------------------------------
    def check(self):
        if self.busy:
            return
        self.busy = True
        self.release = None
        self.btn_main.configure(state="disabled")
        self.btn_check.configure(state="disabled")
        self.btn_skip.pack_forget()
        self.versions.configure(text=f"Установлена версия: {APP_VERSION}")
        self.how.configure(text="")
        self._set_box("")
        self._say("Проверяю GitHub…")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            self.q.put(("release", fetch_latest_release()))
        except UpdateError as e:
            self.q.put(("check_error", str(e)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("check_error", f"{type(e).__name__}: {e}"))

    def _on_release(self, rel):
        self.busy = False
        self.release = rel
        self.btn_check.configure(state="normal")
        self.app.cfg.data["update_last_check"] = time.time()
        self.app.cfg.save()
        self._show_release()

    def _on_check_error(self, msg):
        self.busy = False
        self.btn_check.configure(state="normal")
        self._say(msg, "err")

    def _show_release(self):
        rel = self.release
        self.btn_check.configure(state="normal")
        self.versions.configure(text=f"Установлена версия: {APP_VERSION}\nПоследняя версия на GitHub: {rel['version']}")
        self._set_box(rel["body"].strip() or "Описание изменений не указано.")
        if not is_newer(rel["version"], APP_VERSION):
            self.btn_main.configure(state="disabled", text="Обновить")
            self._say("У вас последняя версия.", "ok")
            self.how.configure(text="")
            self.app.set_update_available(None)
            return
        self.app.set_update_available(rel["version"])
        self.btn_skip.pack(side="left", padx=(8, 0))
        kind = self.mode["kind"]
        asset = pick_asset(rel, kind)
        self.how.configure(text=update_how_text(kind))
        if kind == "source" or asset is None:
            self.btn_main.configure(state="normal", text="Открыть страницу релиза")
            self._say("Доступна новая версия." if kind == "source" else
                      "В релизе нет файла для вашей системы — откройте страницу релиза.", "warn")
        else:
            self.btn_main.configure(state="normal", text="Обновить")
            self._say(f"Доступна версия {rel['version']}.", "warn")

    # -- установка ----------------------------------------------------------------------
    def _on_main(self):
        rel = self.release
        if not rel or self.busy:
            return
        kind = self.mode["kind"]
        asset = pick_asset(rel, kind)
        if kind == "source" or asset is None:
            webbrowser.open(rel["url"])
            return
        live = [r["page"] for r in self.app.tabs if r["page"].is_alive()]
        if live and not ask_confirm(self, "Обновить программу?",
                                    f"Открытых сессий: {len(live)}. При обновлении они будут завершены.",
                                    "Обновить", "warn"):
            return
        self.busy = True
        self.cancel.clear()
        self.btn_main.configure(state="disabled")
        self.btn_check.configure(state="disabled")
        self.btn_skip.pack_forget()
        self.bar.pack(fill="x", padx=20, before=self.status)
        self._say(f"Скачиваю {asset['name']}…")
        threading.Thread(target=self._apply_worker, args=(rel, asset, kind), daemon=True).start()

    def _progress(self, got, total):
        self.q.put(("progress", (got, total)))

    def _apply_worker(self, rel, asset, kind):
        try:
            dest = os.path.join(tempfile.gettempdir(), "SSHConnect_update")
            path, verified = download_asset(rel, asset, dest, self._progress, self.cancel)
            self.q.put(("downloaded", verified))
            if kind in ("win_installer", "win_exe"):
                self._apply_windows(kind, path)
            else:
                self._apply_linux(kind, path)
        except UpdateError as e:
            self.q.put(("apply_error", str(e)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("apply_error", f"{type(e).__name__}: {e}"))

    def _apply_windows(self, kind, path):
        exe = self.mode["exe"]
        if kind == "win_exe":
            target_dir = os.path.dirname(exe)
            if not os.access(target_dir, os.W_OK):
                raise UpdateError(f"Нет прав на запись в папку программы:\n{target_dir}\n"
                                  "Запустите программу от имени администратора или установите её установщиком.")
            new = exe + ".new"
            shutil.copyfile(path, new)
            _silent_remove(path)
            path = new
        bat = os.path.join(tempfile.gettempdir(), "sshconnect_update.bat")
        with open(bat, "w", encoding="utf-8", newline="") as f:
            f.write(build_win_update_bat(kind, path, exe))
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
        subprocess.Popen(["cmd.exe", "/c", bat], creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.q.put(("quit", None))

    def _ask_password(self):
        self.pw_event = threading.Event()
        self.pw_value = None
        self.q.put(("need_password", None))
        self.pw_event.wait()
        return self.pw_value

    def _apply_linux(self, kind, path):
        cmd = linux_install_command(path, kind)
        prefer = "pkexec"
        while True:
            argv, how = linux_privileged_argv(cmd, prefer=prefer)
            pw = None
            if how == "sudo":
                pw = self._ask_password()
                if pw is None:
                    raise UpdateError("Установка отменена")
            self.q.put(("installing", how))
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace")
            if how == "sudo":
                proc.stdin.write(pw + "\n")
            proc.stdin.close()
            for line in proc.stdout:
                self.q.put(("log", line))
            code = proc.wait()
            if code == 0:
                self.q.put(("installed", None))
                return
            if how == "pkexec" and code in (126, 127):      # окно пароля закрыто / нет прав — пробуем sudo
                prefer = "sudo"
                continue
            raise UpdateError(f"Установка не удалась (код {code}). Подробности — в окне выше.")

    # -- события из потоков ---------------------------------------------------------------
    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                getattr(self, "_on_" + kind)(val)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.after(60, self._poll)
        except tk.TclError:
            pass

    def _on_progress(self, v):
        got, total = v
        if total:
            self.bar.set(min(1.0, got / total))
            self._say(f"Скачиваю… {human_size(got)} из {human_size(total)}")
        else:
            self._say(f"Скачиваю… {human_size(got)}")

    def _on_downloaded(self, verified):
        self.bar.set(1.0)
        self._say("Файл скачан, контрольная сумма совпала." if verified else
                  "Файл скачан (контрольной суммы в релизе нет — проверен только размер).", "ok" if verified else "warn")

    def _on_apply_error(self, msg):
        self.busy = False
        self.bar.pack_forget()
        self.btn_check.configure(state="normal")
        self.btn_main.configure(state="normal")
        self.btn_skip.pack(side="left", padx=(8, 0))
        self._say(msg, "err")

    def _on_need_password(self, _v):
        form = ask_form(self, "Пароль администратора",
                        "Для установки пакета нужны права администратора (sudo).",
                        [{"key": "pw", "label": "Пароль", "kind": "password"}], "Продолжить")
        self.pw_value = form["pw"] if form else None
        self.pw_event.set()

    def _on_installing(self, how):
        self._say("Устанавливаю пакет… " + ("(запрос пароля системой)" if how == "pkexec" else ""))
        self._set_box("")

    def _on_log(self, line):
        self._append_box(line)

    def _on_installed(self, _v):
        self.installed = True
        self.busy = False
        self.bar.pack_forget()
        self._say("Готово. Перезапустите программу, чтобы начать работать с новой версией.", "ok")
        self.btn_main.configure(state="normal", text="Перезапустить", command=self._restart_linux)

    def _restart_linux(self):
        script = self.mode.get("script") or os.path.join(LINUX_INSTALL_DIR, "ssh_connect.py")
        try:
            subprocess.Popen([sys.executable, script], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self._say(f"Не удалось перезапустить: {e}. Запустите программу вручную.", "err")
            return
        self.app.on_quit(force=True)

    def _on_quit(self, _v):
        self._say("Закрываю программу для обновления…")
        self.installed = True
        self.app.after(300, lambda: self.app.on_quit(force=True))


def show_update(app, release=None):
    w = UpdateWindow.instance
    try:
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            return w
    except tk.TclError:
        pass
    return UpdateWindow(app, release)


# --------------------------------------------------------------------------
# Справка
# --------------------------------------------------------------------------
# Разметка текста: «## » — подзаголовок, «• » — пункт списка, пустая строка — абзац.
HELP_TOPICS = [
    ("Начало работы", """
Программа подключается к ПК по SSH, открывает терминал и набор инструментов: файлы, быстрые команды, учётные записи, перезагрузка.

## Подключение к одному ПК
• Вкладка «Один ПК»: введите адрес, логин и пароль, нажмите «Подключиться».
• Адрес можно писать как 10.35.21.5, 10.35.21.5:2222 или «10.35.21.5 2222» — порт по умолчанию 22.
• Перед открытием терминала программа проверяет адрес, порт, логин и пароль. Если что-то не так, она скажет что именно (нет ответа, закрыт порт, неверный пароль).
• Последние адреса сохраняются в выпадающем списке. Пароли нигде не сохраняются.

## Вкладки
• Каждое подключение открывается в своей вкладке, как в браузере. Кнопка «+» открывает новую вкладку подключения, «×» закрывает.
• Точка у названия вкладки показывает состояние: подключено, ожидание, ошибка.
"""),
    ("Терминал", """
В окне терминала работает обычная командная строка удалённого ПК: цвета, русский текст, Ctrl+C, прокрутка.

## Копирование и вставка
• Правая кнопка мыши в терминале открывает меню «Копировать» / «Вставить». Само по себе нажатие ничего не вставляет.
• Ctrl+C / Ctrl+V работают и при русской раскладке.

## Кнопки над терминалом
• «Файлы» — окно с двумя проводниками (раздел «Файлы»).
• «Быстрые команды» — готовые действия (раздел «Быстрые команды»).
• «Перезагрузить» и «Выключить» — управление питанием ПК (раздел «Перезагрузка и обрыв связи»).
• «Переподключить» — новое соединение с тем же ПК. «Отключиться» — закрыть соединение.
• Переключатель «Автопереподключение при обрыве» — программа сама восстановит связь, если она пропадёт.

## Блок «Система» под терминалом
При подключении программа сама собирает сведения о ПК: материнская плата, процессор, оперативная память (тип и модули), диски (модель и размер), версия операционной системы и ядра, а также установленная программа SMB-дисков с версией (Astra SMB Mounter и/или mate-smb-tools).
Если данные получить не удалось, в ячейке будет «н/д».
"""),
    ("Перезагрузка и обрыв связи", """
## Перезагрузить / Выключить
• Обе кнопки сначала спрашивают подтверждение.
• После «Перезагрузить» программа скрыто опрашивает ПК и, как только он снова появится в сети, подключается сама. Прогресс виден в полосе над терминалом.
• После «Выключить» включить ПК удалённо не получится.

## Если пропала связь
• Если связь оборвалась, а сеть на вашем компьютере в порядке, программа ждёт ПК и переподключается (если включено автопереподключение).
• Если пропала сеть на самом вашем компьютере, программа это замечает отдельно, ждёт возвращения сети и только потом ищет удалённый ПК. Так потеря вашего Wi-Fi или кабеля не принимается за перезагрузку ПК.
• Кнопка «Отмена» в полосе ожидания прекращает ожидание.
"""),
    ("Быстрые команды", """
Кнопка «Быстрые команды» открывает список готовых действий. Выберите строку и нажмите кнопку запуска (или двойной щелчок): команда выполнится прямо в открытом терминале, вывод виден сразу, работает Ctrl+C.

## Как пользоваться
• Поле «Поиск команды…» ищет по названию и по группе.
• Команды разложены по группам: Учётные записи, Система, Печать, Сеть и «Мои команды».
• Команды с правами администратора выполняются через sudo, пароль подставляется сам. Опасные (очистка очереди печати, перезапуск сети) сначала спрашивают подтверждение.
• «+  Своя команда» — добавить собственную команду. Свои команды сохраняются в настройках, в группе «Мои команды»; крестик ✕ рядом с командой удаляет её.

## Что есть в списке
• Управление учётными записями… — одноимённое окно (раздел «Учётные записи»).
• Сменить имя компьютера… — форма с новым именем; обновляются hostname и записи в hosts. Галочка «Удалить блокировки браузеров» (включена по умолчанию) сразу после смены убирает блокировки, из-за которых Яндекс Браузер и Chromium перестают открываться.
• Исправить запуск браузера (блокировки)… — отдельная команда: удаляет файлы блокировки Singleton* в ~/.config/*/ у всех пользователей. У кого браузер запущен в данный момент, блокировки не трогаются (программа напишет, у кого, чтобы закрыть браузер и повторить).
• Синхронизировать время — по NTP, сервером времени служит DNS-сервер самого ПК (без шлюза).
• Автовключение принтеров — проверяет, установлен ли скрипт; если нет, предлагает установить.
• Перезапустить CUPS, Статус CUPS, Список принтеров, Очистить очередь печати.
• Сменить IP на статический… — окно настройки адреса, маски, шлюза и DNS через NetworkManager; после смены программа переподключается к новому адресу.
• Перезапустить сеть, IP-адреса, Открытые порты.
• Установить SH/DEB… — выбор файла на этом компьютере, он загружается на ПК и устанавливается в терминале.
• Место на дисках, Память, Аптайм и нагрузка, Время и NTP, Кто сейчас работает, Обновить списки пакетов, Ошибки журнала.
"""),
    ("Файлы", """
Кнопка «Файлы» открывает окно из четырёх колонок: слева ваш компьютер, справа подключённый ПК. В каждой половине слева список закреплённых папок (Рабочий стол, Документы, Загрузки и т.д.), справа содержимое выбранной папки.

## Навигация
• Двойной щелчок по папке открывает её, Backspace — на уровень выше, F5 — обновить.
• Адресную строку сверху можно править руками: введите путь и нажмите Enter.
• Слева есть «Этот компьютер» и диски, на ПК — домашняя папка и «/».

## Копирование
• Выделите файлы и папки (Ctrl и Shift работают) и перетащите их мышью в соседнюю панель — они скопируются.
• Файлы из Проводника Windows можно бросить прямо в окно — они загрузятся на ПК в открытую справа папку.
• Файлы из правой панели можно вынести за окно программы, например на Рабочий стол: они скачаются в папку «Рабочий стол» на вашем компьютере.
• Если такие имена уже есть, программа спросит про замену. Передача идёт с индикатором и кнопкой «Отмена».

## Правая кнопка мыши
• Копировать — в соседнюю панель.
• Просмотр и Редактировать — открыть файл (см. ниже).
• Установить — только для .deb и .sh (см. «Установка пакетов»).
• Переименовать (F2) и Удалить (Delete). Новая папка — кнопка внизу панели.

## Кнопка «Открыть»
В левой панели, рядом с «Удалить»: открывает в Проводнике Windows папку, которая сейчас показана слева.

## Просмотр и правка файлов
• Двойной щелчок по файлу открывает его в режиме просмотра. Кнопка «Редактировать» включает правку, Ctrl+S сохраняет, Ctrl+F — поиск.
• Кодировка (UTF-8, cp1251, UTF-16) определяется сама и при сохранении остаётся прежней, как и тип переводов строк.
• Если у пользователя нет прав на запись, программа предложит сохранить с правами root (sudo). Владелец и права файла не меняются.
• Нетекстовые файлы (картинки, PDF, документы) и файлы больше 3 МБ открываются программой по умолчанию. Файл с ПК сначала скачивается во временную папку, это копия: изменения в ней на ПК не возвращаются.
• Пакеты .deb не открываются — их устанавливают.
"""),
    ("Установка пакетов (.deb, .sh)", """
## Откуда
• В окне «Файлы»: выделите .deb или .sh и выберите в меню правой кнопки «Установить». Файл справа уже на ПК, файл слева сначала загружается в открытую справа папку.
• Быстрая команда «Установить SH/DEB…» — выбор файла на вашем компьютере.

## Как проходит установка
• Программа покажет, что именно будет выполнено, и спросит подтверждение. Для .sh можно выбрать запуск с правами root.
• Установка идёт в терминале, поэтому вы видите весь вывод и вопросы установщика.
"""),
    ("Учётные записи", """
Быстрая команда «Управление учётными записями…» открывает окно с тремя вкладками. Оно работает так же, как отдельная программа для Linux.

## Вкладка «Учётные записи»
• Список пользователей ПК: логин, UID, имя, домашняя папка, группы, признак администратора, блокировка, «В системе» (вошёл ли пользователь сейчас), сетевые диски. Можно выделить несколько строк (Ctrl/Shift).
• «Сохранить в TXT…» — выгрузка списка в файл (в него всегда попадают все записи). «Обновить» — перечитать список.
• «Удалить…» — с подтверждением и выбором: удалить только запись или вместе с файлами. Перед удалением программа отключает сетевые диски пользователя. Учётную запись, под которой вы подключены, удалить нельзя.
• «Отмонтировать диски» — отключить сетевые диски SMB выбранных пользователей (файлы на самих дисках не затрагиваются).
• «Сбросить неактивные…» — завершить неактивные сеансы пользователей на ПК.

## Вкладка «Группы»
Список групп, «Создать группу…» и «Удалить…». Удаляются только обычные группы (GID 1000 и выше), которые не являются основными для пользователей; галочка «Показывать системные» показывает остальные.

## Вкладка «Создание»
• «Одна учётная запись»: логин, имя (необязательно), пароль дважды, галочка «Администратор (группа sudo)». Новая запись сразу получает группы, как в окне «Привилегии пользователя» среды MATE.
• «Импорт из txt» («Выбрать файл…»): по одной записи в строке, «логин, табуляция, пароль» или «логин, пробел, пароль». Пустые строки и строки, начинающиеся с #, пропускаются. Третий столбец (через табуляцию) — имя.
• Пароль вида A1234b принимается, даже если политика паролей системы его не одобряет.

Для изменений нужны права администратора: программа использует sudo с вашим паролем подключения.
"""),
    ("Массовый режим", """
Переключатель «Массовый» на форме подключения работает сразу с диапазоном адресов.

## Подключение
• Введите первый и последний адрес диапазона (через дефис), общие логин и пароль и нажмите «Подключиться».
• Откроется таблица: адрес, имя ПК, система (Astra, MATE, Linux, Windows), состояние подключения и результат последней операции.
• Двойной щелчок по строке открывает обычную вкладку с терминалом этого ПК. Правая кнопка — меню строки.
• «Только подключённые» скрывает ПК, до которых достучаться не удалось. «⟳ Обновить» — повторный опрос.

## Массовые команды
Если строки выделены, команда идёт только им, если нет, то всем подключённым.
• Установить ярлык — файл .desktop с вашего компьютера копируется на ПК (на рабочий стол или всем пользователям). На Linux MATE при необходимости ставится служба desktop-shortcuts-sync.
• Отправить файл — копирует файл в указанную папку, можно сделать его исполняемым и записать через sudo.
• Выполнить команду — любая команда bash на каждом ПК; вывод смотрите двойным щелчком по строке результата.
• Проверка SMB — по каждому Linux-ПК узнаёт версию mate-smb-tools («новая» — 1.6.1 и выше, ниже — «старая»), наличие и версию Astra SMB Mounter (старой считается версия с адресами 10.35.*.253 / 192.168.5.40 и ресурсами «Диск K/U»), имя ПК, систему и ядро. Результат виден в колонках «mate-smb-tools» и «Astra SMB Mounter», старые версии подсвечиваются красным, подробности — двойным щелчком по строке. Только чтение, пока не включены действия ниже.
  Действия применяются только к ПК с mate-smb-tools ровно 1.6.1: переименование «Было» → «Стало» (по умолчанию PC → PC1; меняются hostname, /etc/hostname и /etc/hosts) и удаление блокировок браузеров (Singleton*) на ПК с именем «Стало»; у кого браузер запущен, блокировки пропускаются. Перед запуском программа покажет, что именно будет сделано.
• Отчёт в Excel… (над таблицей) — сохраняет результаты последней проверки SMB в .xlsx: лист «mate-smb-tools» (сводка по ПК) и лист «Лог» (каждое действие). Нужна библиотека openpyxl.
• Перезагрузить и Выключить — с подтверждением.
Ход работы виден по полосе прогресса, итог по строке состояния.
"""),
    ("Обновления", """
Программа сама проверяет GitHub и обновляется на Windows и Linux. Источник — репозиторий JERIKKKKKK/ssh-connect-helper, раздел Releases.

## Как это работает
• При запуске (не чаще раза в 12 часов) программа тихо проверяет последний релиз. Если он новее установленной версии, кнопка «Обновления» вверху справа становится жёлтой с номером версии.
• Кнопка «Обновления» открывает окно: видны текущая и последняя версии, описание изменений. «Проверить снова» запускает проверку вручную.
• «Обновить» скачивает файл для вашей системы, проверяет контрольную сумму (SHA-256, если она есть в релизе) и устанавливает.
• «Пропустить версию» убирает подсветку до выхода следующего релиза. Автопроверку можно выключить галочкой в этом окне.

## Что происходит на разных системах
• Windows, установлено установщиком: скачивается SSH_Connect_Setup_*.exe и тихо ставится поверх, программа перезапускается сама.
• Windows, одиночный SSH_Connect.exe: файл заменяется на новый (нужны права на запись в его папку), затем программа запускается снова.
• Linux, пакет .deb или .rpm: пакет ставится через системное окно пароля (pkexec) или через sudo, после установки нажмите «Перезапустить».
• Запуск из исходников: программа только открывает страницу релиза.

## Если что-то не получилось
• «Нет связи с GitHub» — проверьте интернет и прокси; обновление идёт только по HTTPS с адресов github.com.
• «Релизов пока нет» — в репозитории ещё не опубликован ни один релиз.
• Открытые сессии при обновлении завершаются — программа спросит подтверждение.
""" ),
    ("Горячие клавиши и советы", """
## Клавиши
• F1 — эта справка.
• Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+A — работают при любой раскладке, в том числе в полях ввода.
• Окно «Файлы»: Enter — открыть, Backspace — вверх, F5 — обновить, F2 — переименовать, Delete — удалить.
• Редактор файла: Ctrl+S — сохранить, Ctrl+F — поиск, Esc — закрыть.

## Где что хранится
• Настройки (последние адреса, свои команды) лежат в папке SSHConnect: на Windows в %APPDATA%, на Linux в ~/.config.
• Пароли не сохраняются нигде.
• Журнал ошибок — файл error.log в той же папке. Если что-то работает не так, пришлите его.

## Полезно знать
• Программа выполняет команды отдельными каналами, поэтому окно «Файлы», учётные записи и быстрые команды не мешают друг другу и терминалу.
• Если действие требует прав администратора, а вы подключены не под root, программа сама использует sudo и ваш пароль.
"""),
]


class HelpWindow(ctk.CTkToplevel):
    """Справка по всем функциям: слева разделы, справа текст; поиск по разделам."""
    instance = None

    def __init__(self, app):
        super().__init__(app, fg_color=BG)
        HelpWindow.instance = self
        self.title(f"Справка — {APP_NAME} {APP_VERSION}")
        self.minsize(760, 480)
        center_on(self, app, 1000, 680)
        self.topic_btns = []
        self.current = 0
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, width=250)
        left.grid(row=0, column=0, sticky="ns")
        left.grid_propagate(False)
        ctk.CTkLabel(left, text="Справка", font=ui_font(18, "bold"), text_color=TEXT, anchor="w").pack(
            fill="x", padx=16, pady=(16, 8))
        self.search = ctk.CTkEntry(left, placeholder_text="Поиск по справке…", height=32,
                                   fg_color=SURFACE2, border_color=BORDER, text_color=TEXT)
        self.search.pack(fill="x", padx=14, pady=(0, 8))
        self.search.bind("<KeyRelease>", lambda e: self._filter())
        self.list_box = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_box.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(14, 16), pady=14)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        self.title_lbl = ctk.CTkLabel(right, text="", font=ui_font(20, "bold"), text_color=TEXT, anchor="w")
        self.title_lbl.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.box = ctk.CTkTextbox(right, fg_color=SURFACE, text_color=TEXT, corner_radius=10,
                                  border_width=1, border_color=BORDER, wrap="word", font=ui_font(13))
        self.box.grid(row=1, column=0, sticky="nsew")
        t = self.box._textbox
        t.tag_config("h", foreground=ACCENT, font=ui_font(14, "bold"), spacing1=12, spacing3=4)
        t.tag_config("li", lmargin1=14, lmargin2=30, spacing1=3)
        t.tag_config("p", spacing1=4)
        t.tag_config("hit", background=WARN, foreground="#1a1204")
        foot = ctk.CTkFrame(right, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        primary_button(foot, "Закрыть", self.destroy, "accent", width=110, height=34).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Destroy>", self._on_destroy)
        self._filter()
        self.after(200, lambda: (self.lift(), self.focus_force()))

    def _on_destroy(self, e):
        if e.widget is self and HelpWindow.instance is self:
            HelpWindow.instance = None

    def _filter(self):
        q = self.search.get().strip().lower()
        for b in self.topic_btns:
            b.destroy()
        self.topic_btns = []
        shown = [i for i, (title, body) in enumerate(HELP_TOPICS)
                 if not q or q in title.lower() or q in body.lower()]
        if not shown:
            lbl = ctk.CTkLabel(self.list_box, text="Ничего не найдено", font=ui_font(12), text_color=MUTED)
            lbl.pack(pady=12)
            self.topic_btns.append(lbl)
            return
        for i in shown:
            b = primary_button(self.list_box, HELP_TOPICS[i][0], lambda i=i: self.show(i), "ghost",
                               height=34, font=ui_font(12, "bold"), anchor="w")
            b.pack(fill="x", pady=1)
            self.topic_btns.append(b)
        self.show(self.current if self.current in shown else shown[0])

    def show(self, idx):
        self.current = idx
        title, body = HELP_TOPICS[idx]
        self.title_lbl.configure(text=title)
        t = self.box._textbox
        self.box.configure(state="normal")
        t.delete("1.0", "end")
        for line in body.strip().splitlines():
            if line.startswith("## "):
                t.insert("end", line[3:] + "\n", "h")
            elif line.startswith("• "):
                t.insert("end", "•  " + line[2:] + "\n", "li")
            elif line.strip():
                t.insert("end", line + "\n", "p")
            else:
                t.insert("end", "\n")
        q = self.search.get().strip().lower()
        if q:
            pos = "1.0"
            while True:
                pos = t.search(q, pos, "end", nocase=True)
                if not pos:
                    break
                end = f"{pos}+{len(q)}c"
                t.tag_add("hit", pos, end)
                pos = end
        self.box.configure(state="disabled")
        t.yview_moveto(0)
        for b, i in zip(self.topic_btns, [j for j, (tt, bb) in enumerate(HELP_TOPICS)
                                           if not q or q in tt.lower() or q in bb.lower()]):
            try:
                b.configure(fg_color=SURFACE_HOVER if i == idx else "transparent")
            except tk.TclError:
                pass


def show_help(app):
    """Открыть справку (если уже открыта — вынести на передний план)."""
    w = HelpWindow.instance
    try:
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            return w
    except tk.TclError:
        pass
    return HelpWindow(app)


class MainWindow(ctk.CTk):
    """Одно окно с вкладками: форма подключения и SSH-сессии открываются как вкладки браузера."""
    TAB_MAX, TAB_MIN = 220, 120

    def _set_app_icon(self):
        """Иконка окна и панели задач: assets/ssh_connect.ico (в exe лежит внутри PyInstaller-пакета)."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            if sys.platform == "win32":
                ico = os.path.join(base, "assets", "ssh_connect.ico")
                # CustomTkinter сам ставит свою иконку через ~200 мс — перебиваем после неё
                self.after(300, lambda: self._apply_ico(ico))
            else:
                png = os.path.join(base, "assets", "ssh_connect.png")
                if os.path.isfile(png):
                    self._icon_img = tk.PhotoImage(file=png)
                    self.iconphoto(True, self._icon_img)
        except Exception:  # noqa: BLE001
            pass

    def _apply_ico(self, ico):
        try:
            if os.path.isfile(ico):
                self.iconbitmap(ico)
        except Exception:  # noqa: BLE001
            pass

    def __init__(self):
        super().__init__(fg_color=BG)
        self.cfg = Config()
        self.error_log = os.path.join(self.cfg.dir, "error.log")
        if hasattr(threading, "excepthook"):
            threading.excepthook = lambda a: self._log_error("поток", a.exc_type, a.exc_value, a.exc_traceback)
        self.title(f"{APP_NAME} · v{APP_VERSION}")
        self.minsize(900, 600)
        center_screen(self, 1180, 780)
        self._set_app_icon()
        self.tabs = []      # [{"page", "frame", "dot", "lbl", "close"}]
        self.current = None

        self.bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        self.bar.pack(fill="x")
        self.bar.pack_propagate(False)
        self.tab_row = ctk.CTkFrame(self.bar, fg_color="transparent")
        self.tab_row.pack(side="left", fill="y", padx=(10, 0), pady=7)
        self.btn_new = primary_button(self.bar, "+", self.new_connect_tab, "ghost", width=34,
                                      height=32, corner_radius=8, font=ui_font(18, "bold"))
        self.btn_new.pack(side="left", padx=(6, 0), pady=7)
        self.btn_help = primary_button(self.bar, "Справка", lambda: show_help(self), "soft", width=96,
                                       height=32, corner_radius=8, font=ui_font(12, "bold"))
        self.btn_help.pack(side="right", padx=(0, 10), pady=7)
        self.btn_update = primary_button(self.bar, "Обновления", lambda: show_update(self), "ghost", width=110,
                                         height=32, corner_radius=8, font=ui_font(12, "bold"))
        self.btn_update.pack(side="right", padx=(0, 6), pady=7)
        self.update_ver = None
        self.update_q = queue.Queue()
        self.bind_all("<F1>", lambda e: (show_help(self), "break")[1], add="+")
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.pack(fill="both", expand=True)
        self.bar.bind("<Configure>", lambda e: self._layout_tabs())

        self.protocol("WM_DELETE_WINDOW", self.on_quit)
        # Сборщик мусора мог запускаться в рабочем потоке и удалять Tk-объекты не из главного потока
        # (редкие вылеты/зависания) — чистим память только из главного потока.
        gc.disable()
        self.after(1500, self._gc_tick)
        self.clip_menu = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                                 activeforeground="#ffffff", bd=0, font=(UI_FONT, 10))
        self.bind_all("<Control-KeyPress>", self._ctrl_key, add="+")
        self.bind_all("<Button-3>", self._clip_menu, add="+")
        self.new_connect_tab()
        self.after(4000, self._auto_update_check)
        self.after(500, self._update_poll)

    # -- обновления через GitHub -------------------------------------------------
    def set_update_available(self, version):
        """Подсветить кнопку «Обновления», если на GitHub есть версия новее (version=None — снять)."""
        self.update_ver = version
        try:
            if version:
                self.btn_update.configure(text=f"Обновление {version}", width=170, fg_color=WARN, hover_color=WARN_H,
                                          text_color="#1a1204")
            else:
                self.btn_update.configure(text="Обновления", width=110, fg_color="transparent",
                                          hover_color=SURFACE_HOVER, text_color=TEXT)
            self._layout_tabs()
        except tk.TclError:
            pass

    def _auto_update_check(self):
        """Тихая проверка при запуске: не чаще раза в 12 часов, ошибки не показываются."""
        if not self.cfg.data.get("auto_update", True):
            return
        if time.time() - float(self.cfg.data.get("update_last_check", 0) or 0) < UPDATE_CHECK_EVERY:
            return

        def work():
            try:
                self.update_q.put(("ok", fetch_latest_release()))
            except Exception:  # noqa: BLE001
                self.update_q.put(("err", None))
        threading.Thread(target=work, daemon=True).start()

    def _update_poll(self):
        try:
            while True:
                kind, rel = self.update_q.get_nowait()
                if kind != "ok":
                    continue
                self.cfg.data["update_last_check"] = time.time()
                self.cfg.save()
                if is_newer(rel["version"], APP_VERSION) and rel["version"] != self.cfg.data.get("update_skip"):
                    self.set_update_available(rel["version"])
                    self.toast_main(f"Доступна версия {rel['version']} — кнопка «Обновления» вверху справа")
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.after(1000, self._update_poll)

    def toast_main(self, text):
        page = None
        if isinstance(self.current, int) and 0 <= self.current < len(self.tabs):
            page = self.tabs[self.current]["page"]
        t = getattr(page, "toast", None)
        if t is not None:
            try:
                t.show(text, "info", 6000)
            except Exception:  # noqa: BLE001
                pass

    # -- буфер обмена: работает при любой раскладке и по правой кнопке ---------
    CYR_KEYS = {"Cyrillic_em": "<<Paste>>", "Cyrillic_es": "<<Copy>>", "Cyrillic_che": "<<Cut>>",
                "Cyrillic_ef": "select_all", "Cyrillic_EM": "<<Paste>>", "Cyrillic_ES": "<<Copy>>",
                "Cyrillic_CHE": "<<Cut>>", "Cyrillic_EF": "select_all"}
    WIN_KEYCODES = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "select_all"}

    @staticmethod
    def _text_widget(w):
        return isinstance(w, (tk.Entry, ttk.Entry, tk.Text)) and not isinstance(w, TerminalView)

    def _clip_action(self, w, action):
        if action == "select_all":
            if isinstance(w, tk.Text):
                w.tag_add("sel", "1.0", "end-1c")
            else:
                w.select_range(0, "end")
                w.icursor("end")
        else:
            w.event_generate(action)

    def _ctrl_key(self, e):
        """Ctrl+V/C/X/A в русской раскладке: Tk привязывает их к латинским клавишам и не реагирует."""
        w = self.focus_get()
        if not self._text_widget(w):
            return None
        action = self.CYR_KEYS.get(e.keysym)
        if action is None and sys.platform == "win32" and len(e.keysym) > 1 and not e.keysym.startswith(("Control", "Shift")):
            action = self.WIN_KEYCODES.get(e.keycode)
        if action is None:
            return None
        try:
            self._clip_action(w, action)
        except tk.TclError:
            pass
        return "break"

    def _clip_menu(self, e):
        w = e.widget
        if not self._text_widget(w):
            return None
        try:
            if str(w.cget("state")) == "disabled":
                return None
        except tk.TclError:
            pass
        w.focus_set()
        m = self.clip_menu
        m.delete(0, "end")
        for label, act in (("Вырезать", "<<Cut>>"), ("Копировать", "<<Copy>>"), ("Вставить", "<<Paste>>"),
                           ("Выделить всё", "select_all")):
            m.add_command(label=label, command=lambda a=act, ww=w: self._clip_action(ww, a))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        return "break"

    def report_callback_exception(self, exc, val, tb):
        self._log_error("окно", exc, val, tb)

    def _log_error(self, where, exc, val, tb):
        """В exe без консоли ошибки не видны — пишем их в %APPDATA%\\SSHConnect\\error.log."""
        try:
            import traceback
            os.makedirs(self.cfg.dir, exist_ok=True)
            with open(self.error_log, "a", encoding="utf-8") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ({where})\n")
                f.write("".join(traceback.format_exception(exc, val, tb)) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _gc_tick(self):
        try:
            gc.collect()
            self.after(3000, self._gc_tick)
        except tk.TclError:
            pass

    # -- вкладки -----------------------------------------------------------
    def _add_tab(self, page, index=None):
        frame = ctk.CTkFrame(self.tab_row, fg_color="transparent", corner_radius=9, height=34,
                             cursor="hand2")
        frame.pack_propagate(False)
        dot = ctk.CTkLabel(frame, text="●", width=14, font=ui_font(11), text_color=MUTED)
        dot.pack(side="left", padx=(10, 0))
        lbl = ctk.CTkLabel(frame, text="", font=ui_font(12, "bold"), text_color=MUTED, anchor="w")
        lbl.pack(side="left", fill="x", expand=True, padx=(6, 0))
        close = ctk.CTkLabel(frame, text="✕", width=24, font=ui_font(11, "bold"),
                             text_color=MUTED, cursor="hand2")
        close.pack(side="right", padx=(0, 6))
        rec = {"page": page, "frame": frame, "dot": dot, "lbl": lbl, "close": close}
        for w in (frame, dot, lbl):
            w.bind("<Button-1>", lambda e, p=page: self.select(p), add="+")
            w.bind("<Button-2>", lambda e, p=page: self.request_close(p), add="+")
        close.bind("<Button-1>", lambda e, p=page: self.request_close(p), add="+")
        close.bind("<Enter>", lambda e, c=close: c.configure(text_color=DANGER))
        close.bind("<Leave>", lambda e, c=close: c.configure(text_color=MUTED))
        if index is None:
            self.tabs.append(rec)
        else:
            self.tabs.insert(index, rec)
        self._refresh_tab(rec)
        self._layout_tabs()
        return rec

    def _rec(self, page):
        return next((r for r in self.tabs if r["page"] is page), None)

    def _layout_tabs(self):
        n = len(self.tabs)
        if not n:
            return
        try:
            scale = self._get_window_scaling()
            avail = self.bar.winfo_width() / scale - 330
        except Exception:  # noqa: BLE001
            avail = 800
        w = int(max(self.TAB_MIN, min(self.TAB_MAX, avail / n - 6)))
        for r in self.tabs:
            r["frame"].configure(width=w)
            r["frame"].pack_forget()
        for r in self.tabs:
            r["frame"].pack(side="left", padx=(0, 6))

    def _refresh_tab(self, rec):
        page = rec["page"]
        text = page.tab_label()
        max_chars = 26
        rec["lbl"].configure(text=text if len(text) <= max_chars else text[:max_chars - 1] + "…")
        active = page is self.current
        rec["frame"].configure(fg_color=SURFACE2 if active else "transparent",
                               border_width=1 if active else 0, border_color=BORDER)
        rec["lbl"].configure(text_color=TEXT if active else MUTED)
        kind = getattr(page, "state_kind", "off")
        rec["dot"].configure(text_color=TerminalTab.STATE_COLORS.get(kind, MUTED))

    def tab_changed(self, page):
        rec = self._rec(page)
        if rec:
            self._refresh_tab(rec)
        if page is self.current:
            self.title(f"{page.title()} · v{APP_VERSION}")

    def select(self, page):
        if page is self.current and page.winfo_ismapped():
            return
        if self.current is not None and self._rec(self.current):
            self.current.pack_forget()
        self.current = page
        page.pack(in_=self.content, fill="both", expand=True)
        for r in self.tabs:
            self._refresh_tab(r)
        self.title(f"{page.title()} · v{APP_VERSION}")
        page.on_show()

    def new_connect_tab(self):
        page = ConnectPage(self.content, self)
        self._add_tab(page)
        self.select(page)
        return page

    def open_session(self, host, port, user, password, from_page=None):
        """Открыть сессию во вкладке: форма подключения превращается в терминал."""
        tab = TerminalTab(self.content, self.cfg, host, port, user, password, app=self)
        if from_page is not None and self._rec(from_page):
            idx = self.tabs.index(self._rec(from_page))
            self._drop_rec(from_page)
            self._add_tab(tab, idx)
        else:
            self._add_tab(tab)
        self.select(tab)
        return tab

    def open_bulk(self, ips, port, user, password, label, from_page=None):
        tab = BulkTab(self.content, self, ips, port, user, password, label)
        if from_page is not None and self._rec(from_page):
            idx = self.tabs.index(self._rec(from_page))
            self._drop_rec(from_page)
            self._add_tab(tab, idx)
        else:
            self._add_tab(tab)
        self.select(tab)
        return tab

    def _drop_rec(self, page):
        rec = self._rec(page)
        if rec is None:
            return
        self.tabs.remove(rec)
        rec["frame"].destroy()
        if page is self.current:
            self.current = None
        try:
            page.pack_forget()
        except tk.TclError:
            pass

    def remove_tab(self, page):
        """Закрыть вкладку (для сессий вызывается из on_close уже после отключения)."""
        rec = self._rec(page)
        idx = self.tabs.index(rec) if rec else 0
        was_current = page is self.current
        self._drop_rec(page)
        try:
            page.destroy()
        except tk.TclError:
            pass
        if not self.tabs:
            self.new_connect_tab()
        elif was_current:
            self.select(self.tabs[min(idx, len(self.tabs) - 1)]["page"])
        self._layout_tabs()

    def request_close(self, page):
        """Крестик на вкладке: у живой сессии сначала спрашиваем."""
        if page.is_alive() and not ask_confirm(
                self, "Закрыть вкладку?",
                f"Сессия {page.tab_label()} будет завершена.", "Закрыть", "warn"):
            return
        if hasattr(page, "on_close"):
            page.on_close()
        else:
            self.remove_tab(page)

    def on_quit(self, force=False):
        live = [r["page"] for r in self.tabs if r["page"].is_alive()]
        if live and not force and not ask_confirm(
                self, "Закрыть программу?",
                f"Открытых сессий: {len(live)}. Все они будут завершены.", "Закрыть", "warn"):
            return
        for r in list(self.tabs):
            page = r["page"]
            if hasattr(page, "on_close_quiet"):
                page.closing = True
                try:
                    page.on_close_quiet()
                except Exception:  # noqa: BLE001
                    pass
        self.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    MainWindow().mainloop()


if __name__ == "__main__":
    main()
