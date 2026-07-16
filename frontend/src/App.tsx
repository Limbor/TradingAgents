import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/Layout/AppShell";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Analysis = lazy(() => import("./pages/Analysis"));
const Chat = lazy(() => import("./pages/Chat"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const Library = lazy(() => import("./pages/Library"));
const Settings = lazy(() => import("./pages/Settings"));
const Audit = lazy(() => import("./pages/Audit"));

const queryClient = new QueryClient();

function PageSkeleton() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-teal-300 border-t-transparent" />
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <Suspense fallback={<PageSkeleton />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/analysis/:runId" element={<Analysis />} />
              <Route path="/analysis" element={<Analysis />} />
              <Route path="/portfolio" element={<Portfolio />} />
              <Route path="/library" element={<Library />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/settings" element={<Settings />} />
              {/* Redirects for removed pages */}
              <Route path="/reports" element={<Navigate to="/library" replace />} />
              <Route path="/watchlist" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
