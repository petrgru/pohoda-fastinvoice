# External API – zápis z accounting aplikace

Účetní / ERP aplikace běžící u zákazníka čte faktury přes
read-only [Data API](data-api.md) a přes tento router vrací, co se v jejím
systému stalo. Autentizace je stejný firemní `X-API-Key` jako u ostatních
strojových API – klíč vidí jen faktury své firmy (cizí jsou `404`).
Pro dávkovou synchronizaci bridge službou (claim dávek, heartbeat) viz
[ERP Connector API](erp-connector.md).

- Base URL: `https://<host>/api/v1/external`
- Špatný/zrušený (smazaný) klíč → `401`, neaktivní firma → `403`.

## `PATCH /external/invoices/{id}`

Oprava zpracované faktury **před potvrzením/exportem** – stejná pravidla jako
v uživatelském UI:

```json
{
  "issue_date": "2026-01-02",
  "due_date": "2026-01-17",
  "delivery_date": "2026-01-02",
  "vat_items": [
    { "vat_rate": "21%", "base_price": "1000.00", "tax_price": "210.00",
      "total_price": "1210.00", "currency": "CZK" }
  ]
}
```

- položky DPH a sazby lze upravovat, dokud faktura není importovaná
  (`exported`); po importu je faktura **zamčená** (`403`) a nelze ani smazat,
- chybí-li některé z povinných dat (vystavení / splatnost / zdanitelné
  plnění), odpověď je `422`,
- přiřazení firmy a bankovní účty si spravuje člověk v UI – pole
  `recipient_company_id`, `supplier_bank_account`, `supplier_iban` a
  `supplier_bank_accounts` se z těly ignorují.

## `POST /external/invoices/{id}/status`

```json
{ "state": "exported", "document_number": "2026/1234", "note": null }
```

| `state` | povolené ze | vrací/chování |
|---|---|---|
| `exported` | `ready_for_export`, `export_failed` | vyžaduje `document_number` – **číslo dokladu vašeho systému**, uloží se do `external_number` a je vidět v UI i v Data API; opakování je idempotentní (lze jím i opravit číslo) |
| `export_failed` | `ready_for_export`, `needs_review`, `parsing`-result | faktura zůstává upravitelná – `note` rozepište chybu |
| `ready_for_export` | `export_failed` | zařadí opravenou fakturu znovu do fronty |

Jiný přechod → `409`. Importovaná (`exported`) faktura už nelze vracet
zpět ani značit chybu.

## Typický tok aplikace

```
GET /data/invoices?state=ready_for_export
  → stáhne PDF (Data API příloha) / změří data
  → zaúčtuje do svého systému
  → POST /external/invoices/{id}/status  { state: "exported", document_number: "…" }
nebo
  → POST …/status { state: "export_failed", note: "nesedí DPH" }
  → PATCH …/invoices/{id}  (oprava sazeb/dat)
  → POST …/status { state: "ready_for_export" }
```
