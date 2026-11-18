# Logování: JSON na stdout → Loki/Alloy

Aplikace sama nepíše soubory ani nerotuje logy. Vše jde strukturovaně na
stdout (`app/core/logging.py`, `configure_logging()`):

- **Formát:** JSON v produkci (`FASTAPI_ENV != "development"`), konzole ve
  vývoji; explicitní override `LOG_JSON=true/false`, úroveň `LOG_LEVEL`
  (výchozí `INFO`).
- **Jeden pipeline:** stdlib loggery (uvicorn, procrastinate, boto3, …) jsou
  zachycené přes `ProcessorFormatter`, takže každá řádka nese timestamp,
  level, logger a korelační ID.
- **Korelace:** `X-Request-ID` (`CorrelationIdMiddleware`) se loguje s každým
  requestem i s `user_id`; při enqueue se `correlation_id` předává workery
  jako `request_id` kwarg tasku, takže upload→parsování je dohledatelné.
- **Access log:** `uvicorn.access` je vypnutý, nahrazuje ho jedna strukturovaná
  řádka z `RequestLogMiddleware` (`method, path, status, duration_ms, …`);
  cesty `/api/v1/utils/health-check/` a `/metrics` se NELOGUJÍ (probes).
- **Redakce:** klíče ze `SENSITIVE_KEYS` (hesla, tokeny, e-maily, …) se
  maskují na `***` v logách i v Sentry (`before_send`).

Souborové handy ani vlastní rotátor v kontejneru nepřidávat – v Kubernetes o
rotaci rozhoduje kubelet a o sběr Alloy (ephemeral storage + evictions).

## Sběr v Kubernetes (Fáze 4 – mimo repozitář)

Záměr nasazení na clusteru (Helm, samostatný úkol):

1. **Alloy** jako DaemonSet (`loki.source.file` na `/var/log/pods/**/*.log`),
   `loki.process` poštuje JSON a odvozuje labels (`detected_labels` →
   namespace/pod/container) a posílá `loki.write` do Loki.
2. **Loki** single-binary chart. Retence 30 dní: `retention_enabled: true`,
   `limits_config.retention_period: 720h` a **povolený compactor**
   (`delete_request_store`), jinak se data nemažou.
3. Alloy `storage.path` na **PVC** – WAL na tmpfs se při restartu pouzdra
   zahodí (ztráta bufferu).
4. **Rotace kubeletu:** nechat default `containerLogMaxSize=10Mi ×
   containerLogMaxFiles=5` (~50 MiB na kontejner). Při našem objemu (INFO,
   provoz faktur) stačí s rezervou; navyšovat (např. 32Mi) jen při naměřených
   burstech/DEBUG.
5. **Alert na tail-lag Alloy** (`alloy_promtail_last_targets_unhealthy`,
   resp. metrika zpoždění čtení) – to je reálný signál ztráty logů, ne velikost
   souborů.

## LogQL tipy pro dashboard

- chyby requestů: `{app="fastinvoice"} | json | level="error"`
- vše kolem jednoho requestu: `{...} | json | correlation_id="<id>"`
- latence: `quantile_over_time(0.95, ...duration_ms...)` (parser `json`, pole
  `duration_ms`)
- redo parsování: `{...} | json | task="parse_invoice" | line_format ...`

## Reference

- https://structlog.org/en/stable/standard-library.html (ProcessorFormatter)
- https://github.com/snok/asgi-correlation-id
- https://grafana.com/docs/alloy/latest/collect/logs-in-kubernetes
- https://grafana.com/docs/loki/latest/send-data/alloy/
- https://grafana.com/docs/loki/latest/send-data/promtail/logrotation (kubelet rotace)
