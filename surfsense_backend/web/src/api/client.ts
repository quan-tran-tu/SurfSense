import { getState, setState } from "../store";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

/** The backend speaks {"detail": ...} on errors; fall back to the raw body. */
async function errorDetail(res: Response): Promise<string> {
  const body = await res.text();
  try {
    const j = JSON.parse(body);
    return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
  } catch { return body || `HTTP ${res.status}`; }
}

/** backend is "" when the API shares this page's origin — name it anyway. */
const backendName = () => getState().backend || location.origin;

export interface ApiOpts {
  json?: unknown;
  body?: BodyInit;
  signal?: AbortSignal;
}

/**
 * Trade the refresh cookie for a new session cookie. The access cookie lives an
 * hour, the refresh cookie two weeks (sliding), so this is what keeps a user
 * signed in across reloads without the page ever keeping their password.
 * Concurrent 401s share one attempt: the server rotates the refresh token, so a
 * second parallel exchange would present an already-used one.
 */
let refreshing: Promise<boolean> | null = null;
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch(getState().backend + "/auth/jwt/refresh", { method: "POST", credentials: "include" })
    .then((r) => r.ok, () => false)
    .finally(() => { setTimeout(() => { refreshing = null; }, 0); });
  return refreshing;
}

/** Called when the session is gone for good; the app returns to the sign-in screen. */
let onSessionLost: () => void = () => {};
export const setSessionLostHandler = (fn: () => void) => { onSessionLost = fn; };

/**
 * Every request carries the session cookie (credentials: "include"). The browser
 * sets Origin, which is what the backend's CSRF check reads — hence the page must
 * be served from an allow-listed origin.
 *
 * On 401 the session cookie has expired: refresh it and retry once; failing
 * that, sign in again with the password if this page still holds it (it does
 * only until a reload). Neither working means the user must sign in again.
 */
export async function api(method: string, path: string, opts: ApiOpts = {}): Promise<Response> {
  const send = () => fetch(getState().backend + path, {
    method,
    credentials: "include",
    headers: opts.json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body,
    signal: opts.signal,
  });

  let res: Response;
  try { res = await send(); }
  catch { throw new ApiError(0, `cannot reach ${backendName()} — is the backend up?`); }

  if (res.status === 401) {
    const { email, password } = getState();
    let renewed = await refreshSession();
    if (!renewed && password) {
      try { await login(email, password); renewed = true; } catch { /* reported below */ }
    }
    if (!renewed) {
      onSessionLost();
      throw new ApiError(401, "Your session has expired. Sign in again.");
    }
    try { res = await send(); }
    catch { throw new ApiError(0, `cannot reach ${backendName()}`); }
  }
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  return res;
}

export async function apiJson<T = unknown>(method: string, path: string, opts?: ApiOpts): Promise<T> {
  const res = await api(method, path, opts);
  return (res.status === 204 ? null : await res.json()) as T;
}

/** Called when an admin route answers 403: this user is no longer an admin. */
let onAdminLost: () => void = () => {};
export const setAdminLostHandler = (fn: () => void) => { onAdminLost = fn; };

export async function adminApi<T = unknown>(method: string, path: string, opts?: ApiOpts): Promise<T> {
  try {
    return await apiJson<T>(method, `/api/v1/admin${path}`, opts);
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) {
      onAdminLost();
      throw new ApiError(403, "Your admin access has been removed.");
    }
    throw e;
  }
}

export async function login(email: string, password: string) {
  const res = await fetch(getState().backend + "/auth/jwt/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  }).catch(() => { throw new ApiError(0, `cannot reach ${backendName()}`); });

  if (res.status === 400) throw new ApiError(400, "Bad credentials. Register first, or check the password.");
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
}

export async function register(email: string, password: string): Promise<"registered" | "exists"> {
  const res = await fetch(getState().backend + "/auth/register", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).catch(() => { throw new ApiError(0, `cannot reach ${backendName()}`); });

  if (res.ok) return "registered";
  const detail = await errorDetail(res);
  if (res.status === 400 && detail.includes("REGISTER_USER_ALREADY_EXISTS")) return "exists";
  if (res.status === 403) throw new ApiError(403, "Registration is disabled on this backend (AUTH_TYPE=GOOGLE, or REGISTRATION_ENABLED off).");
  if (res.status === 422) throw new ApiError(422, "Invalid email, or password too short (min. 8 characters).");
  if (res.status === 429) throw new ApiError(429, "Rate limited by the backend. Wait, then retry.");
  throw new ApiError(res.status, detail);
}

/**
 * End the session on the server too — revoking the refresh token and clearing
 * both cookies — or the reload below would simply sign the user back in.
 */
export async function signOut() {
  await fetch(getState().backend + "/auth/jwt/revoke", { method: "POST", credentials: "include" }).catch(() => {});
  setState({ password: "" });
  location.reload();
}
