/**
 * Typed API client — API_SPECIFICATION.md envelope, bearer access token in
 * memory (SECURITY §6: no tokens in localStorage), silent refresh via the
 * backend's httpOnly cookie. Types mirror backend/app/api/schemas.py (the
 * generated-from-Pydantic generation lands with the full make api-types task).
 */

const API_BASE = "/api/v1";

export interface ApiErrorBody {
  success: false;
  error: { code: string; message: string };
}

export interface Envelope<T> {
  success: true;
  data: T;
}

export type EnvelopeResult<T> = Envelope<T> | ApiErrorBody;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  /**
   * Error thrown when the envelope is success:false or the body is not JSON.
   *
   * Flow:
   *   Stores code, message, HTTP status. apiFetch may retry once on 401.
   *
   * Debug:
   *   code NETWORK_ERROR means res.json() failed. Other codes come from the API.
   */
  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

interface UserDto {
  id: string;
  mobile: string;
  email: string | null;
  full_name: string;
  totp_enabled: boolean;
}

export interface MeDto {
  user: UserDto;
  businesses: string[];
  firm: string | null;
}

export interface OtpRequestResult {
  otp_sent: boolean;
  dev_otp: string | null;
}

export interface OtpVerifyResult {
  user: UserDto;
  access_token: string;
  refresh_token: string;
}

export interface TotpSetupResult {
  secret: string;
  qr_uri: string;
}

export interface TotpVerifyResult {
  enabled: boolean;
}

export interface FirmCreateResult {
  id: string;
  firm_name: string;
  pan: string;
  ca_code: string;
}

let accessToken: string | null = null;
let refreshPromise: Promise<boolean> | null = null;

/**
 * Keep the access JWT in module memory (not localStorage).
 *
 * Debug:
 *   A full page reload drops it; shells call silentRefresh() first.
 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** True when an access JWT is currently in memory. */
export function hasAccessToken(): boolean {
  return accessToken !== null;
}

/**
 * Fetch /api/v1{path}, unwrap {success,data}, retry once after refresh on 401.
 *
 * Flow:
 *   1. Attach Bearer if accessToken is set; JSON content-type when there is a body.
 *   2. credentials include so the refresh cookie is sent.
 *   3. success true → return data.
 *   4. 401 with a token and allowRefreshRetry → refreshTokens then one retry.
 *   5. Otherwise throw ApiError.
 *
 * Debug:
 *   Second 401 is not retried (allowRefreshRetry false). Cookie path must be /api/v1/auth.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  allowRefreshRetry = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken !== null) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Include cookies on same-origin API calls so the httpOnly refresh token is
  // available to /auth/refresh and auth-state endpoints.
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const body = (await res.json().catch(() => null)) as EnvelopeResult<T> | null;
  if (body !== null && body.success === true) {
    return body.data;
  }
  const errBody = body as ApiErrorBody | null;
  const code = errBody?.error?.code ?? "NETWORK_ERROR";
  const message = errBody?.error?.message ?? `request failed (${res.status})`;
  const apiErr = new ApiError(code, message, res.status);
  if (allowRefreshRetry && apiErr.status === 401 && accessToken !== null) {
    try {
      await refreshTokens();
      return await apiFetch<T>(path, init, false);
    } catch {
      throw apiErr;
    }
  }
  throw apiErr;
}

/**
 * POST /auth/refresh using only the httpOnly cookie. Updates accessToken.
 *
 * Flow:
 *   1. POST {} with credentials include.
 *   2. success → store access_token.
 *   3. Else clear accessToken and throw ApiError.
 *
 * Debug:
 *   REFRESH_REUSE_DETECTED means an old refresh cookie was replayed; user must log in.
 */
async function refreshTokens(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
  });
  const body = (await res.json().catch(() => null)) as
    | Envelope<{ access_token: string }>
    | ApiErrorBody
    | null;
  if (body !== null && body.success === true) {
    accessToken = body.data.access_token;
    return;
  }
  accessToken = null;
  throw new ApiError(
    body?.error?.code ?? "REFRESH_FAILED",
    body?.error?.message ?? "session expired",
    res.status,
  );
}

/**
 * Single-flight refresh used on shell load. Returns true if an access token exists after.
 *
 * Flow:
 *   Reuse refreshPromise if a refresh is already running. Always clear it in finally.
 *
 * Debug:
 *   false → cookie missing or family killed. Do not treat the role cookie as logged-in.
 */
export async function silentRefresh(): Promise<boolean> {
  if (refreshPromise !== null) {
    return refreshPromise;
  }
  refreshPromise = (async () => {
    try {
      await refreshTokens();
      return accessToken !== null;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

/** POST /auth/otp/request. purpose REGISTER or LOGIN. Dev mode may return dev_otp. */
export async function requestOtp(
  identifier: string,
  purpose: "LOGIN" | "REGISTER",
): Promise<OtpRequestResult> {
  return apiFetch("/auth/otp/request", {
    method: "POST",
    body: JSON.stringify({ identifier, purpose }),
  });
}

/** POST /auth/otp/verify. Caller must setAccessToken from the returned access_token. */
export async function verifyOtp(
  identifier: string,
  otp: string,
): Promise<OtpVerifyResult> {
  return apiFetch("/auth/otp/verify", {
    method: "POST",
    body: JSON.stringify({ identifier, otp }),
  });
}

/** GET /auth/me. Needs a bearer token. firm null means client shell. */
export async function fetchMe(): Promise<MeDto> {
  return apiFetch("/auth/me");
}

/** POST /auth/totp/setup. Returns secret and otpauth qr_uri. 409 if already enabled. */
export async function totpSetup(): Promise<TotpSetupResult> {
  return apiFetch("/auth/totp/setup", { method: "POST" });
}

/** POST /auth/totp/verify. Sets totp_enabled_at when the 6-digit code matches. */
export async function totpVerify(code: string): Promise<TotpVerifyResult> {
  return apiFetch("/auth/totp/verify", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

/** POST /firm. Requires TOTP already enabled or the API returns TOTP_REQUIRED. */
export async function createFirm(
  firmName: string,
  pan: string,
): Promise<FirmCreateResult> {
  return apiFetch("/firm", {
    method: "POST",
    body: JSON.stringify({ firm_name: firmName, pan }),
  });
}