import { API_BASE_URL } from "@/api/client"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useAlerts } from "@/state/alerts-context"

export function SettingsPage() {
  const { status, alerts } = useAlerts()

  return (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Backend connection</CardTitle>
        <CardDescription>Where the frontend is reaching the agent backend.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">API URL</span>
          <span className="font-mono">{API_BASE_URL}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Status</span>
          <Badge variant={status === "ready" ? "secondary" : "outline"}>
            {status === "ready" ? "Connected" : "Connecting…"}
          </Badge>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Alerts loaded</span>
          <span>{alerts.length}</span>
        </div>
      </CardContent>
    </Card>
  )
}
