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

// Unterbereiche einer Inselkarte (z. B. "Flotten unterwegs", "Zielrotation")
// einklappen: Klick auf die Ueberschrift. Zugeklappt bleibt es auch beim
// Neuzeichnen und nach dem Neuladen (je Bereich, Insel und Unterbereich).
const zuUnterteile = new Set();
try { (JSON.parse(localStorage.getItem("zu-unterteile") || "[]")).forEach(x => zuUnterteile.add(String(x))); } catch (e) { /* egal */ }
/** Ueberschrift eines einklappbaren Unterbereichs (mit Platz fuer die Anzahl). */
function klappTitel(text) {
  return `<h2 class="klapp-titel" role="button" tabindex="0" aria-expanded="true"><span class="pfeil">▶</span>${esc(text)}<span class="klapp-anzahl mono"></span></h2>`;
}
/** Zustand aus dem Speicher auf neu angelegte Unterbereiche einer Karte uebertragen. */
function klappZustand(bereich, karte) {
  karte.querySelectorAll(".unterteil[data-klapp]").forEach((u) => {
    const zu = zuUnterteile.has(`${bereich}:${karte.dataset.insel}:${u.dataset.klapp}`);
    u.classList.toggle("zu", zu);
    u.querySelector(".klapp-titel")?.setAttribute("aria-expanded", zu ? "false" : "true");
  });
}
/** Anzahl hinter der Ueberschrift (sichtbar vor allem im zugeklappten Zustand). */
function klappAnzahl(karte, teil, text) {
  const el = karte.querySelector(`.unterteil[data-klapp="${teil}"] .klapp-anzahl`);
  if (el) el.textContent = text ? ` · ${text}` : "";
}
function klappKlick(bereich, container) {
  const umschalten = (titel) => {
    const u = titel.closest(".unterteil[data-klapp]"), karte = titel.closest(".insel-karte");
    if (!u || !karte) return;
    const schluessel = `${bereich}:${karte.dataset.insel}:${u.dataset.klapp}`;
    const zu = u.classList.toggle("zu");
    titel.setAttribute("aria-expanded", zu ? "false" : "true");
    if (zu) zuUnterteile.add(schluessel); else zuUnterteile.delete(schluessel);
    try { localStorage.setItem("zu-unterteile", JSON.stringify([...zuUnterteile])); } catch (err) { /* egal */ }
  };
  container.addEventListener("click", (e) => {
    const titel = e.target.closest(".klapp-titel");
    if (titel && container.contains(titel)) umschalten(titel);
  });
  container.addEventListener("keydown", (e) => {
    const titel = e.target.closest?.(".klapp-titel");
    if (titel && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); umschalten(titel); }
  });
}
