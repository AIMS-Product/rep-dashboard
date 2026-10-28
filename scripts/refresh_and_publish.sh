#!/usr/bin/env bash
set -euo pipefail

# Rollbacks below discard only this script's generated commit in a disposable runner.
if [[ "${GITHUB_ACTIONS:-}" != "true" ]] || [[ -n "$(git status --porcelain)" ]]; then
  echo "::error::Publication requires a clean GitHub Actions checkout."
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3; do
  git fetch origin main
  git merge --ff-only origin/main
  base_commit=$(git rev-parse HEAD)

  python3 -u scripts/fetch_data.py
  python3 -u scripts/build_yesterday_preview.py --archive-missing
  python3 -u scripts/build_yesterday_preview.py

  git add data.json archives/ daily-preview.html
  if git diff --staged --quiet; then
    echo "Dashboard data is already current."
    exit 0
  fi
  git commit -m "📊 Update dashboard data - $(date -u +'%Y-%m-%d %H:%M UTC')"

  if git push origin HEAD:main; then
    exit 0
  fi

  git fetch origin main
  if [[ "$(git rev-parse origin/main)" == "$base_commit" ]]; then
    echo "::error::Push failed without a concurrent update; check repository permissions."
    exit 1
  fi
  if [[ "$attempt" == "3" ]]; then
    echo "::error::Main advanced during all three publication attempts."
    exit 1
  fi

  # Regenerate from the new main instead of merging competing data snapshots.
  git reset --hard "$base_commit"
  echo "Main advanced during refresh; rebuilding from the latest version (attempt $((attempt + 1))/3)."
done
