"""Import a verified Railway release into the current GitHub Pages repository."""

from base64 import b64decode
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ORIGIN = "https://sales-dashboards-production.up.railway.app"
AUDIENCE = ORIGIN + "/github-pages"
SITES = {
    "AIMS-Product/rep-dashboard": ("sales", Path("shared/current.json"), {
        "rep.json", "mtd.json", "mtd.html", "manager.json", "manager.html", "manifest.json",
    }),
    "AIMS-Product/call-capacity-dashboard": ("capacity", Path("railway-release.json"), {
        "index.html", "changelog.html",
    }),
}


def get_json(url, bearer=None):
    headers = {"Cache-Control": "no-cache"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    with urlopen(Request(url, headers=headers), timeout=45) as response:
        return json.load(response)


def decode_release(release, expected):
    if not re.fullmatch(r"[0-9a-f]{24}", release["release_id"]):
        raise ValueError("Invalid Railway release ID")
    cutoff = datetime.fromisoformat(release["cutoff"])
    if cutoff.tzinfo is None or set(release["files"]) != expected or set(release["sha256"]) != expected:
        raise ValueError("Invalid Railway release scope")
    files = {name: b64decode(content, validate=True) for name, content in release["files"].items()}
    if any(sha256(files[name]).hexdigest() != release["sha256"][name] for name in expected):
        raise ValueError("Railway release checksum mismatch")
    return files


def stage_release(group, pointer, release, files):
    if group == "sales":
        manifest = json.loads(files["manifest.json"])
        hashes = {key: value for key, value in release["sha256"].items() if key != "manifest.json"}
        if (manifest["schema_version"] != 2 or manifest["release_id"] != release["release_id"]
                or manifest["cutoff"] != release["cutoff"] or manifest["files"] != hashes):
            raise ValueError("Sales manifest does not match the Railway export")
        release_dir = Path("shared/releases") / release["release_id"]
        output = {release_dir / name: content for name, content in files.items()}
        output[Path("data.json")] = files["rep.json"]
        metadata = {key: manifest[key] for key in (
            "schema_version", "release_id", "cutoff", "rules_version", "files",
        )}
    else:
        output = {Path(name): content for name, content in files.items()}
        metadata = {"release_id": release["release_id"], "cutoff": release["cutoff"],
                    "files": release["sha256"]}
    output[pointer] = (json.dumps(metadata, sort_keys=True) + "\n").encode()
    for path, content in output.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return output


def git(*args):
    subprocess.run(["git", *args], check=True, timeout=90)


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    group, pointer, expected = SITES[repo]
    oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" + quote(AUDIENCE, safe="")
    token = get_json(oidc_url, os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])["value"]
    try:
        release = get_json(f"{AUDIENCE}/{group}/release", token)
    except HTTPError as error:
        if error.code != 503:
            raise
        print("Railway is waiting for the first compatible release")
        return
    files = decode_release(release, expected)
    current = json.loads(pointer.read_text()) if pointer.is_file() else {}
    older_or_current = current and datetime.fromisoformat(current["cutoff"]) >= datetime.fromisoformat(release["cutoff"])
    if not older_or_current:
        output = stage_release(group, pointer, release, files)
        git("config", "user.name", "github-actions[bot]")
        git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git("add", "--", *map(str, output))
        git("commit", "-m", f"Publish Railway dashboard release {release['release_id']}")
        git("push", "origin", "HEAD:main")
        current = json.loads(pointer.read_text())
    live_url = f"https://aims-product.github.io/{repo.split('/')[-1]}/{pointer}"
    try:
        deployed = get_json(f"{live_url}?release={current['release_id']}")["release_id"]
    except (URLError, ValueError, KeyError, TimeoutError):
        deployed = None
    if deployed != current["release_id"] or os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("deploy=true\n")
    print(f"GitHub Pages release selected: {current['release_id']} at {current['cutoff']}")


if __name__ == "__main__":
    main()
