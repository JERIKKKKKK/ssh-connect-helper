# -*- coding: utf-8 -*-
import hashlib, json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp(); os.environ["HOME"] = os.environ["APPDATA"]; os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"]
import customtkinter as ctk
import ssh_connect as a

PAY = b"x" * 50000
SHA = hashlib.sha256(PAY).hexdigest()
PORT = [0]
class H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        b = f"http://127.0.0.1:{PORT[0]}"
        if self.path.endswith("/latest"):
            body = json.dumps({"tag_name": "v9.0.0", "body": "- что-то новое", "html_url": "https://github.com/a/b/releases/v9",
                "assets": [{"name": "ssh-connect_9.0.0_all.deb", "browser_download_url": b + "/f", "size": len(PAY), "digest": "sha256:" + SHA}]}).encode()
        else:
            body = PAY
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); PORT[0] = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
a.GITHUB_API = f"http://127.0.0.1:{PORT[0]}"; a.UPDATE_TEST_HOSTS = ("127.0.0.1",)
a.detect_install_mode = lambda *x, **k: {"kind": "deb", "exe": None, "script": "/opt/ssh-connect/ssh_connect.py"}
ran = []
a.linux_privileged_argv = lambda cmd, **k: (["bash", "-c", "echo установка; echo '%s'" % cmd.replace("'", "")], "root")
a.linux_install_command = lambda path, kind, **k: "dpkg -i " + os.path.basename(path)

ctk.set_appearance_mode("dark")
app = a.MainWindow()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.02)

app.cfg.data["update_last_check"] = 0
pump(5.5)
check("auto check lights the button", app.update_ver == "9.0.0" and "9.0.0" in app.btn_update.cget("text"))
app.btn_update.invoke(); pump(1.5)
w = a.UpdateWindow.instance
check("window opened", w is not None and w.winfo_exists())
check("shows versions and notes", "9.0.0" in w.versions.cget("text") and "новое" in w.box.get("1.0", "end"))
check("main button is Update", w.btn_main.cget("text") == "Обновить" and w.btn_main.cget("state") == "normal")
a.show_update(app); check("singleton", a.UpdateWindow.instance is w)
w._on_main(); pump(4)
check("install ran, log shown", "dpkg -i ssh-connect_9.0.0_all.deb" in w.box.get("1.0", "end"))
check("done message + restart button", "Готово" in w.status.cget("text") and w.btn_main.cget("text") == "Перезапустить")
w._skip(); pump(0.5)
check("skip stored and button reset", app.cfg.data.get("update_skip") == "9.0.0" and app.update_ver is None)
a.HelpWindow(app) if hasattr(a, "HelpWindow") else None
pump(0.5)
check("help has updates topic", any(t[0] == "Обновления" for t in a.HELP_TOPICS))
srv.shutdown()
print("ALL OK" if not fails else f"{len(fails)} FAILED")
os._exit(1 if fails else 0)
