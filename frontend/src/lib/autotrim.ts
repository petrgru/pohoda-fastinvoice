// Automatic trimming of white scan margins. The sizes assume the scanner
// output is A4 at 150 dpi (what the scan settings request), so 5 mm of paper
// are 5 / 25.4 * 150 dots.
const MARGIN_MM = 5
export const TRIM_MARGIN_PX = Math.round((MARGIN_MM / 25.4) * 150)

export interface ContentRect {
  x: number
  y: number
  width: number
  height: number
}

/**
 * Bounding box of the dark (non-white) content across all frames, grown by a
 * 5 mm margin and clamped to the image. One rect is computed for all frames
 * so that multi-page documents keep a uniform page size. Returns null when
 * there is nothing worth cropping: the frames are blank, mostly dark, or the
 * margin already covers the whole image (e.g. small test frames).
 */
export function findContentRect(
  images: ImageData[],
  marginPx: number = TRIM_MARGIN_PX,
): ContentRect | null {
  if (images.length === 0) return null
  const { width, height } = images[0]
  if (images.some((image) => image.width !== width || image.height !== height))
    return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -1
  let maxY = -1
  for (const image of images) {
    const data = image.data
    for (let y = 0; y < image.height; y++) {
      const row = y * image.width * 4
      for (let x = 0; x < image.width; x++) {
        const i = row + x * 4
        const gray = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114
        if (gray < 128) {
          if (x < minX) minX = x
          if (x > maxX) maxX = x
          if (y < minY) minY = y
          if (y > maxY) maxY = y
        }
      }
    }
  }
  if (maxX < 0) return null // completely blank
  const x = Math.max(0, minX - marginPx)
  const y = Math.max(0, minY - marginPx)
  const right = Math.min(width, maxX + 1 + marginPx)
  const bottom = Math.min(height, maxY + 1 + marginPx)
  if (x === 0 && y === 0 && right === width && bottom === height) return null
  return { x, y, width: right - x, height: bottom - y }
}

/**
 * Re-encodes JPEG pages with the white margins trimmed (the same crop rect
 * is used for every page). Returns the original bytes when the pages cannot
 * be decoded or trimming would not change anything.
 */
export async function trimJpegPages(
  pages: Uint8Array[],
  marginPx: number = TRIM_MARGIN_PX,
): Promise<Uint8Array[]> {
  const images: ImageData[] = []
  try {
    for (const bytes of pages) {
      const bitmap = await createImageBitmap(
        new Blob([bytes as BlobPart], { type: "image/jpeg" }),
      )
      try {
        const canvas = document.createElement("canvas")
        canvas.width = bitmap.width
        canvas.height = bitmap.height
        const context = canvas.getContext("2d")
        if (!context) return pages
        context.drawImage(bitmap, 0, 0)
        images.push(context.getImageData(0, 0, canvas.width, canvas.height))
      } finally {
        bitmap.close()
      }
    }
  } catch {
    return pages // undecodable pages - upload them as they are
  }
  const rect = findContentRect(images, marginPx)
  if (!rect) return pages
  const trimmed: Uint8Array[] = []
  for (const image of images) {
    const canvas = document.createElement("canvas")
    canvas.width = rect.width
    canvas.height = rect.height
    const context = canvas.getContext("2d")
    if (!context) return pages
    // white base where the crop reaches outside the copied pixels
    context.fillStyle = "#ffffff"
    context.fillRect(0, 0, rect.width, rect.height)
    context.putImageData(image, -rect.x, -rect.y)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.92),
    )
    if (!blob) return pages
    trimmed.push(new Uint8Array(await blob.arrayBuffer()))
  }
  return trimmed
}
