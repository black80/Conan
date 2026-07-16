import { Badge } from "@/components/ui/badge"

export function DisagreementBadge({ severe }: { severe: boolean }) {
  if (severe) {
    return (
      <Badge
        variant="outline"
        style={{
          color: "var(--destructive)",
          borderColor: "color-mix(in oklch, var(--destructive), transparent 65%)",
          backgroundColor: "color-mix(in oklch, var(--destructive), transparent 90%)",
        }}
      >
        Severe
      </Badge>
    )
  }

  return (
    <Badge variant="secondary" className="text-muted-foreground">
      Mild
    </Badge>
  )
}

export function DataCeilingBadge() {
  return (
    <Badge variant="outline" className="text-muted-foreground">
      Ceiling
    </Badge>
  )
}
