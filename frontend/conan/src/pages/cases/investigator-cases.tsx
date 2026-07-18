import * as React from "react"
import { Loader2Icon } from "lucide-react"

import type { QueueEntry } from "@/api/types"
import { AlertsQueueTable } from "@/components/alerts-queue-table"
import { CaseDrawer } from "@/components/case-drawer"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAlerts } from "@/state/alerts-context"

// Fixed roster used by the backend's deterministic fill-first allocator (BACKEND.md §4.1).
const ANALYSTS = ["Aisha", "Omar", "Sara"]

function isOpenForAnalyst(entry: QueueEntry): boolean {
  if (entry.case?.status !== "needs_review") return false
  return !entry.label || entry.label.final_decision === "escalated"
}

export function InvestigatorCasesPage() {
  const { alerts, status } = useAlerts()
  const [analyst, setAnalyst] = React.useState(ANALYSTS[0])
  const [selected, setSelected] = React.useState<string | null>(null)

  const myQueue = React.useMemo(
    () => alerts.filter((entry) => entry.assigned_to === analyst && isOpenForAnalyst(entry)),
    [alerts, analyst]
  )

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
      <Card>
        <CardHeader>
          <CardTitle>My queue</CardTitle>
          <CardDescription>Cases assigned to you that are still open.</CardDescription>
          <CardAction>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Logged in as</span>
              <Select value={analyst} onValueChange={(value) => value && setAnalyst(value)}>
                <SelectTrigger size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ANALYSTS.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardAction>
        </CardHeader>
        <CardContent>
          <AlertsQueueTable
            entries={myQueue}
            onSelect={setSelected}
            emptyLabel={`No open cases assigned to ${analyst}.`}
          />
        </CardContent>
      </Card>

      <CaseDrawer alertId={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </div>
  )
}
