"use strict";
// ======================================================================
// Rohstoff-Kacheln (Allianz-Anfrage, Lieferung von Hand): Mengen per Klick wählen
// ======================================================================
function rkKlick(e, maxFuer) {
  const add = e.target.closest("[data-rk-add]"), mx = e.target.closest("[data-rk-max]"), nul = e.target.closest("[data-rk-null]");
  const knopf = add || mx || nul;
  if (!knopf) return false;
  const feld = $(knopf.dataset.rkAdd || knopf.dataset.rkMax || knopf.dataset.rkNull);
  if (add) feld.value = (Number(feld.value) || 0) + Number(add.dataset.n);
  else if (mx) feld.value = Math.max(0, Math.floor(maxFuer(mx.dataset.k)));
  else feld.value = "";
  feld.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
}
function zeichneAnfrageKacheln() {
  const i = anfrageInsel();
  if (!i) return;
  for (const [k] of ROHSTOFFE) {
    const el = document.querySelector(`[data-rk-bestand="al-anfrage-${k}"]`);
    if (el) el.textContent = `${zahl(i.rohstoffe[k])} von ${zahl(i.rohstoffe.kapazitaet)} im Lager`;
  }
  anfrageSumme();
}
const anfrageInsel = () => allianz?.status?.inseln?.find(x => x.koordinaten === $("al-anfrage-insel").value) || allianz?.status?.inseln?.[0];
function anfrageSumme() {
  const i = anfrageInsel(), el = $("al-anfrage-summe");
  if (!i || !el) return;
  const kap = i.rohstoffe.kapazitaet || 0, teile = [], warn = [];
  for (const [k, n] of ROHSTOFFE) {
    const v = Number($(`al-anfrage-${k}`).value || 0);
    if (v > 0) { teile.push(`${zahl(v)} ${n}`); if ((i.rohstoffe[k] || 0) + v > kap) warn.push(`${n}: passt nicht ins Lager (frei ${zahl(Math.max(0, kap - i.rohstoffe[k]))})`); }
  }
  el.innerHTML = teile.length ? `Anfrage für ${esc(i.name)}: <b>${teile.join(", ")}</b>${warn.length ? ` <span class="fehlt">· ${warn.join("; ")}</span>` : ""}`
    : `<span class="leise">Kacheln anklicken oder Mengen eintippen.</span>`;
}
$("al-anfrage-kacheln").addEventListener("click", (e) => {
  rkKlick(e, (k) => { const i = anfrageInsel(); return i ? Math.max(0, (i.rohstoffe.kapazitaet || 0) - (i.rohstoffe[k] || 0)) : 0; });
});
$("al-anfrage-kacheln").addEventListener("input", anfrageSumme);
$("al-anfrage-insel").addEventListener("change", zeichneAnfrageKacheln);
