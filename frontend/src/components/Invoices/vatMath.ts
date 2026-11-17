/**
 * Linked VAT line maths – the user may edit any of the four fields and the
 * rest of the line recalculates: rate · base without VAT · VAT · total.
 */

export type VatRowInput = {
  vat_rate: string
  base_price: string
  tax_price: string
  total_price: string
  currency: string
}

export type VatField = "vat_rate" | "base_price" | "tax_price" | "total_price"

const num = (value: string): number | null => {
  const cleaned = (value || "").replace(/\s/g, "").replace(",", ".")
  if (!cleaned) return null
  const parsed = Number(cleaned)
  return Number.isFinite(parsed) ? parsed : null
}

const money = (value: number): string =>
  (Math.round(value * 100) / 100).toFixed(2)

/** "21%", "21 %", "21" → 21; "0%", "MPO" → 0; free text → null */
export function ratePercent(
  vat_rate: string | null | undefined,
): number | null {
  const cleaned = (vat_rate || "").replace(/\s/g, "").replace(",", ".")
  if (!cleaned) return null
  if (/^(mpo|reverse charge|0 %?)$/i.test(cleaned)) return 0
  const parsed = Number(cleaned.replace("%", ""))
  return Number.isFinite(parsed) ? parsed : null
}

export function recalcVatRow(row: VatRowInput, changed: VatField): VatRowInput {
  const next = { ...row }
  if (changed === "base_price") {
    const base = num(row.base_price)
    const rate = ratePercent(row.vat_rate)
    if (base !== null && rate !== null) {
      next.tax_price = money((base * rate) / 100)
      next.total_price = money(base + Number(next.tax_price))
    }
  } else if (changed === "tax_price") {
    const tax = num(row.tax_price)
    const rate = ratePercent(row.vat_rate)
    if (tax !== null && rate !== null) {
      next.base_price =
        rate > 0 ? money((tax * 100) / rate) : (row.base_price ?? "0.00")
      next.total_price = money(Number(next.base_price || 0) + tax)
    }
  } else if (changed === "total_price") {
    const total = num(row.total_price)
    const rate = ratePercent(row.vat_rate)
    if (total !== null && rate !== null) {
      next.base_price = money(total / (1 + rate / 100))
      next.tax_price = money(total - Number(next.base_price))
    }
  } else if (changed === "vat_rate") {
    const rate = ratePercent(row.vat_rate)
    const known = num(row.base_price) ?? num(row.total_price)
    if (rate !== null && known !== null) {
      if (num(row.base_price) !== null) {
        next.tax_price = money((known * rate) / 100)
        next.total_price = money(known + Number(next.tax_price))
      } else {
        // no base yet – derive the base from the total so the line stays
        // consistent: total = base × (1 + rate/100)
        next.base_price = money(known / (1 + rate / 100))
        next.tax_price = money(known - Number(next.base_price))
      }
    }
  }
  return next
}
