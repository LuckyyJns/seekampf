"use strict";
// ======================================================================
// Allianz-Bot
// ======================================================================
let allianz = null;
const FAEHIGKEITEN = [
  ["notruf", "Notruf", "Bei einem Angriff auf eine eigene Insel [NOTRUF] posten und Hilfe annehmen."],
  ["beistand", "Beistand", "Anderen Mitgliedern Speerkämpfer leihen, wenn sie angegriffen werden."],
  ["leihe_rueckgabe", "Leihe-Rückgabe", "Geliehene Truppen annehmen und nach dem Kampf zurückgeben. Aus = nur Geschenke; andere Bots leihen dir dann nichts mehr."],
  ["anfrage", "Rohstoff-Anfrage", "Bei knappem Lager automatisch Rohstoffe anfragen. Aus = auch keine Anfragen von Hand."],
];
const STRATEGIE_FELDER = [
  { key: "MIN_SPEERKAEMPFER", label: "Mindestens Speerkämpfer", typ: "number", schritt: 1, hinweis: "Notruf füllt bei Angriff mindestens bis hierhin auf" },
  { key: "HILFE_MAX_ANTEIL", label: "Höchstens verleihen", typ: "prozent", hinweis: "Anteil der eigenen Speerkämpfer je Insel" },
  { key: "ANFRAGE_SCHWELLE", label: "Anfragen unter", typ: "prozent", hinweis: "Anteil der Lagerkapazität" },
  { key: "ANFRAGE_ZIEL", label: "Auffüllen bis", typ: "prozent", hinweis: "Anteil der Lagerkapazität" },
  { key: "RUECKRUF_ERLAUBT", label: "Raid-Flotten zurückrufen erlaubt", typ: "checkbox", hinweis: "wenn für Beistand oder Rückgabe Kriegsschiffe fehlen" },
];

