import { useQuery } from "@tanstack/react-query"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"

import { CompaniesService } from "@/client"

const STORAGE_KEY = "active-company-id"

export interface ActiveCompany {
  id: string
  name: string
  ico: string
}

interface ActiveCompanyContextValue {
  /** All companies the user can work with (for the switcher in the header). */
  companies: ActiveCompany[]
  /** The company selected app-wide or `null` for "all companies". */
  activeCompany: ActiveCompany | null
  setActiveCompanyId: (id: string | null) => void
}

const ActiveCompanyContext = createContext<ActiveCompanyContextValue | null>(
  null,
)

export function ActiveCompanyProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [companyId, setCompanyId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY)
    } catch {
      return null
    }
  })

  const { data } = useQuery({
    queryKey: ["companies", "active-switcher"],
    queryFn: async () =>
      (await CompaniesService.listCompanies({ query: { skip: 0, limit: 200 } }))
        .data,
    staleTime: 30_000,
  })
  const companies = useMemo(
    () =>
      (data?.data ?? []).map((company) => ({
        id: company.id,
        name: company.name,
        ico: company.ico,
      })),
    [data],
  )

  // a company deleted elsewhere must not stay selected
  useEffect(() => {
    if (companyId && data && !companies.some((c) => c.id === companyId)) {
      setCompanyId(null)
      try {
        localStorage.removeItem(STORAGE_KEY)
      } catch {
        /* ignore */
      }
    }
  }, [companyId, companies, data])

  const setActiveCompanyId = useCallback((id: string | null) => {
    setCompanyId(id)
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  }, [])

  const activeCompany =
    companies.find((company) => company.id === companyId) ?? null

  const value = useMemo(
    () => ({ companies, activeCompany, setActiveCompanyId }),
    [companies, activeCompany, setActiveCompanyId],
  )

  return (
    <ActiveCompanyContext.Provider value={value}>
      {children}
    </ActiveCompanyContext.Provider>
  )
}

export function useActiveCompany(): ActiveCompanyContextValue {
  const context = useContext(ActiveCompanyContext)
  if (!context) {
    throw new Error(
      "useActiveCompany must be used within ActiveCompanyProvider",
    )
  }
  return context
}
