import { t } from "./i18n.js";

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const SYMBOL_ICON = { "!!": "brilliant", "!": "great", "?!": "Inaccuracy", "?": "Mistake", "??": "Blunder" };
export const icon = (sym, cls = "icon-move") => SYMBOL_ICON[sym]
  ? `<img class="icon-move ${cls}" src="${window.CQA_API_BASE || ""}/assets/icons/${SYMBOL_ICON[sym]}.png" alt="${esc({ "!!": "блестяще", "!": "хорошо", "?!": "неточность", "?": "ошибка", "??": "зевок" }[sym] || sym)}">` : "";

export const scoreClass = (s) => (s == null ? "s-none" : s >= 85 ? "s-hi" : s >= 60 ? "s-mid" : "s-lo");
export const scorePill = (s) => `<span class="score-pill ${scoreClass(s)}">${s == null ? "—" : fmtNum(s)}</span>`;
export const fmtNum = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d).replace(/\.?0+$/, ""));
export const fmtUsd = (n) => "$" + (n || 0).toFixed(n >= 1 ? 2 : 4);
export const fmtSec = (s) => (s == null ? "—" : s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : `${Math.round(s)} ${t("sec")}`);

export function answerChip(c) {
  if (!c || c.answer == null) return `<span class="chip ans">…</span>`;
  if (c.answer === "na") return `<span class="chip na ans">N/A</span>`;
  if (c.answer === "scale") {
    const r = c.max_score ? c.score / c.max_score : 0;
    return `<span class="chip ans ${r >= 0.99 ? "ok" : r >= 0.5 ? "warn" : "bad"}">${c.value}</span>`;
  }
  if (c.type === "flag") return c.answer === "yes" ? `<span class="chip bad ans">${t("yes")}</span>` : `<span class="chip ok ans">${t("no")}</span>`;
  return c.answer === "yes" ? `<span class="chip ok ans">${t("yes")}</span>` : `<span class="chip bad ans">${t("no")}</span>`;
}

export function ansText(answer, value) {
  if (answer === "na") return "N/A";
  if (answer === "scale") return String(value);
  return answer === "yes" ? t("yes") : answer === "no" ? t("no") : String(answer ?? "—");
}

export function toast(msg, kind = "") {
  let box = document.querySelector(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.append(box); }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="ti">${kind === "error" ? "!" : kind === "ok" ? "✓" : "i"}</span><span></span>`;
  el.lastChild.textContent = msg;
  box.append(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, kind === "error" ? 7000 : 3200);
}

export function modal(html, onMount) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", onKey);
  document.body.append(back);
  onMount && onMount(back.querySelector(".modal"), close);
  return close;
}

// Кольцо итогового балла в стиле Activity rings.
export function ring(score, size = 92) {
  const r = 41, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, score ?? 0));
  const color = score == null ? "var(--text-3)" : score >= 85 ? "var(--green)" : score >= 60 ? "var(--orange)" : "var(--red)";
  return `<div class="ring" style="width:${size}px;height:${size}px"><svg viewBox="0 0 92 92"><circle class="bg" cx="46" cy="46" r="${r}"/>
    <circle class="fg" cx="46" cy="46" r="${r}" stroke="${color}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - p / 100)}"/></svg>
    <div class="num">${score == null ? "—" : fmtNum(score, 1)}</div></div>`;
}

// Подсветка цитат в тексте реплики: ищем интервалы (без учёта регистра, «»/"", ё/е, многоточий),
// объединяем пересечения и размечаем один раз — без вложенных <mark>.
export function highlight(text, quotes) {
  const norm = (x) => x.replace(/[«»“”„]/g, '"').replace(/ё/g, "е").replace(/Ё/g, "Е").toLowerCase();
  const src = norm(String(text ?? ""));
  const ranges = [];
  for (const q of quotes || []) {
    for (const part of String(q || "").split(/\.\.\.|…/).map((p) => p.trim()).filter((p) => p.length > 1)) {
      const i = src.indexOf(norm(part));
      if (i >= 0) ranges.push([i, i + part.length]);
    }
  }
  if (!ranges.length) return esc(text);
  ranges.sort((x, y) => x[0] - y[0]);
  const merged = [ranges[0].slice()];
  for (const [s0, e0] of ranges.slice(1)) {
    const last = merged[merged.length - 1];
    if (s0 <= last[1]) last[1] = Math.max(last[1], e0); else merged.push([s0, e0]);
  }
  let out = "", pos = 0;
  for (const [s0, e0] of merged) {
    out += esc(text.slice(pos, s0)) + `<mark>${esc(text.slice(s0, e0))}</mark>`;
    pos = e0;
  }
  return out + esc(text.slice(pos));
}

export const debounce = (fn, ms = 200) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

export const store = {
  get(k, d) { try { const v = localStorage.getItem("cqa." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cqa." + k, JSON.stringify(v)); } catch {} },
};
