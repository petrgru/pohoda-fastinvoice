import { Link as RouterLink } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import type { InvoicePublic } from "@/client"
import type { DataTableFeatures } from "@/components/Common/DataTable"
import { intlLocale } from "@/i18n"
import { InvoiceActionsMenu } from "./InvoiceActionsMenu"
import { StateBadge } from "./StateBadge"

export const formatPrice = (value: string | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : `${Number(value).toLocaleString("cs-CZ", { minimumFractionDigits: 2 })} Kč`

export function useInvoiceColumns() {
  const { t } = useTranslation()
  return useMemo<ColumnDef<DataTableFeatures, InvoicePublic>[]>(
    () => [
      {
        accessorKey: "created_at",
        header: t("invoices.colUploaded"),
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.created_at
              ? new Date(row.original.created_at).toLocaleDateString(
                  intlLocale(),
                )
              : "—"}
          </span>
        ),
      },
      {
        accessorKey: "invoice_number",
        header: t("invoices.colDocument"),
        cell: ({ row }) => (
          <RouterLink
            to="/invoices/$invoiceId"
            params={{ invoiceId: row.original.id }}
            className="font-medium underline-offset-4 hover:underline"
          >
            {row.original.invoice_number ?? row.original.original_filename}
          </RouterLink>
        ),
      },
      {
        accessorKey: "supplier_name",
        header: t("invoices.colSupplier"),
        cell: ({ row }) => (
          <span className="max-w-52 truncate block">
            {row.original.company?.name ?? row.original.supplier_name ?? "—"}
          </span>
        ),
      },
      {
        accessorKey: "total_price",
        header: t("invoices.colTotal"),
        cell: ({ row }) => (
          <span className="tabular-nums">
            {formatPrice(row.original.total_price)}
          </span>
        ),
      },
      {
        accessorKey: "state",
        header: t("invoices.colState"),
        cell: ({ row }) => <StateBadge state={row.original.state} />,
      },
      {
        id: "actions",
        header: () => <span className="sr-only">{t("common.actions")}</span>,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <InvoiceActionsMenu invoice={row.original} onSuccess={() => {}} />
          </div>
        ),
      },
    ],
    [t],
  )
}
