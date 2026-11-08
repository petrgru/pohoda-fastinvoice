import type { ColumnDef } from "@tanstack/react-table"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import type { SupplierPublic } from "@/client"
import type { DataTableFeatures } from "@/components/Common/DataTable"
import { Badge } from "@/components/ui/badge"
import { ManageSupplier } from "./ManageSupplier"

export function useSupplierColumns() {
  const { t } = useTranslation()
  return useMemo<ColumnDef<DataTableFeatures, SupplierPublic>[]>(
    () => [
      {
        accessorKey: "name",
        header: t("suppliers.colName"),
        cell: ({ row }) => (
          <span className="font-medium">{row.original.name}</span>
        ),
      },
      {
        accessorKey: "ico",
        header: t("suppliers.colIco"),
        cell: ({ row }) => row.original.ico ?? "—",
      },
      {
        accessorKey: "bank_accounts",
        header: t("suppliers.colAccounts"),
        cell: ({ row }) => {
          const rows = row.original.bank_accounts ?? []
          if (rows.length === 0) return "—"
          return (
            <div className="flex flex-col gap-0.5">
              {rows.map((account, index) => (
                <div key={index} className="flex items-center gap-1.5">
                  {account.account ?? account.iban ?? ""}
                  {account.confirmed && (
                    <Badge variant="secondary" className="text-xs">
                      {t("suppliers.confirmed")}
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          )
        },
      },
      {
        accessorKey: "invoice_count",
        header: t("suppliers.colInvoices"),
        cell: ({ row }) => row.original.invoice_count,
      },
      {
        accessorKey: "last_invoice_at",
        header: t("suppliers.colLastInvoice"),
        cell: ({ row }) =>
          row.original.last_invoice_at
            ? new Date(row.original.last_invoice_at).toLocaleDateString()
            : "—",
      },
      {
        id: "actions",
        cell: ({ row }) => <ManageSupplier supplier={row.original} />,
      },
    ],
    [t],
  )
}
