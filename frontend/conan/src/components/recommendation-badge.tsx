import { ArrowUpIcon, CheckIcon, OctagonXIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import type { Recommendation } from "@/api/types"

const CONFIG: Record<
  Recommendation,
  { label: string; icon: typeof CheckIcon; color: string }
> = {
  approve: { label: "Approve", icon: CheckIcon, color: "var(--chart-2)" },
  escalate: { label: "Escalate", icon: ArrowUpIcon, color: "var(--chart-3)" },
  block: { label: "Block", icon: OctagonXIcon, color: "var(--destructive)" },
}

export function RecommendationBadge({
  recommendation,
}: {
  recommendation: Recommendation
}) {
  const { label, icon: Icon, color } = CONFIG[recommendation]

  return (
    <Badge
      variant="outline"
      style={{
        color,
        borderColor: `color-mix(in oklch, ${color}, transparent 65%)`,
        backgroundColor: `color-mix(in oklch, ${color}, transparent 90%)`,
      }}
    >
      <Icon />
      {label}
    </Badge>
  )
}
