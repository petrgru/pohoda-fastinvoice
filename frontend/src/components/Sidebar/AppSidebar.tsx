import { Building2, FileText, Home, Inbox, Users } from "lucide-react"
import { useTranslation } from "react-i18next"
import { SidebarAppearance } from "@/components/Common/Appearance"
import { LanguageSwitcher } from "@/components/Common/LanguageSwitcher"
import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { User } from "./User"

const baseIcons = {
  dashboard: Home,
  invoices: FileText,
  queue: Inbox,
  companies: Building2,
}

export function AppSidebar() {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()

  const baseItems: Item[] = [
    { icon: baseIcons.dashboard, title: t("nav.dashboard"), path: "/" },
    { icon: baseIcons.invoices, title: t("nav.invoices"), path: "/invoices" },
    { icon: baseIcons.queue, title: t("nav.queue"), path: "/queue" },
    {
      icon: baseIcons.companies,
      title: t("nav.companies"),
      path: "/companies",
    },
  ]

  const items = currentUser?.is_superuser
    ? [...baseItems, { icon: Users, title: t("nav.admin"), path: "/admin" }]
    : baseItems

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
      </SidebarContent>
      <SidebarFooter>
        <LanguageSwitcher />
        <SidebarAppearance />
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
