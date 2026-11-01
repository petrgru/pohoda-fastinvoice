import type { InvoicePublic } from "@/client"

/** True while any invoice is still waiting for background parsing. */
export const parsingInProgress = (
  invoices: InvoicePublic[] | undefined,
): boolean =>
  (invoices ?? []).some(
    (invoice) => invoice.state === "uploaded" || invoice.state === "parsing",
  )
