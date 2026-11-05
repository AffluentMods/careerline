// SPDX-License-Identifier: Apache-2.0
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { FullPageLoader } from "./components/ui";
import { useAuthStatus, useMe } from "./lib/auth";
import { AssistantPage } from "./pages/assistant/AssistantPage";
import { InsightsPage } from "./pages/insights/InsightsPage";
import { InterviewHomePage, MockPage, PrepPage } from "./pages/interview/InterviewPages";
import { JobsPage } from "./pages/jobs/JobsPage";
import { PostingPage } from "./pages/jobs/PostingPage";
import { LoginPage } from "./pages/Login";
import { DocumentPage } from "./pages/materials/DocumentPage";
import { MaterialsPage } from "./pages/materials/MaterialsPage";
import { MfaChallengePage } from "./pages/MfaChallenge";
import { OverviewPage } from "./pages/Overview";
import { PlaceholderPage } from "./pages/Placeholder";
import { RegisterPage } from "./pages/Register";
import { SecurityPage } from "./pages/Security";
import { AiSettingsPage } from "./pages/settings/AiSettingsPage";
import { ApiAccessPage } from "./pages/settings/ApiAccessPage";
import { DataPage } from "./pages/settings/DataPage";
import { NotificationsPage } from "./pages/settings/NotificationsPage";
import { ApplicationPage } from "./pages/tracker/ApplicationPage";
import { TrackerPage } from "./pages/tracker/TrackerPage";
import { VaultPage } from "./pages/vault/VaultPage";

function SignedIn() {
  const status = useAuthStatus();
  const active = status.data?.session === "active";
  const me = useMe(active);

  if (status.isPending) return <FullPageLoader />;
  if (status.isError) return <Navigate to="/login" replace />;
  const s = status.data;
  if (s.session === "mfa_pending") return <Navigate to="/login/verify" replace />;
  if (s.session === "none") return <Navigate to={s.has_users ? "/login" : "/setup"} replace />;
  if (me.isPending) return <FullPageLoader />;
  if (me.isError) return <Navigate to="/login" replace />;
  return <AppShell user={me.data} />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/login/verify" element={<MfaChallengePage />} />
      <Route path="/setup" element={<RegisterPage firstRun />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<SignedIn />}>
        <Route index element={<OverviewPage />} />
        <Route path="vault" element={<VaultPage />} />
        <Route path="vault/:tab" element={<VaultPage />} />
        <Route path="vault/:tab/:importId" element={<VaultPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/postings/:id" element={<PostingPage />} />
        <Route path="jobs/:tab" element={<JobsPage />} />
        <Route path="materials" element={<MaterialsPage />} />
        <Route path="materials/:id" element={<DocumentPage />} />
        <Route path="tracker" element={<TrackerPage />} />
        <Route path="tracker/applications/:id" element={<ApplicationPage />} />
        <Route path="tracker/:tab" element={<TrackerPage />} />
        <Route path="interview" element={<InterviewHomePage />} />
        <Route path="interview/:prepId" element={<PrepPage />} />
        <Route path="interview/mock/:mockId" element={<MockPage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="assistant/:id" element={<AssistantPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="settings" element={<Navigate to="/settings/security" replace />} />
        <Route path="settings/security" element={<SecurityPage />} />
        <Route path="settings/ai" element={<AiSettingsPage />} />
        <Route path="settings/notifications" element={<NotificationsPage />} />
        <Route path="settings/data" element={<DataPage />} />
        <Route path="settings/api" element={<ApiAccessPage />} />
        <Route path="*" element={<PlaceholderPage title="Page not found" summary="That page doesn't exist." />} />
      </Route>
    </Routes>
  );
}
