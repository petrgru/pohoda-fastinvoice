import { PDFDocument } from "pdf-lib"

import { trimJpegPages } from "./autotrim"

// NAPS2 shares each scanner profile as an eSCL device on its own HTTP port
// (9801-9850 on the machine running the NAPS2 desktop app).
const PORT_START = 9801
const PORT_END = 9850

export interface ScannerProfile {
  name: string
  port: number
}

// The scan settings element must be in the eSCL namespace. The specification
// writes the date with hyphens, but NAPS2 has always used slashes and matches
// the namespace exactly (EsclXmlHelper.ScanNs), so it rejects spec-conforming
// settings with 400 (cyanfish/naps2#975). Both spellings are tried so that
// scanning works with any NAPS2 version. The shape of the settings follows
// what NAPS2's own EsclClient sends - pwg:MustHonor must be left out, see
// cyanfish/naps2#478.
const SCAN_NAMESPACE_DATES = ["2011-05-03", "2011/05/03"]

function scanSettings(date: string): string {
  // the scan region is expressed in thirtieths of an inch, not in pixels, so
  // A4 is 2480x3508 there (210x297 mm) - at the 150 dpi below that becomes
  // an 1240x1754 pixel image
  const namespace = `http://schemas.hp.com/imaging/escl/${date}`
  return `<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" xmlns:scan="${namespace}">
  <pwg:Version>2.6</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>3508</pwg:Height>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:Width>2480</pwg:Width>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <scan:ColorMode>BlackAndWhite1</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>`
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit = {},
  timeoutMs = 500,
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

/** Lists the shared NAPS2 profiles reachable on localhost. */
export async function discoverScanners(): Promise<ScannerProfile[]> {
  const ports: number[] = []
  for (let port = PORT_START; port <= PORT_END; port++) ports.push(port)
  const results = await Promise.allSettled(
    ports.map(async (port): Promise<ScannerProfile> => {
      const response = await fetchWithTimeout(
        `http://localhost:${port}/eSCL/ScannerCapabilities`,
      )
      if (!response.ok) throw new Error(`status ${response.status}`)
      const doc = new DOMParser().parseFromString(
        await response.text(),
        "application/xml",
      )
      const name =
        doc.getElementsByTagNameNS("*", "MakeAndModel")[0]?.textContent ||
        `Scanner (${port})`
      return { name, port }
    }),
  )
  return results.flatMap((result) =>
    result.status === "fulfilled" ? [result.value] : [],
  )
}

export interface ScanResult {
  file: File
  /** the first page as a JPEG, for the preview before the upload */
  preview: Blob
}

/**
 * Scans through the shared profile and returns all pages as one PDF file.
 * With `autoTrim` the white margins are trimmed first, keeping about 5 mm
 * around the content.
 */
export async function scanWithProfile(
  port: number,
  options: { autoTrim?: boolean } = {},
): Promise<ScanResult> {
  const base = `http://localhost:${port}/eSCL`
  // a 400 means the namespace spelling was not accepted - try the other one
  let created: Response | undefined
  for (const date of SCAN_NAMESPACE_DATES) {
    created = await fetch(`${base}/ScanJobs`, {
      method: "POST",
      headers: { "Content-Type": "text/xml" },
      body: scanSettings(date),
    })
    if (created.status !== 400) break
  }
  if (!created) throw new Error("ScanJobs did not respond")
  if (!created.ok) throw new Error(`ScanJobs failed with ${created.status}`)
  const location = created.headers.get("Location")
  if (!location) throw new Error("ScanJobs returned no Location")
  const jobUrl = new URL(location, `http://localhost:${port}`).href

  // every NextDocument returns the next page; 503 means "still scanning",
  // 404 ends the job
  const pages: Uint8Array[] = []
  for (let attempt = 0; attempt < 600; attempt++) {
    const response = await fetchWithTimeout(
      `${jobUrl}/NextDocument`,
      {},
      30_000,
    )
    if (response.status === 404) break
    if (response.status === 503) {
      await new Promise((resolve) => setTimeout(resolve, 2_000))
      continue
    }
    if (!response.ok) {
      const details = await fetchWithTimeout(
        `${jobUrl}/ErrorDetails`,
        {},
        2_000,
      ).catch(() => null)
      const reason = details
        ? await details.text().catch(() => "")
        : String(response.status)
      throw new Error(`Scan failed: ${reason.slice(0, 200)}`)
    }
    pages.push(new Uint8Array(await response.arrayBuffer()))
  }
  if (pages.length === 0) throw new Error("The scanner returned no pages")
  const trimmed = options.autoTrim ? await trimJpegPages(pages) : pages

  const pdf = await PDFDocument.create()
  for (const bytes of trimmed) {
    const image = await pdf.embedJpg(bytes)
    const page = pdf.addPage([image.width, image.height])
    page.drawImage(image, {
      x: 0,
      y: 0,
      width: image.width,
      height: image.height,
    })
  }
  const data = await pdf.save()
  return {
    file: new File([data as BlobPart], `sken-${Date.now()}.pdf`, {
      type: "application/pdf",
    }),
    preview: new Blob([trimmed[0] as BlobPart], { type: "image/jpeg" }),
  }
}
