import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Navigate,
  Outlet,
  redirect,
  useRouterState,
} from "@tanstack/react-router"

import { useTranslation } from "react-i18next"

import { CompaniesService } from "@/client"
import { ActiveCompanySwitcher } from "@/components/Common/ActiveCompanySwitcher"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { ActiveCompanyProvider } from "@/hooks/useActiveCompany"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
      })
    }
  },
})

// the current section shown in the header – same labels as the sidebar
const SECTION_BY_SEGMENT: Record<
  string,
  { titleKey: string; subtitleKey: string }
> = {
  "": { titleKey: "nav.dashboard", subtitleKey: "dashboard.subtitle" },
  invoices: { titleKey: "nav.invoices", subtitleKey: "invoices.subtitle" },
  queue: { titleKey: "nav.queue", subtitleKey: "queue.subtitle" },
  companies: { titleKey: "nav.companies", subtitleKey: "companies.subtitle" },
  suppliers: { titleKey: "nav.suppliers", subtitleKey: "suppliers.subtitle" },
  settings: { titleKey: "settings.title", subtitleKey: "settings.subtitle" },
  admin: { titleKey: "users.title", subtitleKey: "users.subtitle" },
}

function Layout() {
  const { user } = useAuth()
  const { t } = useTranslation()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  // a brand-new member must set up their first company before anything else
  const { data: companies } = useQuery({
    queryKey: ["companies", "onboarding"],
    queryFn: async () =>
      (await CompaniesService.listCompanies({ query: { limit: 1 } })).data,
    enabled: Boolean(user) && !user?.is_superuser,
  })
  const needsFirstCompany =
    Boolean(user) && !user?.is_superuser && companies?.count === 0

  if (needsFirstCompany && pathname !== "/companies") {
    return <Navigate to="/companies" replace />
  }

  return (
    <ActiveCompanyProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>
          <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background px-4">
            <SidebarTrigger className="-ml-1 text-muted-foreground" />
            <ActiveCompanySwitcher />
            {(() => {
              const section =
                SECTION_BY_SEGMENT[pathname.split("/").filter(Boolean)[0] ?? ""]
              if (!section) return null
              return (
                <div className="hidden min-w-0 flex-col sm:flex">
                  <h1 className="truncate text-base leading-tight font-bold tracking-tight">
                    {t(section.titleKey)}
                  </h1>
                  <p className="truncate text-xs leading-tight text-muted-foreground">
                    {t(section.subtitleKey)}
                  </p>
                </div>
              )
            })()}
          </header>
          <main className="flex-1 p-6 md:p-8">
            <div className="mx-auto max-w-7xl">
              <Outlet />
            </div>
          </main>
        </SidebarInset>
      </SidebarProvider>
    </ActiveCompanyProvider>
  )
}
