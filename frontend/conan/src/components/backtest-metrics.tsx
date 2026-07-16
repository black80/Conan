import type { Backtest } from "@/api/types"

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/50 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  )
}

export function BacktestMetrics({ backtest }: { backtest: Backtest }) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Catches case" value={backtest.catches_case ? "Yes" : "No"} />
        <Stat label="New alerts / day" value={backtest.new_alerts_per_day.toFixed(2)} />
        <Stat label="Precision" value={`${Math.round(backtest.precision * 100)}%`} />
        <Stat label="Overlap rate" value={`${Math.round(backtest.overlap_rate * 100)}%`} />
      </div>
      <p className="text-xs text-muted-foreground">
        Precision is the share of the rule's new (not already alerted) case-days that touch
        real laundering; overlap rate is the Jaccard overlap with existing rules' alerts.{" "}
        {backtest.new_cases} new case(s) across {backtest.case_days_total} case-days,{" "}
        {backtest.txn_matches} matching transactions.
      </p>
    </div>
  )
}
