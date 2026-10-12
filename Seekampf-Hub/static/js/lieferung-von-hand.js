"use strict";
// ======================================================================
// Rohstoff-Ausgleich: Lieferung von Hand
// ======================================================================
const HANDEL_LADUNG = { kleines_handelsschiff: 75, grosses_handelsschiff: 460 };
const lieferInsel = (id) => ausgleich?.inseln?.find(x => String(x.id) === String(id));
const lieferKapazitaet = (i) => Object.entries(HANDEL_LADUNG).reduce((n, [t, v]) => n + (i?.handelsschiffe?.[t] || 0) * v, 0);
function zeichneAusLieferung() {
  if (!ausgleich) return;
  const eigene = ausgleich.inseln.filter(i => i.gehoert_uns);
  const von = $("aus-l-von"), nach = $("aus-l-nach");
  const optionen = eigene.map(i => `<option value="${i.id}">${esc(i.name)} · ${esc(i.koordinaten)}</option>`).join("");
  if (von.dataset.stand !== optionen) {
    const v0 = von.value, n0 = nach.value;
    von.innerHTML = nach.innerHTML = optionen; von.dataset.stand = optionen;
    von.value = v0 || (eigene[0] && eigene[0].id);
    nach.value = n0 || (eigene.find(i => String(i.id) !== String(von.value)) || {}).id;
  }
  const i = lieferInsel(von.value);
  for (const [k] of ROHSTOFFE) {
    const el = document.querySelector(`[data-rk-bestand="aus-l-${k}"]`);
    if (el && i) el.textContent = `${zahl(i.rohstoffe[k])} im Lager`;
  }
  ausLieferSumme();
}
function ausLieferSumme() {
  const i = lieferInsel($("aus-l-von").value), ziel = lieferInsel($("aus-l-nach").value), el = $("aus-l-summe"), knopf = $("aus-l-senden");
  if (!i || !ziel) return;
  const kap = lieferKapazitaet(i);
  const menge = {}; let summe = 0; const probleme = [];
  for (const [k, n] of ROHSTOFFE) {
    const v = Math.max(0, Number($(`aus-l-${k}`).value || 0)); menge[k] = v; summe += v;
    if (v > (i.rohstoffe[k] || 0)) probleme.push(`${n}: nur ${zahl(i.rohstoffe[k])} im Lager`);
  }
  if (String(i.id) === String(ziel.id)) probleme.push("Start und Ziel sind dieselbe Insel");
  if (summe > kap) probleme.push(`mehr als die Handelsschiffe tragen (${zahl(kap)})`);
  if (i.bedrohung) probleme.push("Start-Insel wird bedroht");
  const schiffe = Object.entries(HANDEL_LADUNG).filter(([t]) => i.handelsschiffe?.[t]).map(([t]) => `${i.handelsschiffe[t]} ${t.startsWith("kleines") ? "klein" : "groß"}`).join(", ") || "keine";
  el.innerHTML = `<div>${summe ? `<b>${zahl(summe)}</b> Ladung von ${esc(i.name)} nach ${esc(ziel.name)}` : `<span class="leise">Kacheln anklicken oder Mengen eintippen.</span>`}</div>
    <div class="leise">Handelsschiffe im Hafen von ${esc(i.name)}: ${schiffe} – tragen bis ${zahl(kap)}${probleme.length ? "" : ""}</div>
    ${probleme.length ? `<div class="fehlt">${probleme.map(esc).join(" · ")}</div>` : ""}`;
  knopf.disabled = !summe || probleme.length > 0;
  knopf._menge = menge;
}
$("aus-l-kacheln").addEventListener("click", (e) => {
  rkKlick(e, (k) => { const i = lieferInsel($("aus-l-von").value); if (!i) return 0;
    const schon = ROHSTOFFE.reduce((n, [r]) => n + (r === k ? 0 : Number($(`aus-l-${r}`).value || 0)), 0);
    return Math.min(i.rohstoffe[k] || 0, Math.max(0, lieferKapazitaet(i) - schon)); });
});
$("aus-l-kacheln").addEventListener("input", ausLieferSumme);
$("aus-l-von").addEventListener("change", zeichneAusLieferung);
$("aus-l-nach").addEventListener("change", ausLieferSumme);
$("aus-l-senden").onclick = async (e) => {
  const von = lieferInsel($("aus-l-von").value), nach = lieferInsel($("aus-l-nach").value), menge = e.target._menge || {};
  const text = ROHSTOFFE.filter(([k]) => menge[k] > 0).map(([k, n]) => `${zahl(menge[k])} ${n}`).join(", ");
  const rohstoffe = Object.fromEntries(Object.entries(menge).filter(([, v]) => v > 0));
  if (!(await bestaetigen({ titel: `Von ${von.name} nach ${nach.name} liefern?`, text: "Die Handelsschiffe der Start-Insel fahren sofort los.", icon: "rohstoffausgleich", html: mengenHtml(rohstoffe), ok: "Losschicken" }))) return;
  const r = await aktion(e.target, () => post("/api/flotte/ausgleich/liefern", { von: von.id, nach: nach.id, rohstoffe }),
    `Lieferung ${von.name} → ${nach.name} losgeschickt`);
  if (!r) return;
  ROHSTOFFE.forEach(([k]) => $(`aus-l-${k}`).value = "");
  dirtySetzen("aus-liefern", false);
  setTimeout(ausgleichHolen, 2000);
};
