#!/usr/bin/env bash
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != "true" ]] || [[ -n "$(git status --porcelain)" ]]; then
  echo "Publication requires a clean GitHub Actions checkout." >&2
  exit 1
fi
stage_dir=$(mktemp -d)
trap 'rm -rf "$stage_dir"' EXIT
git fetch origin main
git merge --ff-only origin/main
base_commit=$(git rev-parse HEAD)
python3 -u scripts/publish_sales_dashboards.py build \
  --mtd-root .reporting-dependencies/mtd \
  --manager-root .reporting-dependencies/manager --manager-base .reporting-dependencies/manager/data.json --output "$stage_dir/release"
python3 -u scripts/build_yesterday_preview.py --archive-missing
python3 -u scripts/build_yesterday_preview.py
python3 scripts/publish_sales_dashboards.py accept --stage "$stage_dir/release" --site-root shared
cp "$stage_dir/release/rep.json" data.json
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add -- shared/ data.json archives/ daily-preview.html
if git diff --cached --quiet; then exit 0; fi
python3 - <<'PY'
import subprocess
paths=subprocess.check_output(['git','diff','--cached','--name-only'],text=True).splitlines()
assert all(p in {'data.json','daily-preview.html'} or p.startswith(('shared/','archives/')) for p in paths),paths
PY
git commit -m "Publish shared MTD, rep and manager reporting snapshot"
git fetch origin main
if [[ "$(git rev-parse origin/main)" != "$base_commit" ]]; then
  echo "Main changed during the build; rerun to publish from the current source." >&2
  exit 1
fi
git push origin HEAD:main
