/**
 * Centralized TanStack Query key factories.
 *
 * Query keys used to be inlined as array literals (["plans"], ["holdings"], ...)
 * across 8+ files, so a typo in one place silently broke cache invalidation
 * in another. Route all useQuery / invalidateQueries call sites through these
 * factories to get a single source of truth.
 */
export const queryKeys = {
  plans: () => ["plans"] as const,
  holdings: () => ["holdings"] as const,
  runs: () => ["runs"] as const,
  runsWatchlist: () => ["runs", "watchlist"] as const,
  config: () => ["config"] as const,
  providers: () => ["providers"] as const,
  profile: () => ["profile"] as const,
  health: () => ["health"] as const,
  reflectionsSummary: () => ["reflections-summary"] as const,
  dashboardArtifacts: () => ["dashboard-artifacts"] as const,
  strategyLessons: () => ["strategy-lessons"] as const,
  reflectionCases: (status = "pending") => ["reflection-cases", status] as const,
  artifacts: (type?: string, query?: string, runId?: string) =>
    ["artifacts", type, query, runId] as const,
  artifact: (id: string) => ["artifact", id] as const,
  artifactVersions: (id: string) => ["artifact-versions", id] as const,
};
