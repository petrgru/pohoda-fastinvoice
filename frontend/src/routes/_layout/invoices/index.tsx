import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { ListChecks, Search } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { InvoicesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { useInvoiceColumns } from "@/components/Invoices/columns"
import { parsingInProgress } from "@/components/Invoices/polling"
import UploadInvoice from "@/components/Invoices/UploadInvoice"
import PendingItems from "@/components/Pending/PendingItems"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const STATE_FILTERS = [
  "all",
  "uploaded",
  "parsing",
  "needs_review",
  "ready_for_export",
  "exported",
  "export_failed",
] as const

function Invoices() {
  const { t } = useTranslation()
  const columns = useInvoiceColumns()
  const [search, setSearch] = useState("")
  const [searchTerm, setSearchTerm] = useState("")
  const [state, setState] = useState<string>("all")

  const { data, isPending } = useQuery({
    queryKey: ["invoices", searchTerm, state],
    queryFn: async () =>
      (
        await InvoicesService.listInvoices({
          query: {
            skip: 0,
            limit: 100,
            q: searchTerm || undefined,
            state: state === "all" ? undefined : state,
          },
        })
      ).data,
    refetchInterval: (query) =>
      parsingInProgress(query.state.data?.data) ? 3000 : false,
    placeholderData: keepPreviousData,
  })

  const invoices = data?.data ?? []

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("invoices.title")}
          </h1>
          <p className="text-muted-foreground">{t("invoices.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <a href="/queue" data-testid="queue-link">
              <ListChecks className="mr-2 size-4" />
              {t("nav.queue")}
            </a>
          </Button>
          <UploadInvoice />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form
          className="relative flex-1 min-w-52"
          onSubmit={(e) => {
            e.preventDefault()
            setSearchTerm(search)
          }}
        >
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            data-testid="invoice-search-input"
            placeholder={t("invoices.searchPlaceholder")}
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </form>
        <Select value={state} onValueChange={setState}>
          <SelectTrigger className="w-48" data-testid="state-filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATE_FILTERS.map((value) => (
              <SelectItem key={value} value={value}>
                {value === "all"
                  ? t("invoices.filterAll")
                  : t(`states.${value}`, value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isPending ? (
        <PendingItems />
      ) : invoices.length === 0 ? (
        <div className="flex flex-col items-center justify-center text-center py-12">
          <div className="rounded-full bg-muted p-4 mb-4">
            <Search className="h-8 w-8 text-muted-foreground" />
          </div>
          <h3 className="text-lg font-semibold">{t("invoices.emptyTitle")}</h3>
          <p className="text-muted-foreground">{t("invoices.emptyHint")}</p>
        </div>
      ) : (
        <DataTable columns={columns} data={invoices} />
      )}
    </div>
  )
}

export const Route = createFileRoute("/_layout/invoices/")({
  component: Invoices,
  head: () => ({
    meta: [{ title: "Faktury – Pohoda FastInvoice" }],
  }),
})
