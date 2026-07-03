import { useEffect, useState } from "react";
import type { DailyPipelineFilters } from "@/api/client";

const DEFAULT_FILTERS: DailyPipelineFilters = {
  board_filter: "",
  exclude_st: true,
  exclude_suspended: true,
  exclude_one_price_limit: true,
  min_amount_20d: 0,
  min_price: 0,
  max_price: 0,
  min_market_cap: 0,
  max_market_cap: 0,
  min_listing_days: 0,
  max_pe: 0,
  max_pb: 0,
  max_turnover_rate: 0,
  include_industries: [],
  exclude_industries: [],
};

interface Preset {
  id: string;
  name: string;
  desc: string;
  color: string;
  filters: DailyPipelineFilters;
}

const PRESETS: Preset[] = [
  {
    id: "conservative",
    name: "保守稳健",
    desc: "主板中盘 · 2年+ · 排除银行地产",
    color: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    filters: {
      exclude_st: true,
      exclude_suspended: true,
      exclude_one_price_limit: true,
      board_filter: "main_board",
      min_listing_days: 500,
      min_market_cap: 30,
      max_market_cap: 800,
      max_pe: 80,
      min_price: 5,
      max_price: 80,
      min_amount_20d: 8000,
      max_turnover_rate: 12,
      exclude_industries: ["银行", "房地产"],
    },
  },
  {
    id: "aggressive",
    name: "小市值进攻",
    desc: "全板块 · 15-200亿 · 高弹性小票",
    color: "border-orange-500/40 bg-orange-500/10 text-orange-300",
    filters: {
      exclude_st: true,
      exclude_suspended: true,
      exclude_one_price_limit: true,
      board_filter: "",
      min_listing_days: 250,
      min_market_cap: 15,
      max_market_cap: 200,
      max_pe: 100,
      min_price: 3,
      max_price: 50,
      min_amount_20d: 3000,
      max_turnover_rate: 20,
      exclude_industries: ["银行"],
    },
  },
  {
    id: "defensive",
    name: "价值防御",
    desc: "低估值 · 消费医药公用 · 低换手",
    color: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    filters: {
      exclude_st: true,
      exclude_suspended: true,
      exclude_one_price_limit: true,
      board_filter: "main_board",
      min_listing_days: 750,
      min_market_cap: 50,
      max_market_cap: 2000,
      max_pe: 25,
      max_pb: 3,
      min_price: 8,
      max_price: 60,
      min_amount_20d: 15000,
      max_turnover_rate: 5,
      include_industries: ["食品饮料", "家用电器", "医药生物", "公用事业"],
    },
  },
];

interface FilterPanelProps {
  filters: DailyPipelineFilters;
  onSave: (filters: DailyPipelineFilters) => void;
  compact?: boolean;
}

