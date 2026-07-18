import type { Evidence } from "@/api/types"
import { MarkdownText } from "@/components/markdown-text"

const DOT_COLOR: Record<Evidence["supports"], string> = {
  fraud: "var(--destructive)",
  legit: "var(--chart-2)",
  neutral: "var(--muted-foreground)",
}

function EvidencePoint({ evidence }: { evidence: Evidence }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border p-3">
      <span
        className="mt-1 size-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: DOT_COLOR[evidence.supports] }}
      />
      <div>
        <p className="text-sm font-medium">{evidence.label}</p>
        <MarkdownText text={evidence.value} className="text-muted-foreground" />
      </div>
    </div>
  )
}

function EvidenceColumn({ title, points }: { title: string; points: Evidence[] }) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground uppercase">{title}</p>
      {points.length > 0 ? (
        <div className="space-y-2">
          {points.map((e, i) => (
            <EvidencePoint key={i} evidence={e} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">None noted.</p>
      )}
    </div>
  )
}

export function EvidenceLedger({ evidence }: { evidence: Evidence[] }) {
  const concerning = evidence.filter((e) => e.supports === "fraud")
  const exculpatory = evidence.filter((e) => e.supports !== "fraud")

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <EvidenceColumn title="Concerning evidence" points={concerning} />
      <EvidenceColumn title="Exculpatory or neutral evidence" points={exculpatory} />
    </div>
  )
}
