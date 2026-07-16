import { MarkdownText } from "@/components/markdown-text"
import { Badge } from "@/components/ui/badge"
import type { RuleDsl } from "@/api/types"

function formatConjuncts(rule: RuleDsl): string {
  return rule.conjuncts.map((c) => `${c.feature} ${c.op} ${c.threshold}`).join(" ∧ ")
}

export function RuleDslCard({ rule }: { rule: RuleDsl }) {
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-medium">{rule.name}</span>
        <div className="flex items-center gap-1.5">
          <Badge variant="secondary">{rule.subject_side}</Badge>
          <Badge variant="outline">weight {rule.weight}</Badge>
        </div>
      </div>
      <p className="font-mono text-sm text-muted-foreground">{formatConjuncts(rule)}</p>
      {rule.rationale && <MarkdownText text={rule.rationale} />}
    </div>
  )
}
