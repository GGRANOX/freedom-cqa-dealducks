import { api } from "../api.js";
import { t } from "../i18n.js";
import { state, setState } from "../state.js";
import { esc, scorePill, fmtUsd, toast, store, debounce, modal } from "../util.js";
import { I } from "../icons.js";

// <cqa-list> — список диалогов набора: фильтры, сводка, пакетная оценка, экспорт.
class CqaList extends HTMLElement {
  constructor() {
    super();
    this.rows = [];
    this.groups = [];
    this.f = store.get("filters", { q: "", lang: "", ch: "", score: "", sort: "id", dir: 1 });
    this.job = null;
  }

  async connectedCallback() {
    this.innerHTML = `<div class="page"><div class="empty">${t("loading")}</div></div>`;
    this._onState = () => this.render();
    window.addEventListener("cqa:state", this._onState);
    this.cursor = -1;
    this._onKey = (e) => {
      if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      const rows = this.querySelectorAll(".list-row");
      if (!rows.length) return;
      if (e.key === "j" || e.key === "ArrowDown") this.cursor = Math.min(rows.length - 1, this.cursor + 1);
      else if (e.key === "k" || e.key === "ArrowUp") this.cursor = Math.max(0, this.cursor - 1);
      else if (e.key === "Enter" && this.cursor >= 0) { location.hash = `#/c/${encodeURIComponent(rows[this.cursor].dataset.id)}`; return; }
      else if (e.key === "/") { e.preventDefault(); this.querySelector("#q")?.focus(); return; }
      else return;
      e.preventDefault();
      rows.forEach((r, i) => r.classList.toggle("sel", i === this.cursor));
      rows[this.cursor].scrollIntoView({ block: "nearest" });
    };
    document.addEventListener("keydown", this._onKey);
    try {
      [this.groups, this.checklists] = await Promise.all([api.groups(), api.checklists()]);
      if (!state.group || !this.groups.find((g) => g.group_id === state.group)) {
        setState({ group: this.groups[0]?.group_id || "DEV" });
      }
      await this.load();
      const running = store.get("job", null);
      if (running) this.poll(running);
    } catch (e) {
      this.innerHTML = `<div class="page"><div class="empty">${t("error")}: ${esc(e.message)}</div></div>`;
    }
  }
  disconnectedCallback() {
    window.removeEventListener("cqa:state", this._onState);
    document.removeEventListener("keydown", this._onKey);
    clearTimeout(this._poll);
  }

  async load() {
    this.rows = await api.conversations(state.group);
    this.render();
  }

  filtered() {
    const f = this.f, q = f.q.trim().toLowerCase();
    let r = this.rows.filter((c) =>
      (!q || c.id.toLowerCase().includes(q) || (c.preview || "").toLowerCase().includes(q)) &&
      (!f.lang || c.language === f.lang) &&
      (!f.ch || c.channel === f.ch) &&
      (!f.score ||
        (f.score === "none" && c.total_score == null) ||
        (f.score === "hi" && c.total_score >= 85) ||
        (f.score === "mid" && c.total_score >= 60 && c.total_score < 85) ||
        (f.score === "lo" && c.total_score != null && c.total_score < 60) ||
        (f.score === "gross" && c.gross) ||
        (f.score === "appeals" && c.open_appeals > 0)));
    const k = f.sort, d = f.dir;
    r.sort((a, b) => {
      const x = a[k], y = b[k];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * d;
    });
    return r;
  }

