import { useTranslation } from "react-i18next"

import type { SupplierPublic } from "@/client"
import { Badge } from "@/components/ui/badge"

const STATUS_BADGES: Record<
  string,
  { key: string; className: string } | undefined
> = {
  pending: {
    key: "suppliers.registryPending",
    className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  },
  not_found: {
    key: "suppliers.registryNotFound",
    className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  },
  ico_invalid: {
    key: "suppliers.registryIcoInvalid",
    className: "bg-red-500/15 text-red-600 dark:text-red-400",
  },
  verified: {
    key: "suppliers.registryVerified",
    className: "bg-green-500/15 text-green-600 dark:text-green-400",
  },
}

/** Registry verification state of a supplier, shown in lists and dialogs. */
export function RegistryBadges({ supplier }: { supplier: SupplierPublic }) {
  const { t } = useTranslation()
  const status = STATUS_BADGES[supplier.registry_status ?? "not_checkable"]
  return (
    <div
      className="flex flex-wrap items-center gap-1"
      data-testid="registry-badges"
    >
      {status && (
        <Badge
          variant="outline"
          className={`border-transparent ${status.className}`}
        >
          {t(status.key)}
        </Badge>
      )}
      {supplier.in_insolvency && (
        <Badge
          variant="outline"
          className="border-transparent bg-red-500/15 font-medium text-red-700 dark:text-red-400"
          data-testid="insolvency-badge"
        >
          {t("suppliers.insolvency")}
        </Badge>
      )}
      {supplier.unreliable_payer && (
        <Badge variant="outline" className="border-red-400/60 text-red-600">
          {t("suppliers.unreliablePayer")}
        </Badge>
      )}
    </div>
  )
}
