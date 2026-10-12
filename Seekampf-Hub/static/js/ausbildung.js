"use strict";
// ======================================================================
// Ausbildungs-Bot
// ======================================================================
let ausbildung = null, ausbildungFehler = null;
const EINHEIT_NAME = { steinewerfer: "Steinewerfer", speerkaempfer: "Speerkämpfer", bogenschuetze: "Bogenschützen" };
async function ausbildungHolen() {
  try { ausbildung = await hole("/api/ausbildung/status"); ausbildungFehler = null; }
  catch (e) { ausbildungFehler = `Ausbildungs-Bot nicht erreichbar: ${e.message}`; }
  if (tab === "ausbildung") zeichneAusbildung();
  if (tab === "uebersicht") zeichneUebersichtInseln();
}
const abName = (t) => ausbildung?.katalog?.[t]?.name || EINHEIT_NAME[t] || t;
const abIst = (i, t) => i.ist ? (i.ist[t] || 0) : (i.bestand[t] || 0) + (i.in_ausbildung[t] || 0);
const abAnlage = (k) => k.anlage === "kaserne" ? "Kaserne" : "Hafen";
function zeichneAusbildung() {
  const box = $("ab-fehler"), fehler = ausbildungFehler || ausbildung?.fehler;
  box.hidden = !fehler; box.textContent = fehler ? `Fehler: ${fehler}` : "";
  if (!ausbildung) return;
  $("ab-laeuft").checked = ausbildung.laeuft;
  zeichneAbStandard();
  zeichneAbInseln();
  $("ab-verlauf").innerHTML = ausbildung.verlauf.length ? ausbildung.verlauf.slice(0, 15).map(v =>
    `<div class="klein-text" style="margin:3px 0">${uhrzeit(v.zeit, true)} · ${esc(v.text)}</div>`).join("")
    : `<div class="leise klein-text">noch nichts</div>`;
}
/** Globale Mindestbestände (Truppen + Schiffe) */
function zeichneAbStandard() {
  const el = $("ab-standard"), stand = JSON.stringify(ausbildung.standard);
  if (ungespeichert.has("ab-standard") || el.dataset.gebaut === stand) return;
  el.dataset.gebaut = stand;
  const feld = (t) => {
    const k = ausbildung.katalog[t];
    return `<div class="feld"><label>${gico(t, 24)} ${esc(abName(t))}</label>
      <input type="number" min="0" step="1" class="eingabe" data-ab-std="${t}" value="${ausbildung.standard[t] ?? ""}" placeholder="–">
      <span class="hinweis">${abAnlage(k)} ab Stufe ${k.ab_stufe} · ${ROHSTOFFE.map(([r]) => zahl(k.kosten[r])).join(" / ")} je Stück</span></div>`;
  };
  el.innerHTML = `<div class="gruppe">Truppen</div>${ausbildung.truppen.map(feld).join("")}
    <div class="gruppe">Schiffe</div>${ausbildung.schiffe.map(feld).join("")}`;
}
/** Eine Kachel je Truppe/Schiff: Bestand, Soll, Kosten; Klick öffnet den Ausbilden-Dialog */
function abKachel(i, t) {
  const k = ausbildung.katalog[t], baubar = i.baubar.includes(t);
  const inAus = i.in_ausbildung[t] || 0, ist = abIst(i, t), soll = i.soll_eff[t];
  const kosten = ROHSTOFFE.map(([r]) => `<span class="${(i.rohstoffe[r] || 0) >= k.kosten[r] ? "" : "fehlt"}">${ROHSTOFF_ICON(r)}${zahl(k.kosten[r])}</span>`).join("");
  let marke;
  if (!baubar) marke = `<span class="marke grau">${abAnlage(k)} ab ${k.ab_stufe}</span>`;
  else if (soll != null) marke = ist >= soll ? `<span class="marke gut">Soll erreicht</span>` : `<span class="marke warn">fehlt ${zahl(soll - ist)}</span>`;
  else marke = `<span class="marke grau">kein Soll</span>`;
  return `<button type="button" class="gkachel" data-ab-kachel="${i.id}" data-item="${t}" ${baubar ? "" : "disabled"}
      title="${esc(baubar ? `${abName(t)} auf ${i.name} ausbilden` : `${i.name}: ${abAnlage(k)} Stufe ${i[k.anlage] ?? 0} – braucht ${k.ab_stufe}`)}">
    <span class="gk-kopf">${gico(t, 48)}<span class="gk-name"><b>${esc(abName(t))}</b>
      <span class="leise">${soll != null ? `${zahl(ist)} von ${zahl(soll)}` : zahl(ist)}${inAus ? ` · ${zahl(inAus)} im Bau` : ""}</span></span></span>
    ${soll > 0 ? `<span class="balken"><i style="width:${Math.min(100, 100 * ist / soll)}%"></i></span>` : ""}
    <span class="gk-kosten">${kosten}</span>
    <span class="gk-fuss"><span class="leise">${dauer(k.dauer_s)} je Stück</span>${marke}</span></button>`;
}
function zeichneAbInseln() {
  const container = $("ab-inseln");
  for (const i of ausbildung.inseln) {
    const id = String(i.id);
    const karte = inselKarte(container, id, `
      <div class="unterteil"><h2>Truppen &amp; Schiffe ausbilden</h2><div data-teil="kacheln"></div></div>
      <div class="unterteil"><div class="kopfzeile"><h2>Eigene Werte</h2>
        <div class="rechts"><span class="ungespeichert-marke" data-dirty-anzeige="ab-insel-${id}" hidden>● ungespeichert</span>
          <button class="klein" data-ab-speichern="${id}">Speichern</button></div></div>
        <div data-form="ab-insel-${id}" data-teil="form"></div></div>
      <div class="unterteil" data-teil="hinweis-box"><h2>Hinweise</h2><div data-teil="hinweise" class="klein-text"></div></div>`);
    karte.classList.toggle("offen", offeneInseln.ausbildung.has(id));
    const fehlt = Object.entries(i.soll_eff).reduce((n, [t, s]) => n + (s != null ? Math.max(0, s - abIst(i, t)) : 0), 0);
    const imBau = Object.values(i.in_ausbildung).reduce((n, x) => n + x, 0);
    karte.querySelector(".insel-kopf").innerHTML = `
      <span class="pfeil">▶</span>
      ${avatar(i.name, i.id)}
      <span class="namenblock"><span class="titel">${esc(i.name)}</span><span class="koord mono">${esc(i.koordinaten)}</span></span>
      ${i.bedrohung ? `<span class="marke schlecht"><span class="punkt"></span>bedroht</span>` : ""}
      ${i.aktiv ? `<span class="marke gut"><span class="punkt"></span>an</span>` : `<span class="marke grau"><span class="punkt"></span>aus</span>`}
      <span class="info">
        <span>Kaserne <b>${i.kaserne}</b> · Hafen <b>${i.hafen}</b></span>
        ${fehlt ? `<span>fehlt: <b>${zahl(fehlt)}</b> Stück</span>` : `<span>alles auf Soll</span>`}
        ${imBau ? `<span>${zahl(imBau)} in Ausbildung</span>` : ""}
      </span>
      <span class="schalter-platz"><label class="schalter" title="Ausbildung auf dieser Insel">
        <input type="checkbox" data-ab-aktiv="${id}" ${i.aktiv ? "checked" : ""}>
        <span class="spur"></span><span class="verstecken-schmal">Bot</span></label></span>`;
    if (!karte.classList.contains("offen")) continue;
    karte.querySelector('[data-teil="kacheln"]').innerHTML =
      `<div class="gk-gruppe">Truppen</div><div class="gkacheln">${ausbildung.truppen.map(t => abKachel(i, t)).join("")}</div>
       <div class="gk-gruppe">Schiffe</div><div class="gkacheln">${ausbildung.schiffe.map(t => abKachel(i, t)).join("")}</div>
       <div class="leise klein-text" style="margin-top:8px">Klick auf eine Kachel: Anzahl wählen und ausbilden. Die Kosten zieht das Spiel sofort ab; das geht auch, wenn der Bot hier aus ist.</div>`;
    zeichneAbInselFormular(karte, i);
    const hw = i.hinweise || [];
    karte.querySelector('[data-teil="hinweis-box"]').hidden = !hw.length;
    karte.querySelector('[data-teil="hinweise"]').innerHTML = hw.map(h => `<div style="margin:3px 0">${esc(h)}</div>`).join("");
  }
}
/** Eigene Werte der Insel: leer = Standard, "nicht verwalten" nimmt eine Einheit heraus */
function zeichneAbInselFormular(karte, i) {
  const el = karte.querySelector('[data-teil="form"]'), form = `ab-insel-${i.id}`;
  const stand = JSON.stringify([i.soll, i.aus, ausbildung.standard]);
  if (ungespeichert.has(form) || el.dataset.gebaut === stand) return;
  el.dataset.gebaut = stand;
  const feld = (t) => {
    const std = ausbildung.standard[t];
    return `<div class="feld"><label>${gico(t, 24)} ${esc(abName(t))}</label>
      <input type="number" min="0" step="1" class="eingabe" data-ab-soll="${t}" value="${i.soll[t] ?? ""}"
        placeholder="${std != null ? `Standard ${zahl(std)}` : "Standard: keins"}">
      <label class="ab-aus"><input type="checkbox" data-ab-aus="${t}" ${i.aus.includes(t) ? "checked" : ""}>hier nicht verwalten</label></div>`;
  };
  el.innerHTML = `<div class="formular"><div class="gruppe">Truppen</div>${ausbildung.truppen.map(feld).join("")}
      <div class="gruppe">Schiffe</div>${ausbildung.schiffe.map(feld).join("")}</div>
    <div class="leise klein-text" style="margin-top:8px">Leer = Standard von oben. „Nicht verwalten“: der Bot lässt diese Einheit auf dieser Insel in Ruhe – er bildet nichts aus und gibt nichts ab.</div>`;
}
akkordeonKlick("ausbildung", $("ab-inseln"));

