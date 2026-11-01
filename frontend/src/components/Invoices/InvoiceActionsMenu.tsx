import { Link as RouterLink } from "@tanstack/react-router"
import { Eye } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { InvoicePublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteInvoice from "./DeleteInvoice"

export function InvoiceActionsMenu({
  invoice,
  onSuccess,
}: {
  invoice: InvoicePublic
  onSuccess: () => void
}) {
  const { t } = useTranslation()
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
        {invoice.can_delete && (
          <DeleteInvoice invoice={invoice} onSuccess={onSuccess} />
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
