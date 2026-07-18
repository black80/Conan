import { apiFetch } from "@/api/client"
import { sseFetch } from "@/api/sse"
import type { Backtest, MissedCase, ProposeEvent, Report, RuleDsl, RuleEntry } from "@/api/types"

export function getMissed(): Promise<MissedCase[]> {
  return apiFetch<MissedCase[]>("/api/missed")
}

export function getReports(): Promise<Report[]> {
  return apiFetch<Report[]>("/api/reports")
}

export function postReport(report: {
  subject_account: string
  window_start: string
  window_end: string
  description: string
}): Promise<Report> {
  return apiFetch<Report>("/api/reports", {
    method: "POST",
    body: JSON.stringify(report),
  })
}

export function proposeRule(
  reportId: string,
  onEvent: (event: ProposeEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return sseFetch<ProposeEvent>("/api/rule_lab/propose", { report_id: reportId }, onEvent, signal)
}

export function backtestRule(rule: RuleDsl, reportId?: string): Promise<Backtest> {
  return apiFetch<Backtest>("/api/rule_lab/backtest", {
    method: "POST",
    body: JSON.stringify({ rule, report_id: reportId }),
  })
}

export function getRules(): Promise<RuleEntry[]> {
  return apiFetch<RuleEntry[]>("/api/rules")
}

export function saveRule(rule: RuleDsl, backtest: Backtest, reportId: string): Promise<RuleEntry> {
  return apiFetch<RuleEntry>("/api/rules", {
    method: "POST",
    body: JSON.stringify({ rule, backtest, report_id: reportId }),
  })
}