  render() {
    const rows = this.filtered();
    window.__cqaOrder = rows.map((r) => r.id);
    const ev = this.rows.filter((c) => c.total_score != null);
    const avg = ev.length ? ev.reduce((s, c) => s + c.total_score, 0) / ev.length : null;
    const gross = this.rows.filter((c) => c.gross).length;
    const appeals = this.rows.reduce((s, c) => s + (c.open_appeals || 0), 0);
    const langs = [...new Set(this.rows.map((c) => c.language))].sort();
    const job = this.job;
    const running = job && job.status === "running";
    const pct = job ? Math.round(((job.done + job.failed) / Math.max(1, job.total)) * 100) : 0;
    const head = (k, label) => `<span data-sort="${k}">${label}${this.f.sort === k ? (this.f.dir > 0 ? " ↑" : " ↓") : ""}</span>`;
    const seg = (key, opts) => `<div class="seg" data-seg="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${this.f[key] === v ? "on" : ""}">${l}</button>`).join("")}</div>`;

    this.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div class="grow"><h1>${t("nav_list")}</h1><div class="sub">${t("sub_list")}</div></div>
        <select class="input" id="group" title="${t("dataset")}" aria-label="${t("dataset")}" style="width:auto">
          ${this.groups.map((g) => `<option value="${esc(g.group_id)}" ${g.group_id === state.group ? "selected" : ""}>${t("dataset")}: ${esc(g.group_id)} · ${g.n}</option>`).join("")}
          <option value="__new">+ group_id…</option>
        </select>
        <button class="btn icon" id="sync" title="${t("reload")}" aria-label="${t("reload")}">${I.refresh}</button>
        <label class="btn icon" title="${t("import_file")}" aria-label="${t("import_file")}">${I.upload}<input type="file" id="file" accept=".json" class="sr"></label>
        <a class="btn icon" href="${api.exportUrl(state.group)}" title="${t("export")}" aria-label="${t("export")}">${I.download}</a>
        <button class="btn" id="batch-new" ${running ? "disabled" : ""}>${t("batch_new")}</button>
        <button class="btn primary" id="batch" ${running ? "disabled" : ""}>${I.bolt} ${t("batch_all")}</button>
      </div>

      ${job ? `<div class="card job">
        <div class="row"><span class="dot ${running ? "running" : "done"}"></span>
          <b>${running ? t("evaluating") : t("batch_done")}</b>
          <span class="muted">${job.done + job.failed} / ${job.total}${job.failed ? ` · ✗ ${job.failed}` : ""}</span>
          <span class="grow"></span><span class="muted small" style="font-variant-numeric:tabular-nums">${job.elapsed ?? 0} ${t("sec")} · ${fmtUsd(job.cost_usd)}</span></div>
        <div class="progress"><i style="width:${pct}%"></i></div></div>` : ""}

      ${!ev.length && this.rows.length && !job ? `<div class="card card-b row wrap" style="margin-bottom:22px;gap:14px;background:linear-gradient(135deg,var(--accent-soft),transparent 70%)">
        <div class="grow"><b style="font-size:16px">${t("cta_title")}</b><div class="muted small">${t("cta_sub")}</div></div>
        <button class="btn primary" id="cta">${I.bolt} ${t("batch_all")}</button></div>` : ""}
      <div class="stats">
        <div class="card stat"><div class="l">${t("evaluated")}</div><div class="v">${ev.length}<small> / ${this.rows.length}</small></div></div>
        <div class="card stat"><div class="l">${t("avg")}</div><div class="v">${avg == null ? "—" : avg.toFixed(1)}</div></div>
        <div class="card stat"><div class="l">${t("gross")}</div><div class="v" style="color:${gross ? "var(--red-text)" : "inherit"}">${gross}</div></div>
        <div class="card stat"><div class="l">${t("open_appeals")}</div><div class="v" style="color:${appeals ? "var(--blue-text)" : "inherit"}">${appeals}</div></div>
      </div>

      <div class="toolbar">
        <div class="search grow" style="max-width:340px">${I.search}<input class="input" id="q" type="search" aria-label="${t("search")}" placeholder="${t("search")}" value="${esc(this.f.q)}"></div>
        ${seg("lang", [["", t("all_lang").split(" ")[0]], ...langs.map((l) => [l, l])])}
        ${seg("ch", [["", t("all_ch").split(" ")[0]], ["agent chat", t("chat")], ["voice call", t("call")]])}
        <select class="input" id="score" style="width:auto" aria-label="${t("col_score")}">
          ${[["", "any_score"], ["none", "not_eval"], ["hi", "s_hi"], ["mid", "s_mid"], ["lo", "s_lo"], ["gross", "with_gross"], ["appeals", "with_appeals"]]
            .map(([v, k]) => `<option value="${v}" ${this.f.score === v ? "selected" : ""}>${t(k)}</option>`).join("")}
        </select>
        <span class="faint small">${rows.length}</span>
      </div>

      <div class="list">
        ${rows.length ? `<div class="list-head">${head("id", t("col_id"))}${head("language", t("col_lang"))}${head("channel", t("col_ch"))}<span>${t("col_preview")}</span>${head("messages", t("col_msgs"))}${head("total_score", t("col_score"))}<span>${t("col_status")}</span></div>
        ${rows.map((c, i) => {
          const st = job?.items?.[c.id];
          return `<div class="list-row ${i === this.cursor ? "sel" : ""}" data-id="${esc(c.id)}" data-i="${i}" role="link" tabindex="0" aria-label="${esc(c.id)} · ${c.total_score == null ? t("not_eval") : c.total_score}">
            <span class="mono">${esc(c.id)}</span>
            <span><span class="chip">${esc(c.language)}</span></span>
            <span class="muted small">${c.channel === "voice call" ? `☎︎ ${t("call")}` : t("chat")}</span>
            <span class="preview">${esc(c.preview)}</span>
            <span class="faint small">${c.messages}</span>
            <span>${scorePill(c.total_score)}${c.original_total != null && c.original_total !== c.total_score ? ` <s class="faint small">${c.original_total}</s>` : ""}</span>
            <span class="row">${st && st !== "done" ? `<span class="dot ${st}"></span>` : ""}
              ${c.gross ? `<span class="chip bad">${t("gross_flag")}</span>` : ""}${c.open_appeals ? `<span class="chip info">⚖︎ ${c.open_appeals}</span>` : ""}</span>
          </div>`;
        }).join("")}` : `<div class="empty"><div class="glyph">🗂</div><b>${t("no_data")}</b></div>`}
      </div>
    </div>`;
    this.bind();
  }

  bind() {
    const $ = (s) => this.querySelector(s);
    const saveF = () => { store.set("filters", this.f); this.render(); };
    $("#q").addEventListener("input", debounce((e) => {
      this.f.q = e.target.value; saveF();
      const q = $("#q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
    }, 250));
    this.querySelectorAll("[data-seg]").forEach((g) => g.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { this.f[g.dataset.seg] = b.dataset.v; saveF(); })));
    $("#score").addEventListener("change", (e) => { this.f.score = e.target.value; saveF(); });
    this.querySelectorAll("[data-sort]").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.sort;
      this.f.dir = this.f.sort === k ? -this.f.dir : 1;
      this.f.sort = k;
      saveF();
    }));
    this.querySelectorAll(".list-row").forEach((tr) => {
      const open = () => { location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}`; };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    });
    $("#group").addEventListener("change", async (e) => {
      let g = e.target.value.trim();
      if (g === "__new") { g = (prompt("group_id") || "").trim(); if (!g) return this.render(); }
      if (!g) return;
      setState({ group: g });
      if (!this.groups.find((x) => x.group_id === g)) await this.sync(g); else await this.load();
    });
    $("#sync").addEventListener("click", () => this.sync(state.group));
    $("#file").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const r = await api.importGroup(JSON.parse(await file.text()), state.group || "LOCAL");
        toast(`${r.group_id}: ${r.loaded}`, "ok");
        this.groups = await api.groups();
        setState({ group: r.group_id });
        await this.load();
      } catch (err) { toast(err.message, "error"); }
    });
    $("#batch").addEventListener("click", () => this.confirmBatch(false));
    $("#batch-new").addEventListener("click", () => this.confirmBatch(true));
    $("#cta")?.addEventListener("click", () => this.confirmBatch(false));
  }

  async sync(g) {
    try {
      toast(t("loading"));
      const r = await api.syncGroup(g);
      toast(`${r.group_id}: ${r.loaded}`, "ok");
      this.groups = await api.groups();
      await this.load();
    } catch (e) { toast(e.message, "error"); }
  }

  confirmBatch(onlyNew) {
    const n = onlyNew ? this.rows.filter((c) => c.total_score == null).length : this.rows.length;
    if (!n) return toast(t("nothing_to_eval"));
    const usd = (n * 0.056).toFixed(2), sec = Math.max(20, Math.round(n / 8) * 22);
    modal(`<div class="card-h"><h2>${t("batch_all")}</h2></div>
      <div class="card-b col" style="gap:14px">
        <div class="stats" style="margin:0;grid-template-columns:repeat(3,1fr)">
          <div class="card stat" style="box-shadow:none;background:var(--fill-2)"><div class="l">${t("nav_list")}</div><div class="v">${n}</div></div>
          <div class="card stat" style="box-shadow:none;background:var(--fill-2)"><div class="l">${t("cost")}</div><div class="v">≈$${usd}</div></div>
          <div class="card stat" style="box-shadow:none;background:var(--fill-2)"><div class="l">${t("duration")}</div><div class="v">~${Math.ceil(sec / 60)} ${t("min")}</div></div>
        </div>
        <div class="muted small">${t("batch_hint")}</div>
        <div class="row"><span class="grow"></span><button class="btn" id="b-cancel">${t("cancel")}</button><button class="btn primary" id="b-go">${I.bolt} ${t("start")}</button></div>
      </div>`, (root, close) => {
      root.querySelector("#b-cancel").addEventListener("click", close);
      root.querySelector("#b-go").addEventListener("click", () => { close(); this.startBatch(onlyNew); });
      root.querySelector("#b-go").focus();
    });
  }

  async startBatch(onlyNew) {
    try {
      const cl = state.checklist || {};
      const job = await api.batch({ group_id: state.group, checklist_id: cl.id, checklist_version: cl.version,
        sync: !onlyNew, only_new: onlyNew, annotations: true });
      store.set("job", job.id);
      this.job = job;
      this.render();
      this.poll(job.id);
    } catch (e) { toast(e.message, "error"); }
  }

  async poll(id) {
    try {
      this.job = await api.batchStatus(id);
    } catch { store.set("job", null); return; }
    if (this.job.status === "running") {
      if (!this._lastDone || this._lastDone !== this.job.done) {
        this._lastDone = this.job.done;
        this.rows = await api.conversations(state.group);
      }
      this.render();
      this._poll = setTimeout(() => this.poll(id), 1500);
    } else {
      store.set("job", null);
      this.rows = await api.conversations(state.group);
      this.render();
      if (!this._announced) { toast(`${t("batch_done")}: ${this.job.done}/${this.job.total} · ${fmtUsd(this.job.cost_usd)}`, "ok"); this._announced = true; }
    }
  }
}
customElements.define("cqa-list", CqaList);
