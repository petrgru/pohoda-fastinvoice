// API helpers for invoice e2e tests (run against the served backend).
import type { Page } from "@playwright/test"

import { firstSuperuser, firstSuperuserPassword } from "../config"

const apiBase = process.env.VITE_API_URL ?? "http://localhost:8000"

const login = async (): Promise<string> => {
  const response = await fetch(`${apiBase}/api/v1/login/access-token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      username: firstSuperuser,
      password: firstSuperuserPassword,
    }),
  })
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`)
  }
  const data = (await response.json()) as { access_token: string }
  return data.access_token
}

const authHeaders = async (): Promise<Record<string, string>> => ({
  Authorization: `Bearer ${await login()}`,
})

export const randomIco = (): string => {
  let ico = ""
  for (let i = 0; i < 8; i++) {
    ico += String(Math.floor(Math.random() * 10))
  }
  return ico
}

/** FNV-1a, so the same fixture always synthesizes the same identifiers. */
const hashText = (s: string): number => {
  let h = 0xb794f679
  for (const c of s) {
    h ^= c.charCodeAt(0)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h >>> 0
}

/** A mod-11 clean 8-digit IČO derived from the document text. */
const derivedIco = (s: string): string => {
  for (let round = 0; round < 8; round++) {
    const base = String(1_000_000 + (hashText(`${s}#${round}`) % 9_000_000))
    let total = 0
    const weights = [8, 7, 6, 5, 4, 3, 2]
    for (let i = 0; i < 7; i++) total += Number(base[i]) * weights[i]
    const check = ((-(total % 11) % 11) + 11) % 11
    if (check < 10) return base + String(check)
  }
  return base_fallback(s)
}

const base_fallback = (s: string): string =>
  `9${String(hashText(s) % 10_000_000).padStart(7, "0")}0`

export const createCompany = async (
  name: string,
): Promise<{ id: string; name: string; ico: string }> => {
  const response = await fetch(`${apiBase}/api/v1/companies`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      ico: randomIco(),
      bank_account: "123456789/0100",
    }),
  })
  if (!response.ok) {
    throw new Error(`Company create failed: ${await response.text()}`)
  }
  return await response.json()
}

/** Minimal one-page PDF with text lines (same idea as the backend tests). */
export const makePdfBytes = (lines: string[]): Uint8Array => {
  const content: string[] = ["BT /F1 11 Tf"]
  let y = 750
  for (const line of lines) {
    const escaped = line
      .split("\\")
      .join("\\\\")
      .split("(")
      .join("\\(")
      .split(")")
      .join("\\)")
    content.push(`1 0 0 1 50 ${y} Tm (${escaped}) Tj`)
    y -= 16
  }
  content.push("ET")
  const stream = content.join("\n")
  const streamBytes = new TextEncoder().encode(stream)
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${streamBytes.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ]
  const chunks: number[] = [...new TextEncoder().encode("%PDF-1.4\n")]
  const offsets: number[] = []
  objects.forEach((body, index) => {
    offsets.push(chunks.length)
    chunks.push(
      ...new TextEncoder().encode(`${index + 1} 0 obj\n${body}\nendobj\n`),
    )
  })
  const xrefPosition = chunks.length
  chunks.push(...new TextEncoder().encode(`xref\n0 ${objects.length + 1}\n`))
  chunks.push(...new TextEncoder().encode("0000000000 65535 f \n"))
  for (const offset of offsets) {
    chunks.push(
      ...new TextEncoder().encode(
        `${String(offset).padStart(10, "0")} 00000 n \n`,
      ),
    )
  }
  chunks.push(
    ...new TextEncoder().encode(
      `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefPosition}\n%%EOF\n`,
    ),
  )
  return new Uint8Array(chunks)
}

