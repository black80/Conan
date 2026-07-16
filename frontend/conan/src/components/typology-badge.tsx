import { Badge } from "@/components/ui/badge"
import type { Typology } from "@/api/types"

export function TypologyBadge({ typology }: { typology: Typology }) {
  if (!typology) {
    return <span className="text-muted-foreground">—</span>
  }

  return (
    <Badge variant="secondary" className="font-mono">
      {typology}
    </Badge>
  )
}
