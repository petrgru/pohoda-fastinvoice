# Dokumentace Pohoda FastInvoice

## Přehled

FastAPI + SQLModel backend s React (shadcn/ui) frontendem, postavený na
[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
s rozšířeními pro:

- příjem a parsování faktur (PDF, skeny, obrázky) přes lokální LLM
- frontu úloh na Procrastinate (žádný externí broker)
- rejstříkové ověřování dodavatelů (ARES REST + ADIS + VIES) s gate pro export
- ERP konektor pro obousměrnou synchronizaci s Pohodou / Abrou / Money

## Klíčové dokumenty

| Dokument | Co popisuje |
|----------|-------------|
| [development.md](../development.md) | lokální setup, Docker Compose, testy, lint, MCP servery |
| [deployment.md](../deployment.md) | nasazení na server, K8s, compose.deploy.yml |
| [docs/data-api.md](data-api.md) | REST API pro čtení dat (faktury, dodavatelé, statistiky) |
| [docs/erp-connector.md](erp-connector.md) | ERP konektor API (claim/sync-results) |
| [docs/export-api.md](export-api.md) | export faktur do externích systémů |
| [docs/external-api.md](external-api.md) | externí webhooky (prijem faktury) |
| [docs/scanner-naps2.md](scanner-naps2.md) | skenování faktur přes NAPS2 / eSCL |
| [docs/logging-loki.md](logging-loki.md) | strukturované logování, Loki, Grafana |
| [docs/registry.md](registry.md) | rejstříkové ověřování dodavatelů (ARES/ADIS/VIES) |

## Adresáře

| Cesta | Účel |
|-------|------|
| `backend/app/api/routes/` | FastAPI routery (API_V1_STR=`/api/v1`) |
| `backend/app/services/` | business logika (invoices, registry, erp_connector, parsing) |
| `backend/app/models.py` | SQLModel tabulky a Pydantic schémata |
| `backend/app/alembic/versions/` | migrační skripy (round-trip testované) |
| `backend/tests/` | pytest testy (unit + integration) |
| `frontend/src/client/` | automaticky generovaný TypeScript klient |
| `frontend/src/routes/` | React Router route (TanStack Router) |
| `frontend/src/components/` | React komponenty (shadcn/ui) |
| `scripts/` | pomocné skripty pro build a vývoj |

## Multi-agent workflow (OpenCode)

Viz [AGENTS.md](../AGENTS.md) — architekt (Sisyphus/`plan`) a kodér (Hephaestus/`build`)
s povinnou rutinou přes `context7-docs` a MCP servery (postgresql, ripgrep,
filesystem, postman).
