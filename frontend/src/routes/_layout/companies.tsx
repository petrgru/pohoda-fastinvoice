import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useState } from "react"
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

  // apply the search live – no need to press Enter
  useEffect(() => {
    const timer = setTimeout(() => setSearchTerm(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

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
      {/* the action floats in the corner – it stays put while the list scrolls */}
      <div
        className="fixed bottom-6 right-6 z-40 flex items-center gap-2"
        data-testid="companies-floating-actions"
      >
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
