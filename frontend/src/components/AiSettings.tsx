/**
 * AI settings — an optional, per-request AI key for the custom-checks run.
 *
 * The key lives only in this component's state (and the parent's) for the life of
 * the page; it is sent with the run request over HTTPS and is never stored in
 * localStorage or logged. Leaving AI off keeps the pipeline deterministic.
 */
import { useState } from "react";

import { verifyAi } from "@/services/customChecksService";
import type { AiConfigInput } from "@/types/api";

type Provider = "openai" | "azure";

export function AiSettings({
  onChange,
}: {
  onChange: (ai: AiConfigInput | null) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState<Provider>("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [deployment, setDeployment] = useState("");
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);

  // Build the config from current fields and push it (or null) to the parent.
  const emit = (next: Partial<{
    enabled: boolean; provider: Provider; apiKey: string; model: string;
    baseUrl: string; endpoint: string; deployment: string;
  }>) => {
    const s = {
      enabled, provider, apiKey, model, baseUrl, endpoint, deployment, ...next,
    };
    if (!s.enabled || !s.apiKey.trim()) {
      onChange(null);
      return;
    }
    onChange({
      provider: s.provider,
      api_key: s.apiKey,
      model: s.model,
      base_url: s.provider === "openai" ? s.baseUrl || null : null,
      endpoint: s.provider === "azure" ? s.endpoint || null : null,
      deployment: s.provider === "azure" ? s.deployment || null : null,
    });
    setTest(null);
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const result = await verifyAi({
        provider,
        api_key: apiKey,
        model,
        base_url: provider === "openai" ? baseUrl || null : null,
        endpoint: provider === "azure" ? endpoint || null : null,
        deployment: provider === "azure" ? deployment || null : null,
      });
      setTest(result);
    } catch (err) {
      setTest({ ok: false, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="card space-y-3 p-4">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => {
            setEnabled(e.target.checked);
            emit({ enabled: e.target.checked });
          }}
        />
        Use my AI key (enables code generation)
      </label>

      {!enabled && (
        <p className="text-xs text-slate-500">
          Off — the run stays deterministic. New checks that need an LLM are reported as
          <code className="mx-1">AI_REQUIRED</code>.
        </p>
      )}

      {enabled && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="text-slate-500">Provider</span>
              <select
                value={provider}
                onChange={(e) => {
                  const p = e.target.value as Provider;
                  setProvider(p);
                  emit({ provider: p });
                }}
                className="input w-full"
              >
                <option value="openai">OpenAI-compatible</option>
                <option value="azure">Azure OpenAI</option>
              </select>
            </label>

            <label className="space-y-1 text-sm">
              <span className="text-slate-500">API key</span>
              <input
                type="password"
                value={apiKey}
                autoComplete="off"
                placeholder="sk-…  (never stored)"
                onChange={(e) => {
                  setApiKey(e.target.value);
                  emit({ apiKey: e.target.value });
                }}
                className="input w-full"
              />
            </label>

            <label className="space-y-1 text-sm">
              <span className="text-slate-500">{provider === "azure" ? "Deployment" : "Model"}</span>
              <input
                type="text"
                value={provider === "azure" ? deployment : model}
                placeholder={provider === "azure" ? "my-gpt-4o" : "gpt-4o-mini"}
                onChange={(e) => {
                  if (provider === "azure") {
                    setDeployment(e.target.value);
                    emit({ deployment: e.target.value });
                  } else {
                    setModel(e.target.value);
                    emit({ model: e.target.value });
                  }
                }}
                className="input w-full"
              />
            </label>

            <label className="space-y-1 text-sm">
              <span className="text-slate-500">
                {provider === "azure" ? "Endpoint" : "Base URL"}
              </span>
              <input
                type="text"
                value={provider === "azure" ? endpoint : baseUrl}
                placeholder={
                  provider === "azure"
                    ? "https://name.openai.azure.com"
                    : "https://api.openai.com/v1"
                }
                onChange={(e) => {
                  if (provider === "azure") {
                    setEndpoint(e.target.value);
                    emit({ endpoint: e.target.value });
                  } else {
                    setBaseUrl(e.target.value);
                    emit({ baseUrl: e.target.value });
                  }
                }}
                className="input w-full"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-sm"
              onClick={() => void runTest()}
              disabled={testing || apiKey.trim() === ""}
            >
              {testing ? "Testing…" : "Test key"}
            </button>
            {test && (
              <span
                className={`badge ${
                  test.ok
                    ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                    : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                }`}
              >
                {test.ok ? "✓" : "✗"} {test.message}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500">
            Your key is sent only with the run request and is never saved or logged.
          </p>
        </div>
      )}
    </div>
  );
}
