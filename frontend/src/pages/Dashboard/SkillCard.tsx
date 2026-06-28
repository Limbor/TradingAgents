import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createRun, type SkillInfo } from "../../api/client";
import { Briefcase, CalendarDays, Play, Radar, TrendingUp } from "lucide-react";

interface SkillCardProps {
  skill: SkillInfo;
}

export function SkillCard({ skill }: SkillCardProps) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [ticker, setTicker] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().split("T")[0]!);
  const [portfolioAction, setPortfolioAction] = useState("analyze");
  const [quantity, setQuantity] = useState("100");
  const [avgCost, setAvgCost] = useState("100");
  const [currentPrice, setCurrentPrice] = useState("");
  const [market, setMarket] = useState("cn_a");
  const [minScore, setMinScore] = useState("65");

  const handleStart = async () => {
    if (skill.id === "stock_analysis" && !ticker.trim()) return;
    setLoading(true);
    try {
      const run = await createRun(skill.id, buildParams());
      navigate(`/analysis/${run.id}`);
    } catch (e) {
      alert(`Failed to start: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const buildParams = () => {
    if (skill.id === "portfolio_management") {
      const params: Record<string, unknown> = { action: portfolioAction };
      if (ticker.trim()) params.symbol = ticker.trim().toUpperCase();
      if (portfolioAction === "upsert") {
        params.quantity = Number(quantity);
        params.avg_cost = Number(avgCost);
        if (currentPrice.trim()) params.current_price = Number(currentPrice);
      }
      return params;
    }
    if (skill.id === "market_scanner") {
      return {
        market,
        min_score: Number(minScore),
        limit: 5,
      };
    }
    return {
      ticker: ticker.trim().toUpperCase(),
      analysis_date: date,
    };
  };

  const Icon =
    skill.id === "portfolio_management"
      ? Briefcase
      : skill.id === "market_scanner"
        ? Radar
        : TrendingUp;

  return (
    <div className="rounded-lg border border-stone-800 bg-stone-900 p-4 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold text-stone-50">{skill.name}</h4>
          <p className="mt-1 text-xs text-stone-500">{skill.category} &middot; v{skill.version}</p>
        </div>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
          <Icon className="h-4 w-4 text-teal-300" />
        </div>
      </div>
      <p className="mb-4 min-h-10 text-sm leading-5 text-stone-400">{skill.description}</p>

      {showForm ? (
        <div className="space-y-3">
          {skill.id === "market_scanner" ? (
            <>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-stone-400">Market</span>
                <select
                  value={market}
                  onChange={(e) => setMarket(e.target.value)}
                  className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
                >
                  <option value="cn_a">A-share</option>
                  <option value="us">US</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-stone-400">Minimum score</span>
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={minScore}
                  onChange={(e) => setMinScore(e.target.value)}
                  className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
                />
              </label>
            </>
          ) : skill.id === "portfolio_management" ? (
            <>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-stone-400">Action</span>
                <select
                  value={portfolioAction}
                  onChange={(e) => setPortfolioAction(e.target.value)}
                  className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
                >
                  <option value="analyze">Analyze</option>
                  <option value="list">List</option>
                  <option value="upsert">Add / Update</option>
                  <option value="delete">Delete</option>
                </select>
              </label>
              {portfolioAction !== "analyze" && portfolioAction !== "list" && (
                <TickerInput ticker={ticker} setTicker={setTicker} />
              )}
              {portfolioAction === "upsert" && (
                <div className="grid grid-cols-3 gap-2">
                  <SmallInput label="Qty" value={quantity} setValue={setQuantity} />
                  <SmallInput label="Cost" value={avgCost} setValue={setAvgCost} />
                  <SmallInput label="Price" value={currentPrice} setValue={setCurrentPrice} />
                </div>
              )}
            </>
          ) : (
            <>
              <TickerInput ticker={ticker} setTicker={setTicker} />
              <label className="block">
                <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-stone-400">
                  <CalendarDays className="h-3.5 w-3.5" />
                  Analysis date
                </span>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
                />
              </label>
            </>
          )}
          <div className="flex gap-2">
            <button
              onClick={handleStart}
              disabled={loading || (skill.id === "stock_analysis" && !ticker.trim())}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play className="h-4 w-4" />
              {loading ? "Starting..." : "Start"}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="rounded-lg border border-stone-700 px-3 py-2 text-sm text-stone-300 transition hover:bg-stone-800"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowForm(true)}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-stone-700 bg-stone-950 px-4 py-2 text-sm font-semibold text-stone-100 transition hover:border-teal-500/60 hover:bg-stone-800"
        >
          <Play className="h-4 w-4 text-teal-300" />
          Run Analysis
        </button>
      )}
    </div>
  );
}

function TickerInput({
  ticker,
  setTicker,
}: {
  ticker: string;
  setTicker: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-stone-400">Ticker</span>
      <input
        type="text"
        placeholder="AAPL or 600519.SH"
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
        className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
      />
    </label>
  );
}

function SmallInput({
  label,
  value,
  setValue,
}: {
  label: string;
  value: string;
  setValue: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-stone-400">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full rounded-lg border border-stone-700 bg-stone-950 px-2 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
      />
    </label>
  );
}
