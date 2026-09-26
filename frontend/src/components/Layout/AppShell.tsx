import { Outlet } from "react-router-dom";
import { useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";

export function AppShell({ children }: { children?: React.ReactNode }) {
  const agentPage = useLocation().pathname === "/chat";
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-ui-canvas text-ui-ink md:flex-row">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className={`min-h-0 flex-1 overflow-y-auto bg-ui-canvas ${agentPage ? "p-2 sm:p-3" : "p-3 sm:p-5 xl:p-6"}`}>
          {children || <Outlet />}
        </main>
      </div>
    </div>
  );
}
