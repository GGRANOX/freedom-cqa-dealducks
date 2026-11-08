import { api } from "../api.js";
import { t } from "../i18n.js";
import { state, setState } from "../state.js";
import { esc, fmtNum, fmtUsd, toast } from "../util.js";
import { I } from "../icons.js";

const COLORS = ["#34c759", "#007aff", "#ff9500", "#af52de", "#ff2d55", "#5ac8fa", "#ffcc00", "#8e8e93", "#30b0c7", "#a2845e"];
const clone = (x) => JSON.parse(JSON.stringify(x));

// <cqa-checklist-editor> — редактор чек-листа: каждое сохранение = новая версия.
class CqaChecklistEditor extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = `<div class="page"><div class="empty">${t("loading")}</div></div>`;
    this.lint = {};
    this.trial = null;
    this.open = new Set();
    const lists = await api.checklists();
    const sel = state.checklist?.id || lists[0]?.id;
    await this.loadVersion(sel, state.checklist?.id === sel ? state.checklist.version : undefined);
  }

  async loadVersion(id, version) {
    const cl = await api.checklist(id, version);
    this.versions = cl.versions;
    delete cl.versions;
    this.base = cl;
    this.cl = clone(cl);
    this.trial = null;
    this.lint = {};
    this.render();
  }

  changedIds() {
    const keys = ["name", "type", "weight", "na_allowed", "description", "na_condition", "guidance", "scale", "section_id"];
    const o = Object.fromEntries(this.base.criteria.map((c) => [c.id, c]));
    return this.cl.criteria.filter((c) => !o[c.id] || keys.some((k) => JSON.stringify(c[k] ?? null) !== JSON.stringify(o[c.id][k] ?? null))).map((c) => c.id);
  }
  dirty() {
    return JSON.stringify(this.cl) !== JSON.stringify(this.base);
  }

  render() {
    const cl = this.cl, changed = new Set(this.changedIds());
    const baseIds = new Set(this.base.criteria.map((c) => c.id));
    const scored = cl.criteria.filter((c) => c.type !== "flag");
    const sum = scored.reduce((s, c) => s + Number(c.weight || 0), 0);
    const active = state.checklist && state.checklist.id === cl.id && state.checklist.version === this.base.version;
    this.innerHTML = `<div class="page">
      <div class="page-head">
        <div class="grow"><h1>${t("editor")}</h1><div class="sub">${t("sub_editor")}</div></div>
        <select class="input" id="ver" aria-label="${t("versions")}">${this.versions.map((v) => `<option value="${v.version}" ${v.version === this.base.version ? "selected" : ""}>v${v.version} · ${esc((v.created_at || "").slice(0, 16).replace("T", " "))}${v.comment ? " · " + esc(v.comment).slice(0, 40) : ""}</option>`).join("")}</select>
        <button class="btn ${active ? "" : "primary"}" id="use" ${active ? "disabled" : ""}>${active ? I.check : ""} ${t("use_for_eval")}</button>
      </div>
      <div class="editor-grid">
        <div>
          <div class="card card-b" style="margin-bottom:12px;display:grid;gap:10px">
            <label class="small muted" style="font-weight:600">${t("name")}<input class="input" id="cl-name" value="${esc(cl.name)}" style="width:100%;margin-top:4px"></label>
            <div class="row small"><b>${t("weights_sum")}: ${fmtNum(sum)}</b><span class="muted">· ${t("weights_norm")}</span></div>
            <div class="weights">${scored.map((c, i) => `<i style="width:${sum ? (100 * c.weight) / sum : 0}%;background:${COLORS[i % COLORS.length]}" title="${esc(c.name)}: ${c.weight}"></i>`).join("")}</div>
            <div class="legend-w">${scored.map((c, i) => `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(c.name)} · ${fmtNum(c.weight)}</span>`).join("")}</div>
            <div class="row wrap small">${(cl.sections || []).map((s, i) => `<input class="input" data-sec="${i}" value="${esc(s.name)}" title="${t("section")}" style="height:28px;width:230px;font-size:13px">`).join("")}
              <button class="btn xs" id="add-sec">+ ${t("add_section")}</button></div>
          </div>
          ${cl.criteria.map((c, i) => this.critHtml(c, i, changed.has(c.id), !baseIds.has(c.id))).join("")}
          <button class="btn" id="add">${I.plus} ${t("add_criterion")}</button>
        </div>
        <div class="sticky-side">
          <div class="card card-b" style="display:grid;gap:10px">
            ${this.dirty() ? `<div class="chip warn">● ${t("unsaved")}</div>` : ""}
            <div class="small"><b>${t("changed_criteria")}:</b> ${[...changed].map((x) => `<span class="chip warn">${esc(x)}</span>`).join(" ") || "—"}</div>
            <input class="input" id="comment" aria-label="${t("comment")}" placeholder="${t("comment")}">
            <button class="btn primary" id="save" ${this.dirty() ? "" : "disabled"}>${I.save} ${t("save_version")}</button>
          </div>
          <div class="card">
            <div class="card-h"><b class="grow row" style="gap:6px">${I.flask} ${t("trial")}</b><button class="btn sm primary" id="trial" ${changed.size ? "" : "disabled"}>${t("trial_run")}</button></div>
            <div class="card-b small muted">${t("trial_hint")}</div>
            ${this.trialHtml()}
          </div>
        </div>
      </div></div>`;
    this.bind();
  }

  critHtml(c, i, changed, isNew) {
    const L = this.lint[c.id];
    const secs = this.cl.sections || [];
    const openAttr = this.open.has(c.id) ? "open" : "";
    return `<details class="crit-edit ${isNew ? "new" : changed ? "changed" : ""}" data-i="${i}" ${openAttr}>
      <summary><span class="chip mono">${esc(c.id)}</span><b class="grow">${esc(c.name || "—")}</b>
        <span class="chip">${t(c.type)}</span>${c.type !== "flag" ? `<span class="chip brand">${t("weight")} ${fmtNum(c.weight)}</span>` : ""}
        ${c.na_allowed ? `<span class="chip na">N/A</span>` : ""}</summary>
      <div class="form">
        <label class="c3">${t("name")}<input class="input" data-k="name" value="${esc(c.name)}"></label>
        <label class="c3">${t("section")}<select class="input" data-k="section_id">${secs.map((s) => `<option value="${esc(s.id)}" ${s.id === c.section_id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>
        <label class="c2">${t("type")}<select class="input" data-k="type">${["binary", "scale", "flag"].map((x) => `<option value="${x}" ${c.type === x ? "selected" : ""}>${t(x)}</option>`).join("")}</select></label>
        <label class="c2">${t("weight")}<input class="input" type="number" min="0" step="1" data-k="weight" value="${c.weight}" ${c.type === "flag" ? "disabled" : ""}></label>
        <label class="c2" style="align-self:end"><span class="switch"><input type="checkbox" data-k="na_allowed" ${c.na_allowed ? "checked" : ""}> ${t("na_allowed")}</span></label>
        <label class="c6">${t("description")}<textarea class="input" rows="2" data-k="description">${esc(c.description)}</textarea></label>
        ${c.na_allowed ? `<label class="c6">${t("na_condition")}<textarea class="input" rows="2" data-k="na_condition">${esc(c.na_condition || "")}</textarea></label>` : ""}
        ${c.type === "scale" ? `<div class="c6"><div class="small muted" style="font-weight:600;margin-bottom:4px">${t("steps")}</div><div class="steps">
          ${(c.scale || []).map((s, k) => `<div class="step-row"><input class="input" type="number" data-step="${k}" data-f="value" value="${s.value}"><input class="input" data-step="${k}" data-f="label" value="${esc(s.label || "")}"><button class="btn xs ghost" data-rmstep="${k}">✕</button></div>`).join("")}
          <button class="btn xs" data-addstep>+ ${t("add_step")}</button></div></div>` : ""}
        <label class="c6">${t("guidance")}<textarea class="input" rows="3" data-k="guidance">${esc(c.guidance || "")}</textarea></label>
        <div class="c6 row"><button class="btn sm" data-lint>${I.sparkle} ${t("lint")}</button><span class="grow"></span><button class="btn sm danger" data-remove>${t("remove")}</button></div>
      </div>
      ${L ? (L.loading ? `<div class="lint row"><span class="spinner"></span> ${t("linting")}</div>` : `<div class="lint">
        <div class="row"><b>${t("clarity")}: ${L.clarity_score}/5</b><span class="grow"></span><span class="small muted">${fmtUsd(L.cost_usd)}</span></div>
        ${L.ambiguities?.length ? `<b class="small">${t("ambiguities")}:</b><ul>${L.ambiguities.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
        <div class="small"><b>${t("suggest_desc")}:</b> ${esc(L.suggested_description)} <button class="btn xs" data-apply="description">${t("apply")}</button></div>
        ${L.na_recommended ? `<div class="small" style="margin-top:4px"><b>${t("suggest_na")}:</b> ${esc(L.suggested_na_condition)} <button class="btn xs" data-apply="na">${t("apply")}</button></div>` : ""}
        <div class="small" style="margin-top:4px"><b>${t("guidance")}:</b> ${esc(L.suggested_guidance)} <button class="btn xs" data-apply="guidance">${t("apply")}</button></div>
      </div>`) : ""}
    </details>`;
  }

  trialHtml() {
    const tr = this.trial;
    if (!tr) return "";
    return `<div style="padding-bottom:8px">${(tr.rows || []).map((r) => {
        const d = r.before == null || r.after == null ? null : r.after - r.before;
        return `<div class="trial-row" data-cid="${esc(r.conversation_id)}" title="${esc(r.criteria.map((c) => `${c.criterion_id}: ${c.before ? (c.before.answer === "scale" ? c.before.value : c.before.answer) : "—"} → ${c.after.answer === "scale" ? c.after.value : c.after.answer}`).join(" · "))}">
          <span class="mono">${esc(r.conversation_id)}</span><span class="faint" style="text-align:right">${fmtNum(r.before)}</span><span class="faint">→</span><b>${fmtNum(r.after)}</b>
          <span class="${d > 0 ? "delta-up" : d < 0 ? "delta-down" : "delta-0"}">${d == null ? "" : (d > 0 ? "+" : "") + fmtNum(d)}</span></div>`;
      }).join("")}
      ${tr.running ? `<div class="trial-row"><span class="row"><span class="spinner"></span></span></div>` : ""}
      ${tr.cost_usd != null ? `<div class="card-b small faint" style="padding-top:4px">${t("cost")}: ${fmtUsd(tr.cost_usd)}</div>` : ""}</div>`;
  }

  bind() {
    const $ = (s) => this.querySelector(s);
    $("#ver").addEventListener("change", (e) => this.loadVersion(this.cl.id, Number(e.target.value)));
    $("#use").addEventListener("click", () => { setState({ checklist: { id: this.cl.id, version: this.base.version } }); toast(`${t("use_for_eval")}: v${this.base.version}`, "ok"); this.render(); });
    $("#cl-name").addEventListener("input", (e) => { this.cl.name = e.target.value; });
    $("#cl-name").addEventListener("change", () => this.render());
    this.querySelectorAll("[data-sec]").forEach((inp) => inp.addEventListener("change", () => { this.cl.sections[+inp.dataset.sec].name = inp.value; this.render(); }));
    $("#add-sec").addEventListener("click", () => {
      const n = (this.cl.sections || []).length + 1;
      (this.cl.sections ||= []).push({ id: `s${Date.now().toString(36)}`, name: `Секция ${n}` });
      this.render();
    });
    $("#add").addEventListener("click", () => {
      let k = this.cl.criteria.length + 1, id;
      do { id = `c${String(k++).padStart(2, "0")}`; } while (this.cl.criteria.some((c) => c.id === id));
      this.cl.criteria.push({ id, section_id: this.cl.sections?.[0]?.id, name: "Новый критерий", type: "binary", weight: 10,
        na_allowed: false, description: "Да — …", na_condition: "", guidance: "" });
      this.open.add(id);
      this.render();
      this.querySelector(`details[data-i="${this.cl.criteria.length - 1}"]`)?.scrollIntoView({ block: "center" });
    });
    this.querySelectorAll("details.crit-edit").forEach((det) => {
      const i = +det.dataset.i, c = this.cl.criteria[i];
      det.addEventListener("toggle", () => { det.open ? this.open.add(c.id) : this.open.delete(c.id); });
      det.querySelectorAll("[data-k]").forEach((inp) => {
        const k = inp.dataset.k;
        const apply = () => {
          c[k] = inp.type === "checkbox" ? inp.checked : inp.type === "number" ? Number(inp.value) : inp.value;
          if (k === "type") {
            if (c.type === "scale" && !c.scale) c.scale = [{ value: 10, label: "" }, { value: 5, label: "" }, { value: 0, label: "" }];
            if (c.type === "flag") { c.weight = 0; c.na_allowed = false; }
            if (c.type !== "scale") delete c.scale;
          }
        };
        inp.addEventListener("input", apply);
        inp.addEventListener("change", () => { apply(); this.render(); });
      });
      det.querySelectorAll("[data-step]").forEach((inp) => inp.addEventListener("change", () => {
        const s = c.scale[+inp.dataset.step];
        s[inp.dataset.f] = inp.dataset.f === "value" ? Number(inp.value) : inp.value;
        this.render();
      }));
      det.querySelectorAll("[data-rmstep]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); c.scale.splice(+b.dataset.rmstep, 1); this.render(); }));
      det.querySelector("[data-addstep]")?.addEventListener("click", (e) => { e.preventDefault(); c.scale.push({ value: 0, label: "" }); this.render(); });
      det.querySelector("[data-remove]").addEventListener("click", (e) => { e.preventDefault(); this.cl.criteria.splice(i, 1); this.render(); });
      det.querySelector("[data-lint]").addEventListener("click", async (e) => {
        e.preventDefault();
        this.lint[c.id] = { loading: true };
        this.render();
        try { this.lint[c.id] = await api.lint(this.cl, c.id); } catch (err) { delete this.lint[c.id]; toast(err.message, "error"); }
        this.render();
      });
      det.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", (e) => {
        e.preventDefault();
        const L = this.lint[c.id];
        if (b.dataset.apply === "description") c.description = L.suggested_description;
        if (b.dataset.apply === "guidance") c.guidance = L.suggested_guidance;
        if (b.dataset.apply === "na") { c.na_allowed = true; c.na_condition = L.suggested_na_condition; }
        this.render();
      }));
    });
    $("#save").addEventListener("click", async () => {
      try {
        const saved = await api.saveChecklist(this.cl, $("#comment").value.trim());
        setState({ checklist: { id: saved.id, version: saved.version } });
        toast(`${t("saved")} v${saved.version}`, "ok");
        await this.loadVersion(saved.id, saved.version);
      } catch (e) { toast(e.message, "error"); }
    });
    $("#trial").addEventListener("click", () => this.runTrial());
    this.querySelectorAll(".trial-row[data-cid]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/c/${encodeURIComponent(tr.dataset.cid)}`; }));
  }

  async runTrial() {
    this.trial = { rows: [], running: true };
    this.render();
    try {
      await api.trial({ checklist: this.cl, base_version: this.base.version, group_id: state.group, n: 5 }, (ev) => {
        if (ev.type === "row") this.trial.rows.push(ev);
        if (ev.type === "done") Object.assign(this.trial, { rows: ev.rows, cost_usd: ev.cost_usd, running: false });
        if (ev.type === "error") throw new Error(ev.error);
        this.render();
      });
    } catch (e) { toast(e.message, "error"); }
    this.trial.running = false;
    this.render();
  }
}
customElements.define("cqa-checklist-editor", CqaChecklistEditor);
