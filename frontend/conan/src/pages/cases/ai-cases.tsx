import * as React from "react"
import { Loader2Icon } from "lucide-react"

import { AlertsQueueTable } from "@/components/alerts-queue-table"
import { CaseDrawer } from "@/components/case-drawer"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useAlerts } from "@/state/alerts-context"

export function AiCasesPage() {
  const { alerts, status } = useAlerts()
  const [selected, setSelected] = React.useState<string | null>(null)

  const counters = React.useMemo(() => {
    let pending = 0
    let autoClosed = 0
    let escalated = 0
    let blocked = 0

    for (const entry of alerts) {
      if (!entry.case) {
        pending += 1
      } else if (entry.case.status === "auto_closed") {
        autoClosed += 1
      } else if (entry.case.recommendation === "block") {
        blocked += 1
      } else if (entry.case.recommendation === "escalate") {
        escalated += 1
      }
    }

    return { pending, autoClosed, escalated, blocked }
  }, [alerts])

  if (status === "connecting") {
    return (
      <div className="flex min-h-[calc(100svh-8rem)] flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed">
        <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Connecting to the agent backend…</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCell label="Pending" value={counters.pending} />
        <StatCell label="Auto-closed" value={counters.autoClosed} />
        <StatCell label="Escalated" value={counters.escalated} />
        <StatCell label="Blocked" value={counters.blocked} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agent queue</CardTitle>
          <CardDescription>Every alert the agent has investigated, newest first.</CardDescription>
        </CardHeader>
        <CardContent>
          <AlertsQueueTable entries={alerts} onSelect={setSelected} />
        </CardContent>
      </Card>

      <CaseDrawer alertId={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </div>
  )
}

function StatCell({ label, value }: { label: string; value: number }) {
  return (
    <Card size="sm">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value}</p>
      </CardContent>
    </Card>
  )
}
