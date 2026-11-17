import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link as RouterLink } from "@tanstack/react-router"
import { Eye, RotateCcw } from "lucide-react"
import { useTranslation } from "react-i18next"

import { type InvoicePublic, InvoicesService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import DeleteInvoice from "./DeleteInvoice"

export function InvoiceActionsMenu({
  invoice,
  onSuccess,
}: {
  invoice: InvoicePublic
  onSuccess: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const requeueMutation = useMutation({
    mutationKey: ["requeueExport", invoice.id],
    mutationFn: () =>
      InvoicesService.requeueExportInvoice({
        path: { invoice_id: invoice.id },
      }),
    onSuccess: () => {
      showSuccessToast(t("invoice.requeued"))
      onSuccess()
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] })
      queryClient.invalidateQueries({ queryKey: ["queue"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="size-8 text-muted-foreground"
          data-testid={`invoice-actions-menu-${invoice.id}`}
        >
          <span className="sr-only">{t("common.openActionsMenu")}</span>⋮
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuItem asChild>
          <RouterLink
            to="/invoices/$invoiceId"
            params={{ invoiceId: invoice.id }}
          >
            <Eye className="mr-2 size-4" />
            {t("common.detail")}
          </RouterLink>
        </DropdownMenuItem>
        {invoice.state === "export_failed" && (
          <DropdownMenuItem
            data-testid={`requeue-export-menu-item-${invoice.id}`}
            onSelect={() => requeueMutation.mutate()}
          >
            <RotateCcw className="mr-2 size-4" />
            {t("invoice.requeueMenuItem")}
          </DropdownMenuItem>
        )}
        {invoice.can_delete && <DropdownMenuSeparator />}
        {invoice.can_delete && (
          <DeleteInvoice invoice={invoice} onSuccess={onSuccess} />
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
