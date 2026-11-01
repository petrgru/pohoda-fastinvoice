# Export API – kontrakt pro Pohoda konector

Tato aplikace (řídící UI + DB) poskytuje druhému projektu (aplikace běžící u
serveru s MS SQL / Pohoda klientem) REST API pro **dávkový export schválených
faktur** a **synchronizaci partnerů**.

- Base URL: `https://<host>/api/v1/export`
- Authentizace: header `X-API-Key: <klíč>`. Kromě globálního `EXPORT_API_KEY`
  (přístup ke všem firmám, nutný pro `PUT /companies/sync`) může správce firmy
  vytvořit v UI **klíč na míru firmě** – ten vidí queue, soubory i výsledky
  pouze této firmy. Bez globálního klíče **a** bez aktivních firemních klíčů
  jsou všechny endpointy `503`.
- Formát: JSON, identifikátory UUID, částky jako string (`"1210.00"`).

## Tok exportu

```
[UI] upload → parse → přiřazení firmy → potvrzení uživatelem
                              ↓ READY_FOR_EXPORT
[Konektor] GET /queue → stáhne PDF → import do Pohody → POST /result
                              ↓ EXPORTED (zamčeno, nelze smazat)
```

Faktura se do fronty **nedostane**, pokud nemá přiřazenou existující firmu
nebo ji uživatel nepotvrdil. Takové zůstávají v frontě úloh v této aplikaci.

## Endpointy

### `GET /export/queue`

Vrací schválené faktury připravené k importu (bez duplicit, idempotentní).

```json
{
  "count": 2,
  "data": [
    {
      "id": "8a67…",
      "state": "ready_for_export",
      "original_filename": "faktura-2024-001.pdf",
      "content_type": "application/pdf",
      "export_url": "/api/v1/export/invoices/8a67…/file",
      "company": { "name": "…", "ico": "47123737", "dic": "CZ47123737", "bank_account": "123456789/0100", "is_active": true },
      "invoice_number": "2024000123",
      "document_type": "invoice",
      "issue_date": "2024-03-01",
      "due_date": "2024-03-15",
      "total_price": "1210.00",
      "total_vat": "210.00",
      "total_without_vat": "1000.00",
      "currency": "CZK",
      "vat_items": [
        { "vat_rate": "21%", "base_price": "1000.00", "tax_price": "210.00", "total_price": "1210.00" }
      ],
      "approved_at": "2026-10-30T10:12:00Z"
    }
  ]
}
```

### `GET /export/invoices/{id}/file`

Binární příloha (PDF/obrázek) pro importní dialog Pohody. `409`, pokud faktura
mezitím pozbyla nárok na export.

### `POST /export/result`

```json
{ "invoice_id": "8a67…", "success": true, "batch_ref": "d-2026-0042" }
```

- `success: true` → faktura → `EXPORTED` (uložena do systému, **uzamčena
  vůči mazání i úpravám**), `batch_ref` je volitelný identifikátor dávky.
- `success: false` + `error` → faktura → `EXPORT_FAILED`, vrátí se do fronty
  úloh s důvodem; uživatel ji může opravit a znovu schválit.
- Opakované hlášení úspěchu je idempotentní (`200`).
- Pokus o úspěch u faktury bez firmy/schválení → `409`.

### `PUT /export/companies/sync`

Hromadný upsert partnerů z Pohody (klíč = IČO):

```json
{ "companies": [ { "name": "…", "ico": "47123737", "dic": "CZ47123737", "bank_account": "123456789/0100", "note": null, "is_active": true } ] }
```

- validní IČO (8 číslic), DIČ (CZ prefix + mod-11), číslo účtu (mod-11)
  – jinak `422` (celý payload je atomický).
- nové firmy se vytvoří se `source=pohoda_sync`, existující se aktualizují.

## Doporučené chování konektoru

1. `GET /queue` po nabootování / každou N minuty.
2. Pro každou fakturu: stáhnout soubor, provést import do Pohody
   (kontrola duplicate IČ/DIČ na straně Pohody je na konektoru).
3. `POST /result` pro každou fakturu zvlášť (nebo celou dávku smyčkou).
4. Při selhání aplikace/Pohody fakturu označit `success: false` – uživatel
   ji uvidí ve frontě úloh s důvodem a může proces zopakovat.
