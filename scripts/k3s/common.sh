#!/usr/bin/env bash
# Shared helpers for the k3s management scripts. Sources k8s/k3s.env, exposes
# the image name, template rendering and the app secret sync.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
ENV_FILE=${K3S_ENV:-"$ROOT/k8s/k3s.env"}
KUBECTL=${KUBECTL:-kubectl}
# on a single-node k3s the script runs on the node; point KUBECTL elsewhere
# (and REGISTRY at a pullable registry) for a remote cluster
K3S_CTR=${K3S_CTR:-sudo k3s ctr}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE - run scripts/k3s/setup-env.sh or copy k8s/env.example" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

K3S_NS=${K3S_NS:-fastinvoice}
IMAGE_TAG=${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}
APP_IMAGE="${REGISTRY:+${REGISTRY}/}pohoda-fastinvoice:${IMAGE_TAG}"

# Render a manifest template; only our own placeholders are substituted so
# $-strings inside the YAML survive untouched.
render() {
  envsubst '${K3S_NS} ${APP_IMAGE}' <"$1"
}

# The whole env file becomes the app secret (the app reads plain env vars),
# minus cluster-only keys, with surrounding quotes stripped (--from-env-file
# takes values verbatim). DATABASE_URL is derived, matching the Service names.
app_env_file() {
  local tmp
  tmp=$(mktemp)
  grep -vE '^(K3S_NS|REGISTRY|IMAGE_TAG)=' "$ENV_FILE" |
    sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)="?([^"]*)"?$/\1=\2/' >"$tmp"
  echo "DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD:?}@db:5432/app" >>"$tmp"
  echo "$tmp"
}

sync_secret() {
  local tmp
  tmp=$(app_env_file)
  $KUBECTL -n "$K3S_NS" create secret generic fastinvoice-env \
    --from-env-file="$tmp" --dry-run=client -o yaml |
    $KUBECTL apply -f -
  rm -f "$tmp"
}

build_image() {
  echo "==> building $APP_IMAGE"
  docker build -f "$ROOT/backend/Dockerfile" -t "$APP_IMAGE" "$ROOT"
  if [[ -n "${REGISTRY:-}" ]]; then
    echo "==> pushing to $REGISTRY"
    docker push "$APP_IMAGE"
  else
    # no registry: hand the tar to k3s's containerd (docs.k3s.io air-gap docs)
    local tar=/tmp/pohoda-fastinvoice-image.tar
    echo "==> importing into k3s containerd ($tar)"
    docker save -o "$tar" "$APP_IMAGE"
    # shellcheck disable=SC2086
    $K3S_CTR images import "$tar"
    rm -f "$tar"
  fi
}

apply_manifests() {
  $KUBECTL apply -f <(render "$ROOT/k8s/00-namespace.yaml")
  sync_secret
  local files=(20-postgres 40-backend)
  [[ "${MEILISEARCH_ENABLED:-true}" == "true" ]] && files+=(30-meilisearch)
  for f in "${files[@]}"; do
    $KUBECTL -n "$K3S_NS" apply -f <(render "$ROOT/k8s/$f.yaml")
  done
}

wait_rollouts() {
  $KUBECTL -n "$K3S_NS" rollout status statefulset/db --timeout=180s
  [[ "${MEILISEARCH_ENABLED:-true}" == "true" ]] &&
    $KUBECTL -n "$K3S_NS" rollout status deployment/meilisearch --timeout=120s
  # runs the alembic initContainer through to a Ready pod == migrated schema
  $KUBECTL -n "$K3S_NS" rollout status deployment/backend --timeout=300s
  $KUBECTL -n "$K3S_NS" rollout status deployment/worker-parse --timeout=180s
  $KUBECTL -n "$K3S_NS" rollout status deployment/worker-light --timeout=180s
}