function zeichneAllianz() {
  if (allianz?.status) zeichneKasse(allianz.status);
  if (!allianz) return;
  const st = allianz.status;
  const box = $("al-fehler");
  const alt = st ? jetzt() - st.zeit : null;
  const fehler = !st ? "Noch kein Status vom Allianz-Bot – läuft er schon mit der neuen Version?"
    : alt > 180 ? `Letztes Lebenszeichen ${vor(st.zeit)} – der Bot scheint zu hängen.`
    : st.fehler ? `Letzter Fehler: ${st.fehler}` : null;
  box.hidden = !fehler;
  box.textContent = fehler || "";
  if (!st) return;

  // Faehigkeiten
  const f = st.faehigkeiten;
  $("al-faehigkeiten").innerHTML = FAEHIGKEITEN.map(([k, name, text]) => {
    const will = f.gewuenscht.includes(k), gebunden = f.gebunden.includes(k), gemeldet = (f.gemeldet || []).includes(k);
    let zustand;
    if (will) zustand = gemeldet ? `<span class="marke gut"><span class="punkt"></span>aktiv</span>` : `<span class="marke warn"><span class="punkt"></span>wird gemeldet …</span>`;
    else if (gebunden) zustand = `<span class="marke warn"><span class="punkt"></span>endet nach laufendem Vorgang</span>`;
    else zustand = gemeldet ? `<span class="marke warn"><span class="punkt"></span>wird abgemeldet …</span>` : `<span class="marke grau"><span class="punkt"></span>aus</span>`;
    return `<div class="faehigkeit">
      <label class="schalter"><input type="checkbox" data-faehigkeit="${k}" ${will ? "checked" : ""}><span class="spur"></span></label>
      <div style="flex:1;min-width:0"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${name}</b>${zustand}</div>
        <div class="leise klein-text">${text}</div></div></div>`;
  }).join("");

  // Anfrage-Formular: Inseln
  const sel = $("al-anfrage-insel");
  const wahl = sel.value;
  const optionen = st.inseln.map(i => `<option value="${esc(i.koordinaten)}">${esc(i.name)} (${esc(i.koordinaten)}) · ${ROHSTOFFE.map(([k]) => kompakt(i.rohstoffe[k])).join(" / ")} von ${kompakt(i.rohstoffe.kapazitaet)}</option>`).join("");
  if (sel.dataset.stand !== optionen) { sel.innerHTML = optionen; sel.dataset.stand = optionen; if (wahl) sel.value = wahl; }
  const anfrageAn = f.gewuenscht.includes("anfrage");
  $("al-anfrage-senden").disabled = !anfrageAn;
  zeichneAnfrageKacheln();
  $("al-anfrage-info").textContent = anfrageAn ? "Der Bot postet [ANFRAGE] ins Allianz-Forum und sagt Angebote automatisch zu."
    : "Rohstoff-Anfrage ist oben abgeschaltet – so darf der Bot nichts anfragen.";

  // Vorgaenge
  zeichneVorgaenge(st);
  zeichneStrategie(st);

  // Mitglieder
  const mit = st.mitglieder;
  $("al-mitglieder-info").textContent = `${mit.filter(m => m.aktiv).length} von ${mit.length} mit aktivem Bot`;
  $("al-mitglieder").innerHTML = [...mit].sort((a, b) => (b.aktiv - a.aktiv) || a.name.localeCompare(b.name)).map(m => `<tr>
    <td>${esc(m.name)}${m.ich ? ` <span class="marke">du</span>` : ""}</td>
    <td>${m.praesenz ? `${m.aktiv ? `<span class="marke gut"><span class="punkt"></span>${esc(m.praesenz.bot || "Bot")}</span>` : `<span class="marke grau">${esc(m.praesenz.bot || "Bot")} · inaktiv</span>`}` : `<span class="leise">–</span>`}</td>
    <td class="klein-text">${m.praesenz ? (m.praesenz.faehigkeiten.length ? m.praesenz.faehigkeiten.map(esc).join(", ") : "keine") : ""}</td>
    <td class="zahl leise klein-text">${m.praesenz ? vor(m.praesenz.zeit) : ""}</td></tr>`).join("");

  befehleAuswerten(st.befehle || []);
}
function zeichneVorgaenge(st) {
  const teile = [];
  const karte = (titel, inhalt, art = "") => `<div class="vorgang ${art}"><div style="margin-bottom:4px"><b>${titel}</b></div>${inhalt}</div>`;
  for (const n of st.notrufe) {
    const angebote = Object.values(n.angebote || {});
    teile.push(karte(`Notruf ${esc(n.vorgang)} – ${esc(n.insel_name)} (${esc(n.insel)})`, `
      <div class="klein-text">Angreifer ${esc((n.angreifer || []).join(", ") || "?")} · Ankunft ${n.ankunft ? `${uhrzeit(n.ankunft)} (${countdown(n.ankunft, "angekommen")})` : "?"} ·
        Stärke ${zahl(n.staerke)} · Bedarf ${zahl(n.bedarf)} Speerkämpfer${n.entwarnt ? ` · entwarnt ${uhrzeit(n.entwarnt)}` : ""}</div>
      ${angebote.length ? `<div class="klein-text leise" style="margin-top:4px">${angebote.map(a => `${esc(a.helfer_name)}: ${esc(a.status)} ${mengenText(a.zusage || a.einheiten)}`).join(" · ")}</div>` : ""}`, "notruf"));
  }
  for (const a of st.anfragen) {
    const angebote = Object.values(a.angebote || {});
    teile.push(karte(`Rohstoff-Anfrage ${esc(a.vorgang)} – ${esc(a.insel_name)} (${esc(a.insel)})`, `
      <div class="klein-text">Angefragt: ${ROHSTOFFE.filter(([k]) => a.rohstoffe[k]).map(([k, n]) => `${zahl(a.rohstoffe[k])} ${n}`).join(", ") || "–"}${a.manuell ? " (von Hand)" : ""} ·
        läuft bis ${uhrzeit(a.bis, true)}</div>
      ${angebote.length ? `<div class="klein-text leise" style="margin-top:4px">${angebote.map(x => `${esc(st.mitglieder.find(m => m.id === x.helfer_id)?.name || x.helfer_id)}: ${esc(x.status)} ${beuteText(x.geliefert || x.zusage)}`).join(" · ")}</div>` : `<div class="klein-text leise">noch keine Angebote</div>`}
      <div style="margin-top:8px"><button class="klein" data-anfrage-beenden="${a.insel_id}">Anfrage beenden</button></div>`));
  }
  const schulden = st.schulden.filter(s => s.status === "offen");
  if (schulden.length) teile.push(karte("Geliehen – muss zurück", schulden.map(s =>
    `<div class="klein-text">${esc(s.helfer_name)}: ${mengenText(s.leihe)} (erhalten ${uhrzeit(s.erhalten, true)}, Vorgang ${esc(s.vorgang)})</div>`).join(""), "schuld"));
  const verliehen = st.verliehen.filter(l => l.status === "offen");
  if (verliehen.length) teile.push(karte("Verliehen – kommt zurück", verliehen.map(l =>
    `<div class="klein-text">${esc(l.empfaenger_name || l.empfaenger_id)}: ${mengenText(l.einheiten || l.leihe)} (gesendet ${uhrzeit(l.gesendet, true)})</div>`).join("")));
  const hilfe = st.hilfe.filter(h => h.angebot && ["offen", "wartet"].includes(h.angebot.status));
  if (hilfe.length) teile.push(karte("Hilfe für andere", hilfe.map(h =>
    `<div class="klein-text">${esc(h.eigentuemer_name)} (${esc(h.insel)}): Angebot ${esc(h.angebot.status)}</div>`).join("")));
  if (!teile.length) teile.push(`<div class="leise klein-text">Gerade läuft nichts. ${st.anfragen_alt.length ? `Letzte Anfrage: ${esc(st.anfragen_alt[st.anfragen_alt.length - 1].insel_name)}, beendet ${uhrzeit(st.anfragen_alt[st.anfragen_alt.length - 1].ende, true)}.` : ""}</div>`);
  $("al-vorgaenge").innerHTML = teile.join("");
}
function zeichneStrategie(st) {
  const el = $("al-strategie");
  const stand = JSON.stringify(st.strategie);
  if (ungespeichert.has("al-strategie") || el.dataset.gebaut === stand) return;
  el.dataset.gebaut = stand;
  el.innerHTML = STRATEGIE_FELDER.map(f => {
    const wert = st.strategie[f.key], std = st.strategie_standard[f.key];
    const standard = f.typ === "prozent" ? `${Math.round(std * 100)} %` : f.typ === "checkbox" ? (std ? "an" : "aus") : std;
    const hinweis = `<span class="hinweis">${f.hinweis} · Standard ${standard}</span>`;
    if (f.typ === "checkbox") return `<div class="feld"><div class="feld check"><input type="checkbox" id="st_${f.key}" ${wert ? "checked" : ""}>
      <label for="st_${f.key}">${f.label}</label></div>${hinweis}</div>`;
    if (f.typ === "prozent") return `<div class="feld"><label for="st_${f.key}">${f.label} (%)</label>
      <input type="number" min="0" max="100" step="1" id="st_${f.key}" value="${Math.round(wert * 100)}">${hinweis}</div>`;
    return `<div class="feld"><label for="st_${f.key}">${f.label}</label>
      <input type="number" min="0" step="${f.schritt}" id="st_${f.key}" value="${wert}">${hinweis}</div>`;
  }).join("");
}
$("al-faehigkeiten").addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-faehigkeit]");
  if (!cb) return;
  const name = FAEHIGKEITEN.find(x => x[0] === cb.dataset.faehigkeit)[1];
  if (!cb.checked && !(await bestaetigen({ titel: `${name} abschalten?`, text: "Laufende Vorgänge werden noch zu Ende geführt, danach meldet der Bot die Fähigkeit im Forum ab.", icon: "allianz_hilfe", ok: "Abschalten", gefahr: true }))) { cb.checked = true; return; }
  await aktion(cb, () => put("/api/allianz/faehigkeiten", { [cb.dataset.faehigkeit]: cb.checked }),
    `${name} ${cb.checked ? "eingeschaltet" : "abgeschaltet"}`);
  allianzHolen();
});
$("al-strategie-speichern").onclick = async (e) => {
  const nutzlast = {};
  for (const f of STRATEGIE_FELDER) {
    const el = $(`st_${f.key}`);
    nutzlast[f.key] = f.typ === "checkbox" ? el.checked : f.typ === "prozent" ? Number(el.value) / 100 : Number(el.value);
  }
  const r = await aktion(e.target, () => put("/api/allianz/strategie", nutzlast), "Strategie gespeichert – gilt ab dem nächsten Takt");
  if (r) { dirtySetzen("al-strategie", false); $("al-strategie").dataset.gebaut = ""; allianzHolen(); }
};
$("al-anfrage-senden").onclick = async (e) => {
  const rohstoffe = {};
  for (const [k] of ROHSTOFFE) rohstoffe[k] = Number($(`al-anfrage-${k}`).value || 0);
  if (!Object.values(rohstoffe).some(n => n > 0)) { melde("Bitte mindestens eine Menge eintragen.", "schlecht"); return; }
  const sel = $("al-anfrage-insel");
  const ziel = sel.options[sel.selectedIndex]?.text.split(" · ")[0];
  const text = ROHSTOFFE.filter(([k]) => rohstoffe[k] > 0).map(([k, n]) => `${zahl(rohstoffe[k])} ${n}`).join(", ");
  if (!(await bestaetigen({ titel: `Für ${ziel} anfragen?`, text: "Der Bot postet die Anfrage ins Allianz-Forum.", icon: "allianz_hilfe", html: mengenHtml(rohstoffe), ok: "Anfragen" }))) return;
  const r = await aktion(e.target, () => post("/api/allianz/befehl", { typ: "anfrage", insel: sel.value, rohstoffe }));
  if (!r) return;
  wartendeBefehle.set(r.id, "Anfrage");
  melde("Anfrage an den Bot übergeben …");
  dirtySetzen("al-anfrage", false);
  ROHSTOFFE.forEach(([k]) => $(`al-anfrage-${k}`).value = "");
  anfrageSumme();
  setTimeout(allianzHolen, 3000);
};
$("al-vorgaenge").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-anfrage-beenden]");
  if (!b) return;
  if (!(await bestaetigen({ titel: "Anfrage beenden?", text: "Der Bot postet [ERLEDIGT]; bereits zugesagte Lieferungen kommen trotzdem an.", icon: "allianz_hilfe", ok: "Beenden", gefahr: true }))) return;
  const r = await aktion(b, () => post("/api/allianz/befehl", { typ: "anfrage_beenden", insel_id: Number(b.dataset.anfrageBeenden) }));
  if (r) { wartendeBefehle.set(r.id, "Anfrage beenden"); melde("Wird beendet …"); setTimeout(allianzHolen, 3000); }
});
async function allianzHolen() {
  try {
    allianz = await hole("/api/allianz");
    zeitVersatz = allianz.serverzeit - Date.now() / 1000;
  } catch (e) { return; }
  if (tab === "allianz") zeichneAllianz();
  if (tab === "uebersicht") zeichneUebersichtInseln();
}
