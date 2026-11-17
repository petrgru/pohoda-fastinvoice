import { useTranslation } from "react-i18next"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useActiveCompany } from "@/hooks/useActiveCompany"

/** App-wide company picker – everything (lists, queue, stats, uploads)
 *  works with the selected company only; empty means "all companies". */
export function ActiveCompanySwitcher() {
  const { t } = useTranslation()
  const { companies, activeCompany, setActiveCompanyId } = useActiveCompany()

  return (
    <Select
      value={activeCompany?.id ?? "all"}
      onValueChange={(value) =>
        setActiveCompanyId(value === "all" ? null : value)
      }
    >
      <SelectTrigger
        className="w-64"
        data-testid="active-company"
        aria-label={t("companySwitcher.label")}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{t("companySwitcher.allCompanies")}</SelectItem>
        {companies.map((company) => (
          <SelectItem key={company.id} value={company.id}>
            {company.name} ({company.ico})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export default ActiveCompanySwitcher