export function FilterPanel({ filters: initial, onSave, compact }: FilterPanelProps) {
  const [filters, setFilters] = useState<DailyPipelineFilters>({ ...DEFAULT_FILTERS, ...initial });
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setFilters({ ...DEFAULT_FILTERS, ...initial });
    setDirty(false);
  }, [initial]);

  const update = <K extends keyof DailyPipelineFilters>(key: K, value: DailyPipelineFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
    setActivePreset(null); // manual edit clears preset highlight
  };

  const numVal = (key: keyof DailyPipelineFilters) => {
    const v = filters[key];
    return typeof v === "number" && v > 0 ? v : "";
  };

  const save = () => {
    const cleaned: DailyPipelineFilters = {};
    for (const [k, v] of Object.entries(filters)) {
      if (v === "" || v === 0 || v === false) continue;
      if (Array.isArray(v) && v.length === 0) continue;
      (cleaned as Record<string, unknown>)[k] = v;
    }
    onSave(cleaned);
    setDirty(false);
  };

  const reset = () => {
    setFilters({ ...DEFAULT_FILTERS });
    setDirty(true);
  };

  const [activePreset, setActivePreset] = useState<string | null>(null);

  const applyPreset = (preset: Preset) => {
    setFilters({ ...DEFAULT_FILTERS, ...preset.filters });
    setActivePreset(preset.id);
    setDirty(true);
  };

  const inputCls =
    "w-full rounded border border-stone-700 bg-stone-950 px-2.5 py-1.5 text-sm focus:border-teal-500 focus:outline-none";
  const labelCls = "mb-0.5 block text-xs text-stone-500";
  const groupTitle = "mb-1.5 text-xs font-semibold uppercase tracking-wider text-stone-500";

  return (
    <div className={compact ? "space-y-3" : "space-y-4"}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-stone-400">
          选择预设方案或自定义过滤条件，保存后每次运行自动应用
        </p>
        <div className="flex gap-2">
          <button
            onClick={reset}
            className="rounded border border-stone-700 px-2.5 py-1 text-xs text-stone-400 transition hover:bg-stone-800"
          >
            清空
          </button>
          <button
            onClick={save}
            disabled={!dirty}
            className="rounded bg-teal-500 px-3 py-1 text-xs font-semibold text-stone-950 transition hover:bg-teal-400 disabled:opacity-40"
          >
            保存
          </button>
        </div>
      </div>

      {/* Preset buttons */}
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            onClick={() => applyPreset(preset)}
            className={`flex flex-col items-start rounded-lg border px-3 py-2 text-left transition hover:brightness-110 ${
              activePreset === preset.id
                ? preset.color
                : "border-stone-700 bg-stone-900 text-stone-300 hover:border-stone-600"
            }`}
          >
            <span className="text-sm font-medium">{preset.name}</span>
            <span className={`text-xs ${activePreset === preset.id ? "opacity-80" : "text-stone-500"}`}>
              {preset.desc}
            </span>
          </button>
        ))}
      </div>

      {/* 基础过滤 + 板块 */}
      <div className={compact ? "grid gap-3 sm:grid-cols-2" : "space-y-3"}>
        <div>
          <p className={groupTitle}>基础过滤</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <Toggle label="排除 ST" checked={filters.exclude_st !== false} onChange={(v) => update("exclude_st", v)} />
            <Toggle label="排除停牌" checked={filters.exclude_suspended !== false} onChange={(v) => update("exclude_suspended", v)} />
            <Toggle label="排除一字板" checked={filters.exclude_one_price_limit !== false} onChange={(v) => update("exclude_one_price_limit", v)} />
          </div>
        </div>
        <div>
          <p className={groupTitle}>板块</p>
          <select
            value={filters.board_filter ?? ""}
            onChange={(e) => update("board_filter", e.target.value as DailyPipelineFilters["board_filter"])}
            className={inputCls}
          >
            <option value="">全部板块</option>
            <option value="main_board">仅主板</option>
            <option value="chinext_star">仅科创+创业</option>
          </select>
        </div>
      </div>

      {/* 价格与市值 */}
      <div>
        <p className={groupTitle}>价格与市值</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className={labelCls}>最低股价 (元)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("min_price")}
              onChange={(e) => update("min_price", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>最高股价 (元)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("max_price")}
              onChange={(e) => update("max_price", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>最低市值 (亿)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("min_market_cap")}
              onChange={(e) => update("min_market_cap", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>最高市值 (亿)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("max_market_cap")}
              onChange={(e) => update("max_market_cap", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
        </div>
      </div>

      {/* 估值与流动性 */}
      <div>
        <p className={groupTitle}>估值与流动性</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          <div>
            <label className={labelCls}>最高 PE</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("max_pe")}
              onChange={(e) => update("max_pe", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>最高 PB</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("max_pb")}
              onChange={(e) => update("max_pb", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>换手率 (%)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("max_turnover_rate")}
              onChange={(e) => update("max_turnover_rate", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>上市天数</label>
            <input type="number" min={0} step="1" placeholder="不限" value={numVal("min_listing_days")}
              onChange={(e) => update("min_listing_days", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>20日均额 (万)</label>
            <input type="number" min={0} step="any" placeholder="不限" value={numVal("min_amount_20d")}
              onChange={(e) => update("min_amount_20d", e.target.value === "" ? 0 : Number(e.target.value))} className={inputCls} />
          </div>
        </div>
      </div>

      {/* 行业 */}
      <div>
        <p className={groupTitle}>行业过滤</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <label className={labelCls}>包含行业（白名单，逗号分隔）</label>
            <input type="text" placeholder="新能源, 半导体, 医药"
              value={(filters.include_industries ?? []).join(", ")}
              onChange={(e) => update("include_industries", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
              className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>排除行业（黑名单）</label>
            <input type="text" placeholder="房地产, 银行"
              value={(filters.exclude_industries ?? []).join(", ")}
              onChange={(e) => update("exclude_industries", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
              className={inputCls} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-1.5 cursor-pointer">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5 rounded border-stone-600 bg-stone-900" />
      <span className="text-xs text-stone-300">{label}</span>
    </label>
  );
}
