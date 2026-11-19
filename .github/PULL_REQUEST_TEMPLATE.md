## Popis změny

Pokud měníte backend API:
- [ ] Přidat testy v `backend/tests/` (každý endpoint musí mít test)
- [ ] Ověřit přes `context7-docs`, že syntaxe použitých balíčků je aktuální
- [ ] Po změně API spustit `scripts/generate-client.sh` pro regeneraci frontend klienta
- [ ] Přidat alembic migraci (`backend/app/alembic/versions/`), pokud se mění schéma

Pokud měníte frontend:
- [ ] `cd frontend && bun run lint && bun run build`
- [ ] Přidat test / upravit Playwright E2E, pokud jde o kritický business flow

Pokud měníte databázi:
- [ ] Přidat alembic migraci s `upgrade()` i `downgrade()`
- [ ] Ověřit round-trip: `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`

## Kontext

Je-li to feature flag, jak se vypíná (`REGISTRY_ENABLED`, `QUEUE_MODE`, `MEILISEARCH_ENABLED`)?

## Type of change

- [ ] Bug fix (nekompatibilní změna)
- [ ] Nová funkce (nekompatibilní změna)
- [ ] Break change (funkce se chová jinak)
- [ ] Dokumentace
- [ ] Refactor (bez změny chování)
- [ ] CI/CD

## Testování

Jak byly změny otestovány? Které testy?
