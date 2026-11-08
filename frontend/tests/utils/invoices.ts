// API helpers for invoice e2e tests (run against the served backend).
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
): Promise<{ id: string }> => {
  const form = new FormData()
  const pdf = makePdfBytes([
    ...lines,
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
  timeoutMs = 60_000,
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
