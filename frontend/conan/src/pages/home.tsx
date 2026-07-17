import * as React from "react"
import { BotIcon, FolderKanbanIcon, UserCheckIcon, WalletIcon } from "lucide-react"

import type { QueueEntry } from "@/api/types"
import { CasesOverviewChart, type CasesOverviewPoint } from "@/components/cases-overview-chart"
import { RecommendationBadge } from "@/components/recommendation-badge"
import { StatCard } from "@/components/stat-card"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useAlerts } from "@/state/alerts-context"

const numberFormatter = new Intl.NumberFormat("en-US")
const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
})

function isClosedByInvestigator(entry: QueueEntry): boolean {
  return entry.label !== null && entry.label.final_decision !== "escalated"
}

function buildOverview(alerts: QueueEntry[]): CasesOverviewPoint[] {
  const byDay = new Map<string, CasesOverviewPoint>()

  for (const entry of alerts) {
    const day = entry.timestamp.slice(0, 10)
    const point = byDay.get(day) ?? { day, ai: 0, investigator: 0 }
    if (entry.case?.status === "auto_closed") point.ai += 1
    if (isClosedByInvestigator(entry)) point.investigator += 1
    byDay.set(day, point)
  }

  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day))
}

export function HomePage() {
  const { alerts } = useAlerts()

  const stats = React.useMemo(() => {
    const totalCases = alerts.length
    const totalValue = alerts.reduce(
      (sum, entry) => sum + (entry.case?.alert.txn.amount_paid ?? 0),
      0
    )
    const casesClosedByAi = alerts.filter((entry) => entry.case?.status === "auto_closed").length
    const casesClosedByInvestigator = alerts.filter(isClosedByInvestigator).length

    return { totalCases, totalValue, casesClosedByAi, casesClosedByInvestigator }
  }, [alerts])

  const overview = React.useMemo(() => buildOverview(alerts), [alerts])

  const myQueue = React.useMemo(
    () =>
      alerts
        .filter((entry) => entry.case?.status === "needs_review" && !entry.label)
        .slice(0, 5),
    [alerts]
  )

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Total cases"
          value={numberFormatter.format(stats.totalCases)}
          icon={FolderKanbanIcon}
        />
        <StatCard
          label="Total value (investigated)"
          value={currencyFormatter.format(stats.totalValue)}
          icon={WalletIcon}
        />
        <StatCard
          label="Closed by AI"
          value={numberFormatter.format(stats.casesClosedByAi)}
          icon={BotIcon}
        />
        <StatCard
          label="Closed by investigator"
          value={numberFormatter.format(stats.casesClosedByInvestigator)}
          icon={UserCheckIcon}
        />
      </div>

      <CasesOverviewChart data={overview} />

      <Card>
        <CardHeader>
          <CardTitle>My queue</CardTitle>
          <CardDescription>A preview of open cases still awaiting a decision.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Risk</TableHead>
                <TableHead>Value</TableHead>
                <TableHead>Assigned</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {myQueue.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                    No cases in your queue.
                  </TableCell>
                </TableRow>
              )}
              {myQueue.map((entry) => (
                <TableRow key={entry.alert_id}>
                  <TableCell className="font-mono">{entry.case!.case_id}</TableCell>
                  <TableCell className="font-mono">{entry.subject_account}</TableCell>
                  <TableCell>
                    <RecommendationBadge recommendation={entry.case!.recommendation} />
                  </TableCell>
                  <TableCell>
                    {currencyFormatter.format(entry.case!.alert.txn.amount_paid)}
                  </TableCell>
                  <TableCell>{entry.assigned_to}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
