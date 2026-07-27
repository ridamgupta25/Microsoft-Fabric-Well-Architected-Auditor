/**
 * Read-only Microsoft Entra sign-in.
 *
 * The browser only ever holds an opaque session id — the Fabric access token
 * stays on the server. Sign-in is asynchronous: start a flow, then poll until it
 * completes.
 */
import { apiClient } from "./apiClient";
import type { SessionResponse, SessionStatusResponse } from "@/types/api";

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

export async function pollLogin(session: string): Promise<SessionStatusResponse> {
  const { data } = await apiClient.get<SessionStatusResponse>(`/login/${session}`);
  return data;
}

export async function logout(session: string): Promise<void> {
  await apiClient.post("/logout", null, { params: { session } });
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
