import { Link as RouterLink } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import type { InvoicePublic, UserBasic } from "@/client"
import type { DataTableFeatures } from "@/components/Common/DataTable"
import { intlLocale } from "@/i18n"
import { AddCompanyForInvoice } from "./AddCompanyForInvoice"
import { InvoiceActionsMenu } from "./InvoiceActionsMenu"
import { StateBadge } from "./StateBadge"

export const formatDateOnly = (value: string | null | undefined) => {
  if (!value) return "—"
  const [year, month, day] = value.split("-")
  return day ? `${Number(day)}. ${Number(month)}. ${year}` : value
}

export const formatPrice = (value: string | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : `${Number(value).toLocaleString("cs-CZ", { minimumFractionDigits: 2 })} Kč`

function PersonName({ actor }: { actor?: UserBasic | null }) {
  const name = actor?.full_name || actor?.email
  return (
    <span
      className="truncate text-muted-foreground"
      title={actor?.email ?? undefined}
    >
      {name || "—"}
    </span>
  )
}

export function useInvoiceColumns() {
  const { t } = useTranslation()
  return useMemo<ColumnDef<DataTableFeatures, InvoicePublic>[]>(
    () => [
      {
        accessorKey: "created_at",
        header: t("invoices.colUploaded"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {row.original.created_at
              ? new Date(row.original.created_at).toLocaleString(intlLocale(), {
                  dateStyle: "short",
                  timeStyle: "short",
                })
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
          <div className="flex items-center gap-1">
            <span className="max-w-52 truncate block">
              {row.original.company?.name ?? row.original.supplier_name ?? "—"}
            </span>
            {!row.original.recipient_company_id && (
              <AddCompanyForInvoice
                invoice={row.original}
                size="sm"
                variant="ghost"
              />
            )}
          </div>
        ),
      },
      {
        accessorKey: "uploaded_by",
        header: t("invoices.colUploadedBy"),
        cell: ({ row }) => <PersonName actor={row.original.uploaded_by} />,
      },
      {
        accessorKey: "approved_by",
        header: t("invoices.colApprovedBy"),
        cell: ({ row }) => <PersonName actor={row.original.approved_by} />,
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
        accessorKey: "issue_date",
        header: t("invoice.issueDate"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {formatDateOnly(row.original.issue_date)}
          </span>
        ),
      },
      {
        accessorKey: "due_date",
        header: t("invoice.dueDate"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {formatDateOnly(row.original.due_date)}
          </span>
        ),
      },
      {
        accessorKey: "delivery_date",
        header: t("invoice.taxDate"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {formatDateOnly(row.original.delivery_date)}
          </span>
        ),
      },
      {
        accessorKey: "kh_date",
        header: t("invoice.khDate"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {formatDateOnly(row.original.kh_date)}
          </span>
        ),
      },
      {
        accessorKey: "total_vat",
        header: t("invoice.vat"),
        cell: ({ row }) => (
          <span className="tabular-nums">
            {formatPrice(row.original.total_vat)}
          </span>
        ),
      },
      {
        // one rate per line – stacked when the document carries several
        accessorKey: "vat_rates",
        header: t("invoice.vatRate"),
        cell: ({ row }) => {
          const items = row.original.vat_items
          if (!items || items.length === 0) {
            return <span className="text-muted-foreground">—</span>
          }
          return (
            <div className="flex flex-col">
              {items.map((item, index) => (
                <span
                  key={`${item.vat_rate ?? ""}-${index}`}
                  className="tabular-nums"
                >
                  {item.vat_rate ?? "—"}
                  {item.currency && item.currency !== row.original.currency
                    ? ` · ${item.currency}`
                    : ""}
                </span>
              ))}
            </div>
          )
        },
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
