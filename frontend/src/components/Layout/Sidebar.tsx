import { NavLink } from "react-router-dom";
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

function NavItem({ item }: { item: typeof primary[number] }) {
  return <NavLink to={item.to} end={item.end} className={({ isActive }) => cn(
    "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
    isActive ? "bg-teal-500/10 font-medium text-teal-200 ring-1 ring-inset ring-teal-500/20" : "text-stone-400 hover:bg-stone-800 hover:text-stone-100",
  )}><item.icon className="h-4 w-4 shrink-0" /><span>{item.label}</span></NavLink>;
}

export function Sidebar() {
  return <>
    <aside className="hidden w-[176px] shrink-0 flex-col border-r border-stone-800/80 bg-[#222824] md:flex">
      <div className="flex h-[52px] items-center gap-3 border-b border-stone-800 px-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-teal-500/15 text-teal-300"><TrendingUp className="h-5 w-5" /></span>
        <div><strong className="block text-sm text-stone-50">TradingAgents</strong><span className="text-xs text-stone-500">× StockManager</span></div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5">
        <p className="mb-2 px-3 text-[11px] font-medium tracking-[0.15em] text-stone-600">工作空间</p>
        <nav aria-label="主导航" className="space-y-1">{primary.map((item) => <NavItem key={item.to} item={item} />)}</nav>
        <div className="my-5 border-t border-stone-800" />
        <p className="mb-2 px-3 text-[11px] font-medium tracking-[0.15em] text-stone-600">更多工具</p>
        <nav aria-label="更多工具" className="space-y-1">{secondary.map((item) => <NavItem key={item.to} item={item} />)}</nav>
      </div>
      <div className="border-t border-stone-800 px-5 py-4 text-xs text-stone-500"><span className="mr-2 inline-block h-2 w-2 rounded-full bg-teal-400" />本地交易工作台</div>
    </aside>
    <nav aria-label="移动端主导航" className="flex shrink-0 gap-1 overflow-x-auto border-b border-stone-800 bg-[#222824] px-2 py-2 md:hidden">
      {primary.map((item) => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => cn("flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs", isActive ? "bg-teal-500/15 text-teal-200" : "text-stone-400")}><item.icon className="h-3.5 w-3.5" />{item.label}</NavLink>)}
      {secondary.map((item) => <NavLink key={item.to} to={item.to} className="flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-stone-400"><item.icon className="h-3.5 w-3.5" />{item.label}</NavLink>)}
    </nav>
  </>;
}
