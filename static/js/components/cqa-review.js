import { api } from "../api.js";
import { t, getLang } from "../i18n.js";
import { state, setState } from "../state.js";
import { esc, icon, answerChip, ansText, fmtNum, fmtUsd, fmtSec, highlight, modal, toast, ring, SYMBOL_ICON } from "../util.js";
import { I } from "../icons.js";

const NAMES = {
  ru: { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" },
  en: { client: "Client", operator: "Operator", bot: "Bot", system: "System" },
  kz: { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Жүйе" },
};
const mmss = (s) => (s == null ? "" : `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`);

// <cqa-review conversation-id="..."> — разбор диалога: чат со знаками ходов, eval bar, плеер, результат.
class CqaReview extends HTMLElement {
  async connectedCallback() {
    this.id_ = this.getAttribute("conversation-id");
    this.opened = new Set();
    this.focusIdx = -1;
    this.innerHTML = `<div class="page"><div class="empty"><span class="spinner"></span></div></div>`;
    this._onState = (e) => { if ("strict" in (e.detail || {})) this.render(); };
    window.addEventListener("cqa:state", this._onState);
    this._onKey = (e) => this.onKey(e);
    document.addEventListener("keydown", this._onKey);
    try {
      const [conv, lists] = await Promise.all([api.conversation(this.id_), api.checklists()]);
      this.conv = conv;
      this.lists = lists;
      this.ev = await api.latestEvaluation(this.id_).catch(() => null);
      this.autoOpen();
      await this.loadChecklist();
      this.render();
      if (conv.audio_url) this.loadAudio();
    } catch (e) {
      this.innerHTML = `<div class="page"><div class="empty"><div class="glyph">⚠︎</div><b>${t("error")}</b>${esc(e.message)}</div></div>`;
    }
  }
  disconnectedCallback() {
    window.removeEventListener("cqa:state", this._onState);
    document.removeEventListener("keydown", this._onKey);
    clearInterval(this._timer);
    this._abort?.abort();
    this.querySelector("audio")?.pause();
  }

  // Критерии, где потеряны баллы, N/A, грубая ошибка или апелляция, раскрыты сразу — их и смотрят первыми.
  autoOpen() {
    this.opened = new Set((this.ev?.criteria || []).filter((c) => c.answer === "na" || c.low_confidence ||
      (c.max_score && c.score < c.max_score) || (c.answer === "yes" && c.weight === 0) || c.overridden ||
      (this.ev.appeals || []).some((a) => a.criterion_id === c.criterion_id)).map((c) => c.criterion_id));
  }

  async loadChecklist() {
    const sel = this.ev ? { id: this.ev.checklist_id, version: this.ev.checklist_version } : state.checklist;
    const id = sel?.id || this.lists[0]?.id;
    this.cl = await api.checklist(id, sel?.version);
    this.types = Object.fromEntries(this.cl.criteria.map((c) => [c.id, c.type]));
  }

  async loadAudio() {
    try { this.audio = await api.audio(this.id_); } catch { this.audio = { src: null }; }
    const p = this.querySelector(".player");
    if (p) { p.outerHTML = this.playerHtml(); this.bindPlayer(); }
  }

  // ---------- рендер ----------
  render() {
    const c = this.conv, ev = this.ev, strict = state.strict, m = c.metrics || {};
    const isCall = c.channel === "voice call";
    const order = window.__cqaOrder || [];
    const pos = order.indexOf(this.id_);
    this.innerHTML = `
    <div class="page">
      <div class="crumbs">
        <a class="btn plain sm" href="#/">${I.back} ${t("nav_list")}</a>
        ${pos >= 0 ? `<span class="faint small">${pos + 1} / ${order.length}</span>
          <button class="btn plain sm" id="prev" ${pos <= 0 ? "disabled" : ""} title="${t("prev_dialog")} (K)">↑</button>
          <button class="btn plain sm" id="next" ${pos >= order.length - 1 ? "disabled" : ""} title="${t("next_dialog")} (J)">↓</button>` : ""}
      </div>
      <div class="review-head">
        <h1>${esc(c.conversation_id)}</h1>
        <span class="chip">${esc(c.language)}</span>
        <span class="chip ${isCall ? "info" : ""}">${isCall ? `☎︎ ${t("call")}` : t("chat")}</span>
        <span class="grow"></span>
        <span class="live hidden" id="live"><span class="spinner" style="width:14px;height:14px"></span><span id="live-t">0.0 ${t("sec")}</span><span id="live-c">$0.0000</span></span>
        <select class="input" id="cl" title="${t("checklist")}" aria-label="${t("checklist")}" style="width:auto;max-width:300px">${this.checklistOptions()}</select>
        <button class="btn primary" id="run" title="E">${I.bolt} ${ev ? t("reevaluate") : t("evaluate")}</button>
      </div>
      <div class="metrics">
        <div class="metric"><div class="l">${t("first_resp")}</div><div class="v">${fmtSec(m.first_response_sec)}</div></div>
        <div class="metric"><div class="l">${t("max_pause")}</div><div class="v">${fmtSec(m.max_pause_sec)}</div></div>
        <div class="metric"><div class="l">${t("op_share")}</div><div class="v">${m.operator_talk_share == null ? "—" : Math.round(m.operator_talk_share * 100) + "%"}</div></div>
        <div class="metric"><div class="l">${t("col_msgs")}</div><div class="v">${c.transcript.length}</div></div>
      </div>
      ${!strict && ev?.sentiment_curve?.length ? `<div class="card evalbar">${this.evalBarHtml()}</div>` : ""}
      <div class="review-grid ${strict ? "strict" : ""}">
        ${strict ? this.strictHtml() + this.chatCard() : this.chatCard() + `<div class="card result" id="result" tabindex="-1" aria-live="polite">${this.resultHtml()}</div>`}
      </div>
    </div>`;
    this.bind();
  }

  checklistOptions() {
    const out = [];
    for (const l of this.lists) for (let v = l.version; v >= 1; v--) {
      const sel = this.cl && this.cl.id === l.id && this.cl.version === v;
      out.push(`<option value="${esc(l.id)}|${v}" ${sel ? "selected" : ""}>${esc(l.name || l.id)} · v${v}</option>`);
    }
    return out.join("");
  }

  chatCard() {
    return `<div class="card">${this.conv.audio_url ? this.playerHtml() : ""}<div class="chat" id="chat" tabindex="0" role="log" aria-label="${t("fragment")}">${this.chatHtml()}</div></div>`;
  }

  chatHtml() {
    const ann = !this.ev || state.strict ? {} : Object.fromEntries((this.ev.annotations || []).map((a) => [a.message_index, a]));
    const mood = !this.ev || state.strict ? {} : Object.fromEntries((this.ev.sentiment_curve || []).map((s) => [s.message_index, s.value]));
    const quotes = {};
    for (const c of this.ev?.criteria || []) for (const e of c.evidence || []) (quotes[e.message_index] ||= []).push(e.quote);
    const isCall = this.conv.channel === "voice call";
    const rel = this.conv.rel_times || [];
    const names = NAMES[getLang()] || NAMES.ru;
    let prevSpk = null;
    return this.conv.transcript.map((m, i) => {
      const a = ann[i], mv = mood[i];
      const ts = isCall ? mmss(rel[i]) : m.timestamp;
      const showWho = m.speaker !== prevSpk;
      prevSpk = m.speaker;
      const long = (m.speaker === "bot") && m.text.length > 160;
      const moodDot = mv != null ? `<span class="mood" title="${t("mood")}: ${mv}" style="background:${mv > 0.15 ? "var(--green)" : mv < -0.15 ? "var(--red)" : "var(--text-3)"}"></span>` : "";
      return `<div class="msg ${m.speaker}" id="m${i}" data-i="${i}">
        ${m.speaker === "operator" ? `<span class="sym">${a ? `<span title="${esc(a.comment)}">${icon(a.symbol, "")}</span>` : ""}</span>` : ""}
        ${m.speaker === "client" ? moodDot : ""}
        <div class="bubble-wrap">
          ${showWho || m.speaker === "system" ? `<div class="meta"><span>${names[m.speaker] || m.speaker}</span>
            <span class="ts ${isCall ? "clickable" : ""}" data-seek="${i}" title="${esc(m.timestamp)}">${esc(ts)} · #${i}</span></div>` : ""}
          <div class="bubble ${long ? "clamp" : ""}" title="${showWho ? "" : `${esc(ts)} · #${i}`}">${highlight(m.text, quotes[i])}</div>
          ${a ? `<div class="ann">${esc(a.comment)}</div>${a.best_move ? `<div class="best"><b>♟ ${t("best_move")}</b>${esc(a.best_move)}</div>` : ""}` : ""}
        </div>
      </div>`;
    }).join("");
  }

  playerHtml() {
    const a = this.audio;
    if (!a) return `<div class="player"><div class="row muted small"><span class="spinner"></span> ${t("audio_loading")}</div></div>`;
    if (!a.src) return `<div class="player"><div class="row muted small">${t("audio_none")} ${this.conv.audio_url ? `· <a href="${esc(this.conv.audio_url)}" target="_blank" rel="noopener">${t("open_player")} ↗</a>` : ""}</div></div>`;
    return `<div class="player">
      <audio id="audio" preload="metadata" src="${esc(a.src)}"></audio>
      <div class="player-row">
        <button class="play" id="play" aria-label="Play / Pause">${I.play}</button>
        <div class="track" id="track"><div class="rail"></div><div class="fill" id="fill" style="width:0"></div></div>
        <span class="time" id="time">0:00 / 0:00</span>
        <select class="input" id="rate" aria-label="speed" style="width:auto;height:28px;font-size:12px">${[0.75, 1, 1.25, 1.5, 2].map((r) => `<option ${r === 1 ? "selected" : ""}>${r}×</option>`).join("")}</select>
      </div>
      <a class="small" href="${esc(a.page)}" target="_blank" rel="noopener">${t("open_player")} ↗</a>
    </div>`;
  }

  evalBarHtml() {
    const n = this.conv.transcript.length, W = 1000, H = 64, mid = 28;
    const pts = (this.ev.sentiment_curve || []).slice().sort((a, b) => a.message_index - b.message_index);
    const x = (i) => (n <= 1 ? 0 : (i / (n - 1)) * W);
    const y = (v) => mid - v * 22;
    let path = "";
    if (pts.length) {
      const seq = [{ i: 0, v: pts[0].value }, ...pts.map((p) => ({ i: p.message_index, v: p.value })), { i: n - 1, v: pts[pts.length - 1].value }];
      path = seq.map((p, k) => {
        if (!k) return `M${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`;
        const q = seq[k - 1], cx = (x(q.i) + x(p.i)) / 2;
        return `C${cx.toFixed(1)},${y(q.v).toFixed(1)} ${cx.toFixed(1)},${y(p.v).toFixed(1)} ${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`;
      }).join(" ");
    }
    const area = path ? `${path} L${W},${mid} L0,${mid} Z` : "";
    const marks = (this.ev.annotations || []).map((a) => `<image href="${window.CQA_API_BASE || ""}/assets/icons/${SYMBOL_ICON[a.symbol]}.png" x="${(x(a.message_index) - 8).toFixed(1)}" y="${H - 16}" width="16" height="16"><title>#${a.message_index} ${esc(a.symbol)} ${esc(a.comment)}</title></image>`).join("");
    return `<div class="row small" style="margin-bottom:6px"><b>${t("mood")}</b><span class="grow"></span>
        ${[["!!", "legend_brilliant"], ["!", "legend_great"], ["?!", "legend_inacc"], ["?", "legend_mistake"], ["??", "legend_blunder"]].map(([s, k]) => `<span class="faint row" style="gap:4px">${icon(s, "")}${t(k)}</span>`).join("")}</div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="evalbar">
        <defs><clipPath id="ab"><rect x="0" y="0" width="${W}" height="${mid}"/></clipPath><clipPath id="be"><rect x="0" y="${mid}" width="${W}" height="${H}"/></clipPath></defs>
        <line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="var(--sep-strong)" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"/>
        ${area ? `<path d="${area}" fill="var(--green)" opacity=".22" clip-path="url(#ab)"/><path d="${area}" fill="var(--red)" opacity=".2" clip-path="url(#be)"/>` : ""}
        ${path ? `<path d="${path}" fill="none" stroke="var(--text-2)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>` : ""}
        ${marks}<rect x="0" y="0" width="${W}" height="${H - 18}" fill="transparent"/>
      </svg>`;
  }

  resultHtml() {
    const ev = this.ev;
    if (this.live) return this.liveHtml();
    if (!ev) return `<div class="empty"><div class="glyph">♟︎</div><b>${t("not_evaluated")}</b><span>${t("start_eval")}</span>
      <button class="btn primary" id="run2" style="margin-top:10px">${I.bolt} ${t("evaluate")}</button></div>`;
    const changed = ev.original_total != null && ev.original_total !== ev.total_score;
    return `
      <div class="total">${ring(ev.total_score)}
        <div class="grow col" style="gap:4px">
          <div class="faint small">${t("total")} · ${esc(ev.checklist_id)} v${ev.checklist_version}</div>
          ${changed ? `<div class="was"><s>${fmtNum(ev.original_total)}</s> → <b style="color:var(--text)">${fmtNum(ev.total_score)}</b> <span class="chip info">${t("from_appeal")}</span></div>` : ""}
          <div>${ev.gross_error.flag ? `<span class="gross-flag">${icon("??", "")} ${t("gross_flag")}</span>` : `<span class="chip ok">${t("no_gross")}</span>`}</div>
          ${ev.gross_error.flag ? `<div class="small muted">${esc(ev.gross_error.reason).slice(0, 220)}</div>` : ""}
          <div class="tiny faint">${esc(ev.model)} · ${fmtUsd(ev.cost_usd)} · ${(ev.duration_ms / 1000).toFixed(1)} ${t("sec")}</div>
        </div>
      </div>
      ${ev.sections?.length ? `<div class="sections">${ev.sections.map((s) => `<div class="sec">
        <span class="muted">${esc(s.name)}</span><b style="font-variant-numeric:tabular-nums">${s.score == null ? "N/A" : `${fmtNum(s.score)} / ${fmtNum(s.max_score)}`}</b>
        <div class="progress"><i style="width:${s.max_score ? (100 * (s.score || 0)) / s.max_score : 0}%"></i></div></div>`).join("")}</div>` : ""}
      <div class="row" style="padding:0 20px 8px"><b class="grow">${t("criteria")}</b>
        <button class="btn plain xs" id="toggle-all">${this.opened.size ? t("collapse_all") : t("expand_all")}</button></div>
      <div class="crit-list">${ev.criteria.map((c, i) => this.criterionHtml(c, i)).join("")}</div>`;
  }

  criterionHtml(c, i) {
    const type = this.types[c.criterion_id];
    const appeals = (this.ev.appeals || []).filter((a) => a.criterion_id === c.criterion_id);
    const hasOpen = appeals.some((a) => a.status === "open");
    const pts = type === "flag" ? "" : c.answer === "na" ? "N/A" : `${fmtNum(c.score)} / ${fmtNum(c.max_score)}`;
    const open = this.opened.has(c.criterion_id);
    const evid = (c.evidence || []).map((e) => `<button class="ev ${e.verified ? "" : "unverified"}" data-i="${e.message_index}" data-q="${esc(e.quote)}">
        <span class="idx">#${e.message_index}</span><q>${esc(e.quote)}</q></button>`).join("");
    return `<div class="crit ${open ? "open" : ""} ${i === this.focusIdx ? "focus" : ""}" id="crit-${c.criterion_id}" data-cid="${c.criterion_id}">
      <div class="crit-h" role="button" tabindex="0" aria-expanded="${open}">${answerChip({ ...c, type })}<span class="name">${esc(c.name)}</span>
        ${appeals.length ? `<span class="chip ${hasOpen ? "warn" : "info"}">⚖︎</span>` : ""}${c.low_confidence ? `<span class="chip warn">!</span>` : ""}
        <span class="pts">${pts}</span>${I.chevron}</div>
      <div class="crit-body"><div><div class="crit-inner">
        <div class="meta">
          ${type !== "flag" ? `<span>${t("weight")} ${fmtNum(c.weight)}${c.effective_weight && c.effective_weight !== c.weight ? ` → ${fmtNum(c.effective_weight)} <span class="faint">(${t("eff_weight")})</span>` : ""}</span>` : ""}
          <span>${t("confidence")} ${Math.round((c.confidence || 0) * 100)}%</span>
          ${c.votes?.length ? `<span title="${t("votes_hint")}">🗳 ${c.votes.map((v) => (v.startsWith("s") ? v.slice(1) : ansText(v))).join(" · ")}</span>` : ""}${c.overridden ? `<span class="chip info">${t("changed")}</span>` : ""}
        </div>
        <div class="expl">${esc(c.explanation)}</div>
        ${c.answer === "na" && c.na_reason ? `<div class="small"><b>${t("na_reason")}:</b> ${esc(c.na_reason)}</div>` : ""}
        ${evid}
        ${c.checked_range && (c.answer === "no" || !c.evidence?.length) ? `<button class="ev" data-range="${c.checked_range.from_index}-${c.checked_range.to_index}"><span class="idx">#${c.checked_range.from_index}–${c.checked_range.to_index}</span><span>${t("checked_range")}</span></button>` : ""}
        ${c.low_confidence ? `<div class="lowconf">⚠︎ ${t("low_conf")}</div>` : ""}
        ${appeals.map((a) => this.trailHtml(a)).join("")}
        <div class="row"><span class="grow"></span><button class="btn sm" data-appeal="${c.criterion_id}" ${hasOpen ? "disabled" : ""}>${I.scale} ${t("appeal")}</button></div>
      </div></div></div>
    </div>`;
  }

  trailHtml(a) {
    const o = a.original || {}, d = a.decision, f = a.final || {};
    return `<div class="trail">
      <div class="muted">“${esc(a.comment)}”</div>
      <div class="steps-flow"><span><b>${t("original")}</b> ${ansText(o.answer, o.value)} · ${fmtNum(o.score)}</span><span class="arrow">→</span>
        <span><b>${t("decision")}</b> ${a.status === "open" ? `<span class="chip warn">${t("open")}</span>` : `${t(a.status)}${d?.reason ? ` — ${esc(d.reason)}` : ""}`}</span>
        ${a.status !== "open" ? `<span class="arrow">→</span><span><b>${t("final")}</b> ${ansText(f.answer, f.value)} · ${fmtNum(f.score)} · ${t("total")} ${fmtNum(f.total_score)}</span>` : ""}</div>
    </div>`;
  }

  liveHtml() {
    const L = this.live, n = L.criteria.length || 8;
    return `<div class="total">${ring(n ? Math.round((100 * L.done) / n) : 0).replace(/<div class="num">[^<]*<\/div>/, `<div class="num" style="font-size:18px">${L.done}/${n}</div>`)}
      <div class="col" style="gap:2px"><b>${t("evaluating")}…</b><span class="faint small">${esc(L.model || "")}</span></div></div>
      <div class="crit-list">${L.criteria.map((c) => {
        const r = L.results[c.id];
        const max = this.maxStep(c.id);
        return `<div class="crit ${r ? "fresh open" : "pending"}"><div class="crit-h">${r ? answerChip({ answer: r.answer, value: r.value, type: this.types?.[c.id], score: r.answer === "scale" ? r.value : 1, max_score: r.answer === "scale" ? max : 1 }) : `<span class="chip ans"><span class="spinner" style="width:12px;height:12px"></span></span>`}
          <span class="name">${esc(c.name)}</span></div>
          <div class="crit-body"><div><div class="crit-inner">${r ? `<div class="expl small">${esc(r.explanation || "")}</div>` : ""}</div></div></div></div>`;
      }).join("")}
      ${L.annotating ? `<div class="crit pending"><div class="crit-h"><span class="chip ans"><span class="spinner" style="width:12px;height:12px"></span></span><span class="name">♟ ${t("best_move")}…</span></div></div>` : ""}</div>`;
  }
  maxStep(id) {
    const c = this.cl?.criteria.find((x) => x.id === id);
    return c?.scale ? Math.max(...c.scale.map((s) => s.value)) : 1;
  }

  strictHtml() {
    const ev = this.ev;
    if (!ev) return `<div class="card result" id="result">${this.resultHtml()}</div>`;
    const tp = (id) => this.types[id];
    return `<div class="card" id="result">
      <div class="card-h"><h2 class="grow">${t("total")}: ${ev.total_score == null ? "—" : fmtNum(ev.total_score)}</h2>
        ${ev.gross_error.flag ? `<span class="gross-flag">${icon("??", "")} ${t("gross_flag")}</span>` : `<span class="chip ok">${t("no_gross")}</span>`}</div>
      <div class="card-b" style="overflow:auto"><table class="strict-table">
      <thead><tr><th>#</th><th>${t("criteria")}</th><th>${t("answer")}</th><th style="text-align:right">${t("points")}</th><th style="text-align:right">${t("weight")}</th><th>${t("explanation")}</th><th>${t("evidence")}</th></tr></thead>
      <tbody>${ev.criteria.map((c) => `<tr>
        <td class="faint">${esc(c.criterion_id)}</td><td><b>${esc(c.name)}</b></td><td>${answerChip({ ...c, type: tp(c.criterion_id) })}</td>
        <td class="num">${tp(c.criterion_id) === "flag" ? "—" : c.answer === "na" ? "N/A" : `${fmtNum(c.score)} / ${fmtNum(c.max_score)}`}</td>
        <td class="num">${fmtNum(c.weight)}${c.effective_weight && c.effective_weight !== c.weight ? ` → ${fmtNum(c.effective_weight)}` : ""}</td>
        <td>${esc(c.answer === "na" && c.na_reason ? c.na_reason : c.explanation)}</td>
        <td>${(c.evidence || []).map((e) => `<a href="javascript:void 0" class="ev-link" data-i="${e.message_index}" data-q="${esc(e.quote)}">#${e.message_index}</a> <span class="muted">${esc(e.quote).slice(0, 80)}</span>`).join("<br>")}</td></tr>`).join("")}</tbody>
      <tfoot>${ev.sections.map((s) => `<tr><td></td><td colspan="2">${esc(s.name)}</td><td class="num">${s.score == null ? "N/A" : `${fmtNum(s.score)} / ${fmtNum(s.max_score)}`}</td><td colspan="3"></td></tr>`).join("")}</tfoot>
      </table></div></div>`;
  }

  // ---------- взаимодействие ----------
  bind() {
    const $ = (s) => this.querySelector(s);
    $("#run").addEventListener("click", () => this.run());
    $("#run2")?.addEventListener("click", () => this.run());
    $("#prev")?.addEventListener("click", () => this.step(-1));
    $("#next")?.addEventListener("click", () => this.step(1));
    $("#cl").addEventListener("change", async (e) => {
      const [id, v] = e.target.value.split("|");
      setState({ checklist: { id, version: Number(v) } });
      this.cl = await api.checklist(id, Number(v));
      this.types = Object.fromEntries(this.cl.criteria.map((c) => [c.id, c.type]));
    });
    $("#toggle-all")?.addEventListener("click", () => {
      if (this.opened.size) this.opened.clear(); else this.ev.criteria.forEach((c) => this.opened.add(c.criterion_id));
      this.repaintResult();
    });
    this.bindResult();
    this.querySelectorAll(".bubble.clamp").forEach((b) => b.addEventListener("click", () => b.classList.remove("clamp")));
    const svg = $("#evalbar");
    svg?.addEventListener("click", (e) => {
      const r = svg.getBoundingClientRect();
      const i = Math.round(((e.clientX - r.left) / r.width) * (this.conv.transcript.length - 1));
      this.goTo(Math.max(0, Math.min(this.conv.transcript.length - 1, i)));
    });
    this.querySelectorAll("[data-seek]").forEach((el) => el.addEventListener("click", () => this.seek(+el.dataset.seek)));
    this.bindPlayer();
    const focus = this.getAttribute("focus");
    if (focus && this.querySelector(`#m${focus}`)) { this.removeAttribute("focus"); setTimeout(() => this.goTo(+focus), 80); }
  }

  bindResult() {
    this.querySelectorAll(".crit[data-cid] .crit-h").forEach((h) => {
      const toggle = () => {
        const cid = h.parentElement.dataset.cid;
        this.opened.has(cid) ? this.opened.delete(cid) : this.opened.add(cid);
        h.parentElement.classList.toggle("open");
        h.setAttribute("aria-expanded", h.parentElement.classList.contains("open"));
      };
      h.addEventListener("click", toggle);
      h.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === h) { e.preventDefault(); e.stopPropagation(); toggle(); } });
    });
    this.querySelectorAll(".ev[data-i], .ev-link").forEach((el) => el.addEventListener("click", () => this.goTo(+el.dataset.i, el.dataset.q)));
    this.querySelectorAll(".ev[data-range]").forEach((el) => el.addEventListener("click", () => {
      const [a, b] = el.dataset.range.split("-").map(Number);
      this.querySelectorAll(".msg.in-range").forEach((m) => m.classList.remove("in-range"));
      for (let i = a; i <= b; i++) this.querySelector(`#m${i}`)?.classList.add("in-range");
      this.goTo(a);
    }));
    this.querySelectorAll("[data-appeal]").forEach((b) => b.addEventListener("click", () => this.appeal(b.dataset.appeal)));
  }

  repaintResult() {
    const r = this.querySelector("#result");
    if (!r || state.strict) return this.render();
    r.innerHTML = this.resultHtml();
    r.querySelector("#toggle-all")?.addEventListener("click", () => {
      if (this.opened.size) this.opened.clear(); else this.ev.criteria.forEach((c) => this.opened.add(c.criterion_id));
      this.repaintResult();
    });
    r.querySelector("#run2")?.addEventListener("click", () => this.run());
    this.bindResult();
  }

  onKey(e) {
    if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey || document.querySelector(".modal-back")) return;
    if (e.key === "j") return this.step(1);
    if (e.key === "k") return this.step(-1);
    if (e.key === "e" && !this.live) return this.run();
    if (e.key === "Escape") { location.hash = "#/"; return; }
    if (!this.ev || state.strict) return;
    const n = this.ev.criteria.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      this.focusIdx = e.key === "ArrowDown" ? Math.min(n - 1, this.focusIdx + 1) : Math.max(0, this.focusIdx - 1);
      this.querySelectorAll(".crit[data-cid]").forEach((el, i) => el.classList.toggle("focus", i === this.focusIdx));
      this.querySelectorAll(".crit[data-cid]")[this.focusIdx]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (e.key === "Enter" && this.focusIdx >= 0) {
      e.preventDefault();
      const el = this.querySelectorAll(".crit[data-cid]")[this.focusIdx];
      el?.querySelector(".crit-h").click();
      const first = this.ev.criteria[this.focusIdx].evidence?.[0];
      if (el?.classList.contains("open") && first) this.goTo(first.message_index, first.quote);
    }
  }

  step(d) {
    const order = window.__cqaOrder || [];
    const pos = order.indexOf(this.id_);
    const next = order[pos + d];
    if (pos >= 0 && next) location.hash = `#/c/${encodeURIComponent(next)}`;
  }

  goTo(i, quote) {
    const el = this.querySelector(`#m${i}`);
    if (!el) return;
    const chat = this.querySelector("#chat");
    chat.scrollTo({ top: el.offsetTop - chat.offsetTop - chat.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
    if (chat.getBoundingClientRect().top < 0 || chat.getBoundingClientRect().bottom > innerHeight) chat.scrollIntoView({ block: "nearest", behavior: "smooth" });
    this.querySelectorAll(".msg.flash").forEach((m) => m.classList.remove("flash"));
    this.querySelectorAll("mark.active").forEach((m) => m.classList.remove("active"));
    el.querySelector(".bubble.clamp")?.classList.remove("clamp");
    el.classList.add("flash");
    el.querySelectorAll("mark").forEach((mk) => { if (!quote || quote.toLowerCase().includes(mk.textContent.toLowerCase().slice(0, 20))) mk.classList.add("active"); });
    setTimeout(() => el.classList.remove("flash"), 2600);
    this.seek(i, false);
  }

  bindPlayer() {
    const audio = this.querySelector("#audio");
    if (!audio || !this.audio?.offsets) return;
    const off = this.audio.offsets;
    const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
    const play = this.querySelector("#play"), fill = this.querySelector("#fill"), time = this.querySelector("#time"), track = this.querySelector("#track");
    play.addEventListener("click", () => (audio.paused ? audio.play().catch((e) => toast(e.message, "error")) : audio.pause()));
    audio.addEventListener("play", () => { play.innerHTML = I.pause; });
    audio.addEventListener("pause", () => { play.innerHTML = I.play; });
    this.querySelector("#rate").addEventListener("change", (e) => { audio.playbackRate = parseFloat(e.target.value); });
    const pins = () => {
      if (!audio.duration) return;
      track.querySelectorAll(".pin").forEach((p) => p.remove());
      for (const a of this.ev?.annotations || []) {
        const s = off[a.message_index];
        if (s == null) continue;
        const pin = document.createElement("span");
        pin.className = "pin";
        pin.style.left = `${Math.min(100, (100 * s) / audio.duration)}%`;
        pin.title = `#${a.message_index} ${a.symbol} ${a.comment}`;
        pin.innerHTML = icon(a.symbol, "");
        pin.addEventListener("click", (e) => { e.stopPropagation(); this.goTo(a.message_index); this.seek(a.message_index); });
        track.append(pin);
      }
      time.textContent = `0:00 / ${fmt(audio.duration)}`;
    };
    audio.addEventListener("loadedmetadata", pins);
    if (audio.readyState >= 1) pins();
    track.addEventListener("click", (e) => {
      if (!audio.duration) return;
      const r = track.getBoundingClientRect();
      audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
    });
    let last = -1;
    audio.addEventListener("timeupdate", () => {
      if (audio.duration) { fill.style.width = `${(100 * audio.currentTime) / audio.duration}%`; time.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`; }
      if (audio.paused) return;
      let idx = -1;
      for (let i = 0; i < off.length; i++) if (off[i] != null && off[i] <= audio.currentTime + 0.5) idx = i;
      if (idx !== last && idx >= 0) {
        last = idx;
        this.querySelectorAll(".msg.playing").forEach((m) => m.classList.remove("playing"));
        const el = this.querySelector(`#m${idx}`);
        el?.classList.add("playing");
        const chat = this.querySelector("#chat");
        if (el && chat) chat.scrollTo({ top: el.offsetTop - chat.offsetTop - chat.clientHeight / 2, behavior: "smooth" });
      }
    });
  }

  seek(i, play = true) {
    const audio = this.querySelector("#audio");
    const s = this.audio?.offsets?.[i];
    if (!audio || s == null) return;
    audio.currentTime = Math.max(0, s - 1.5);
    if (play) audio.play().catch(() => {});
  }

  async run() {
    if (this.live) return;
    const body = { conversation_id: this.id_, checklist_id: this.cl?.id, checklist_version: this.cl?.version, annotations: true };
    this.live = { criteria: this.cl.criteria.map((c) => ({ id: c.id, name: c.name })), results: {}, done: 0, model: "" };
    const t0 = performance.now();
    this.ev = null;
    this.render();
    this.querySelector("#live").classList.remove("hidden");
    this.querySelector("#run").disabled = true;
    this._timer = setInterval(() => { const el = this.querySelector("#live-t"); if (el) el.textContent = `${((performance.now() - t0) / 1000).toFixed(1)} ${t("sec")}`; }, 100);
    this._abort = new AbortController();
    const repaint = () => { const r = this.querySelector("#result"); if (r) r.innerHTML = this.liveHtml(); };
    let doneId = null;
    try {
      await api.evaluate(body, (ev) => {
        if (ev.type === "start") { this.live.criteria = ev.criteria; this.live.model = ev.model; repaint(); }
        else if (ev.type === "criterion") {
          this.live.results[ev.criterion_id] = ev;
          this.live.done++;
          if (this.live.done === this.live.criteria.length) this.live.annotating = true;
          this.querySelector("#live-c").textContent = fmtUsd(ev.cost_usd);
          repaint();
        } else if (ev.type === "done") { this.querySelector("#live-c").textContent = fmtUsd(ev.cost_usd); doneId = ev.evaluation_id; }
        else if (ev.type === "error") throw new Error(ev.error);
      }, this._abort.signal);
      clearInterval(this._timer);
      this.ev = await api.evaluation(doneId);
      this.autoOpen();
      this.live = null;
      await this.loadChecklist();
      this.render();
      toast(`${t("total")} ${fmtNum(this.ev.total_score)} · ${fmtUsd(this.ev.cost_usd)} · ${(this.ev.duration_ms / 1000).toFixed(1)} ${t("sec")}`, "ok");
    } catch (e) {
      clearInterval(this._timer);
      this.live = null;
      this.ev = await api.latestEvaluation(this.id_).catch(() => null);
      this.render();
      if (e.name !== "AbortError") toast(e.message, "error");
    }
  }

  appeal(criterionId) {
    const c = this.ev.criteria.find((x) => x.criterion_id === criterionId);
    modal(`<div class="card-h"><h2>${t("appeal_title")}</h2></div>
      <div class="card-b col" style="gap:12px">
        <div class="row">${answerChip({ ...c, type: this.types[c.criterion_id] })}<b>${esc(c.name)}</b></div>
        <div class="muted small">${esc(c.explanation)}</div>
        <textarea class="input" rows="4" id="ap-comment" placeholder="${t("appeal_comment")}"></textarea>
        <div class="row"><span class="grow"></span><button class="btn" id="ap-cancel">${t("cancel")}</button><button class="btn primary" id="ap-send">${t("submit")}</button></div>
      </div>`, (root, close) => {
      root.querySelector("#ap-comment").focus();
      root.querySelector("#ap-cancel").addEventListener("click", close);
      root.querySelector("#ap-send").addEventListener("click", async () => {
        const comment = root.querySelector("#ap-comment").value.trim();
        if (!comment) return root.querySelector("#ap-comment").focus();
        try {
          await api.createAppeal(this.ev.evaluation_id, criterionId, comment);
          close();
          toast(t("appeal_sent"), "ok");
          this.ev = await api.evaluation(this.ev.evaluation_id);
          this.opened.add(criterionId);
          this.repaintResult();
          window.dispatchEvent(new CustomEvent("cqa:appeals-changed"));
        } catch (e) { toast(e.message, "error"); }
      });
    });
  }
}
customElements.define("cqa-review", CqaReview);
