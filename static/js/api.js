// Единственный модуль, который общается с бэкендом. Базовый URL настраивается (window.CQA_API_BASE).
const base = () => (window.CQA_API_BASE || "").replace(/\/$/, "");

async function req(method, path, body) {
  const r = await fetch(base() + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
  return data;
}

// POST с потоковым ответом (Server-Sent Events): onEvent вызывается на каждое событие.
async function stream(path, body, onEvent, signal) {
  const r = await fetch(base() + path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

export const api = {
  base,
  health: () => req("GET", "/api/health"),
  groups: () => req("GET", "/api/groups"),
  syncGroup: (g) => req("POST", `/api/groups/${encodeURIComponent(g)}/sync`),
  importGroup: (payload, group_id) => req("POST", "/api/groups/import", { payload, group_id }),
  conversations: (g) => req("GET", "/api/conversations" + (g ? `?group_id=${encodeURIComponent(g)}` : "")),
  conversation: (id) => req("GET", `/api/conversations/${encodeURIComponent(id)}`),
  audio: (id) => req("GET", `/api/conversations/${encodeURIComponent(id)}/audio`),
  latestEvaluation: (id) => req("GET", `/api/conversations/${encodeURIComponent(id)}/evaluation`),
  evaluation: (eid) => req("GET", `/api/evaluations/${eid}`),
  evaluate: (body, onEvent, signal) => stream("/api/evaluate", body, onEvent, signal),
  checklists: () => req("GET", "/api/checklists"),
  checklist: (id, version) => req("GET", `/api/checklists/${encodeURIComponent(id)}` + (version ? `?version=${version}` : "")),
  saveChecklist: (checklist, comment) => req("POST", "/api/checklists", { checklist, comment }),
  lint: (checklist, criterion_id) => req("POST", "/api/checklists/lint", { checklist, criterion_id }),
  trial: (body, onEvent) => stream("/api/checklists/trial", body, onEvent),
  batch: (body) => req("POST", "/api/batch", body),
  batchStatus: (id) => req("GET", `/api/batch/${id}`),
  appeals: (status) => req("GET", "/api/appeals" + (status ? `?status=${status}` : "")),
  appeal: (id) => req("GET", `/api/appeals/${id}`),
  createAppeal: (evaluation_id, criterion_id, comment) => req("POST", "/api/appeals", { evaluation_id, criterion_id, comment }),
  decide: (id, body) => req("POST", `/api/appeals/${id}/decision`, body),
  second: (id) => req("POST", `/api/appeals/${id}/second`),
  exportUrl: (g) => base() + "/api/export.csv" + (g ? `?group_id=${encodeURIComponent(g)}` : ""),
};
