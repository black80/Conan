import { TrendingDownIcon, TrendingUpIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import type { RerunOutcome } from "@/api/types"

const CONFIG: Record<RerunOutcome, { label: string; color: string; icon?: typeof TrendingUpIcon }> = {
  flipped_correct: { label: "Flipped correct", color: "var(--chart-2)", icon: TrendingUpIcon },
  still_correct: { label: "Still correct", color: "var(--chart-2)" },
  still_wrong: { label: "Still wrong", color: "var(--destructive)" },
  regressed: { label: "Regressed", color: "var(--destructive)", icon: TrendingDownIcon },
}

export function RerunOutcomeBadge({ outcome }: { outcome: RerunOutcome }) {
  const { label, color, icon: Icon } = CONFIG[outcome]

  return (
    <Badge
      variant="outline"
      style={{
        color,
        borderColor: `color-mix(in oklch, ${color}, transparent 65%)`,
        backgroundColor: `color-mix(in oklch, ${color}, transparent 90%)`,
      }}
    >
      {Icon ? <Icon /> : null}
      {label}
    </Badge>
  )
}
