// The single place that talks to the backend JSON API. Every function returns
// the parsed JSON body so callers can check for an `error` field.

const jsonPost = (url, body) => fetch(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(r => r.json());

export const getConfig = () => fetch("/api/config").then(r => r.json());

export const getWorkspaces = (project, mode) =>
  fetch(`/api/workspaces?project=${encodeURIComponent(project)}&mode=${mode}`).then(r => r.json());

export const getLiveWorkspaces = (session) => jsonPost("/api/workspaces/live", { session });

export const postDiag = (session) => jsonPost("/api/diag", { session });

export const authLogin = (payload) => jsonPost("/api/auth/login", payload);

export const authPoll = (session) => jsonPost("/api/auth/poll", { session });

export const authAzcli = () => fetch("/api/auth/azcli", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
}).then(r => r.json());

export const postRun = (body) => jsonPost("/api/run", body);
