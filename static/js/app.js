// Оболочка: боковая панель, маршрутизация по hash, общие настройки (язык, тема, строгий режим).
import { api } from "./api.js";
import { t, setLang, getLang } from "./i18n.js";
import { state, setState } from "./state.js";
import { I } from "./icons.js";
import "./components/cqa-list.js";
import "./components/cqa-review.js";
import "./components/cqa-appeal.js";
import "./components/cqa-checklist-editor.js";

class CqaApp extends HTMLElement {
  connectedCallback() {
    this.renderShell();
    window.addEventListener("hashchange", () => this.route());
    window.addEventListener("cqa:lang", () => { this.renderShell(); this.route(true); });
    window.addEventListener("cqa:appeals-changed", () => this.refreshCounts());
    this.route();
    this.refreshCounts();
  }

  renderShell() {
    const dark = document.documentElement.dataset.theme === "dark";
    this.innerHTML = `
      <div class="shell">
        <aside class="sidebar" aria-label="CQA">
          <a class="brand" href="#/"><span class="brand-mark">FB</span><span class="brand-text"><b>CQA</b><small>Freedom Broker</small></span></a>
          <nav aria-label="${t("workspace")}" style="display:contents"><div class="side-label">${t("workspace")}</div>
          <a class="nav-item" href="#/" data-nav="list">${I.list}<span>${t("nav_list")}</span></a>
          <a class="nav-item" href="#/appeals" data-nav="appeals">${I.scale}<span>${t("nav_appeals")}</span><span class="badge hidden" id="appeal-count"></span></a>
          <a class="nav-item" href="#/checklist" data-nav="checklist">${I.checklist}<span>${t("nav_checklist")}</span></a></nav>
          <div class="side-foot">
            <label class="switch small"><input type="checkbox" id="strict" ${state.strict ? "checked" : ""}><span>${t("strict")}</span></label>
            <label class="switch small"><input type="checkbox" id="theme" ${dark ? "checked" : ""}><span>${t("dark")}</span></label>
            <div class="seg small" id="lang">${["ru", "en", "kz"].map((l) => `<button data-l="${l}" class="${getLang() === l ? "on" : ""}">${l.toUpperCase()}</button>`).join("")}</div>
            <div class="kbd-hint" id="spend"></div>
            <div class="kbd-hint"><b>${t("hk")}</b><br>${t("hk_list")}<br>${t("hk_crit")}<br>${t("hk_eval")}</div>
          </div>
        </aside>
        <main class="view" id="view"></main>
      </div>`;
    this.querySelector("#strict").addEventListener("change", (e) => setState({ strict: e.target.checked }));
    this.querySelectorAll("#lang button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.l)));
    this.querySelector("#theme").addEventListener("change", (e) => {
      const next = e.target.checked ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("cqa.theme", next); } catch {}
    });
  }

  route(force = false) {
    const hash = location.hash.replace(/^#\/?/, "");
    const [page, ...rest] = hash.split("/");
    if (!force && this._key === hash) return;
    this._key = hash;
    const view = this.querySelector("#view");
    let el;
    if (page === "c" && rest[0]) {
      el = document.createElement("cqa-review");
      el.setAttribute("conversation-id", decodeURIComponent(rest[0]));
      if (rest[1]) el.setAttribute("focus", rest[1]);
    } else if (page === "appeals") {
      el = document.createElement("cqa-appeal");
      if (rest[0]) el.setAttribute("appeal-id", rest[0]);
    } else if (page === "checklist") {
      el = document.createElement("cqa-checklist-editor");
    } else {
      el = document.createElement("cqa-list");
    }
    view.replaceChildren(el);
    view.style.animation = "none"; void view.offsetWidth; view.style.animation = "";
    this.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (page === "c" || !page ? "list" : page)));
    window.scrollTo(0, 0);
  }

  async refreshCounts() {
    try {
      const h = await api.health();
      const el = this.querySelector("#spend");
      if (el) el.innerHTML = `<b>${t("model")}</b><br>${h.criteria_model}${h.votes > 1 ? ` · 🗳×${h.votes}` : ""}<br>${t("spent_today")}: $${h.spent_today_usd.toFixed(2)}${h.daily_budget_usd ? ` / $${h.daily_budget_usd}` : ""}`;
    } catch {}
    try {
      const open = await api.appeals("open");
      const el = this.querySelector("#appeal-count");
      el.textContent = open.length;
      el.classList.toggle("hidden", !open.length);
    } catch {}
  }
}
customElements.define("cqa-app", CqaApp);

// Порядок диалогов в списке — для перехода J/K из карточки.
export function setNavOrder(ids) { window.__cqaOrder = ids; }
