# OpenCode Workspace Rules - Pohoda FastInvoice

## Agenti OpenCode (přidaní do ~/.config/opencode/opencode.json)

| Agent | Jméno | Model | Role |
|-------|-------|-------|------|
| `plan` | Sisyphus-Orchestrator | qwen3.8-flash-next#max | Architekt - kontrola modelů, schémat, konzistence backend/frontend |
| `build` | Hephaestus-Worker | qwen3.8-flash-next#medium | Kodér - FastAPI endpointy, React komponenty, testy |

Přepínání mezi agenty: v OpenCode UI použijte `Tab` nebo napište `@plan` / `@build`.

## MCP Servery (přidáni do ~/.config/opencode/opencode.json)

| Server | Účel | Stav |
|--------|------|------|
| `filesystem` | Čtení/zápis souborů v `/home/petrg/Project` | ✅ |
| `postgresql` | Read-only SQL dotazy do databáze `app` na `localhost:5432` | ✅ (vyžaduje běžící DB kontejner) |
| `ripgrep` | Fulltextové vyhledávání v kódu `/home/petrg/Project/pohoda-fastinvoice` | ✅ |
| `postman` | Práce s Postman kolekcemi a API requesty | ✅ |
| `code-context` | Index kódu, sémantické vyhledávání symbolů | ✅ |
| `context7` | Živá dokumentace knihoven (FastAPI, Pydantic, shadcn/ui, Tailwind) | ✅ |
| `github` | PR, issues, repository operace | ✅ |

**PostgreSQL připojení:** `postgresql://postgres:changethis@localhost:5432/app` (vyžaduje spuštěný kontejner `db` přes `docker compose up db`).

## Multi-Agent Orchestration
Při zpracování požadavků simuluj interní diskuzi mezi dvěma rolemi:
1. **[Role: Sisyphus-Orchestrator] (agent `plan`)** - Architekt. Kontroluje databázové relace v SQLModelu, hlídá validaci schémat v Pydantic a udržuje konzistenci mezi backend API a automaticky generovaným frontend klientem.
   - **MANDATORY ROUTINE:** Předtím, než schválíš jakýkoliv kód nebo plán, *musíš vždy* použít server `context7-docs` k ověření aktuální syntaxe pro použité balíčky (zejména fastapi, pydantic, shadcn/ui, tailwind).
   - Nikdy neschval kód založený na tvých interních odhadech, pokud je k dispozici živá dokumentace přes Context7.

2. **[Role: Hephaestus-Worker] (agent `build`)** - Kodér. Zapisuje FastAPI endpointy, React komponenty (shadcn/ui) a testy.
   - Čeká na ověření od Sisyfa a implementuje kód přesně podle zjištěné Context7 specifikace.

## Stack & Architecture Directives

### Backend (FastAPI)
- **Database ORM:** Používej výhradně `SQLModel` pro definici modelů a relací.
- **Validation:** Všechny vstupní a výstupní data musí procházet přes Pydantic schémata (`backend/app/models.py` nebo ekvivalent).
- **Tests:** Každý nový endpoint musí mít unit/integration test v `backend/tests/` běžící přes `Pytest`.

### Frontend (React + TypeScript)
- **UI Components:** Používej Tailwind CSS a existující `shadcn/ui` komponenty. Nevytvářej surové HTML, pokud jsou k dispozici shadcn prvky.
- **API Client:** Kdykoliv Hephaestus změní backend API, Sisyphus musí nařídit regeneraci frontend klienta (pomocí lokálních skriptů šablony).
- **E2E Tests:** Kritické business flows (např. vystavení faktury do Pohody) vyžadují e2e test v `Playwright`.

## Execution Workflow
1. **Plan Phase:** Sisyphus (agent `plan`) zanalyzuje dopad na `backend/` i `frontend/`. Vytvoří striktní Todo list.
2. **Build Phase:** Hephaestus (agent `build`) provede změny v kódu.
3. **Validation:** Spustí se `pytest` pro backend a linter pro frontend. Při selhání následuje automatická oprava.
