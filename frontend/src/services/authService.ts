/**
 * Read-only Microsoft Entra sign-in.
 *
 * The browser only ever holds an opaque session id — the Fabric access token
 * stays on the server. Sign-in is asynchronous: start a flow, then poll until it
 * completes.
 */
import { apiClient } from "./apiClient";
import type {
  AuthorizeResponse,
  LoginConfig,
  SessionResponse,
  SessionStatusResponse,
  UserProfile,
} from "@/types/api";

/** Which sign-in methods the server offers (e.g. whether redirect SSO is set up). */
export async function getLoginConfig(): Promise<LoginConfig> {
  const { data } = await apiClient.get<LoginConfig>("/login/config");
  return data;
}

/**
 * Begin the redirect (Authorization Code) sign-in.
 *
 * Returns the Microsoft URL to send the browser to. After the user signs in,
 * Microsoft redirects to `redirectUri` (a route this app serves) with a code,
 * which {@link completeAuthCodeLogin} exchanges server-side. The token never
 * reaches the browser.
 */
export async function startAuthCodeLogin(redirectUri: string): Promise<AuthorizeResponse> {
  const { data } = await apiClient.post<AuthorizeResponse>("/login/authorize", {
    redirect_uri: redirectUri,
  });
  return data;
}

/** Complete the redirect sign-in by handing the callback's query params to the server. */
export async function completeAuthCodeLogin(
  authResponse: Record<string, string>,
): Promise<SessionResponse> {
  const { data } = await apiClient.post<SessionResponse>("/login/callback", {
    auth_response: authResponse,
  });
  return data;
}

export async function startInteractiveLogin(params: {
  email?: string;
  tenantId?: string;
  clientId?: string;
}): Promise<SessionResponse> {
  const { data } = await apiClient.post<SessionResponse>("/login", {
    email: params.email ?? null,
    tenant_id: params.tenantId ?? null,
    client_id: params.clientId ?? null,
  });
  return data;
}

export async function loginWithAzureCli(): Promise<SessionResponse> {
  const { data } = await apiClient.post<SessionResponse>("/login/azure-cli");
  return data;
}

/**
 * Start a device-code sign-in — the browser-based flow for a hosted/remote app.
 *
 * Returns a short `user_code` and a `verification_uri`; the user opens that URI
 * in their **own** browser and enters the code. The token is acquired and kept
 * on the server, so the browser still only ever holds the session id. Poll with
 * {@link waitForSignIn} until it completes.
 */
export async function startDeviceCodeLogin(params?: {
  tenantId?: string;
  clientId?: string;
}): Promise<SessionResponse> {
  const { data } = await apiClient.post<SessionResponse>("/login/device-code", {
    tenant_id: params?.tenantId ?? null,
    client_id: params?.clientId ?? null,
    scopes: [],
  });
  return data;
}

export async function pollLogin(session: string): Promise<SessionStatusResponse> {
  const { data } = await apiClient.get<SessionStatusResponse>(`/login/${session}`);
  return data;
}

export async function logout(session: string): Promise<void> {
  await apiClient.post("/logout", null, { params: { session } });
}

/** The signed-in user's display identity for a session. Never a token. */
export async function getMe(session: string): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>("/me", { params: { session } });
  return data;
}

/**
 * Poll a sign-in session until it resolves.
 *
 * Resolves with the session id on success and throws on failure, so callers can
 * `await` a sign-in as a single operation.
 */
export async function waitForSignIn(
  session: string,
  signal?: AbortSignal,
  intervalMs = 1500,
  timeoutMs = 300_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    if (signal?.aborted) throw new DOMException("Sign-in aborted", "AbortError");

    const { status, error } = await pollLogin(session);
    if (status === "done") return session;
    if (status === "error") throw new Error(error ?? "Sign-in failed.");

    if (Date.now() > deadline) throw new Error("Sign-in timed out.");
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
