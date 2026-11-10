// Minimal typed API client.
//
// Security model: the short-lived access token lives only in memory (this module + React state),
// never in localStorage. The long-lived refresh token is an httpOnly cookie scoped to
// /api/v1/auth that JavaScript cannot read. On a 401 the client refreshes once (single-flight, so
// concurrent requests don't trigger the server's refresh-token reuse detection) and retries.

export const API = "/api/v1";

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown; request_id?: string | null };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshing: Promise<string | null> | null = null;
let onSessionExpired: (() => void) | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken() {
  return accessToken;
}

export function setSessionExpiredHandler(handler: (() => void) | null) {
  onSessionExpired = handler;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as ApiErrorBody;
    if (body?.error) return new ApiError(res.status, body.error.code, body.error.message, body.error.details);
  } catch {
    /* non-JSON error */
  }
  return new ApiError(res.status, `http_${res.status}`, res.statusText || "Request failed");
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Exchange the refresh cookie for a new access token. Returns null only when the session is
 * really over (401/403). Transient failures — rate limiting, server errors, network — are retried
 * briefly instead of signing the user out.
 */
export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= (async () => {
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        let res: Response;
        try {
          res = await fetch(`${API}/auth/refresh`, { method: "POST", credentials: "same-origin" });
        } catch {
          await sleep(500 * 2 ** attempt); // network error
          continue;
        }
        if (res.ok) {
          const body = (await res.json()) as { access_token: string };
          accessToken = body.access_token;
          return accessToken;
        }
        if (res.status === 429 || res.status >= 500) {
          const retryAfter = Number(res.headers.get("retry-after")) || 2 ** attempt;
          await sleep(Math.min(retryAfter, 5) * 1000);
          continue;
        }
        accessToken = null; // 401/403: no valid session
        return null;
      }
      return null;
    } finally {
      // Let the next expiry trigger a new refresh.
      setTimeout(() => {
        refreshing = null;
      }, 0);
    }
  })();
  return refreshing;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Return the raw Response (downloads, streaming) instead of parsed JSON. */
  raw?: boolean;
  /** Don't attempt a token refresh on 401 (used by auth endpoints themselves). */
  noRefresh?: boolean;
}

export async function apiFetch<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, raw, noRefresh, headers, ...init } = options;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;

  const send = (token: string | null) =>
    fetch(path.startsWith("/") ? path : `${API}/${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        ...(body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    });

  let res = await send(accessToken);
  if (res.status === 401 && !noRefresh) {
    const token = await refreshAccessToken();
    if (token) {
      res = await send(token);
    } else {
      onSessionExpired?.();
    }
  }
  if (!res.ok) throw await toError(res);
  if (raw) return res as unknown as T;
  const text = await res.text(); // empty for 204 / bodiless 202
  return (text ? JSON.parse(text) : undefined) as T;
}

const RATE_LIMIT_RETRIES = 3;
const MAX_RETRY_WAIT_SECONDS = 60;

/** Multipart upload with progress (fetch has no upload progress events).
 *
 * A 429 (upload rate limit) is retried after the server's Retry-After, so a large batch of
 * one-file requests slows down instead of failing.
 */
export function uploadWithProgress<T>(
  path: string,
  form: FormData,
  onProgress: (fraction: number) => void,
): Promise<T> {
  const attempt = (token: string | null) =>
    new Promise<{ status: number; body: string; retryAfter: number }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API}/${path}`);
      xhr.withCredentials = true;
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () =>
        resolve({
          status: xhr.status,
          body: xhr.responseText,
          retryAfter: Number(xhr.getResponseHeader("Retry-After")) || 5,
        });
      xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error during upload"));
      xhr.send(form);
    });

  return (async () => {
    let result = await attempt(accessToken);
    for (let retry = 0; result.status === 429 && retry < RATE_LIMIT_RETRIES; retry++) {
      onProgress(0);
      const waitSeconds = Math.min(result.retryAfter, MAX_RETRY_WAIT_SECONDS);
      await new Promise((r) => setTimeout(r, waitSeconds * 1000));
      result = await attempt(accessToken);
    }
    if (result.status === 401) {
      const token = await refreshAccessToken();
      if (!token) {
        onSessionExpired?.();
        throw new ApiError(401, "not_authenticated", "Your session has expired");
      }
      result = await attempt(token);
    }
    const parsed = result.body ? JSON.parse(result.body) : undefined;
    if (result.status >= 400) {
      const err = (parsed as ApiErrorBody | undefined)?.error;
      throw new ApiError(result.status, err?.code ?? `http_${result.status}`, err?.message ?? "Upload failed", err?.details);
    }
    return parsed as T;
  })();
}

/** Fetch a protected file (download / preview) as a Blob with the auth header. */
export async function fetchBlob(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const res = await apiFetch<Response>(path, { raw: true });
  const disposition = res.headers.get("content-disposition") ?? "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const plain = /filename="([^"]+)"/i.exec(disposition);
  const filename = star ? decodeURIComponent(star[1]) : (plain?.[1] ?? null);
  return { blob: await res.blob(), filename };
}

export async function downloadFile(path: string, fallbackName: string) {
  const { blob, filename } = await fetchBlob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const problems = (error.details as { problems?: string[] } | undefined)?.problems;
    return problems?.length ? `${error.message}: ${problems.join(", ")}` : error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}
