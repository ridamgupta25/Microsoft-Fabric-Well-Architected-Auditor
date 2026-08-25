/**
 * Checks — one page for the whole rule surface.
 *
 * Two in-page tabs sit side by side so the deterministic catalog and the
 * AI-authored custom checks live together instead of in separate nav items:
 *   - "Default checks" — the read-only rule catalog ({@link CatalogPage}).
 *   - "Custom checks"  — type checks in plain English and run them ({@link CustomChecksPage}).
 *
 * Both tabs reuse their existing page components as-is; this file only owns the
 * tab switch, so neither flow changes behaviour.
 */
import { useState } from "react";

import { CatalogPage } from "@/pages/CatalogPage";
import { CustomChecksPage } from "@/pages/CustomChecksPage";

type Tab = "default" | "custom";

export function ChecksPage({ initialTab = "default" }: { initialTab?: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab);

  return (
    <div className="space-y-4">
      <div
        role="tablist"
        aria-label="Checks"
        className="flex gap-1 border-b border-slate-200 dark:border-slate-700"
      >
        <TabButton active={tab === "default"} onClick={() => setTab("default")}>
          Default checks
        </TabButton>
        <TabButton active={tab === "custom"} onClick={() => setTab("custom")}>
          Custom checks
        </TabButton>
      </div>

      {tab === "default" ? <CatalogPage /> : <CustomChecksPage />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
        active
          ? "border-blue-600 text-blue-700 dark:border-blue-400 dark:text-blue-300"
          : "border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
      }`}
    >
      {children}
    </button>
  );
}
