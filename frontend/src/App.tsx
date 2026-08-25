/**
 * Routing and global providers.
 */
import { Navigate, Route, Routes } from "react-router-dom";

import { AuditProvider } from "@/context/AuditContext";
import { MainLayout } from "@/layouts/MainLayout";
import { AuthCallbackPage } from "@/pages/AuthCallbackPage";
import { ChecklistPage } from "@/pages/ChecklistPage";
import { ChecksPage } from "@/pages/ChecksPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { ReportPage } from "@/pages/ReportPage";
import { RunAuditPage } from "@/pages/RunAuditPage";
import { SignInPage } from "@/pages/SignInPage";

export default function App() {
  return (
    <AuditProvider>
      <Routes>
        {/* OAuth redirect landing — bare (no app chrome), handles ?code&state. */}
        <Route path="auth/callback" element={<AuthCallbackPage />} />
        <Route element={<MainLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="run" element={<RunAuditPage />} />
          <Route path="report/:auditId" element={<ReportPage />} />
          <Route path="catalog" element={<ChecksPage />} />
          <Route path="checklist" element={<ChecklistPage />} />
          <Route path="custom-checks" element={<ChecksPage initialTab="custom" />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="sign-in" element={<SignInPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AuditProvider>
  );
}
