import { useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link as RouterLink } from "@tanstack/react-router"
import { ListChecks } from "lucide-react"
import { Suspense } from "react"
import { useTranslation } from "react-i18next"

import { InvoicesService } from "@/client"
import { AddCompanyForInvoice } from "@/components/Invoices/AddCompanyForInvoice"
import { parsingInProgress } from "@/components/Invoices/polling"
import { StateBadge } from "@/components/Invoices/StateBadge"
import PendingItems from "@/components/Pending/PendingItems"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { translateServerMessage } from "@/utils"

/** Queue reasons arrive as machine keys ("queue.reason.*[|detail]"). */
export const displayReason = (reason: string): string =>
  translateServerMessage(reason)

function QueueContent() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data } = useSuspenseQuery({
    queryKey: ["queue"],
    queryFn: async () =>
      (await InvoicesService.listQueue({ query: { skip: 0, limit: 100 } }))
        .data,
    // poll continuously so uploads made in other tabs/views surface promptly;
    // faster while any invoice is still being parsed in the background
    refetchInterval: (query) =>
      parsingInProgress(query.state.data?.data) ? 3000 : 5000,
  })

  if (data.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <ListChecks className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">{t("queue.emptyTitle")}</h3>
        <p className="text-muted-foreground">{t("queue.emptyHint")}</p>
      </div>
    )
  }

  return (
    <Table data-testid="queue-table">
      <TableHeader>
        <TableRow>
          <TableHead>{t("queue.colDocument")}</TableHead>
          <TableHead>{t("queue.colSupplier")}</TableHead>
          <TableHead>{t("queue.colState")}</TableHead>
          <TableHead>{t("queue.colWhy")}</TableHead>
          <TableHead className="text-right">{t("queue.colAction")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.data.map((invoice) => (
          <TableRow key={invoice.id} data-testid={`queue-row-${invoice.id}`}>
            <TableCell>
              <RouterLink
                to="/invoices/$invoiceId"
                params={{ invoiceId: invoice.id }}
                className="font-medium underline-offset-4 hover:underline"
              >
                {invoice.invoice_number ?? invoice.original_filename}
              </RouterLink>
            </TableCell>
            <TableCell>
              {invoice.company?.name ?? invoice.supplier_name ?? "—"}
            </TableCell>
            <TableCell>
              <StateBadge state={invoice.state} />
            </TableCell>
            <TableCell className="text-muted-foreground">
              {invoice.queue_reasons?.map(displayReason).join("; ") || "—"}
            </TableCell>
            <TableCell className="text-right">
              {!invoice.company && invoice.can_delete && (
                <AddCompanyForInvoice
                  invoice={invoice}
                  size="sm"
                  disabled={
                    invoice.state === "uploaded" || invoice.state === "parsing"
                  }
                  onSuccess={() => {
                    queryClient.invalidateQueries({ queryKey: ["queue"] })
                    queryClient.invalidateQueries({ queryKey: ["invoices"] })
                    queryClient.invalidateQueries({ queryKey: ["companies"] })
                  }}
                />
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Queue() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          {t("queue.title")}
        </h1>
        <p className="text-muted-foreground">{t("queue.subtitle")}</p>
      </div>
      <Suspense fallback={<PendingItems />}>
        <QueueContent />
      </Suspense>
    </div>
  )
}

export const Route = createFileRoute("/_layout/queue")({
  component: Queue,
  head: () => ({ meta: [{ title: "Fronta úloh – Pohoda FastInvoice" }] }),
})
