# Data API – read-only přístup pro externí aplikace

Aplikace poskytuje externím integracím **jen pro čtení** přehled faktur a ich
příloh. Navazuje na stejné autentizační schéma jako [Export API](export-api.md):

- Base URL: `https://<host>/api/v1/data`
- Authentizace: header `X-API-Key: <klíč>`
  - globální `EXPORT_API_KEY` → vidí **všechny** faktury,
  - firemní API klíč (vytvořený v UI ve správě firmy) → vidí jen faktury
    přiřazené této firmě–příjemci; cizí faktury jsou `404` (aby nedošlo
    k prozrazení jejich existence),
  - bez jakéhokoliv aktivního klíče → `503`, špatný klíč → `401`.
- Žádný endpoint nikam nezapíše – mutace zůstávají výhradně v uživatelském UI.

## Endpointy

### `GET /data/invoices`

Seznam faktur viditelných pro daný klíč, nejnovější první.

| parametr | význam |
|---|---|
| `skip` / `limit` | stránkování (výchozí 0 / 50, max 200) |
| `state` | filtr stavu: `uploaded`, `needs_review`, `ready_for_export`, `exported`, `export_failed` (`400` při neznámé hodnotě) |
| `q` | hledání v čísle faktury, názvu dodavatele a IČ dodavatele |

```json
{
  "count": 132,
  "data": [
    {
      "id": "8a67…",
      "created_at": "2026-10-30T09:00:00Z",
      "state": "exported",
      "invoice_number": "2024000123",
      "document_type": "invoice",
      "supplier_name": "TEST SUPPLIER s.r.o.",
      "supplier_ico": "47123737",
      "supplier_dic": "CZ47123737",
      "recipient_name": "NAŠE FIRMA s.r.o.",
      "recipient_ico": "47123737",
      "recipient_dic": "CZ47123737",
      "recipient_company_id": "b512…",
      "company": { "name": "NAŠE FIRMA s.r.o.", "ico": "47123737", "dic": "CZ47123737", "is_active": true },
      "issue_date": "2024-03-01",
      "due_date": "2024-03-15",
      "total_price": "1210.00",
      "total_vat": "210.00",
      "total_without_vat": "1000.00",
      "currency": "CZK",
      "vat_items": [
        { "vat_rate": "21%", "base_price": "1000.00", "tax_price": "210.00", "total_price": "1210.00" }
      ],
      "original_filename": "faktura-2024-001.pdf",
      "content_type": "application/pdf",
      "file_size": 27431,
      "approved_at": "2026-10-30T10:12:00Z",
      "exported_at": "2026-10-30T10:20:00Z"
    }
  ]
}
```

### `GET /data/invoices/{id}`

Detail jedné faktury – stejné pole jako položka seznamu. Faktura mimo rozsah
klíče nebo neznámé `id` → `404`.

### `GET /data/invoices/{id}/file`

Původní dokument (PDF/obrázek) tak, jak byl uložen – **v jakémkoliv stavu**
narozdíl od exportního frontového endpointu. Chyba skladu → `404`.

## Použití firemního klíče

```bash
curl -H "X-API-Key: fik_…" \
  "https://<host>/api/v1/data/invoices?state=exported&limit=100"
```

Klíč vznikne v *Správě firmy → API klíče*; zobrazí se právě jednou. Zrušení
klíče okamžitě odebere přístup všem datovým i exportním endpointům.
