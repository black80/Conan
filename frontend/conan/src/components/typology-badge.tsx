import { Badge } from "@/components/ui/badge"
import type { Typology } from "@/api/types"
import { titleCase } from "@/lib/utils"

export function TypologyBadge({ typology }: { typology: Typology }) {
  if (!typology) {
    return <span className="text-muted-foreground">—</span>
  }

  return <Badge variant="secondary">{titleCase(typology)}</Badge>
}
