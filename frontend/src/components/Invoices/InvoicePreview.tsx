import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Maximize2, Minus, Plus, RotateCcw } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

export const useInvoiceFile = (invoiceId: string | undefined) => {
  const { data } = useQuery({
    queryKey: ["invoice-file", invoiceId],
    enabled: Boolean(invoiceId),
    queryFn: async () => {
      const response = await fetch(
        `${import.meta.env.VITE_API_URL ?? ""}/api/v1/invoices/${invoiceId}/file`,
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("access_token") ?? ""}`,
          },
        },
      )
      if (!response.ok) return null
      const blob = await response.blob()
      return URL.createObjectURL(blob)
    },
  })
  return data
}

interface InvoicePreviewProps {
  invoiceId: string
  contentType: string
  filename: string
}

const MIN_SCALE = 0.5
const MAX_SCALE = 4

/** Document preview with a full-size dialog (zoom for images, viewer for PDFs). */
export function InvoicePreview({
  invoiceId,
  contentType,
  filename,
}: InvoicePreviewProps) {
  const { t } = useTranslation()
  const fileUrl = useInvoiceFile(invoiceId)
  const [expanded, setExpanded] = useState(false)
  const [scale, setScale] = useState(1)
  const isPdf = contentType === "application/pdf"

  if (!fileUrl) return null

  const openInNewTab = () => {
    window.open(fileUrl, "_blank", "noopener")
  }
  const zoom = (delta: number) =>
    setScale((current) =>
      Math.min(
        MAX_SCALE,
        Math.max(MIN_SCALE, Number((current + delta).toFixed(2))),
      ),
    )

  const renderDocument = (large: boolean) =>
    isPdf ? (
      <iframe
        title={t("invoice.previewTitle")}
        src={fileUrl}
        className={
          large
            ? "h-full w-full rounded-md border"
            : "h-160 w-full rounded-md border"
        }
      />
    ) : large ? (
      <div className="h-full w-full overflow-auto rounded-md border">
        <img
          src={fileUrl}
          alt={t("invoice.previewTitle")}
          style={{
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        />
      </div>
    ) : (
      <img
        src={fileUrl}
        alt={t("invoice.previewTitle")}
        className="max-h-160 w-full rounded-md border object-contain"
      />
    )

  return (
    <div className="flex flex-col gap-2">
      <div className="group relative">
        {renderDocument(false)}
        <Button
          variant="secondary"
          size="icon"
          data-testid="expand-preview-button"
          className="absolute top-2 right-2 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
          onClick={() => {
            setScale(1)
            setExpanded(true)
          }}
        >
          <Maximize2 className="size-4" />
          <span className="sr-only">{t("invoice.zoom")}</span>
        </Button>
      </div>

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          data-testid="preview-dialog"
          className="flex h-[90vh] w-[92vw] max-w-5xl flex-col gap-3"
        >
          <DialogHeader className="flex-row items-center justify-between space-y-0">
            <DialogTitle className="truncate pr-4 text-base">
              {filename}
            </DialogTitle>
            <div className="flex items-center gap-1">
              {!isPdf && (
                <>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={t("invoice.zoomOut")}
                    disabled={scale <= MIN_SCALE}
                    onClick={() => zoom(-0.5)}
                  >
                    <Minus className="size-4" />
                  </Button>
                  <span className="w-14 text-center text-sm tabular-nums">
                    {Math.round(scale * 100)} %
                  </span>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={t("invoice.zoomIn")}
                    disabled={scale >= MAX_SCALE}
                    onClick={() => zoom(0.5)}
                  >
                    <Plus className="size-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={t("invoice.resetZoom")}
                    onClick={() => setScale(1)}
                  >
                    <RotateCcw className="size-4" />
                  </Button>
                </>
              )}
              <Button
                variant="outline"
                size="icon"
                aria-label={t("invoice.openNewTab")}
                onClick={openInNewTab}
              >
                <ExternalLink className="size-4" />
              </Button>
            </div>
          </DialogHeader>
          <div className="min-h-0 flex-1">{renderDocument(true)}</div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
