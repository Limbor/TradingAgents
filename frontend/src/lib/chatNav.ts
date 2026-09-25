import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Single source of truth for page → Chat jump contracts.
 *
 * Every navigation into /chat goes through `useGoChat` with a `ChatNavState`:
 * - `prompt` is what the user sees in the input / message list;
 * - `intentHint` deterministically names the target skill so the backend can
 *   skip LLM routing entirely (`{skill_id, params}` validated server-side,
 *   degrading to normal text routing on any mismatch);
 * - `context` carries structured page data (holding / selection / news /
 *   risk event) the text itself cannot express;
 * - `nonce` de-duplicates autoSend across renders while still allowing the
 *   same prompt to be sent twice via two separate navigations.
 */

export interface IntentHint {
  skill_id: string;
  params: Record<string, unknown>;
}

export interface ChatContext {
  holding_context?: Record<string, unknown>;
  selection_context?: Record<string, unknown>;
  news_context?: Record<string, unknown>;
  risk_event_context?: Record<string, unknown>;
}

export interface ChatNavState {
  prompt: string;
  autoSend?: boolean;
  context?: ChatContext;
  intentHint?: IntentHint;
  nonce: string;
}

/** Navigate to /chat with a fresh nonce so repeated jumps always fire. */
export function useGoChat() {
  const navigate = useNavigate();
  return useCallback(
    (state: Omit<ChatNavState, "nonce">) => {
      navigate("/chat", { state: { ...state, nonce: crypto.randomUUID() } });
    },
    [navigate],
  );
}

/* --- Semantic intent hint builders (new entries should only add here) --- */

export function analyzeStockHint(symbol: string): IntentHint {
  return { skill_id: "stock_analysis", params: { ticker: symbol } };
}

export function dailyPipelineHint(
  limit: number,
  industries?: string[],
  concepts?: string[],
  industryScope?: {
    taxonomy?: string;
    level?: string;
    codes?: string[];
  },
): IntentHint {
  const params: Record<string, unknown> = { limit };
  if (industries && industries.length > 0) params.industries = industries;
  if (concepts && concepts.length > 0) params.concepts = concepts;
  if (industryScope?.taxonomy) params.industry_taxonomy = industryScope.taxonomy;
  if (industryScope?.level) params.industry_level = industryScope.level;
  if (industryScope?.codes?.length) params.industry_codes = industryScope.codes;
  return { skill_id: "daily_pipeline", params };
}

export function dailyReviewHint(
  dailyLimit = 5,
  candidateLimit = 120,
): IntentHint {
  return {
    skill_id: "daily_review",
    params: { daily_limit: dailyLimit, candidate_limit: candidateLimit },
  };
}

export function positionAdviceHint(symbol: string, intent = "review"): IntentHint {
  return { skill_id: "position_advisor", params: { symbol, intent } };
}

export function riskMonitorHint(): IntentHint {
  return { skill_id: "risk_monitor", params: {} };
}

export function marketScannerHint(limit: number, minScore?: number): IntentHint {
  const params: Record<string, unknown> = { limit };
  if (minScore !== undefined) params.min_score = minScore;
  return { skill_id: "market_scanner", params };
}
