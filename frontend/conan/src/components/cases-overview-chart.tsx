import { Bar, BarChart, CartesianGrid, XAxis } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"

export type CasesOverviewPoint = {
  day: string
  ai: number
  investigator: number
}

const chartConfig = {
  ai: {
    label: "Closed by AI",
    color: "var(--sidebar-primary)",
  },
  investigator: {
    label: "Closed by investigator",
    color: "var(--muted-foreground)",
  },
} satisfies ChartConfig

export function CasesOverviewChart({ data }: { data: CasesOverviewPoint[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cases closed by day</CardTitle>
      </CardHeader>
      <CardContent>
        <ChartContainer config={chartConfig} className="h-[280px] w-full">
          <BarChart data={data}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="day"
              tickLine={false}
              tickMargin={10}
              axisLine={false}
            />
            <ChartTooltip content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar dataKey="ai" fill="var(--color-ai)" radius={4} />
            <Bar
              dataKey="investigator"
              fill="var(--color-investigator)"
              radius={4}
            />
          </BarChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}
