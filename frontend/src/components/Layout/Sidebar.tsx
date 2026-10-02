import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  BookOpen, Brain, BriefcaseBusiness, ClipboardCheck, FlaskConical,
  Globe, LayoutDashboard, MessageSquareText, Settings, TrendingUp, Wallet,
} from "lucide-react";
import { cn } from "@/lib/utils";

const primary = [
  { to: "/", label: "决策工作台", icon: LayoutDashboard, end: true },
  { to: "/chat", label: "交易 Agent", icon: MessageSquareText },
  { to: "/research", label: "策略研究", icon: FlaskConical },
  { to: "/paper", label: "模拟盘", icon: Wallet },
  { to: "/audit", label: "决策审计", icon: ClipboardCheck },
];
const secondary = [
  { to: "/market", label: "市场全景", icon: Globe },
  { to: "/portfolio", label: "持仓管理", icon: BriefcaseBusiness },
  { to: "/library", label: "产物库", icon: BookOpen },
  { to: "/reflection", label: "反思记录", icon: Brain },
  { to: "/settings", label: "设置", icon: Settings },
];

function DesktopNavLink({ to, label, icon: Icon, end = false }: {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}) {
  return <NavLink
    to={to}
    end={end}
    aria-label={label}
    className={({ isActive }) => cn(
      "group relative flex h-9 w-9 shrink-0 items-center justify-center rounded-md transition-colors",
      isActive ? "bg-teal-400/15 text-teal-200" : "text-stone-400 hover:bg-stone-700/60 hover:text-stone-100",
    )}
  >
    <Icon className="h-4 w-4" />
    <span aria-hidden="true" className="pointer-events-none invisible absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap rounded-md border border-stone-700 bg-[#222824] px-2.5 py-1.5 text-xs font-medium text-stone-100 opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100">
      {label}
    </span>
  </NavLink>;
}

export function Sidebar() {
  return <>
    <aside className="relative z-20 hidden w-14 shrink-0 flex-col items-center overflow-visible border-r border-stone-800/80 bg-[#222824] py-3 md:flex">
      <div className="mb-6"><DesktopNavLink to="/" label="TradingAgents 首页" icon={TrendingUp} end /></div>
      <nav aria-label="主导航" className="flex flex-col gap-1">{primary.map((item) => <DesktopNavLink key={item.to} {...item} />)}</nav>
      <div className="mt-5 border-t border-stone-700/70 pt-4"><nav aria-label="更多工具" className="flex flex-col gap-1">{secondary.map((item) => <DesktopNavLink key={item.to} {...item} />)}</nav></div>
    </aside>
    <nav aria-label="移动端主导航" className="flex shrink-0 gap-1 overflow-x-auto border-b border-stone-800 bg-[#222824] px-2 py-2 md:hidden">
      {primary.map((item) => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => cn("flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs", isActive ? "bg-teal-500/15 text-teal-200" : "text-stone-400")}><item.icon className="h-3.5 w-3.5" />{item.label}</NavLink>)}
      {secondary.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => cn("flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs", isActive ? "bg-teal-500/15 text-teal-200" : "text-stone-400")}><item.icon className="h-3.5 w-3.5" />{item.label}</NavLink>)}
    </nav>
  </>;
}
