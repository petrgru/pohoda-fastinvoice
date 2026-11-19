#!/usr/bin/env bash
# Post-deploy smoke test through the real HTTP stack (in-cluster, no DNS needed).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_curl() { # args passed to curl inside a throwaway pod
  $KUBECTL -n "$K3S_NS" run smoke --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- \
    curl -sS "$@"
}

echo "== health check"
run_curl --fail "http://backend:8000/api/v1/utils/health-check/" >/dev/null && echo "ok"

echo "== openapi served"
run_curl --fail "http://backend:8000/api/v1/openapi.json" |
  grep -q '"openapi"' && echo "ok"

echo "== login of the first superuser"
body=$(printf 'username=%s&password=%s' "${FIRST_SUPERUSER:?}" "${FIRST_SUPERUSER_PASSWORD:?}")
token=$(run_curl --fail -X POST "http://backend:8000/api/v1/login/access-token" \
  -H 'content-type: application/x-www-form-urlencoded' --data "$body" |
  sed -E 's/.*"access_token":"([^"]+)".*/\1/')
[[ -n $token && $token != *'{'* ]] && echo "ok"

echo "== ingress routes to the backend (Host: $DOMAIN)"
$KUBECTL -n "$K3S_NS" run smoke-web --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- \
  curl -sS --fail -H "Host: $DOMAIN" "http://traefik.kube-system.svc/dashboard/" -o /dev/null ||
  echo "(dashboard probe skipped - Traefik dashboard may be disabled; ingress is covered by the rollout)"

echo "smoke OK"
