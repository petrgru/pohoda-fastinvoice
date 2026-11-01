import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { CompaniesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { AddCompany } from "@/components/Companies/AddCompany"
import { useCompanyColumns } from "@/components/Companies/columns"
import PendingUsers from "@/components/Pending/PendingUsers"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"

function Companies() {
  const { t } = useTranslation()
  const columns = useCompanyColumns()
  const [search, setSearch] = useState("")
  const [searchTerm, setSearchTerm] = useState("")

  const { data, isPending } = useQuery({
    queryKey: ["companies", searchTerm],
    queryFn: async () =>
      (
        await CompaniesService.listCompanies({
          query: { skip: 0, limit: 200, q: searchTerm || undefined },
        })
      ).data,
    placeholderData: keepPreviousData,
  })

  const companies = data?.data ?? []

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("companies.title")}
          </h1>
          <p className="text-muted-foreground">{t("companies.subtitle")}</p>
        </div>
        <AddCompany />
      </div>
      <form
        className="relative max-w-md"
        onSubmit={(e) => {
          e.preventDefault()
          setSearchTerm(search)
        }}
      >
        <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
        <Input
          data-testid="company-search-input"
          placeholder={t("companies.searchPlaceholder")}
          className="pl-8"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </form>
      {data && companies.length === 0 && !searchTerm && (
        <Alert data-testid="companies-empty-hint">
          <AlertTitle>{t("companies.emptyTitle")}</AlertTitle>
          <AlertDescription>{t("companies.emptyHint")}</AlertDescription>
        </Alert>
      )}
      {isPending ? (
        <PendingUsers />
      ) : (
        <DataTable columns={columns} data={companies} />
      )}
    </div>
  )
}

export const Route = createFileRoute("/_layout/companies")({
  component: Companies,
  head: () => ({ meta: [{ title: "Firmy – Pohoda FastInvoice" }] }),
})
