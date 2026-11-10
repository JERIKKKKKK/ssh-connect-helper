import re, sys
src = open("ssh_connect.py", encoding="utf-8").read()
m = re.search(r'^APP_VERSION\s*=\s*"([0-9][0-9A-Za-z.]*)"', src, re.M)
print(m.group(1) if m else "1.0.0")
