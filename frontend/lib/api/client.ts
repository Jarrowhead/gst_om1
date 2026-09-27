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

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

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

/** Silent refresh via the backend's httpOnly cookie (no token in storage). */
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

export async function requestOtp(
  identifier: string,
  purpose: "LOGIN" | "REGISTER",
): Promise<OtpRequestResult> {
  return apiFetch("/auth/otp/request", {
    method: "POST",
    body: JSON.stringify({ identifier, purpose }),
  });
}

export async function verifyOtp(
  identifier: string,
  otp: string,
): Promise<OtpVerifyResult> {
  return apiFetch("/auth/otp/verify", {
    method: "POST",
    body: JSON.stringify({ identifier, otp }),
  });
}

export async function fetchMe(): Promise<MeDto> {
  return apiFetch("/auth/me");
}

export async function totpSetup(): Promise<TotpSetupResult> {
  return apiFetch("/auth/totp/setup", { method: "POST" });
}

export async function totpVerify(code: string): Promise<TotpVerifyResult> {
  return apiFetch("/auth/totp/verify", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function createFirm(
  firmName: string,
  pan: string,
): Promise<FirmCreateResult> {
  return apiFetch("/firm", {
    method: "POST",
    body: JSON.stringify({ firm_name: firmName, pan }),
  });
}