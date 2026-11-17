# ERP Connector API – obousměrná synchronizace pro bridge službu

Router pro **konektorovou službu** běžící u zákazníka (typicky Windows
bridge pro Pohodu, později i Abra Flexi / Money). Na rozdíl od
[External API](external-api.md), kde účetní aplikace reaguje na jednotlivé
faktury, si konektor sám **vyzvedává dávky** a vrací výsledek pro každou
položku. Jména polí jsou obecná (bez pohodových termínů) – ERP si mapování
resolve na své strany.

- Base URL: `https://<host>/api/v1/erp-connector`
- Autentizace: stejný firemní `X-API-Key` jako [Data API](data-api.md) a
  External API – klíč vidí jen doklady a dodavatele **své firmy**.
- Špatný/zrušený klíč → `401`, neaktivní firma → `403`.

## Stavový automat na dokladu

```
ready_for_export ──claim──▶ processing ──synced──▶ exported
       ▲                        │
       │                        └──error──▶ export_failed ──(automaticky ≤ 3×)──▶ processing
       └────────────── (uživatel: znovu zařadit) ◀── export_failed (po 3 pokusech)
```

- `processing` = doklad je **claimnutý** konektorem, právě letí import.
- Každý `error` se počítá jako jeden automatický pokus
  (`export_retry_count`); po **3** pokusech dávka doklad už nenabídne a
  uživatel v UI musí potvrdit znovuzařazení (tlačítko *Znovu zařadit k
  importu*, resetne počet pokusů).
- Claim bez výsledku (konektor spadl uprostřed) uvolní **watchdog** po 10
  minutách – doklad se vrátí do fronty.
- Uživatel/editace uvolní claim také: úprava dokladu nebo odvolání
  schválení ho vrátí do `ready_for_export`; přeřazení jiné firmy ani
  potvrzení importu během `processing` není možné (`409`).
- Mazat doklad ve `processing` **lze** (maže i rozjetý pokus).

## `GET /erp-connector/pending-batch?limit=50`

Vyzvedne další dávku (1–200). Doklady v odpovědi přejdou do `processing`;
`SKIP LOCKED`, takže dva konektory nikdy nevezmou tentýž doklad.

```json
{
  "company_name": "Naše Firma a.s.",
  "company_ico": "27195406",
  "claimed_at": "2026-11-17T10:12:03Z",
  "suppliers": [
    { "id": "…uuid…", "erp_remote_id": null, "name": "Test Supplier s.r.o.",
      "ico": "47123737", "dic": "CZ47123737", "email": null,
      "bank_accounts": [ { "account_number": "1234567/0100", "iban": null, "confirmed": true } ] }
  ],
  "invoices": [
    { "id": "…uuid…", "invoice_number": "2024000123",
      "variable_symbol": "2024000123", "document_type": "invoice",
      "issue_date": "2024-03-01", "delivery_date": "2024-03-01",
      "due_date": "2024-03-15", "kh_date": "2024-03-01",
      "payment_method": "bank_transfer", "currency": "CZK",
      "total_price": "1210.00", "total_vat": "210.00", "total_without_vat": "1000.00",
      "vat_items": [ { "vat_rate": "21%", "base_price": "1000.00",
                       "tax_price": "210.00", "total_price": "1210.00", "currency": "CZK" } ],
      "supplier": { "…": "táž struktura jako suppliers[]" },
      "erp_remote_id": null, "external_number": null, "retry_count": 0,
      "attachment_url": "/api/v1/data/invoices/<id>/file" }
  ]
}
```

- **`suppliers[]` zpracujte před `invoices[]`** – doklad odkazuje na
  dodavatele ve stejném poli.
- `erp_remote_id` je interní ID z ERP (dodavatel/doklad) – při `null`
  doklad nebo dodavatele vytvořte, při vyplněné hodnotě aktualizujte.
- `variable_symbol` na dokladu není – derivuje se z čísla dokladu
  (cifry, posledních 8).
- Originál PDF stáhněte přes `attachment_url` (Data API, tentýž klíč).

## `POST /erp-connector/sync-results`

Výsledek **pro každou** položku dávky – volejte co nevidět po importu
(dokončení dávky je na konektoru).

```json
{ "results": [
  { "entity_type": "supplier", "local_id": "…uuid…", "status": "synced",
    "remote_id": "A00000001" },
  { "entity_type": "invoice", "local_id": "…uuid…", "status": "synced",
    "remote_id": "FA00000042", "remote_number": "260100145" },
  { "entity_type": "invoice", "local_id": "…uuid…", "status": "error",
    "error_message": "Neplatný kód banky" }
] }
```

| `status` | význam | dopad |
|---|---|---|
| `synced` | import proběhl; u faktury **povinné** `remote_number` (číslo vašeho dokladu → `external_number`), u dodavatele `remote_id` | faktura → `exported` (zamčená), opakované `synced` je idempotentní |
| `error` | trvalá chyba importu; `error_message` se uloží a zobrazí v UI (tooltip nad *Chyba exportu*) | faktura → `export_failed`, **+1 pokus** |
| `retry_later` | přechodná chyba (ERP offline…); počítá se jako výsledek, ale **nepočítá** se jako pokus | faktura → `ready_for_export`, znovu nabídne příští dávka |

Odpověď vrací `applied[]` per položka (`ok`, `detail`) – neznámé ID nebo
cizí firma = `ok: false`, ostatní se aplikují.

## `POST /erp-connector/heartbeat`

```json
{ "system": "pohoda", "version": "1.4.2", "mode": "mserver",
  "queue_size": 7, "last_error": null }
```

`204`. Slouží k zelené kontrolce ve správě firmy; každé zavolání
`pending-batch`/`sync-results` počítá jako heartbeat také. Doporučená
frekvence: 60 s. `mode` je `mserver` (trvalá služba) nebo `exe` (jednoráz),
`queue_size` je počet dokladů čekajících na konektorové straně.

## Chování UI (pro kontrolu implementace bridge)

- badge **Odesílání do systému** (fialová) = `processing`;
- badge **Chyba exportu** má tooltip s `error_message` z ERP;
- fronta (`GET /invoices/queue`) ukazuje i `processing` s důvodem
  *Doklad právě odesílá účetní konektor*;
- po 3 chybných pokusech se doklad objeví s tlačítkem *Znovu zařadit k
  importu* (resetne počet pokusů, uživatel nese odpovědnost).

## Typický tok bridge

```
proved GET  /erp-connector/pending-batch        ← claimne dávku (processing)
  pro každého suppliera: vytvořit/aktualizovat v ERP (EXML/AIS)
  pro každou fakturu:     stáhnout PDF přes attachment_url, vytvořit doklad
POST /erp-connector/sync-results                ← výsledky per položka
cyklus každých 30–60 s; mimo pracovní dobu klidně méně
při pádu bridge se neděje nic – watchdog vrátí nedokončené doklady do fronty
```
