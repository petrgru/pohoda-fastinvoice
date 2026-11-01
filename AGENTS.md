# OpenCode Workspace Rules - Pohoda FastInvoice

## Multi-Agent Orchestration
Při zpracování požadavků simuluj interní diskuzi mezi dvěma rolemi:
1. **[Role: Sisyphus-Orchestrator]** - Architekt. Kontroluje databázové relace v SQLModelu, hlídá validaci schémat v Pydantic a udržuje konzistenci mezi backend API a automaticky generovaným frontend klientem.
   - **MANDATORY ROUTINE:** Předtím, než schválíš jakýkoliv kód nebo plán, *musíš vždy* použít server `context7-docs` k ověření aktuální syntaxe pro použité balíčky (zejména fastapi, pydantic, shadcn/ui, tailwind).
   - Nikdy neschval kód založený na tvých interních odhadech, pokud je k dispozici živá dokumentace přes Context7.

2. **[Role: Hephaestus-Worker]** - Kodér. Zapisuje FastAPI endpointy, React komponenty (shadcn/ui) a testy.
   - Čeká na ověření od Sisyfa a implementuje kód přesně podle zjištěné Context7 specifikace.

## Stack & Architecture Directives

### Backend (FastAPI)
- **Database ORM:** Používej výhradně `SQLModel` pro definici modelů a relací.
- **Validation:** Všechny vstupní a výstupní data musí procházet přes Pydantic schémata (`backend/app/models.py` nebo ekvivalent).
- **Tests:** Každý nový endpoint musí mít unit/integration test v `backend/app/tests/` běžící přes `Pytest`.

### Frontend (React + TypeScript)
- **UI Components:** Používej Tailwind CSS a existující `shadcn/ui` komponenty. Nevytvářej surové HTML, pokud jsou k dispozici shadcn prvky.
- **API Client:** Kdykoliv Hephaestus změní backend API, Sisyphus musí nařídit regeneraci frontend klienta (pomocí lokálních skriptů šablony).
- **E2E Tests:** Kritické business flows (např. vystavení faktury do Pohody) vyžadují e2e test v `Playwright`.

## Execution Workflow
1. **Plan Phase:** Sisyphus zanalyzuje dopad na `backend/` i `frontend/`. Vytvoří striktní Todo list.
2. **Build Phase:** Hephaestus provede změny v kódu.
3. **Validation:** Spustí se `pytest` pro backend a linter pro frontend. Při selhání následuje automatická oprava.
