// Shared, mutable UI state and constant lookups.
//
// Everything the app mutates lives on a single exported object so that ES
// modules can both READ and UPDATE it. (A bare `export let x` binding is
// read-only when imported, so reassignment across modules would not work.)

export const state = {
  CONFIG: { project: "", pillars: [] },
  fetchedWs: [],      // workspaces returned by the backend (mock or live)
  manualWs: [],       // workspaces the user typed in by name/ID
  selected: new Set(),// ids currently ticked
  removed: new Set(), // ids the user removed from the list
  roleById: {},       // id -> layer role
  authSession: null,  // opaque sign-in session id
  authTimer: null,    // setInterval handle for the sign-in poll
};

// Fixed layer roles used for grouping and the role dropdowns.
export const ROLES = [
  "Data Prep", "Data Storage", "Data Logs",
  "Data Operations", "Reporting / Semantic", "Mixed",
];

// One-line helper text shown under each pillar checkbox.
export const PILLAR_HELP = {
  "Reliability": "retry, failure paths, notifications, timeouts",
  "Security": "roles, groups, labels, guests, hardcoded secrets",
  "Cost Optimization": "capacity, orphaned items",
  "Operational Excellence": "naming, Git, deployment, descriptions",
  "Performance Efficiency": "Phase 2 (Delta / model)",
};
