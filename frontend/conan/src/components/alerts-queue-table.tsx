import * as React from "react"
import { Loader2Icon } from "lucide-react"

import type { InvestigateEvent, QueueEntry } from "@/api/types"
import { RecommendationBadge } from "@/components/recommendation-badge"
import { TablePagination } from "@/components/table-pagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePagination } from "@/hooks/use-pagination"
import { titleCase } from "@/lib/utils"
import { useAlerts } from "@/state/alerts-context"

function lastToolLabel(events: InvestigateEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.type === "tool") return event.label
    if (event.type === "filing") return event.label
  }
  return null
}

function QueueRowStatus({ entry }: { entry: QueueEntry }) {
  const { investigations, ensureInvestigating, retryInvestigating } = useAlerts()

  // Pending alerts start investigating themselves as soon as the row appears — the
  // backend's 3-concurrent semaphore paces the rest (BACKEND.md §1), no button needed.
  // ensureInvestigating is idempotent per alert_id, so this is also safe to call again
  // from the case drawer without starting a second, separately-billed investigation.
  React.useEffect(() => {
    if (!entry.case) ensureInvestigating(entry.alert_id)
  }, [entry.case, entry.alert_id, ensureInvestigating])

  if (entry.case) {
    return (
      <div className="flex items-center gap-2">
        <RecommendationBadge recommendation={entry.case.recommendation} />
        <span className="text-xs text-muted-foreground">
          {entry.case.status === "auto_closed" ? "Auto-closed" : "Needs review"}
        </span>
      </div>
    )
  }

  const investigation = investigations[entry.alert_id]

  if (investigation?.status === "error") {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="destructive">Failed</Badge>
        <Button
          size="xs"
          variant="outline"
          onClick={(e) => {
            e.stopPropagation()
            retryInvestigating(entry.alert_id)
          }}
        >
          Retry
        </Button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2Icon className="size-3.5 animate-spin" />
      <span className="truncate">{lastToolLabel(investigation?.events ?? []) ?? "Queued…"}</span>
    </div>
  )
}

export function AlertsQueueTable({
  entries,
  onSelect,
  emptyLabel = "No alerts.",
}: {
  entries: QueueEntry[]
  onSelect: (alertId: string) => void
  emptyLabel?: string
}) {
  const newestFirstEntries = React.useMemo(
    () =>
      [...entries].sort(
        (left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp),
      ),
    [entries],
  )
  const { page, setPage, pageCount, pageItems } = usePagination(newestFirstEntries)

  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Alert</TableHead>
            <TableHead>Account</TableHead>
            <TableHead>Rules fired</TableHead>
            <TableHead>Assigned</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Label</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                {emptyLabel}
              </TableCell>
            </TableRow>
          )}
          {pageItems.map((entry) => (
            <TableRow
              key={entry.alert_id}
              className="cursor-pointer"
              onClick={() => onSelect(entry.alert_id)}
            >
              <TableCell className="text-muted-foreground">
                {new Date(entry.timestamp).toLocaleString()}
              </TableCell>
              <TableCell className="font-mono">{entry.alert_id}</TableCell>
              <TableCell className="font-mono">{entry.subject_account}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {entry.rules_fired.slice(0, 2).map((rule) => (
                    <Badge key={rule} variant="secondary">
                      {titleCase(rule)}
                    </Badge>
                  ))}
                  {entry.rules_fired.length > 2 && (
                    <Badge variant="secondary">+{entry.rules_fired.length - 2}</Badge>
                  )}
                </div>
              </TableCell>
              <TableCell>{entry.assigned_to}</TableCell>
              <TableCell onClick={(e) => e.stopPropagation()}>
                <QueueRowStatus entry={entry} />
              </TableCell>
              <TableCell>
                {entry.label ? (
                  <Badge variant="outline">{entry.label.label}</Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination page={page} pageCount={pageCount} onPageChange={setPage} />
    </div>
  )
}