export const uploadInvoice = async (
  lines: string[],
  options: { supplierIdentity?: boolean } = {},
): Promise<{ id: string }> => {
  const form = new FormData()
  const body = [...lines]
  // the mandatory dates are part of a tax document – synthesize them unless
  // the test already provides its own
  if (!/vystav|vyd[áa]n|issued/i.test(body.join("\n"))) {
    body.push("Datum vystaveni: 1.1.2025")
  }
  if (!/splatn/i.test(body.join("\n"))) {
    body.push("Datum splatnosti: 31.1.2025")
  }
  // The rule extractor (app/services/parsing.py) takes over when a number
  // with a digit, a total and a supplier identity are all present; documents
  // missing them wait for the LLM instead. CI runners cannot reach the LLM
  // cluster, so complete fixtures deterministically from the text - identical
  // lines (duplicate tests) derive identical identifiers and stay duplicates.
  // `supplierIdentity: false` keeps a document anonymous on purpose.
  const withIco = options.supplierIdentity ?? true
  let content = body.join("\n")
  if (withIco && !/i[cč]\s?o\s*[:.]?\s*\d{8}(?!\d)/i.test(content)) {
    const icoLine = `ICO: ${derivedIco(content)}` // ASCII: the PDF text layer
    // drops diacritics and the extractor accepts the unaccented spelling
    // right below the seller's name, so the extractor attributes it to it
    const seller = body.findIndex((l) => /dodavatel|prodavaj/i.test(l))
    if (seller >= 0) body.splice(seller + 1, 0, icoLine)
    else body.push(icoLine)
    content = body.join("\n")
  }
  if (
    !/číslo\s+dokladu|var\.?\s*symbol|faktur(?:a|y)\s+(?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{3,29}/i.test(
      content,
    )
  ) {
    body.push(`Var.symbol: 2${hashText(content) % 1_000_000_000}`)
  }
  const pdf = makePdfBytes([
    ...body,
    `unique-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  ])
  // the Uint8Array copy normalizes the buffer type for BlobPart
  form.append(
    "file",
    new Blob([new Uint8Array(pdf)], { type: "application/pdf" }),
    "faktura-e2e.pdf",
  )
  const response = await fetch(`${apiBase}/api/v1/invoices/upload`, {
    method: "POST",
    headers: await authHeaders(),
    body: form,
  })
  if (!response.ok) {
    throw new Error(`Upload failed: ${await response.text()}`)
  }
  return await response.json()
}

export const getInvoice = async (
  invoiceId: string,
): Promise<{
  id: string
  state: string
  recipient_company_id: string | null
  supplier_bank_account: string | null
  issue_date: string | null
  due_date: string | null
  delivery_date: string | null
}> => {
  const response = await fetch(`${apiBase}/api/v1/invoices/${invoiceId}`, {
    headers: await authHeaders(),
  })
  if (!response.ok) {
    throw new Error(`Invoice get failed: ${await response.text()}`)
  }
  return await response.json()
}

/** Background parsing finishes shortly after upload; wait for a final state.
 *  With a real LLM backend parsing can take tens of seconds. */
export const waitForParsed = async (
  invoiceId: string,
  timeoutMs = 120_000,
): Promise<{ state: string; recipient_company_id: string | null }> => {
  const attempts = Math.ceil(timeoutMs / 500)
  for (let attempt = 0; attempt < attempts; attempt++) {
    const invoice = await getInvoice(invoiceId)
    if (invoice.state !== "uploaded" && invoice.state !== "parsing") {
      return invoice
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Invoice ${invoiceId} still parsing after ${timeoutMs}ms`)
}

export const markInvoiceExported = async (invoiceId: string): Promise<void> => {
  const response = await fetch(`${apiBase}/api/v1/export/result`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": process.env.EXPORT_API_KEY ?? "e2e-export-key",
    },
    body: JSON.stringify({
      invoice_id: invoiceId,
      success: true,
      batch_ref: `e2e-${Date.now()}`,
    }),
  })
  if (!response.ok) {
    throw new Error(`Export result failed: ${await response.text()}`)
  }
}

/** Fill the mandatory dates a real document may not carry in a parseable way. */
export const fillMissingDates = async (invoiceId: string): Promise<void> => {
  const invoice = await getInvoice(invoiceId)
  const body: Record<string, string> = {}
  if (!invoice.issue_date) body.issue_date = "2025-01-02"
  if (!invoice.delivery_date)
    body.delivery_date = invoice.issue_date ?? "2025-01-02"
  if (!invoice.due_date) body.due_date = "2025-01-17"
  if (Object.keys(body).length === 0) return
  const response = await fetch(`${apiBase}/api/v1/invoices/${invoiceId}`, {
    method: "PUT",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`Filling dates failed: ${await response.text()}`)
  }
}

export const tryDeleteInvoice = async (invoiceId: string): Promise<number> => {
  const response = await fetch(`${apiBase}/api/v1/invoices/${invoiceId}`, {
    method: "DELETE",
    headers: await authHeaders(),
  })
  return response.status
}

/** Remove stored invoices with the given filename (content-addressed uploads
 * would otherwise reject a re-upload with 409). */
export const deleteInvoicesByFilename = async (
  filename: string,
): Promise<void> => {
  const headers = await authHeaders()
  const list = await (
    await fetch(`${apiBase}/api/v1/invoices?limit=200`, { headers })
  ).json()
  for (const invoice of list.data ?? []) {
    if (invoice.original_filename === filename) {
      await fetch(`${apiBase}/api/v1/invoices/${invoice.id}`, {
        method: "DELETE",
        headers,
      })
    }
  }
}

export const deleteCompanyByIco = async (ico: string): Promise<void> => {
  const headers = await authHeaders()
  const list = await (
    await fetch(`${apiBase}/api/v1/companies?q=${ico}`, { headers })
  ).json()
  for (const company of list.data ?? []) {
    if (company.ico === ico) {
      await fetch(`${apiBase}/api/v1/companies/${company.id}`, {
        method: "DELETE",
        headers,
      })
    }
  }
}

export const deleteCompaniesByName = async (
  nameIncludes: string,
): Promise<void> => {
  const headers = await authHeaders()
  const list = await (
    await fetch(`${apiBase}/api/v1/companies?limit=500`, { headers })
  ).json()
  for (const company of list.data ?? []) {
    if ((company.name ?? "").includes(nameIncludes)) {
      await fetch(`${apiBase}/api/v1/companies/${company.id}`, {
        method: "DELETE",
        headers,
      })
    }
  }
}

/** Suppliers seen on invoices (registry is deduplicated by IČO). */
export const getSuppliers = async (
  q?: string,
): Promise<
  { id: string; name: string; ico: string | null; invoice_count: number }[]
> => {
  const response = await fetch(
    `${apiBase}/api/v1/suppliers?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`,
    { headers: await authHeaders() },
  )
  if (!response.ok) {
    throw new Error(`Supplier list failed: ${await response.text()}`)
  }
  const body = (await response.json()) as { data: unknown[] }
  return body.data as {
    id: string
    name: string
    ico: string | null
    invoice_count: number
  }[]
}

/** Aborts the eSCL sharing ports so scanner tests don't see a NAPS2 running
 * on the developer machine. Registered before any mock, which then wins. */
export const blockScannerSharing = async (page: Page): Promise<void> => {
  await page.route(
    (url) =>
      url.hostname === "localhost" &&
      Number(url.port) >= 9801 &&
      Number(url.port) <= 9850,
    (route) => route.abort(),
  )
}
