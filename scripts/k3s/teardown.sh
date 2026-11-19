#!/usr/bin/env bash
# Remove the whole install (everything lives in one namespace, PVCs included).
# Snapshot the data first: scripts/k3s/backup.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

$KUBECTL delete namespace "$K3S_NS"
echo "namespace $K3S_NS deleted - images stay in containerd until pruned"
