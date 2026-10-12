"use strict";
// ======================================================================
// Ungespeicherte Aenderungen: jedes Formular mit data-form meldet sich hier
// ======================================================================
const ungespeichert = new Set();
function dirtySetzen(form, an) {
  if (an) ungespeichert.add(form); else ungespeichert.delete(form);
  document.querySelectorAll(`[data-dirty-anzeige="${form}"]`).forEach(el => el.hidden = !an);
}
document.addEventListener("input", (e) => {
  const form = e.target.closest("[data-form]");
  if (form) dirtySetzen(form.dataset.form, true);
});
document.addEventListener("change", (e) => {
  const form = e.target.closest("[data-form]");
  if (form) dirtySetzen(form.dataset.form, true);
});
window.addEventListener("beforeunload", (e) => {
  if (ungespeichert.size) { e.preventDefault(); e.returnValue = ""; }
});
function ungespeichertIn(tab) {
  const container = $(`tab-${tab}`);
  return [...ungespeichert].filter(f => container && container.querySelector(`[data-form="${f}"]`));
}
