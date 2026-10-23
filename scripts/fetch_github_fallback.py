"""Fallback metadata for repos the API rate-limited: use atom feeds + raw LICENSE (no API quota)."""
import json, re, sys, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
meta_p = ROOT / "toolbox" / "github_meta.json"
meta = json.loads(meta_p.read_text(encoding="utf-8"))

def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "renderforge"})
        return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        return None

def lic(text):
    t = (text or "")[:600].lower()
    for k, v in (("gnu affero", "AGPL-3.0"), ("gnu general public license", "GPL"), ("gnu lesser", "LGPL"), ("mit license", "MIT"),
                 ("apache license", "Apache-2.0"), ("bsd", "BSD"), ("mozilla public", "MPL-2.0"), ("unlicense", "Unlicense")):
        if k in t: return v
    return "UNKNOWN" if t else None

for repo, m in meta.items():
    if not m.get("error"): continue
    rel = fetch(f"https://github.com/{repo}/releases.atom")
    com = fetch(f"https://github.com/{repo}/commits.atom")
    m["latest_release"] = (re.findall(r"<title>([^<]+)</title>", rel or "")[1:2] or [None])[0]
    m["release_date"] = (re.findall(r"<updated>([^<]+)</updated>", rel or "")[1:2] or [None])[0]
    m["pushed_at"] = (re.findall(r"<updated>([^<]+)</updated>", com or "")[:1] or [None])[0]
    for branch in ("master", "main"):
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
            t = fetch(f"https://raw.githubusercontent.com/{repo}/{branch}/{name}")
            if t:
                m["license"] = lic(t); break
        if m.get("license"): break
    m["source"] = "atom-fallback"; m["error"] = None if (m["pushed_at"]) else m["error"]
    print(repo, m["license"], m["pushed_at"], m["latest_release"])
meta_p.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
