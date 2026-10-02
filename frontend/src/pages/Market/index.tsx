import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Sparkles } from "lucide-react";
import {
  createRun,
  getRun,
  getMarketOverview,
  listHoldings,
  type TaggedNewsItem,
} from "../../api/client";
import { queryKeys } from "@/api/queryKeys";
import { dailyPipelineHint, useGoChat } from "@/lib/chatNav";
import { RegimeBanner } from "../../components/Market/RegimeBanner";
import { MarketKpiRow } from "../../components/Market/MarketKpiRow";
import { IndustryMatrix } from "../../components/Market/IndustryMatrix";
import { NewsFeed } from "../../components/Market/NewsFeed";
import { EventCalendarStrip } from "../../components/Market/EventCalendarStrip";

const REFRESH_POLL_INTERVAL_MS = 5_000;
const REFRESH_MAX_WAIT_MS = 5 * 60_000;
const REFRESH_MAX_POLL_ERRORS = 3;
const REFRESH_STATUS_REQUEST_TIMEOUT_MS = 10_000;

async function getRunWithTimeout(runId: string) {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      getRun(runId),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(
          () => reject(new Error("状态查询超时")),
          REFRESH_STATUS_REQUEST_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

export default function Market() {
  const goChat = useGoChat();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [trackedRunId, setTrackedRunId] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshStartedAt = useRef<number | null>(null);
  const consecutivePollErrors = useRef(0);

  const overviewQuery = useQuery({
    queryKey: queryKeys.marketOverview(),
    queryFn: getMarketOverview,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const holdingsQuery = useQuery({
    queryKey: queryKeys.holdings(),
    queryFn: listHoldings,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  // Poll the exact run instead of searching a truncated recent-runs list.
  // Stop on a terminal status, repeated API failures, or an overall deadline
  // so a disconnected backend can never leave the refresh button spinning.
  useEffect(() => {
    if (!refreshing || !trackedRunId) return;
    let pollInFlight = false;

    const stopRefreshing = (error?: string) => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = null;
      setRefreshing(false);
      setTrackedRunId(null);
      refreshStartedAt.current = null;
      consecutivePollErrors.current = 0;
      if (error) setRefreshError(error);
    };

    pollTimer.current = setInterval(async () => {
      if (pollInFlight) return;
      pollInFlight = true;
      try {
        const startedAt = refreshStartedAt.current ?? Date.now();
        if (Date.now() - startedAt >= REFRESH_MAX_WAIT_MS) {
          stopRefreshing("市场全景生成超过 5 分钟，已停止等待。后端任务可能仍在运行，请稍后重试。");
          return;
        }

        const run = await getRunWithTimeout(trackedRunId);
        consecutivePollErrors.current = 0;
        if (["completed", "failed", "cancelled"].includes(run.status)) {
          const terminalError =
            run.status === "failed"
              ? run.error || "生成失败"
              : run.status === "cancelled"
                ? "生成已取消"
                : undefined;
          stopRefreshing(terminalError);
          await queryClient.invalidateQueries({ queryKey: queryKeys.marketOverview() });
          await queryClient.invalidateQueries({ queryKey: queryKeys.runs() });
        }
      } catch (exc) {
        consecutivePollErrors.current += 1;
        if (consecutivePollErrors.current >= REFRESH_MAX_POLL_ERRORS) {
          const detail = exc instanceof Error ? `：${exc.message}` : "";
          stopRefreshing(`连续 3 次无法获取生成状态${detail}`);
        }
      } finally {
        pollInFlight = false;
      }
    }, REFRESH_POLL_INTERVAL_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [refreshing, trackedRunId, queryClient]);

  const refresh = async () => {
    setRefreshError(null);
    setRefreshing(true);
    refreshStartedAt.current = Date.now();
    consecutivePollErrors.current = 0;
    try {
      const run = await createRun("market_overview", {});
      setTrackedRunId(run.id);
    } catch (exc) {
      setRefreshing(false);
      refreshStartedAt.current = null;
      setRefreshError(exc instanceof Error ? exc.message : "触发失败");
    }
  };

  const askNews = (item: TaggedNewsItem) => {
    const summary = item.interpretation ? `（解读：${item.interpretation}）` : "";
    goChat({
      prompt: `这条新闻对市场有什么影响：${item.title}${summary}`,
      autoSend: true,
      context: {
        news_context: {
          title: item.title,
          interpretation: item.interpretation,
          polarity: item.polarity,
          impact_scope: item.impact_scope,
          industries: item.industries,
          symbols: item.symbols,
        },
      },
    });
  };

  const data = overviewQuery.data;
  const payload = data?.artifact?.payload;
  const holdings = (holdingsQuery.data ?? []).map((h) => ({ symbol: h.symbol, name: h.name }));

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 border-b border-ui-line pb-5 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-ui-accent">
            Market panorama
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-ui-ink">市场全景</h2>
          <p className="mt-1 max-w-2xl text-sm text-ui-muted">
            大盘体制 · 机构行业与热点主题 · 要闻解读 · 大事日历
          </p>
        </div>
        {refreshError && (
          <span className="rounded border border-ui-danger/30 bg-ui-danger/10 px-2 py-1 text-xs text-ui-danger">
            {refreshError}
          </span>
        )}
      </div>

      {overviewQuery.isLoading ? (
        <div className="flex items-center justify-center py-24">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-ui-accent border-t-transparent" />
        </div>
      ) : !data?.available || !payload ? (
        /* Empty state: never generated yet */
        <div className="rounded-lg border border-dashed border-ui-strong bg-ui-panel px-4 py-16 text-center">
          <Globe className="mx-auto h-10 w-10 text-ui-faint" />
          <p className="mt-4 text-sm font-medium text-ui-body">还没有市场全景数据</p>
          <p className="mt-1 text-xs text-ui-faint">
            生成一次约需 1-2 分钟（串行抓取市场数据 + AI 汇总），之后每个交易日收盘后自动更新
          </p>
          <button
            onClick={refresh}
            disabled={refreshing}
            className="mt-4 inline-flex items-center gap-2 rounded-lg border border-ui-accent/40 bg-ui-accent/10 px-4 py-2 text-sm font-semibold text-ui-accent transition hover:bg-ui-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            {refreshing ? "生成中，请稍候" : "立即生成"}
          </button>
        </div>
      ) : (
        <>
          <RegimeBanner
            regime={payload.regime}
            asofDate={payload.market_asof_date}
            generatedAt={payload.generated_at}
            isStale={data.is_stale}
            refreshing={refreshing}
            onRefresh={refresh}
          />
          <MarketKpiRow
            indices={payload.market_data.indices}
            breadth={payload.market_data.breadth}
            northbound={payload.market_data.northbound}
            turnoverAmount={payload.market_data.turnover_amount}
          />
          <div className="grid gap-5 xl:grid-cols-3">
            <IndustryMatrix
              stances={payload.industry_stances}
              onDeepDive={(industry) => goChat({ prompt: `分析今天${industry}板块的驱动逻辑和持续性`, autoSend: true })}
              onPickStocks={(row) => {
                const selectionIndustries = row.selection_industries?.length
                  ? row.selection_industries
                  : row.selection_concept ? [] : [row.industry];
                const selectionConcepts = row.selection_concept
                  ? [row.selection_concept]
                  : [];
                const scope = row.selection_concept
                  ? `只看${row.industry}概念成分股`
                  : row.selection_mode === "proxy"
                  ? `围绕${row.industry}板块（MCP行业口径：${selectionIndustries.join("/")}）`
                  : `只看${row.industry}板块`;
                goChat({
                  prompt: `每日选股 top 5，${scope}`,
                  autoSend: true,
                  intentHint: dailyPipelineHint(
                    5,
                    selectionIndustries,
                    selectionConcepts,
                    row.selection_industry_codes?.length
                      ? {
                          taxonomy: row.selection_taxonomy ?? row.taxonomy,
                          level: row.selection_level ?? row.industry_level,
                          codes: row.selection_industry_codes,
                        }
                      : undefined,
                  ),
                });
              }}
            />
            <NewsFeed
              news={payload.news}
              holdings={holdings}
              onAsk={askNews}
            />
          </div>
          <EventCalendarStrip
            macro={payload.events.macro}
            unlocks={payload.events.unlocks}
            holdings={holdings}
          />
        </>
      )}
    </div>
  );
}