/** Dialog: Anzahl wählen und ausbilden */
function abDialog(iid, t) {
  const i = ausbildung.inseln.find(x => String(x.id) === String(iid)), k = ausbildung.katalog[t];
  const max = Math.max(0, Math.min(...ROHSTOFFE.map(([r]) => k.kosten[r] > 0 ? Math.floor((i.rohstoffe[r] || 0) / k.kosten[r]) : Infinity)));
  const soll = i.soll_eff[t], fehlt = soll != null ? Math.max(0, soll - abIst(i, t)) : 0;
  const start = Math.max(1, Math.min(max || 1, fehlt || 5));
  document.getElementById("ab-dialog")?.remove();
  ton("oeffnen");
  const d = document.createElement("div");
  d.id = "ab-dialog"; d.className = "dialog-schleier";
  d.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-label="${esc(abName(t))} ausbilden">
    <div class="dialog-kopf">${gico(t, 64)}<div><h3>${esc(abName(t))} ausbilden</h3>
      <div class="leise">auf ${esc(i.name)} · ${abAnlage(k)} Stufe ${i[k.anlage] ?? 0}${soll != null ? ` · Soll ${zahl(soll)}, vorhanden ${zahl(abIst(i, t))}` : ""}</div></div></div>
    <div class="dialog-zahl">
      <button type="button" data-d="-10">−10</button><button type="button" data-d="-1">−1</button>
      <input type="number" class="eingabe" id="ab-d-n" min="1" step="1" value="${start}" aria-label="Anzahl">
      <button type="button" data-d="1">+1</button><button type="button" data-d="10">+10</button>
      <button type="button" data-d="max" ${max ? "" : "disabled"}>Max ${zahl(max)}</button>
      ${fehlt ? `<button type="button" data-d="fehlt">Bis Soll (${zahl(fehlt)})</button>` : ""}</div>
    <div class="dialog-summe" id="ab-d-summe"></div>
    <div class="dialog-leiste"><button type="button" id="ab-d-abbr">Abbrechen</button><button type="button" class="haupt" id="ab-d-ok">Ausbilden</button></div></div>`;
  document.body.appendChild(d);
  const feld = d.querySelector("#ab-d-n"), ok = d.querySelector("#ab-d-ok");
  const zu = () => { d.remove(); document.removeEventListener("keydown", taste); };
  const taste = (e) => { if (e.key === "Escape") zu(); if (e.key === "Enter" && !ok.disabled) ok.click(); };
  document.addEventListener("keydown", taste);
  const rechne = () => {
    const n = Math.max(0, Math.floor(Number(feld.value) || 0));
    const geht = n >= 1 && n <= max;
    d.querySelector("#ab-d-summe").innerHTML = `<div class="gk-kosten">${ROHSTOFFE.map(([r]) =>
        `<span class="${(i.rohstoffe[r] || 0) >= n * k.kosten[r] ? "" : "fehlt"}">${ROHSTOFF_ICON(r)}${zahl(n * k.kosten[r])}</span>`).join("")}</div>
      <div class="leise klein-text">Dauer ca. ${dauer(n * k.dauer_s)} · ${n > max ? `<b class="fehlt">so viel kann die Insel nicht bezahlen (höchstens ${zahl(max)})</b>` : `danach noch ${ROHSTOFFE.map(([r]) => zahl((i.rohstoffe[r] || 0) - n * k.kosten[r])).join(" / ")} im Lager`}</div>`;
    ok.disabled = !geht; ok.textContent = geht ? `${zahl(n)} ausbilden` : "Ausbilden";
  };
  d.addEventListener("click", (e) => {
    if (e.target === d || e.target.id === "ab-d-abbr") return zu();
    const b = e.target.closest("[data-d]");
    if (!b) return;
    const w = b.dataset.d;
    feld.value = w === "max" ? max : w === "fehlt" ? Math.min(fehlt, max) : Math.max(1, (Number(feld.value) || 0) + Number(w));
    rechne();
  });
  feld.addEventListener("input", rechne);
  ok.onclick = async () => {
    const n = Math.floor(Number(feld.value));
    const r = await aktion(ok, () => post(`/api/ausbildung/inseln/${iid}/ausbilden`, { einheit: t, anzahl: n }),
      `${i.name}: ${zahl(n)} ${abName(t)} in Ausbildung gegeben`);
    if (r) { zu(); setTimeout(ausbildungHolen, 800); }
  };
  rechne(); feld.focus(); feld.select();
}
$("ab-inseln").addEventListener("click", async (e) => {
  const kachel = e.target.closest("[data-ab-kachel]");
  if (kachel && !kachel.disabled) return abDialog(kachel.dataset.abKachel, kachel.dataset.item);
  const sp = e.target.closest("[data-ab-speichern]");
  if (sp) {
    const id = sp.dataset.abSpeichern, f = document.querySelector(`[data-form="ab-insel-${id}"]`);
    const nutzlast = { aus: [...f.querySelectorAll("[data-ab-aus]:checked")].map(x => x.dataset.abAus) };
    f.querySelectorAll("[data-ab-soll]").forEach(x => { nutzlast[x.dataset.abSoll] = x.value === "" ? null : x.value; });
    const r = await aktion(sp, () => post(`/api/ausbildung/inseln/${id}/soll`, nutzlast), "Eigene Werte gespeichert");
    if (r) { dirtySetzen(`ab-insel-${id}`, false); f.dataset.gebaut = ""; setTimeout(ausbildungHolen, 800); }
  }
});
$("ab-inseln").addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-ab-aktiv]");
  if (!cb) return;
  const name = ausbildung.inseln.find(x => String(x.id) === cb.dataset.abAktiv)?.name;
  await aktion(cb, () => post(`/api/ausbildung/inseln/${cb.dataset.abAktiv}/soll`, { aktiv: cb.checked }),
    `${name}: Ausbildung ${cb.checked ? "eingeschaltet" : "ausgeschaltet"}`);
  ausbildungHolen();
});
$("ab-std-speichern").onclick = async (e) => {
  const werte = {};
  document.querySelectorAll("[data-ab-std]").forEach(f => { werte[f.dataset.abStd] = f.value === "" ? null : f.value; });
  const r = await aktion(e.target, () => post("/api/ausbildung/standard", werte), "Standard gespeichert – gilt für alle Inseln ohne eigenen Wert");
  if (r) { dirtySetzen("ab-standard", false); $("ab-standard").dataset.gebaut = ""; setTimeout(ausbildungHolen, 800); }
};
$("ab-laeuft").onchange = async (e) => {
  const an = e.target.checked;
  await aktion(e.target, () => post("/api/ausbildung/control", { aktion: an ? "start" : "stop" }), `Ausbildungs-Bot ${an ? "aktiv" : "angehalten"}`);
  ausbildungHolen();
};
