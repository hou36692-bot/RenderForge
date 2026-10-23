"""Fetch GitHub metadata (license, last push, archived, latest release + assets) for every GitHub tool in the registry."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS = sys.argv[1:] or json.loads((ROOT / "toolbox" / "github_repos.json").read_text())


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "renderforge", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


out = {}
for repo in REPOS:
    r = get(f"https://api.github.com/repos/{repo}")
    rel = get(f"https://api.github.com/repos/{repo}/releases/latest")
    out[repo] = {
        "license": (r.get("license") or {}).get("spdx_id") if "error" not in r else None,
        "pushed_at": r.get("pushed_at"), "archived": r.get("archived"), "stars": r.get("stargazers_count"),
        "language": r.get("language"), "description": r.get("description"), "default_branch": r.get("default_branch"),
        "latest_release": rel.get("tag_name"), "release_date": rel.get("published_at"),
        "assets": [a["name"] for a in rel.get("assets", [])][:12] if "error" not in rel else [],
        "error": r.get("error"),
    }
    print(repo, out[repo]["license"], out[repo]["pushed_at"], out[repo]["latest_release"], out[repo]["error"] or "")
(ROOT / "toolbox" / "github_meta.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
