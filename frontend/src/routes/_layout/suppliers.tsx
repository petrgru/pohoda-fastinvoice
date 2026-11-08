import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { SuppliersService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import PendingUsers from "@/components/Pending/PendingUsers"
import { useSupplierColumns } from "@/components/Suppliers/columns"
import { Input } from "@/components/ui/input"

function Suppliers() {
  const { t } = useTranslation()
  const columns = useSupplierColumns()
  const [search, setSearch] = useState("")
  const [searchTerm, setSearchTerm] = useState("")

  // apply the search live – no need to press Enter
  useEffect(() => {
    const timer = setTimeout(() => setSearchTerm(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  const { data, isPending } = useQuery({
    queryKey: ["suppliers", searchTerm],
    queryFn: async () =>
      (
        await SuppliersService.listSuppliers({
          query: { skip: 0, limit: 200, q: searchTerm || undefined },
        })
      ).data,
    placeholderData: keepPreviousData,
  })

  const suppliers = data?.data ?? []

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          {t("suppliers.title")}
        </h1>
        <p className="text-muted-foreground">{t("suppliers.subtitle")}</p>
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
          data-testid="supplier-search-input"
          placeholder={t("suppliers.searchPlaceholder")}
          className="pl-8"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </form>
      {isPending ? (
        <PendingUsers />
      ) : (
        <DataTable columns={columns} data={suppliers} />
      )}
    </div>
  )
}

export const Route = createFileRoute("/_layout/suppliers")({
  component: Suppliers,
  head: () => ({ meta: [{ title: "Dodavatelé – Pohoda FastInvoice" }] }),
})
