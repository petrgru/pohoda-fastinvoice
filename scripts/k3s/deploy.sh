#!/usr/bin/env bash
# Deploy (or update) Pohoda FastInvoice on the k3s cluster.
#
#   scripts/k3s/deploy.sh                build the app image + apply everything
#   scripts/k3s/deploy.sh --skip-build   apply manifests only (same image tag)
#   scripts/k3s/deploy.sh --with-extras  also adminer + mailpit (dev cluster)
#
# Migrations run as an initContainer of the backend deployment (prestart.sh:
# alembic upgrade head + initial data, the procrastinate schema included in
# the alembic chain), so the rollout itself is the migration step. Alembic's
# Postgres advisory lock serializes the per-replica runs.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

build=true
extras=false
for arg in "$@"; do
  case "$arg" in
    --skip-build) build=false ;;
    --with-extras) extras=true ;;
    *) echo "unknown flag: $arg" >&2 && exit 1 ;;
  esac
done

$build && build_image
apply_manifests "$extras"
wait_rollouts

echo "==> done"
$KUBECTL -n "$K3S_NS" get pods,svc,ingress
