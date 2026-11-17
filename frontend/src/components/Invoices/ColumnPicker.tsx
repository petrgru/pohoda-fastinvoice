import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Columns3 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { UsersService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

/** Which invoice columns the user sees – stored in their profile. */
export const INVOICE_COLUMNS: Array<{ id: string; labelKey: string }> = [
  { id: "created_at", labelKey: "invoices.colUploaded" },
  { id: "uploaded_by", labelKey: "invoices.colUploadedBy" },
  { id: "approved_by", labelKey: "invoices.colApprovedBy" },
  { id: "invoice_number", labelKey: "invoices.colDocument" },
  { id: "supplier_name", labelKey: "invoices.colSupplier" },
  { id: "total_price", labelKey: "invoices.colTotal" },
  { id: "issue_date", labelKey: "invoice.issueDate" },
  { id: "due_date", labelKey: "invoice.dueDate" },
  { id: "delivery_date", labelKey: "invoice.taxDate" },
  { id: "kh_date", labelKey: "invoice.khDate" },
  { id: "total_vat", labelKey: "invoice.vat" },
  { id: "vat_rates", labelKey: "invoice.vatRate" },
  { id: "state", labelKey: "invoices.colState" },
]

export function ColumnPicker() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const selected = user?.invoice_columns
  const isVisible = (id: string) =>
    selected === null || selected === undefined ? true : selected.includes(id)

  const mutation = useMutation({
    mutationFn: (columns: string[] | null) =>
      UsersService.updateUserMe({ body: { invoice_columns: columns } }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["currentUser"] }),
    onError: handleError.bind(showErrorToast),
  })

  const toggle = (id: string) => {
    const visible = INVOICE_COLUMNS.map((column) => column.id).filter(
      (columnId) =>
        columnId === id ? !isVisible(columnId) : isVisible(columnId),
    )
    // saving all columns explicitly is the same as no preference at all
    const next = visible.length === INVOICE_COLUMNS.length ? null : visible
    mutation.mutate(next)
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          data-testid="column-picker"
          aria-label={t("invoices.columns")}
        >
          <Columns3 className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel>{t("invoices.columns")}</DropdownMenuLabel>
        {INVOICE_COLUMNS.map((column) => (
          <DropdownMenuCheckboxItem
            key={column.id}
            checked={isVisible(column.id)}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={() => toggle(column.id)}
            data-testid={`column-toggle-${column.id}`}
          >
            {t(column.labelKey)}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
