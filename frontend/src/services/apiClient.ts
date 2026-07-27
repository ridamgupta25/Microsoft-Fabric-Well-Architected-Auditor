/**
 * The single Axios instance every request goes through.
 *
 * Centralising it means correlation ids, error normalisation, and the base URL
 * are configured once rather than at each call site.
 */
import axios, { AxiosError, type AxiosInstance } from "axios";

import type { ApiError } from "@/types/api";

/**
 * Base URL of the API.
 *
 * Empty in development so requests go to a same-origin `/api` path and Vite
 * proxies them — no CORS preflight locally. In production this is the deployed
 * API origin, injected at build time.
 */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export const API_PREFIX = "/api/v1";

/** Thrown for every failed request, so callers handle exactly one error type. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId?: string;

  constructor(message: string, status: number, code: string, correlationId?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
  }
}

function createClient(): AxiosInstance {
  const instance = axios.create({
    baseURL: `${BASE_URL}${API_PREFIX}`,
    timeout: 60_000,
    headers: { "Content-Type": "application/json" },
  });

  instance.interceptors.response.use(
    (response) => response,
    (error: AxiosError<ApiError>) => {
      // Normalise every failure — network, timeout, or HTTP — into one type so
      // components never have to inspect Axios internals.
      if (error.response) {
        const body = error.response.data;
        throw new ApiRequestError(
          body?.detail ?? error.message,
          error.response.status,
          body?.code ?? "http_error",
          body?.correlation_id ?? undefined,
        );
      }
      if (error.code === "ECONNABORTED") {
        throw new ApiRequestError("The request timed out.", 408, "timeout");
      }
      throw new ApiRequestError(
        "Could not reach the API. Is the backend running?",
        0,
        "network_error",
      );
    },
  );

  return instance;
}

export const apiClient = createClient();

/** Absolute URL for a file download, which bypasses Axios. */
export function downloadUrl(path: string): string {
  return `${BASE_URL}${API_PREFIX}${path}`;
}
