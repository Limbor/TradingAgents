import { NavLink } from "react-router-dom";
import {
  BriefcaseBusiness,
  Brain,
  FileText,
  LayoutDashboard,
  MessageSquareText,
  Settings,
  TrendingUp,
  ClipboardCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/chat", label: "Chat", icon: MessageSquareText },
  { to: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness },
  { to: "/library", label: "Library", icon: FileText },
  { to: "/audit", label: "Audit", icon: ClipboardCheck },
  { to: "/reflection", label: "Reflection", icon: Brain },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  return (
    <aside className="flex w-60 flex-col border-r border-stone-800 bg-stone-900">
      <div className="flex h-16 items-center gap-3 border-b border-stone-800 px-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
          <TrendingUp className="h-5 w-5 text-teal-300" />
        </div>
        <div>
          <span className="block text-sm font-semibold tracking-wide text-stone-50">TradingAgents</span>
          <span className="text-xs text-stone-500">Market intelligence desk</span>
        </div>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-stone-800 text-stone-50 shadow-sm"
                  : "text-stone-400 hover:bg-stone-800/60 hover:text-stone-100"
              )
            }
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-stone-800 p-4">
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <p className="text-xs font-medium text-stone-300">TradingAgents v2</p>
          <p className="mt-1 text-xs text-stone-500">Chat-first Agent Desk</p>
        </div>
      </div>
    </aside>
  );
}
