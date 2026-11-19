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

echo "== load balancer entrypoint"
ip=$($KUBECTL -n "$K3S_NS" get svc backend -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
if [[ -n $ip ]]; then
  run_curl --fail "http://${ip}/api/v1/utils/health-check/" >/dev/null && echo "ok: http://$ip/"
else
  echo "servicelb has not assigned the IP yet - re-run once the svc is Ready"
fi

echo "smoke OK"
