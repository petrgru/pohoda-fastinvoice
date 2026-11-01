import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"

const STATE_CLASSES: Record<string, string> = {
  uploaded: "bg-muted text-muted-foreground",
  parsing: "bg-muted text-muted-foreground",
  needs_review: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  ready_for_export: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  exported: "bg-green-500/15 text-green-600 dark:text-green-400",
  export_failed: "bg-red-500/15 text-red-600 dark:text-red-400",
}

export function StateBadge({ state }: { state: string }) {
  const { t } = useTranslation()
  return (
    <Badge
      data-testid="state-badge"
      variant="outline"
      className={`${STATE_CLASSES[state] ?? ""} border-transparent font-normal`}
    >
      {t(`states.${state}`, { defaultValue: state })}
    </Badge>
  )
}
