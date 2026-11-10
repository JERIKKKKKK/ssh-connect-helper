# -*- coding: utf-8 -*-
"""Обновление через GitHub: без реального GitHub — локальный сервер, подменяющий API и загрузки."""
import hashlib, json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ssh_connect as a

fails = 0


def eq(name, got, want):
    global fails
    good = got == want
    fails += not good
    print(("PASS " if good else "FAIL ") + name + ("" if good else f"\n   got : {got!r}\n   want: {want!r}"))


def ok(name, cond, info=""):
    eq(name + (f" [{info}]" if not cond and info else ""), bool(cond), True)


PAYLOAD = os.urandom(200_000)
SHA = hashlib.sha256(PAYLOAD).hexdigest()
STATE = {"mode": "ok", "port": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        base = f"http://127.0.0.1:{STATE['port']}"
        if self.path.endswith("/releases/latest"):
            m = STATE["mode"]
            if m == "404":
                return self._send(404, b'{"message":"Not Found"}')
            if m == "403":
                return self._send(403, b"{}")
            if m == "badjson":
                return self._send(200, b"<html>")
            if m == "notag":
                return self._send(200, json.dumps({"tag_name": "latest"}).encode())
            assets = [
                {"name": "SSH_Connect.exe", "browser_download_url": base + "/dl/exe", "size": len(PAYLOAD),
                 "digest": "sha256:" + (SHA if m != "badsum" else "0" * 64)},
                {"name": "SSH_Connect_Setup_1.8.0.exe", "browser_download_url": base + "/dl/exe", "size": len(PAYLOAD)},
                {"name": "ssh-connect_1.8.0_all.deb", "browser_download_url": base + "/dl/exe", "size": len(PAYLOAD)},
                {"name": "ssh-connect-1.8.0-1.noarch.rpm", "browser_download_url": base + "/dl/exe", "size": len(PAYLOAD) + 5},
                {"name": "SHA256SUMS.txt", "browser_download_url": base + "/dl/sums", "size": 100},
                {"name": "redir.bin", "browser_download_url": base + "/dl/redir", "size": len(PAYLOAD)},
            ]
            return self._send(200, json.dumps({"tag_name": "v1.8.0", "name": "Версия 1.8.0", "body": "- новое\n- исправлено",
                                               "html_url": "https://github.com/x/y/releases/tag/v1.8.0",
                                               "assets": assets}).encode())
        if self.path == "/dl/exe":
            return self._send(200, PAYLOAD, "application/octet-stream")
        if self.path == "/dl/sums":
            body = f"{SHA}  ssh-connect_1.8.0_all.deb\n{'1'*64}  other.bin\n".encode()
            return self._send(200, body, "text/plain")
        if self.path == "/dl/redir":
            return self._send(302, b"", extra={"Location": f"http://127.0.0.2:{STATE['port']}/dl/exe"})
        self._send(404)


srv = ThreadingHTTPServer(("0.0.0.0", 0), H)
STATE["port"] = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
a.GITHUB_API = f"http://127.0.0.1:{STATE['port']}"
a.UPDATE_TEST_HOSTS = ("127.0.0.1",)
tmp = tempfile.mkdtemp()

# --- версии
eq("version_tuple", (a.version_tuple("v1.8.0"), a.version_tuple("1.10"), a.version_tuple("релиз"), a.version_tuple("")),
   ((1, 8, 0), (1, 10), (), ()))
eq("is_newer", (a.is_newer("1.8.0", "1.7.0"), a.is_newer("v1.10.0", "1.9.9"), a.is_newer("1.7", "1.7.0"),
                a.is_newer("1.7.0", "1.7.1"), a.is_newer("junk", "1.0")), (True, True, False, False, False))

# --- разрешённые адреса
U = a.update_url_allowed
saved = a.UPDATE_TEST_HOSTS
a.UPDATE_TEST_HOSTS = ()
eq("url allowlist", [U("https://api.github.com/x"), U("https://github.com/a/b"), U("https://objects.githubusercontent.com/z"),
                     U("https://release-assets.githubusercontent.com/z"), U("http://github.com/a"), U("https://evil.com/github.com"),
                     U("https://github.com.evil.com/x"), U("https://notgithub.com/x"), U("ftp://github.com/x"), U("")],
   [True, True, True, True, False, False, False, False, False, False])
a.UPDATE_TEST_HOSTS = saved

# --- релиз
rel = a.fetch_latest_release()
eq("release parsed", (rel["tag"], rel["version"], rel["name"], len(rel["assets"])), ("v1.8.0", "1.8.0", "Версия 1.8.0", 6))
ok("release body and url", "исправлено" in rel["body"] and rel["url"].startswith("https://github.com/"))
for mode, frag in (("404", "пока нет"), ("403", "ограничил"), ("badjson", "непонятный"), ("notag", "версию релиза")):
    STATE["mode"] = mode
    try:
        a.fetch_latest_release()
        got = ""
    except a.UpdateError as e:
        got = str(e)
    ok(f"fetch error {mode}", frag in got, got)
STATE["mode"] = "ok"
a.GITHUB_API = "http://127.0.0.1:1"
try:
    a.fetch_latest_release()
    got = ""
except a.UpdateError as e:
    got = str(e)
ok("no connection -> clear message", got.startswith("Нет связи с GitHub"), got)
a.GITHUB_API = f"http://127.0.0.1:{STATE['port']}"

# --- выбор файла релиза
P = lambda k: (a.pick_asset(rel, k) or {}).get("name")
eq("pick asset per platform", (P("win_installer"), P("win_exe"), P("deb"), P("rpm"), P("source")),
   ("SSH_Connect_Setup_1.8.0.exe", "SSH_Connect.exe", "ssh-connect_1.8.0_all.deb", "ssh-connect-1.8.0-1.noarch.rpm", None))

# --- загрузка и проверка суммы
exe_asset = a.pick_asset(rel, "win_exe")
prog = []
path, verified = a.download_asset(rel, exe_asset, os.path.join(tmp, "d1"), lambda g, t: prog.append((g, t)))
ok("download ok, digest verified", verified and open(path, "rb").read() == PAYLOAD and not os.path.exists(path + ".part"))
ok("progress reported up to the end", prog and prog[-1][0] == len(PAYLOAD) and prog[-1][1] == len(PAYLOAD))
deb = a.pick_asset(rel, "deb")
path2, verified2 = a.download_asset(rel, deb, os.path.join(tmp, "d2"))
ok("checksum taken from SHA256SUMS.txt", verified2 and os.path.basename(path2) == deb["name"])
inst = a.pick_asset(rel, "win_installer")
path3, verified3 = a.download_asset(rel, inst, os.path.join(tmp, "d3"))
ok("no checksum anywhere -> still ok but flagged unverified", not verified3 and os.path.exists(path3))
STATE["mode"] = "badsum"
rel_bad = a.fetch_latest_release()
try:
    a.download_asset(rel_bad, a.pick_asset(rel_bad, "win_exe"), os.path.join(tmp, "d4"))
    got = ""
except a.UpdateError as e:
    got = str(e)
ok("wrong checksum -> refused, nothing left on disk", "Контрольная сумма" in got and not os.listdir(os.path.join(tmp, "d4")), got)
STATE["mode"] = "ok"
rpm = a.pick_asset(rel, "rpm")
try:
    a.download_asset(rel, rpm, os.path.join(tmp, "d5"))
    got = ""
except a.UpdateError as e:
    got = str(e)
ok("size mismatch -> refused", "не полностью" in got and not os.listdir(os.path.join(tmp, "d5")), got)
ev = threading.Event()
ev.set()
try:
    a.download_asset(rel, exe_asset, os.path.join(tmp, "d6"), cancel=ev)
    got = ""
except a.UpdateError as e:
    got = str(e)
ok("cancel stops the download and cleans up", "отменена" in got and not os.listdir(os.path.join(tmp, "d6")), got)
redir = [x for x in rel["assets"] if x["name"] == "redir.bin"][0]
try:
    a.download_asset(rel, redir, os.path.join(tmp, "d7"))
    got = ""
except a.UpdateError as e:
    got = str(e)
ok("redirect to a non-GitHub host is refused", "не на GitHub" in got, got)

# --- как установлена программа
ex = lambda paths: (lambda p: p in paths)
D = a.detect_install_mode
eq("mode: windows installer", D(frozen=True, platform="win32", exe=r"C:\App\SSH_Connect.exe", exists=lambda p: p.endswith("unins000.exe"))["kind"], "win_installer")
eq("mode: windows portable exe", D(frozen=True, platform="win32", exe=r"C:\App\SSH_Connect.exe", exists=lambda p: False)["kind"], "win_exe")
eq("mode: windows from source", D(frozen=False, platform="win32")["kind"], "source")
eq("mode: linux deb", D(frozen=False, platform="linux", script="/opt/ssh-connect/ssh_connect.py", which=lambda n: "/usr/bin/dpkg" if n == "dpkg" else None)["kind"], "deb")
eq("mode: linux rpm", D(frozen=False, platform="linux", script="/opt/ssh-connect/ssh_connect.py", which=lambda n: "/usr/bin/rpm" if n == "rpm" else None)["kind"], "rpm")
eq("mode: linux from source", D(frozen=False, platform="linux", script="/home/u/ssh_connect.py", which=lambda n: "/x")["kind"], "source")

# --- bat для Windows
bat = a.build_win_update_bat("win_exe", r"C:\Users\Иван Петров\App\SSH_Connect.exe.new", r"C:\Users\Иван Петров\App\SSH_Connect.exe")
ok("bat: CRLF only", "\r\n" in bat and "\n" not in bat.replace("\r\n", ""))
ok("bat: utf-8 codepage, retry loop, move, restart, self delete",
   "chcp 65001" in bat and ":retry" in bat and 'move /Y "C:\\Users\\Иван Петров\\App\\SSH_Connect.exe.new" "C:\\Users\\Иван Петров\\App\\SSH_Connect.exe"' in bat
   and 'start "" "C:\\Users\\Иван Петров\\App\\SSH_Connect.exe"' in bat and 'del "%~f0"' in bat, bat)
bat2 = a.build_win_update_bat("win_installer", r"C:\T\Setup 100%.exe", r"C:\App\SSH_Connect.exe")
ok("bat installer: silent flags, % escaped, restart after", '"C:\\T\\Setup 100%%.exe" /SILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS /NOCANCEL' in bat2
   and bat2.index("/SILENT") < bat2.index('start ""'), bat2)

# --- Linux
which_only = lambda *names: (lambda n: "/usr/bin/" + n if n in names else None)
eq("deb command", a.linux_install_command("/tmp/x y.deb", "deb"), "dpkg -i '/tmp/x y.deb' || (apt-get install -f -y && dpkg -i '/tmp/x y.deb')")
eq("rpm: dnf / zypper / yum / rpm", [a.linux_install_command("/t/p.rpm", "rpm", which_only(*n)) for n in (("dnf",), ("zypper",), ("yum",), ())],
   ["dnf install -y /t/p.rpm", "zypper --non-interactive --no-gpg-checks install /t/p.rpm", "yum install -y /t/p.rpm", "rpm -U /t/p.rpm"])
A = a.linux_privileged_argv
eq("root runs directly", A("echo 1", uid=0)[1], "root")
eq("user with display and pkexec -> pkexec", A("echo 1", uid=1000, which=which_only("pkexec"), has_display=True)[1], "pkexec")
eq("no display -> sudo -S", A("echo 1", uid=1000, which=which_only("pkexec"), has_display=False)[1], "sudo")
eq("no pkexec -> sudo -S", A("echo 1", uid=1000, which=which_only(), has_display=True)[0][:3], ["sudo", "-S", "-p"])
eq("fallback to sudo after pkexec refused", A("echo 1", uid=1000, which=which_only("pkexec"), has_display=True, prefer="sudo")[1], "sudo")
argv, how = A("echo привет-ok", uid=0)
r = subprocess.run(argv, capture_output=True, text=True)
ok("root argv really runs", r.stdout.strip() == "привет-ok", r.stderr)
ok("texts for every mode", all(a.update_how_text(k) for k in ("win_installer", "win_exe", "deb", "rpm", "source")))
ok("repo is configured", a.GITHUB_REPO == "JERIKKKKKK/ssh-connect-helper")

srv.shutdown()
print(f"\n{'ALL OK' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
