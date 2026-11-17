import { useQuery } from "@tanstack/react-query"
import { SearchCode } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { InvoicesService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const ENTITY_LABEL: Record<string, string> = {
  seller: "invoice.candidateSeller",
  buyer: "invoice.candidateBuyer",
  unknown: "invoice.candidateUnknown",
}

/** Offers every IČ/DIČ-like value from the document when the parsed one is
 *  missing or malformed – click fills the input. */
export function IdCandidates({
  invoiceId,
  kind,
  onPick,
}: {
  invoiceId: string
  kind: "ico" | "dic"
  onPick: (value: string) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  const { data, isFetching } = useQuery({
    queryKey: ["id-candidates", invoiceId],
    enabled: open,
    staleTime: 60_000,
    queryFn: async () =>
      (
        await InvoicesService.invoiceIdCandidates({
          path: { invoice_id: invoiceId },
        })
      ).data,
  })

  const candidates = data?.[kind] ?? []

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 gap-1 px-2 text-xs text-muted-foreground"
        data-testid={`id-candidates-${kind}-button`}
        onClick={() => setOpen(true)}
      >
        <SearchCode className="size-3.5" />
        {t("invoice.findInDocument")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-h-[80vh] overflow-y-auto sm:max-w-lg"
          data-testid={`id-candidates-${kind}-dialog`}
        >
          <DialogHeader>
            <DialogTitle>{t("invoice.idCandidatesTitle")}</DialogTitle>
            <DialogDescription>
              {t("invoice.idCandidatesHint")}
            </DialogDescription>
          </DialogHeader>
          {isFetching && candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">…</p>
          ) : null}
          {!isFetching && data && candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("invoice.noCandidates")}
            </p>
          ) : null}
          <ul className="flex flex-col gap-1.5">
            {candidates.map((candidate) => (
              <li key={candidate.value}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-0.5 rounded-md border px-3 py-2 text-left text-sm hover:bg-accent"
                  data-testid={`id-candidate-${kind}-${candidate.value}`}
                  onClick={() => {
                    onPick(candidate.value)
                    setOpen(false)
                  }}
                >
                  <span className="flex items-center gap-2 font-medium">
                    {candidate.value}
                    <Badge variant="secondary">
                      {t(ENTITY_LABEL[candidate.entity ?? "unknown"])}
                    </Badge>
                    {candidate.looks_valid ? (
                      <Badge variant="outline">
                        {t("invoice.candidateValid")}
                      </Badge>
                    ) : null}
                  </span>
                  {candidate.context ? (
                    <span className="truncate text-xs text-muted-foreground">
                      {candidate.context}
                    </span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>
    </>
  )
}
