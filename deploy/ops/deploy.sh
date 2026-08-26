#!/usr/bin/env bash
# Deploy the local working tree to the demo server — single client (SDB).
#
# Usage (from anywhere, Git Bash on Windows works):
#   bash deploy/ops/deploy.sh            # build UI + ship + restart
#   bash deploy/ops/deploy.sh --skip-ui  # backend-only change, reuse last UI build
#
# What it does, in order:
#   1. builds ui/dist locally (vite)
#   2. ships the working tree (tracked + local changes, minus junk/secrets/state)
#      to a new timestamped dir under /srv/claimint/app/releases/
#   3. syncs python deps into the shared venv (no-op when unchanged)
#   4. links persistent state INTO the release: backend/data (SQLite) and
#      backend/uploads both live under /srv/claimint/data/ and survive deploys
#   5. flips the `current` symlink, restarts the service, health-checks it
#   6. prunes releases, keeping the last 2 (disk on the box is tight)
#
# Notes:
#   * Adapted from pre-qualification-agent deploy/ops/deploy.sh, minus the
#     alembic/tenant machinery (this app is single-tenant, SQLite via stdlib).
#   * Restarting kills any in-flight analysis — deploy when no run is active.
#   * Rollback: ln -sfn <previous release> /srv/claimint/app/current && restart.
#   * Code ships from THIS working tree, not from git — commit independently.
#   * Server config (.env with Azure keys) lives at /srv/claimint/.env and is
#     NOT shipped by this script — edit it on the server.
set -euo pipefail

SERVER="forge@143.110.175.251"
BASE=/srv/claimint
APP=$BASE/app
DATA=$BASE/data
PORT=9201
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REL="$(date +%Y%m%d-%H%M%S)"

cd "$REPO_ROOT"

if [[ "${1:-}" != "--skip-ui" ]]; then
  echo ">> building UI"
  (cd ui && npm run build)
fi

echo ">> shipping release $REL"
ssh "$SERVER" "mkdir -p $APP/releases/$REL"
tar -czf - \
  --exclude=.git --exclude="backend/.venv" --exclude="backend/data" \
  --exclude="backend/uploads" --exclude="backend/.env" --exclude="backend/.env.bak*" \
  --exclude="backend/.pytest_cache" --exclude="ui/node_modules" \
  --exclude=supporting_docs --exclude="__pycache__" --exclude="*.pyc" \
  --exclude="scratch_*.log" \
  . | ssh "$SERVER" "tar -xzf - -C $APP/releases/$REL"

ssh "$SERVER" bash -s <<EOF
set -euo pipefail
echo ">> syncing python deps"
$APP/venv/bin/pip install -q -r $APP/releases/$REL/backend/requirements.txt

echo ">> linking persistent state"
ln -sfn $DATA/db $APP/releases/$REL/backend/data
ln -sfn $DATA/uploads $APP/releases/$REL/backend/uploads

echo ">> switching current -> $REL"
ln -sfn $APP/releases/$REL $APP/current

echo ">> restarting service"
sudo systemctl restart claimint

# Wait for readiness rather than guessing it — the app imports the analyzer
# stack before uvicorn binds, and the box has 2 vCPUs (see the prequal deploy
# scripts for the history behind this loop).
code=000
for _ in \$(seq 1 45); do  # up to 90s before calling it a failure
  code=\$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" || true)
  [ "\$code" = "200" ] && break
  sleep 2
done
echo "   health :$PORT -> \$code"

echo ">> pruning old releases (keep 2)"
ls -1dt $APP/releases/* | tail -n +3 | xargs -r rm -rf
df -h / | tail -1
[ "\$code" = "200" ]
EOF

echo ""
echo "Deployed $REL — https://sdb-twm.vrm-poc.pearls.consulting/"
