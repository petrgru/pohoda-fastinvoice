# k3s deployment

The compose stack expressed as plain Kubernetes manifests, driven by thin
bash wrappers in `scripts/k3s/`. Target: a single-node k3s on the LAN
(the LLM cluster at `192.168.22.154` is reachable from pods via normal
pod egress).

No extra cluster software: k3s's packaged components cover everything —
**servicelb** (KlusterLB) assigns the node IP to the backend `LoadBalancer`
Service, **local-path** provisions the PVCs
([packaged components](https://docs.k3s.io/installation/packaged-components)).
No Traefik/Ingress objects, and the compose-only adminer + mailpit pair is
not deployed.

## Compose → k3s mapping

| compose.yml                | k3s                                                            |
| -------------------------- | -------------------------------------------------------------- |
| `proxy` (Traefik + labels) | nothing — backend Service `type: LoadBalancer` (built-in servicelb) |
| `db` (postgres:18 + PVC)   | StatefulSet `db`, PVC 5 Gi (`k8s/20-postgres.yaml`)             |
| `meilisearch`              | Deployment + PVC (`k8s/30-meilisearch.yaml`)                     |
| `backend` (API + SPA)      | Deployment, 2 replicas, migrate initContainer (`40-backend`)   |
| `worker-parse`/`worker-light` | Deployments, same image, procrastinate args                  |
| `adminer`/`mailpit` (dev)  | not deployed — use `docker compose` locally for that             |
| `prestart.sh` before `up`  | backend initContainer — `deploy.sh` waits on the rollout         |

One image serves API, `/docs` (Swagger UI) and the built SPA, so the
LoadBalancer on port 80 is the whole entry point: `http://<node-IP>/`.
The whole app env (secrets included) is synced from `k8s/k3s.env` into
one Secret, `fastinvoice-env`; `DATABASE_URL` is derived from it (`db`
Service DNS).

## First deploy

```bash
scripts/k3s/setup-env.sh      # writes k8s/k3s.env with generated secrets
$EDITOR k8s/k3s.env           # FRONTEND_HOST (node IP), SMTP_*, LLM_*, REGISTRY
scripts/k3s/deploy.sh         # build + import/push + apply + migrate + wait
scripts/k3s/smoke.sh          # health, openapi, login, load-balancer entry
```

Run the scripts **on the k3s node** (default `kubectl` + `sudo k3s ctr`);
for a remote cluster export `REGISTRY` (images go through a registry, no
`ctr`) and point `KUBECTL` at it, e.g. `KUBECTL="kubectl --context=lab"`.

## Day-2 operations

| task             | command                                                    |
| ---------------- | ---------------------------------------------------------- |
| update to HEAD   | `scripts/k3s/deploy.sh`                                     |
| manifests only   | `scripts/k3s/deploy.sh --skip-build`                        |
| state / node IP  | `scripts/k3s/status.sh`                                     |
| logs             | `scripts/k3s/logs.sh backend -f`                            |
| rollback app     | `scripts/k3s/rollback.sh` (schema does NOT downgrade — see the caveat in the script) |
| backup DB        | `scripts/k3s/backup.sh` (rotate with `KEEP=`, cron it)      |
| restore DB       | `scripts/k3s/restore.sh k8s/backups/<file>.sql.gz`          |
| remove everything| `scripts/k3s/teardown.sh`                                   |

## Notes and limits

- **Image import without a registry**: `docker save | k3s ctr images import`
  — the mechanism documented for air-gap installs
  ([k3s air-gap docs](https://docs.k3s.io/installation/airgap)). Tags are
  git SHAs by default; the manifest keeps `imagePullPolicy: IfNotPresent`
  and the rollout re-triggers because the tag changes every build.
- **PLAIN-TEXT HTTP**: servicelb hands out the node IP, no certificates
  involved. If the app must leave the LAN, put your own reverse proxy in
  front of the backend Service — the manifests deliberately ship none.
- **Swagger in the open**: `/docs` rides the same Service — same exposure
  as the compose deploy. Set `docs_url=None` in `app/main.py` if that
  stops being acceptable.
- **Migrations and concurrency**: every backend replica runs `prestart.sh`
  as an initContainer; Alembic serializes them with a Postgres advisory
  lock, so 2 replicas are safe (initial data is idempotent by design).
- **Sizing**: requests/limits are homelab defaults — bump the 768 Mi
  backend cap before pointing real traffic at it.
- **Playwright**: CI-only service, intentionally not ported.
