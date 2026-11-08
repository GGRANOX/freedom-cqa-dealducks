import { store } from "./util.js";

// Общие настройки интерфейса (сохраняются в браузере).
export const state = {
  strict: store.get("strict", false),
  group: store.get("group", null),
  checklist: store.get("checklist", null), // {id, version}
};
export function setState(patch) {
  Object.assign(state, patch);
  for (const k of Object.keys(patch)) store.set(k, state[k]);
  window.dispatchEvent(new CustomEvent("cqa:state", { detail: patch }));
}
