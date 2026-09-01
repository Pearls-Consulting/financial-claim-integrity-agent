#!/usr/bin/env bash
# Server-side deploy of one git SHA — invoked by GitHub Actions over SSH.
#
#   bash remote_deploy.sh <git-sha>
#
# Expects the CI-built UI bundle at /tmp/ui-dist-<sha>.tgz (containing dist/),
# uploaded by the workflow before this script runs. The code itself is fetched
# from GitHub via the server's deploy key — CI never uploads the backend.
#
# Same post-ship sequence as deploy/ops/deploy.sh (the manual working-tree
# path): pip-sync shared venv -> link persistent state -> flip `current` ->
# restart + health check -> prune releases. Keep the two in step when
# changing either.
set -euo pipefail

SHA="${1:?usage: remote_deploy.sh <git-sha>}"
BASE=/srv/claimint
APP=$BASE/app
DATA=$BASE/data
PORT=9201
# github.com-claimint = ~/.ssh/config alias on the server pinning this repo's
# deploy key (the default identity authenticates as an unrelated account).
REPO="git@github.com-claimint:Pearls-Consulting/financial-claim-integrity-agent.git"
REL="$APP/releases/git-${SHA:0:12}"
DIST_TGZ="/tmp/ui-dist-${SHA}.tgz"

[ -f "$DIST_TGZ" ] || { echo "missing $DIST_TGZ (workflow uploads it first)"; exit 1; }

echo ">> fetching $SHA"
if [ ! -d "$REL/.git" ]; then
  rm -rf "$REL"
  mkdir -p "$REL"
  git -C "$REL" init -q
  git -C "$REL" remote add origin "$REPO"
fi
git -C "$REL" fetch -q --depth 1 origin "$SHA"
git -C "$REL" checkout -q --detach FETCH_HEAD

echo ">> unpacking CI-built UI"
rm -rf "$REL/ui/dist"
tar -xzf "$DIST_TGZ" -C "$REL/ui"
rm -f "$DIST_TGZ"
[ -f "$REL/ui/dist/index.html" ] || { echo "dist unpack failed"; exit 1; }

echo ">> syncing python deps"
"$APP/venv/bin/pip" install -q -r "$REL/backend/requirements.txt"

echo ">> linking persistent state"
rm -rf "$REL/backend/data" "$REL/backend/uploads" "$REL/backend/.cache"
mkdir -p "$DATA/cache"
ln -sfn "$DATA/db" "$REL/backend/data"
ln -sfn "$DATA/uploads" "$REL/backend/uploads"
# Model-read cache (EXTRACTION_CACHE): keyed by file content, so it must
# outlive releases — otherwise every deploy re-reads every demo document.
ln -sfn "$DATA/cache" "$REL/backend/.cache"

echo ">> switching current -> $REL"
ln -sfn "$REL" "$APP/current"

echo ">> restarting service"
sudo systemctl restart claimint

# Wait for readiness rather than guessing it (2-vCPU box shared with the
# prequal fleet; a flat sleep produced red pipelines for good deploys there).
code=000
for _ in $(seq 1 45); do  # up to 90s before calling it a failure
  code="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" || true)"
  [ "$code" = "200" ] && break
  sleep 2
done
echo "   health :$PORT -> $code"

echo ">> pruning old releases (keep 2)"
ls -1dt "$APP"/releases/* | tail -n +3 | xargs -r rm -rf
df -h / | tail -1
[ "$code" = "200" ]
