import { api } from "../api.js";
import { t } from "../i18n.js";
import { esc, ansText, fmtNum, toast, highlight } from "../util.js";
import { I } from "../icons.js";

// <cqa-appeal> — рабочее место проверяющего: очередь, спорная оценка, фрагмент, ИИ-секундант, решение.
class CqaAppeal extends HTMLElement {
  async connectedCallback() {
    this.tab = "open";
    this.sel = this.getAttribute("appeal-id");
    await this.load();
  }

  async load() {
    try {
      this.all = await api.appeals();
      const list = this.list();
      if (!this.sel || !this.all.find((a) => a.id === this.sel)) this.sel = list[0]?.id || null;
      this.cur = this.sel ? await api.appeal(this.sel) : null;
      this.render();
    } catch (e) {
      this.innerHTML = `<div class="page"><div class="empty">${t("error")}: ${esc(e.message)}</div></div>`;
    }
  }
  list() { return this.all.filter((a) => (this.tab === "open" ? a.status === "open" : a.status !== "open")); }

  render() {
    const list = this.list();
    const nOpen = this.all.filter((a) => a.status === "open").length;
    this.innerHTML = `<div class="page">
      <div class="page-head"><div class="grow"><h1>${t("nav_appeals")}</h1><div class="sub">${t("sub_appeals")}</div></div>
        <div class="seg"><button data-tab="open" class="${this.tab === "open" ? "on" : ""}">${t("queue")} · ${nOpen}</button>
        <button data-tab="done" class="${this.tab === "done" ? "on" : ""}">${t("reviewed")} · ${this.all.length - nOpen}</button></div></div>
      <div class="split">
        <div class="group side-list">${list.length ? list.map((a) => `<div class="side-item ${a.id === this.sel ? "on" : ""}" data-id="${a.id}" role="button" tabindex="0" aria-current="${a.id === this.sel}">
            <div class="row"><b class="mono">${esc(a.conversation_id)}</b><span class="grow"></span>
              ${a.status === "open" ? `<span class="chip warn">${t("open")}</span>` : `<span class="chip ${a.status === "changed" ? "info" : "ok"}">${t(a.status)}</span>`}</div>
            <div class="small" style="font-weight:550">${esc(a.original?.name || a.criterion_id)}</div>
            <div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.comment)}</div>
          </div>`).join("") : `<div class="empty"><div class="glyph">⚖︎</div><b>${t("no_appeals")}</b></div>`}</div>
        <div>${this.cur ? this.detailHtml() : ""}</div>
      </div></div>`;
    this.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { this.tab = b.dataset.tab; this.sel = null; this.load(); }));
    this.querySelectorAll(".side-item").forEach((el) => el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } }));
    this.querySelectorAll(".side-item").forEach((el) => el.addEventListener("click", async () => {
      this.sel = el.dataset.id;
      history.replaceState(null, "", `#/appeals/${this.sel}`);
      this.cur = await api.appeal(this.sel);
      this.render();
    }));
    if (this.cur) this.bindDetail();
  }

  detailHtml() {
    const a = this.cur, o = a.original || {}, c = a.criterion, open = a.status === "open";
    const frag = (o.fragment || "").split("\n").map((line) => {
      const m = line.match(/^\[(\d+)\] ([^:]+): ([\s\S]*)$/);
      if (!m) return "";
      const isEv = (o.evidence || []).some((e) => e.message_index === +m[1]);
      const quotes = (o.evidence || []).filter((e) => e.message_index === +m[1]).map((e) => e.quote);
      const cls = m[2] === "ОПЕРАТОР" ? "operator" : m[2] === "КЛИЕНТ" ? "client" : m[2] === "БОТ" ? "bot" : "system";
      return `<div class="msg ${cls} ${isEv ? "flash" : ""}"><div class="bubble-wrap"><div class="meta"><span>${esc(m[2].charAt(0) + m[2].slice(1).toLowerCase())}</span><span class="ts">#${m[1]}</span></div>
        <div class="bubble">${highlight(m[3], quotes)}</div></div></div>`;
    }).join("");
    const steps = c.type === "scale" ? c.scale.map((s) => s.value).sort((x, y) => y - x) : [];
    const options = [...(c.type === "scale" ? steps.map((v) => [`scale:${v}`, String(v)]) : [["yes", t("yes")], ["no", t("no")]]),
      ...(c.na_allowed ? [["na", "N/A"]] : [])];
    const s = a.second;
    return `<div class="card">
      <div class="card-h"><div class="grow"><div class="faint small">${t("disputed")} · <span class="mono">${esc(a.conversation_id)}</span></div><h2>${esc(c.name)}</h2></div>
        <a class="btn sm" href="#/c/${encodeURIComponent(a.conversation_id)}/${(o.evidence || [])[0]?.message_index ?? ""}">${t("open_dialog")} ↗</a></div>
      <div class="card-b" style="display:grid;gap:16px">
        <div class="flow">
          <div><div class="lbl">${t("original")}</div><div class="big">${ansText(o.answer, o.value)}</div><div class="small muted">${fmtNum(o.score)} / ${fmtNum(o.max_score)} · ${t("total")} ${fmtNum(o.total_score)}</div></div>
          <div class="arr">→</div>
          <div><div class="lbl">${t("decision")}</div>${open ? `<span class="chip warn">${t("open")}</span>` :
            `<div class="big" style="font-size:17px">${t(a.status)}</div><div class="small muted">${esc(a.decision?.reason || "")}</div>`}</div>
          <div class="arr">→</div>
          <div><div class="lbl">${t("final")}</div>${open ? `<div class="big faint">—</div>` : `<div class="big">${ansText(a.final?.answer, a.final?.value)}</div>
            <div class="small">${fmtNum(a.final?.score)} · ${t("total")} <b>${fmtNum(a.final?.total_score)}</b></div>`}</div>
        </div>
        <div><div class="small muted" style="font-weight:600">${t("explanation")}</div>${esc(o.explanation || "")}</div>
        <div class="trail"><b>${t("operator_comment")}</b><span>“${esc(a.comment)}”</span></div>
        <div><div class="small muted" style="font-weight:600;margin-bottom:4px">${t("fragment")}</div><div class="frag">${frag}</div></div>
        <div class="small muted"><b>${t("description")}:</b> ${esc(c.description)}${c.na_allowed ? ` <b>N/A:</b> ${esc(c.na_condition)}` : ""}</div>

        <div class="second" id="second">${s ? `<div class="row"><b class="row" style="gap:6px">${I.sparkle} ${t("ai_second")}</b><span class="grow"></span><span class="small muted">${t("confidence")} ${Math.round((s.confidence || 0) * 100)}%</span></div>
          <div style="margin:6px 0"><b>${s.verdict === "confirm" ? t("second_confirm") : `${t("second_change")} ${ansText(s.suggested_answer, s.suggested_value)}`}</b></div>
          <div class="small">${esc(s.reasoning)}</div>
          ${(s.evidence || []).map((e) => `<a class="ev" href="#/c/${encodeURIComponent(a.conversation_id)}/${e.message_index}"><span class="idx">#${e.message_index}</span><q>${esc(e.quote)}</q></a>`).join("")}`
          : `<div class="row"><b class="row" style="gap:6px">${I.sparkle} ${t("ai_second")}</b><span class="grow"></span><button class="btn sm primary" id="ask">${t("ask_second")}</button></div>`}</div>

        ${open ? `<div class="col" style="gap:12px;padding-top:6px;border-top:1px solid var(--sep)">
          <h3 style="margin-top:10px">${t("reviewer")}</h3>
          <div class="seg"><button data-act="confirm" class="on">${t("confirm")}</button><button data-act="change">${t("change")}</button></div>
          <label class="row hidden" id="new-wrap">${t("new_answer")}:
            <select class="input" id="new" aria-label="${t("new_answer")}">${options.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
          <textarea class="input" id="reason" rows="3" aria-label="${t("reason")}" placeholder="${t("reason")}"></textarea>
          <div class="row"><span class="grow"></span><button class="btn primary" id="decide">${t("decide")}</button></div>
        </div>` : ""}
      </div></div>`;
  }

  bindDetail() {
    const $ = (s) => this.querySelector(s);
    $("#ask")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.innerHTML = `<span class="spinner" style="width:14px;height:14px;border-top-color:#fff"></span>`;
      try {
        this.cur.second = await api.second(this.cur.id);
        this.render();
      } catch (err) { toast(err.message, "error"); e.target.disabled = false; e.target.textContent = t("ask_second"); }
    });
    let act = "confirm";
    this.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      act = b.dataset.act;
      this.querySelectorAll("[data-act]").forEach((x) => x.classList.toggle("on", x === b));
      $("#new-wrap").classList.toggle("hidden", act !== "change");
      if (act === "change" && this.cur.second?.verdict === "change") {
        const s = this.cur.second;
        $("#new").value = s.suggested_answer === "scale" ? `scale:${s.suggested_value}` : s.suggested_answer;
      }
    }));
    $("#decide")?.addEventListener("click", async () => {
      const reason = $("#reason").value.trim();
      if (!reason) return $("#reason").focus();
      const body = { action: act, reason };
      if (act === "change") {
        const v = $("#new").value;
        if (v.startsWith("scale:")) { body.new_answer = "scale"; body.new_value = Number(v.slice(6)); } else body.new_answer = v;
      }
      try {
        await api.decide(this.cur.id, body);
        toast(t("decided"), "ok");
        window.dispatchEvent(new CustomEvent("cqa:appeals-changed"));
        this.cur = await api.appeal(this.cur.id);
        this.all = await api.appeals();
        this.render();
      } catch (e) { toast(e.message, "error"); }
    });
  }
}
customElements.define("cqa-appeal", CqaAppeal);
