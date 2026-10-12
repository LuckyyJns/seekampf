"use strict";
// ======================================================================
// Akkordeon-Helfer: offene Inseln bleiben beim Neuzeichnen offen
// ======================================================================
const offeneInseln = { flotte: new Set(), upgrade: new Set(), ausbildung: new Set() };
try {
  for (const k of Object.keys(offeneInseln)) (JSON.parse(localStorage.getItem(`offen-${k}`) || "[]")).forEach(x => offeneInseln[k].add(String(x)));
} catch (e) { /* egal */ }
function akkordeonKlick(bereich, container) {
  container.addEventListener("click", (e) => {
    const kopf = e.target.closest(".insel-kopf");
    if (!kopf || e.target.closest(".schalter, button, a, input, select, label")) return;
    const karte = kopf.parentElement, id = karte.dataset.insel;
    karte.classList.toggle("offen");
    if (karte.classList.contains("offen")) offeneInseln[bereich].add(id); else offeneInseln[bereich].delete(id);
    try { localStorage.setItem(`offen-${bereich}`, JSON.stringify([...offeneInseln[bereich]])); } catch (err) { /* egal */ }
    if (bereich === "flotte" && karte.classList.contains("offen") && verlauf) {
      const reihe = verlauf.inseln[id];
      if (reihe) diagramm(`fl-diagramm-${id}`, reihe);
    }
  });
}
/** Karte je Insel anlegen, falls noch nicht da; liefert das Element. */
function inselKarte(container, id, rumpfHtml) {
  let karte = container.querySelector(`.insel-karte[data-insel="${id}"]`);
  if (!karte) {
    karte = document.createElement("div");
    karte.className = "insel-karte";
    karte.dataset.insel = id;
    karte.innerHTML = `<div class="insel-kopf"></div><div class="insel-rumpf">${rumpfHtml}</div>`;
    container.appendChild(karte);
  }
  return karte;
}
