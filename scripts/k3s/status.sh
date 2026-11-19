#!/usr/bin/env bash
# Cluster state at a glance: workloads, images, PVCs, the Ingress URL.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

echo "== pods"
$KUBECTL -n "$K3S_NS" get pods -o wide
echo
echo "== images in use"
$KUBECTL -n "$K3S_NS" get deploy,sts \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image'
echo
echo "== persistent volumes"
$KUBECTL -n "$K3S_NS" get pvc
echo
echo "== rollouts"
for d in backend worker-parse worker-light meilisearch; do
  $KUBECTL -n "$K3S_NS" rollout status "deployment/$d" 2>/dev/null || true
done
$KUBECTL -n "$K3S_NS" rollout status statefulset/db 2>/dev/null || true
echo
echo "== endpoints"
$KUBECTL -n "$K3S_NS" get ingress -o custom-columns='NAME:.metadata.name,HOSTS:.spec.rules[*].host,ADDRESS:.status.loadBalancer.ingress[*].ip'
