import { Route, Routes } from "react-router"

import { AppLayout } from "@/layouts/app-layout"
import { AiCasesPage } from "@/pages/cases/ai-cases"
import { InvestigatorCasesPage } from "@/pages/cases/investigator-cases"
import { ModelTuningPage } from "@/pages/fine-tuning/model-tuning"
import { RuleGenerationPage } from "@/pages/fine-tuning/rule-generation"
import { HomePage } from "@/pages/home"
import { SettingsPage } from "@/pages/settings"

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="cases/ai" element={<AiCasesPage />} />
        <Route path="cases/investigator" element={<InvestigatorCasesPage />} />
        <Route
          path="fine-tuning/rule-generation"
          element={<RuleGenerationPage />}
        />
        <Route
          path="fine-tuning/model-tuning"
          element={<ModelTuningPage />}
        />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  )
}

export default App
