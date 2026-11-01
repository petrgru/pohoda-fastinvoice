import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { InvoicesService } from "@/client"
import { formatPrice } from "@/components/Invoices/columns"
import { StateBadge } from "@/components/Invoices/StateBadge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [
      {
        title: "Pohoda FastInvoice",
      },
    ],
  }),
})

const STATES = [
  "uploaded",
  "parsing",
  "needs_review",
  "ready_for_export",
  "exported",
  "export_failed",
] as const

const STATE_COLORS: Record<(typeof STATES)[number], string> = {
  uploaded: "bg-slate-400",
  parsing: "bg-sky-400",
  needs_review: "bg-amber-400",
  ready_for_export: "bg-violet-400",
  exported: "bg-emerald-500",
  export_failed: "bg-red-500",
}

type Granularity = "day" | "week" | "month"

function periodLabel(period: string, granularity: Granularity) {
  if (granularity === "day") return period.slice(8, 10)
  if (granularity === "month") return period.slice(5, 7)
  return period.slice(-2)
}

function Dashboard() {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()
  const [granularity, setGranularity] = useState<Granularity>("day")
  const [search, setSearch] = useState("")
  const [searchTerm, setSearchTerm] = useState("")

  // debounce the full-text search like the invoice list does
  useEffect(() => {
    const timer = setTimeout(() => setSearchTerm(search), 350)
    return () => clearTimeout(timer)
  }, [search])

  const { data: stats } = useQuery({
    queryKey: ["invoice-stats"],
    queryFn: async () => (await InvoicesService.invoiceStats()).data,
    refetchInterval: 15_000,
  })

  const { data: found } = useQuery({
    queryKey: ["dashboard-search", searchTerm],
    enabled: searchTerm.trim().length > 0,
    queryFn: async () =>
      (
        await InvoicesService.listInvoices({
          query: { skip: 0, limit: 10, q: searchTerm },
        })
      ).data,
  })

  const buckets = stats?.[granularity] ?? []
  const maxTotal = Math.max(1, ...buckets.map((bucket) => bucket.total ?? 0))
  const totals = stats?.totals ?? {}

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl truncate max-w-sm">
          {t("dashboard.greeting", {
            name: currentUser?.full_name || currentUser?.email || "",
          })}
        </h1>
        <p className="text-muted-foreground">{t("dashboard.welcome")}</p>
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">{t("stats.title")}</h2>
          <Tabs
            value={granularity}
            onValueChange={(value) => setGranularity(value as Granularity)}
          >
            <TabsList>
              <TabsTrigger value="day" data-testid="stats-tab-day">
                {t("stats.day")}
              </TabsTrigger>
              <TabsTrigger value="week" data-testid="stats-tab-week">
                {t("stats.week")}
              </TabsTrigger>
              <TabsTrigger value="month" data-testid="stats-tab-month">
                {t("stats.month")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {STATES.map((state) => (
            <Card key={state} data-testid={`stats-card-${state}`}>
              <CardHeader className="p-3 pb-1">
                <CardTitle className="text-sm font-normal text-muted-foreground">
                  {t(`states.${state}`)}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-3 pt-0 text-2xl font-semibold">
                {totals[state] ?? 0}
              </CardContent>
            </Card>
          ))}
        </div>

        <div
          className="flex h-44 items-end gap-1 rounded-lg border p-4"
          data-testid="stats-chart"
        >
          {buckets.map((bucket) => {
            const byState = bucket.by_state ?? {}
            return (
              <div
                key={bucket.period}
                title={`${bucket.period}: ${bucket.total ?? 0}`}
                className="flex h-full flex-1 flex-col justify-end gap-1"
              >
                <div className="text-center text-[10px] text-muted-foreground">
                  {periodLabel(bucket.period ?? "", granularity)}
                </div>
                <div
                  className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                  style={{
                    height: `${((bucket.total ?? 0) / maxTotal) * 100}%`,
                    minHeight: bucket.total ? "3px" : "0",
                  }}
                >
                  {STATES.map((state) => {
                    const count = byState[state] ?? 0
                    if (!count || !bucket.total) return null
                    return (
                      <div
                        key={state}
                        className={STATE_COLORS[state]}
                        style={{ height: `${(count / bucket.total) * 100}%` }}
                      />
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {STATES.map((state) => (
            <span key={state} className="flex items-center gap-1.5">
              <span className={`size-2.5 rounded-sm ${STATE_COLORS[state]}`} />
              {t(`states.${state}`)}
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="font-semibold">{t("stats.searchTitle")}</h2>
        <form
          className="relative max-w-xl"
          onSubmit={(event) => event.preventDefault()}
        >
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            data-testid="dashboard-search"
            placeholder={t("stats.searchPlaceholder")}
            className="pl-8"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </form>
        {searchTerm && !found ? <Skeleton className="h-16 max-w-xl" /> : null}
        {found && found.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("stats.noResults")}{" "}
            <Link to="/invoices" className="underline">
              {t("stats.viewAll")}
            </Link>
          </p>
        ) : null}
        <ul className="flex max-w-xl flex-col gap-1">
          {(found?.data ?? []).map((invoice) => (
            <li key={invoice.id} data-testid="dashboard-search-result">
              <Link
                to="/invoices/$invoiceId"
                params={{ invoiceId: invoice.id }}
                className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-accent"
              >
                <span className="font-medium">
                  {invoice.invoice_number ?? invoice.original_filename}
                </span>
                <span className="text-muted-foreground">
                  {invoice.supplier_name ?? "—"}
                </span>
                <span className="ml-auto">
                  {invoice.total_price ? formatPrice(invoice.total_price) : ""}
                </span>
                <StateBadge state={invoice.state} />
              </Link>
            </li>
          ))}
        </ul>
        {(found?.data.length ?? 0) > 0 && (
          <p className="text-sm">
            <Link to="/invoices" className="text-primary underline">
              {t("stats.viewAll")}
            </Link>
          </p>
        )}
      </div>
    </div>
  )
}
