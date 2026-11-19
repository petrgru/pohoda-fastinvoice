#!/usr/bin/env bash
# pg_dumpall into k8s/backups/, keeping the newest KEEP files (default 7).
# Cron it once the cluster is boring enough to trust:
#   @daily scripts/k3s/backup.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

dest_dir=${BACKUP_DIR:-"$ROOT/k8s/backups"}
keep=${KEEP:-7}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$dest_dir/fastinvoice-$stamp.sql.gz"
mkdir -p "$dest_dir"

# -i: single connection, consistent enough for this app size; --no-owner so a
# restore can run against any postgres superuser
$KUBECTL -n "$K3S_NS" exec statefulset/db -- \
  pg_dumpall -U postgres --no-owner | gzip >"$out"

ls -1t "$dest_dir"/fastinvoice-*.sql.gz 2>/dev/null |
  tail -n +$((keep + 1)) | xargs -r rm -f
echo "backup written: $out"
