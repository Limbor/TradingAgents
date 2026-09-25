import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";

export function AppShell({ children }: { children?: React.ReactNode }) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#151a18] text-stone-100 md:flex-row">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="min-h-0 flex-1 overflow-y-auto bg-[#1b201d] p-3 sm:p-5 xl:p-6">
          {children || <Outlet />}
        </main>
      </div>
    </div>
  );
}
