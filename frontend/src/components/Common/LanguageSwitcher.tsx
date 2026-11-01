import { Check, Languages } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { SUPPORTED_LANGUAGES } from "@/i18n"

/** Language picker kept next to the appearance switcher, choice persisted
 * in localStorage (see i18n detection config). */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const { isMobile } = useSidebar()
  const current =
    SUPPORTED_LANGUAGES.find((lng) => i18n.resolvedLanguage === lng.code) ??
    SUPPORTED_LANGUAGES[0]

  const menu = (
    <DropdownMenuContent
      side={isMobile ? "top" : "right"}
      align="end"
      className="min-w-36"
    >
      {SUPPORTED_LANGUAGES.map((lng) => (
        <DropdownMenuItem
          key={lng.code}
          data-testid={`language-${lng.code}`}
          onClick={() => i18n.changeLanguage(lng.code)}
        >
          {lng.label}
          {i18n.resolvedLanguage === lng.code && <Check className="ml-auto" />}
        </DropdownMenuItem>
      ))}
    </DropdownMenuContent>
  )

  const trigger = (label: string) => (
    <>
      <Languages />
      <span>{label}</span>
    </>
  )

  return (
    <DropdownMenu>
      {isMobile ? (
        <DropdownMenuTrigger asChild>
          <Button data-testid="language-button" variant="outline" size="icon">
            <Languages className="size-4" />
          </Button>
        </DropdownMenuTrigger>
      ) : (
        <SidebarMenuItem>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              tooltip={t("nav.language")}
              data-testid="language-button"
            >
              {trigger(current.label)}
            </SidebarMenuButton>
          </DropdownMenuTrigger>
        </SidebarMenuItem>
      )}
      {menu}
    </DropdownMenu>
  )
}
