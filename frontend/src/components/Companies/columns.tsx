import type { ColumnDef } from "@tanstack/react-table"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import type { CompanyPublic } from "@/client"
import type { DataTableFeatures } from "@/components/Common/DataTable"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteCompany from "./DeleteCompany"
import EditCompany from "./EditCompany"
import { ManageCompany } from "./ManageCompany"

export function useCompanyColumns() {
  const { t } = useTranslation()
  return useMemo<ColumnDef<DataTableFeatures, CompanyPublic>[]>(
    () => [
      {
        accessorKey: "name",
        header: t("companies.colName"),
        cell: ({ row }) => (
          <span className="font-medium">{row.original.name}</span>
        ),
      },
      { accessorKey: "ico", header: t("companies.colIco") },
      {
        accessorKey: "dic",
        header: t("companies.colDic"),
        cell: ({ row }) => row.original.dic ?? "—",
      },
      {
        accessorKey: "address",
        header: t("companies.colAddress"),
        cell: ({ row }) => row.original.address ?? "—",
      },
      {
        accessorKey: "bank_account",
        header: t("companies.colAccount"),
        cell: ({ row }) => {
          const rows = row.original.bank_accounts ?? []
          if (rows.length === 0) {
            return row.original.bank_account ?? "—"
          }
          return (
            <div className="flex flex-col gap-0.5">
              {rows.map((account, index) => (
                <div key={index}>
                  {account.account ?? ""}
                  {account.iban && (
                    <span className="text-xs text-muted-foreground">
                      {account.account ? ` · ${account.iban}` : account.iban}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )
        },
      },
      {
        accessorKey: "is_active",
        header: t("companies.colState"),
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge
              variant="outline"
              className="border-transparent bg-green-500/15 text-green-600 dark:text-green-400"
            >
              {t("companies.active")}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              {t("companies.inactive")}
            </Badge>
          ),
      },
      {
        id: "actions",
        header: () => <span className="sr-only">{t("common.actions")}</span>,
        cell: ({ row }) =>
          row.original.my_role === "admin" ? (
            <div className="flex justify-end">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    className="size-8 text-muted-foreground"
                    data-testid={`company-actions-menu-${row.original.id}`}
                  >
                    <span className="sr-only">
                      {t("common.openActionsMenu")}
                    </span>
                    ⋮
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-40">
                  <ManageCompany company={row.original} />
                  <EditCompany company={row.original} onSuccess={() => {}} />
                  <DeleteCompany company={row.original} onSuccess={() => {}} />
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ) : null,
      },
    ],
    [t],
  )
}
