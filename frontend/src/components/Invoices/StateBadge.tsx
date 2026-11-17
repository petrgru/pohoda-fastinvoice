import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const STATE_CLASSES: Record<string, string> = {
  uploaded: "bg-muted text-muted-foreground",
  parsing: "bg-muted text-muted-foreground",
  needs_review: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  ready_for_export: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  processing: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  exported: "bg-green-500/15 text-green-600 dark:text-green-400",
  export_failed: "bg-red-500/15 text-red-600 dark:text-red-400",
}

export function StateBadge({
  state,
  errorMessage,
}: {
  state: string
  /** Last message from the accounting system, shown above the badge. */
  errorMessage?: string | null
}) {
  const { t } = useTranslation()
  const badge = (
    <Badge
      data-testid="state-badge"
      variant="outline"
      className={`${STATE_CLASSES[state] ?? ""} border-transparent font-normal`}
    >
      {t(`states.${state}`, { defaultValue: state })}
    </Badge>
  )
  if (!errorMessage) return badge
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{badge}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80">
        {t("states.exportFailedDetail", {
          detail: errorMessage,
          defaultValue: errorMessage,
        })}
      </TooltipContent>
    </Tooltip>
  )
}
