# Rejstříkové ověřování dodavatelů (ARES / ADIS / VIES)

Každý dodavatel s identifikátorem (IČO / DIČ) je ověřován proti oficiálním
rejstříkům. Cílem je nenechat exportovat fakturu do ERP za subjekt, který
v rejstříku neexistuje, je v úpadku nebo je spolehlivostí na seznamu
nespolehlivých plátců.

## Zdroje

| Zdroj | Protokol | Účel |
|-------|----------|------|
| ARES | REST / JSON, bez klíče | primární: existence, název, VAT flag, příznak úpadku (ČR) |
| ADIS (MOJE daně) | SOAP, batch do 100 DIČ | nespolehlivý plátce, zveřejněné bankovní účty |
| VIES | SOAP (oficiální EC) | platnost DIČ pro přeshraniční EU plátce |

Parser je záměrně tolerantní – pole se hledají podle názvových vzorů, ne
podle ztvrdlého stromu, aby revize schématu rejstříku nepřepsala všechna
vyjádření na „nenalezeno".

## Životní cyklus stavu

Stav je sloupec `supplier.registry_status`
(`SupplierRegistryStatus` v `app/models.py`):

| Stav | Význam | Gate exportu? |
|------|--------|---------------|
| `pending` | čeká na ověření / rejstřík nedostupný | **ano** |
| `verified` | rejstřík subjekt potvrdil | ne (úpadek viz níže) |
| `not_found` | rejstřík odpověděl, subjekt neexistuje | **ano** |
| `ico_invalid` | IČO neprošlo kontrolním součtem | ne (data-quality warning) |
| `not_checkable` | žádný identifikátor | ne |

Rejstřík, který je nedostupný / rate-limited / degradovaný, **není selhání** –
dodavatel zůstává `pending` a opakuje se po backoff schodu
(`REGISTRY_BACKOFF_STEPS`, výchozí 10 m → 1 h → 6 h, pak 24 h).
`not_found` se re-probuje po `REGISTRY_NOT_FOUND_RETRY_HOURS` (výchozí 24 h), protože rejstřík může
odpovídat 404 i při výpadku.

## Gate exportu

`claim_export_batch` (`app/services/invoices.py`) nevydá k exportu fakturu,
jejíž dodavatel je `pending` nebo `not_found`, anebo je v úpadku – dokud
rejstřík nepotvrdí nebo admin nezasáhne. Důvod bloku vidí UI přes
`queue_reasons` / `export_block_reason` (chip na detailu faktury,
`data-testid="queue-reasons-alert"`).

Ruční uvolnění: `POST /api/v1/suppliers/{id}/confirm-export` nastaví
`export_confirmed_manually=true` (tlačítko ve dialogu dodavatele). I terminalní
stavy se periodicky re-checkují, aby pozdější úpadek neunikl.

## Fronta

Veškerá volání běží ve workeru (fronta Procrastinate, queue `maintenance`);
API nikdy neblokuje na rejstříku:

- `check_supplier_registry` – ověření jednoho dodavatele (enqueue při
  vytvoření dodavatele i ručním re-checku)
- `registry_checks` – periodický sběr dodavatelů s `registry_next_check_at <= now`
- `adis_reliability` – denní batch přes ADIS pro DIČ

Ruční re-check: `POST /api/v1/suppliers/{id}/registry-recheck`.

## Konfigurace (`app/core/config.py`)

| Proměnná | Výchozí | Popis |
|----------|---------|-------|
| `REGISTRY_ENABLED` | `true` | hlavní vypínač (testy používají `false`) |
| `ARES_REST_URL` | mfcr.cz REST | adresa ARES |
| `ADIS_WSDL_URL` | adisapi.mfcr.cz | WSDL ADIS |
| `VIES_SOAP_URL` | ec.europa.eu | SOAP VIES |
| `REGISTRY_TIMEOUT_SECONDS` | `8.0` | HTTP timeout jednoho volání |
| `REGISTRY_MIN_INTERVAL_SECONDS` | `0.25` | slušný odstup (ARES: 500 req/min/IP) |
| `REGISTRY_BACKOFF_STEPS` | `[600, 3600, 21600]` | schod opakování při nedostupnosti (sekundy) |
| `REGISTRY_NOT_FOUND_RETRY_HOURS` | `24` | re-probace stavu `not_found` |
| `INSOLVENCY_RECHECK_DAYS` | `7` | perioda opakování u `verified` |

## Testování a kalibrace

- Unit/integration: `backend/tests/services/test_registry.py`,
  `tests/api/routes/test_suppliers.py` (respisy ARES/ADIS/VIES, gate chování).
- E2E: `frontend/tests/suppliers.spec.ts` (badge v seznamu),
  `frontend/tests/invoices.spec.ts` (full flow s `queue-reasons-alert`).
- Smoke proti reálnému clusteru: `backend/scripts/check_registries.py`.
