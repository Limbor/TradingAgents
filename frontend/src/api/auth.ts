/**
 * API auth token handling.
 *
 * When the backend has `api_auth_token` configured (env
 * TRADINGAGENTS_API_AUTH_TOKEN), REST requests must carry
 * `Authorization: Bearer <token>` and WebSocket URLs must append `?token=`.
 * The token is stored in localStorage so it survives reloads and is read on
 * every request. When unset (the local-desktop default), no auth header/query
 * is added and the backend runs in open mode.
 */

const STORAGE_KEY = "tradingagents_api_auth_token";

export function getAuthToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAuthToken(token: string): void {
  try {
    const trimmed = token.trim();
    if (trimmed) {
      localStorage.setItem(STORAGE_KEY, trimmed);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // localStorage may be unavailable (private mode); auth just won't persist.
  }
}

/** Build the Authorization header object if a token is set, else empty. */
export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Append `?token=` (or `&token=`) to a URL if a token is set. */
export function withTokenQuery(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
