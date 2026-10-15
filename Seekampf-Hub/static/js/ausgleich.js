"use strict";
// ======================================================================
// Takt
// ======================================================================
// ======================================================================
// Rohstoff-Ausgleich (laeuft im Flotten-Manager)
// ======================================================================
let ausgleich = null, ausgleichFormGebaut = "";

function ausgleichLeiste(r, k, kap, unterwegs, ziel, grenze) {
  const b = Math.min(100, 100 * (r[k] || 0) / kap);
  const u = Math.max(0, Math.min(100 - b, 100 * (unterwegs || 0) / kap));
  const strich = (wert, klasse, titel) => wert == null ? "" :
    `<span class="strich ${klasse}" title="${titel}" style="left:${Math.min(99, 100 * wert)}%"></span>`;
  return `<span class="aus-leiste"><i style="width:${b}%;background:var(--${k})"></i><i class="unterwegs" style="width:${u}%;background:var(--${k})"></i>
    ${strich(ziel, "", "auffüllen bis")}${strich(grenze, "grenze", "gibt erst darüber ab")}</span>`;
}
/** Karte einer Insel einmal anlegen; danach werden nur die Teile neu gezeichnet. */
function ausgleichKarte(i) {
  const box = $("aus-inseln");
  let k = box.querySelector(`[data-aus-insel="${i.id}"]`);
  if (k) return k;
  box.querySelector(".leise:not([data-aus-insel])")?.remove();
  k = document.createElement("section");
  k.className = "karte";
  k.dataset.ausInsel = i.id;
  k.innerHTML = `<div class="oben" data-teil="kopf"></div>
    <div class="aus-schalter">
      <label class="schalter"><input type="checkbox" data-aus-feld="gibt"><span class="spur"></span>gibt ab</label>
      <label class="schalter"><input type="checkbox" data-aus-feld="bekommt"><span class="spur"></span>bekommt</label>
    </div>
    <div data-teil="balken"></div>
    <div class="aus-form" data-form="aus-insel-${i.id}">
      <div class="feld"><label>Auffüllen bis %</label><input type="number" min="0" max="95" step="5" data-aus-feld="ziel"></div>
      <div class="feld"><label>Behält min. %</label><input type="number" min="0" max="95" step="5" data-aus-feld="reserve"></div>
      <div class="feld"><label>Max. je Lieferung</label><input type="number" min="0" step="100" data-aus-feld="max_abgabe" placeholder="unbegrenzt"></div>
    </div>
    <div class="formular-leiste" style="margin:0">
      <button class="klein" data-aus-speichern="${i.id}">Grenzen speichern</button>
      <span class="ungespeichert-marke" data-dirty-anzeige="aus-insel-${i.id}" hidden>● ungespeichert</span>
      <span class="leise klein-text">leer = Standard</span>
    </div>
    <div data-teil="info" class="klein-text"></div>`;
  box.appendChild(k);
  return k;
}
function zeichneAusgleichInsel(i, st) {
  const k = ausgleichKarte(i);
  const e = i.einstellung || {}, r = i.rohstoffe || {}, kap = r.kapazitaet || 1;
  let marke = "";
  if (i.bedrohung) marke = `<span class="marke warn"><span class="punkt"></span>Bedrohung</span>`;
  else if (i.reserviert) marke = `<span class="marke warn"><span class="punkt"></span>hält ${
    (i.reserviert_typen || []).length === 1 && i.reserviert_typen[0] === "grosses_handelsschiff" ? "große " : ""}Handelsschiffe zurück</span>`;
  else if (!e.gibt && !e.bekommt) marke = `<span class="marke grau"><span class="punkt"></span>nimmt nicht teil</span>`;
  k.querySelector('[data-teil="kopf"]').innerHTML = `${avatar(i.name, i.id)}
    <span class="namenblock" style="display:flex;flex-direction:column"><b>${esc(i.name)}</b><span class="leise mono klein-text">${esc(i.koordinaten)}</span></span>
    <span style="margin-left:auto">${marke}</span>`;
  k.querySelector('[data-aus-feld="gibt"]').checked = !!e.gibt;
  k.querySelector('[data-aus-feld="bekommt"]').checked = !!e.bekommt;

  const zahlen = (x) => {
    const teile = [`${zahl(r[x])}`];
    if (i.unterwegs?.[x]) teile.push(`<span class="leise">+${zahl(i.unterwegs[x])}</span>`);
    if (e.bekommt && i.bedarf?.[x]) teile.push(`<span style="color:var(--warn)">fehlt ${zahl(i.bedarf[x])}</span>`);
    if (e.gibt && i.ueberschuss?.[x]) teile.push(`<span style="color:var(--gut)">frei ${zahl(i.ueberschuss[x])}</span>`);
    return teile.join(" ");
  };
  k.querySelector('[data-teil="balken"]').innerHTML = `<div class="res" style="grid-template-columns:56px 1fr auto">${ROHSTOFFE.map(([x, n]) => `
      <span class="res-key">${ROHSTOFF_ICON(x)}${n}</span>
      ${ausgleichLeiste(r, x, kap, i.unterwegs?.[x], e.bekommt ? i.soll?.[x] / kap : null, e.gibt ? i.grenze?.[x] / kap : null)}
      <span class="mono klein-text">${zahlen(x)}</span>`).join("")}</div>`;

  // Formular nur neu fuellen, wenn niemand tippt und sich etwas geaendert hat.
  const form = k.querySelector(".aus-form"), stand = JSON.stringify([e, st.ausgleich_ziel, st.ausgleich_reserve]);
  if (!ungespeichert.has(form.dataset.form) && form.dataset.gebaut !== stand) {
    form.dataset.gebaut = stand;
    const feld = (n) => form.querySelector(`[data-aus-feld="${n}"]`);
    feld("ziel").value = e.ziel == null ? "" : Math.round(e.ziel * 100);
    feld("ziel").placeholder = `Standard ${Math.round(st.ausgleich_ziel * 100)}`;
    feld("reserve").value = e.reserve == null ? "" : Math.round(e.reserve * 100);
    feld("reserve").placeholder = `Standard ${Math.round(st.ausgleich_reserve * 100)}`;
    feld("max_abgabe").value = e.max_abgabe ? e.max_abgabe : "";
  }

  const schiffe = (i.handelsschiffe?.kleines_handelsschiff || 0) + (i.handelsschiffe?.grosses_handelsschiff || 0);
  const zeilen = [`Lager ${zahl(kap)} · ${schiffe} Handelsschiffe im Hafen${e.gibt && !i.schiffe_kap ? " – ohne Handelsschiffe kann sie nichts abgeben" : ""}`];
  if (i.upgrade && (e.bekommt || e.gibt)) zeilen.push(`Upgrade-Bot als Nächstes: ${esc(gname(i.upgrade.gebaeude))} ${i.upgrade.ziel_stufe ?? ""}${
      Object.keys(i.upgrade.fehlt || {}).length ? ` – fehlt ${beuteText(i.upgrade.fehlt)} (wird nicht abgegeben)` : ""}`);
  if (i.hinweis && (e.gibt || e.bekommt)) zeilen.push(`<span style="color:var(--text)">${esc(i.hinweis)}</span>`);
  k.querySelector('[data-teil="info"]').innerHTML = zeilen.map(z => `<div class="leise">${z}</div>`).join("");
}
function zeichneAusgleich() {
  const a = ausgleich;
  if (!a) return;
  const st = a.settings;
  $("aus-aktiv").checked = !!st.ausgleich_aktiv;
  $("aus-hinweis").textContent = !a.laeuft ? "Flotten-Manager ist gestoppt – es wird nichts geliefert"
    : a.hinweis ? a.hinweis : "aktiv – prüft alle 5 Sekunden";
  const gebaut = JSON.stringify(st);
  if (!ungespeichert.has("aus-settings") && ausgleichFormGebaut !== gebaut) {
    ausgleichFormGebaut = gebaut;
    $("aus-ziel").value = Math.round(st.ausgleich_ziel * 100);
    $("aus-reserve").value = Math.round(st.ausgleich_reserve * 100);
    $("aus-min").value = st.ausgleich_min_menge;
    $("aus-upgrade").checked = !!st.ausgleich_upgrade_bedarf;
  }
  const g = a.stand.geliefert || {};
  $("aus-kacheln").innerHTML = [
    { wert: zahl(a.stand.fahrten || 0), label: "Lieferungen" },
    ...ROHSTOFFE.map(([k, n]) => ({ wert: zahl(g[k] || 0), label: `${n} geliefert`, farbe: `var(--${k})` })),
  ].map(x => `<div class="kachel" ${x.farbe ? `style="--kachel-farbe:${x.farbe}"` : ""}>
      <div class="beschriftung">${x.farbe && /^(gold|stein|holz)/i.test(x.label) ? ROHSTOFF_ICON(x.label.toLowerCase().split(" ")[0]) : (x.farbe ? `<i class="swatch" style="background:${x.farbe}"></i>` : "")}${x.label}</div>
      <div class="wert">${x.wert}</div></div>`).join("");

  const eigene = a.inseln.filter(i => i.gehoert_uns);
  if (!eigene.length) $("aus-inseln").innerHTML = `<div class="leise">keine Inseln</div>`;
  eigene.forEach(i => zeichneAusgleichInsel(i, st));

  const l = a.stand.lieferungen || [];
  $("aus-lieferungen").innerHTML = l.length ? `<div class="tabelle-rahmen"><table><thead><tr><th>Abfahrt</th><th>Von → Nach</th>
      <th>Rohstoffe</th><th class="verstecken-schmal">Schiffe</th><th class="zahl">Ankunft</th></tr></thead><tbody>${l.map(x => `<tr>
      <td>${uhrzeit(x.zeit)}</td><td>${esc(x.von_name)} → ${esc(x.nach_name)}</td><td>${beuteText(x.rohstoffe)}</td>
      <td class="verstecken-schmal klein-text">${mengenText(x.schiffe)}</td>
      <td class="zahl">${x.ankunft && x.ankunft > jetzt() ? `<b>${countdown(x.ankunft, "angekommen")}</b>` : uhrzeit(x.ankunft)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="leise klein-text">noch keine Lieferung</div>`;
}
let kasseFormGebaut = "";
function zeichneKasse(st) {
  const k = st.kasse;
  if (!k) return;
  const e0 = k.einstellung, stand = k.stand || {};
  $("kasse-aktiv").checked = !!e0.aktiv;
  const summe = stand.eingezahlt ? ` · bisher eingezahlt: ${beuteText(stand.eingezahlt)}` : "";
  $("kasse-hinweis").textContent = (k.hinweis || (e0.aktiv ? "aktiv" : "ausgeschaltet")) + summe;
  const gebaut = JSON.stringify([e0.ab, e0.bis, e0.min_menge]);
  if (!ungespeichert.has("kasse-settings") && kasseFormGebaut !== gebaut) {
    kasseFormGebaut = gebaut;
    $("kasse-ab").value = Math.round(e0.ab * 100);
    $("kasse-bis").value = Math.round(e0.bis * 100);
    $("kasse-min").value = e0.min_menge;
  }
  const e = stand.einzahlungen || [];
  $("kasse-einzahlungen").innerHTML = e.length ? `<div class="tabelle-rahmen"><table><thead><tr><th>Zeit</th><th>Insel</th>
      <th>Rohstoffe</th></tr></thead><tbody>${e.slice(0, 15).map(x => `<tr><td>${uhrzeit(x.zeit, true)}</td>
      <td>${esc(x.name || x.insel)}</td><td>${beuteText(x.rohstoffe)}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="leise klein-text">noch keine Einzahlung</div>`;
}
$("kasse-aktiv").onchange = async (e) => {
  const an = e.target.checked;
  await aktion(e.target, () => post("/api/allianz/kasse", { aktiv: an }), `Überlauf in die Allianzkasse ${an ? "eingeschaltet" : "ausgeschaltet"} – greift in wenigen Sekunden`);
  allianzHolen();
};
$("kasse-speichern").onclick = async (e) => {
  const ab = Number($("kasse-ab").value) / 100, bis = Number($("kasse-bis").value) / 100;
  if (!(ab > 0 && ab <= 1) || !(bis >= 0 && bis < ab)) return melde("„bis auf“ muss kleiner sein als „ab Füllstand“ (beides 0–100 %)", "schlecht");
  const r = await aktion(e.target, () => post("/api/allianz/kasse", {
    ab, bis, min_menge: Math.max(0, Math.round(Number($("kasse-min").value) || 0)) }), "Gespeichert – greift in wenigen Sekunden");
  if (!r) return;
  dirtySetzen("kasse-settings", false);
  kasseFormGebaut = "";
  setTimeout(allianzHolen, 3000);
};
/** Menüpunkt: grün = liefert, gelb = an, aber blockiert, grau = aus, rot = nicht erreichbar. */
function ausgleichPunkt(a, fehler) {
  const dot = $("nav-dot-ausgleich");
  let klasse = "", titel = "ausgeschaltet";
  if (fehler) { klasse = "schlecht"; titel = "Flotten-Manager nicht erreichbar"; }
  else if (a.aktiv && !a.laeuft) { klasse = "warn"; titel = "an, aber der Flotten-Manager ist gestoppt"; }
  else if (a.aktiv && a.hinweis) { klasse = "warn"; titel = a.hinweis; }
  else if (a.aktiv) { klasse = "gut"; titel = "aktiv"; }
  dot.className = `dot ${klasse}`;
  dot.title = titel;
}
async function ausgleichHolen() {
  const box = $("aus-fehler");
  try {
    ausgleich = await hole("/api/flotte/ausgleich");
    box.hidden = true;
  } catch (e) {
    box.hidden = false;
    box.textContent = `Flotten-Manager nicht erreichbar: ${e.message}`;
    ausgleichPunkt(null, true);
    return;
  }
  ausgleichPunkt(ausgleich, false);
  if (tab === "ausgleich") { zeichneAusgleich(); zeichneAusLieferung(); }
}
$("aus-inseln").addEventListener("change", async (e) => {
  const cb = e.target.closest('input[type=checkbox][data-aus-feld]');
  if (!cb) return;
  e.stopPropagation();
  const id = cb.closest("[data-aus-insel]").dataset.ausInsel, feld = cb.dataset.ausFeld;
  const name = ausgleich?.inseln.find(x => String(x.id) === id)?.name || id;
  await aktion(cb, () => post(`/api/flotte/inseln/${id}/ausgleich`, { [feld]: cb.checked }),
    `${name}: ${feld === "gibt" ? "gibt ab" : "bekommt"} – ${cb.checked ? "an" : "aus"}`);
  ausgleichHolen();
});
$("aus-inseln").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-aus-speichern]");
  if (!b) return;
  const id = b.dataset.ausSpeichern, form = b.closest("[data-aus-insel]").querySelector(".aus-form");
  const wert = (n) => form.querySelector(`[data-aus-feld="${n}"]`).value.trim();
  const prozent = (n) => wert(n) === "" ? null : Number(wert(n)) / 100;
  const r = await aktion(b, () => post(`/api/flotte/inseln/${id}/ausgleich`, {
    ziel: prozent("ziel"), reserve: prozent("reserve"), max_abgabe: Math.max(0, Math.round(Number(wert("max_abgabe")) || 0)),
  }), "Grenzen gespeichert");
  if (!r) return;
  dirtySetzen(form.dataset.form, false);
  form.dataset.gebaut = "";
  ausgleichHolen();
});
$("aus-aktiv").addEventListener("change", async (e) => {
  const an = e.target.checked;
  if (an && !(await bestaetigen({ titel: "Rohstoff-Ausgleich einschalten?", text: "Der Flotten-Manager schickt dann selbständig Handelsfahrten von den Spender- zu den Empfänger-Inseln.", icon: "rohstoffausgleich", ok: "Einschalten" }))) {
    e.target.checked = false; return;
  }
  await aktion(e.target, () => post("/api/flotte/settings", { ausgleich_aktiv: an }), `Rohstoff-Ausgleich ${an ? "eingeschaltet" : "ausgeschaltet"}`);
  ausgleichHolen();
});
$("aus-speichern").onclick = async (e) => {
  const ziel = Number($("aus-ziel").value) / 100, reserve = Number($("aus-reserve").value) / 100;
  if (!(ziel > 0 && ziel < 1) || !(reserve >= 0 && reserve < 1)) return melde("Prozentwerte zwischen 0 und 100 angeben", "schlecht");
  const r = await aktion(e.target, () => post("/api/flotte/settings", {
    ausgleich_ziel: ziel, ausgleich_reserve: reserve,
    ausgleich_min_menge: Math.max(0, Math.round(Number($("aus-min").value) || 0)),
    ausgleich_upgrade_bedarf: $("aus-upgrade").checked,
  }));
  if (!r) return;
  dirtySetzen("aus-settings", false);
  ausgleichFormGebaut = "";
  const n = Object.keys(r.geaendert).length;
  $("aus-settings-info").textContent = n ? `${n} Änderung(en) übernommen` : "nichts geändert";
  setTimeout(() => ($("aus-settings-info").textContent = ""), 4000);
  ausgleichHolen();
};
