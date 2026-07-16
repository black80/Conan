import { Outlet, useLocation } from "react-router"

import { AppSidebar } from "@/components/app-sidebar"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { AlertsProvider } from "@/state/alerts-context"

const PAGE_TITLES: Record<string, string> = {
  "/": "Home",
  "/cases/ai": "Agent Queue",
  "/cases/investigator": "My Queue",
  "/fine-tuning/rule-generation": "Rule Engine",
  "/fine-tuning/model-tuning": "Feedback Loop",
  "/settings": "Settings",
}

export function AppLayout() {
  const { pathname } = useLocation()
  const title = PAGE_TITLES[pathname] ?? "Conan"

  return (
    <AlertsProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>
          <header className="flex h-14 shrink-0 items-center gap-2 border-b">
            <div className="flex items-center gap-2 px-4">
              <SidebarTrigger className="-ml-1" />
              <Separator
                orientation="vertical"
                className="mr-2 data-vertical:h-4 data-vertical:self-auto"
              />
              <h1 className="text-sm font-medium">{title}</h1>
            </div>
          </header>
          <div className="flex flex-1 flex-col gap-4 p-4">
            <Outlet />
          </div>
        </SidebarInset>
      </SidebarProvider>
    </AlertsProvider>
  )
}
