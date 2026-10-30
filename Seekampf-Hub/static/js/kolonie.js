"use strict";
// ======================================================================
// Kolonisations-Bot
// ======================================================================
let kolonie = null, kolonieFehler = null;
const KO_STATUS = {
  wartet: ["grau", "wartet"], spart: ["warn", "spart"], baut: ["warn", "Schiff in Ausbildung"],
  bereit: ["gut", "Schiff bereit"], unterwegs: ["gut", "unterwegs"],
};
const KO_ERGEBNIS = { kolonisiert: "gut", besiedelt: "schlecht", gescheitert: "schlecht", entfernt: "grau" };
function begleitungKurz(e) {
  const b = e.begleitung || {}, m = { ...(b.schiffe || {}), ...(b.truppen || {}) };
  return Object.keys(m).length ? `<div class="leise klein-text">+ ${esc(mengenText(m))}</div>` : "";
}
async function kolonieHolen() {
  try { kolonie = await hole("/api/kolonie/status"); kolonieFehler = null; }
  catch (e) { kolonieFehler = `Kolonisations-Bot nicht erreichbar: ${e.message}`; }
  if (tab === "kolonie") zeichneKolonie();
  if (tab === "karte") { zeichneKarte(); karteSeiteZeichnen(); }
}
function zeichneKolonie() {
  const box = $("ko-fehler"), fehler = kolonieFehler || kolonie?.fehler;
  box.hidden = !fehler; box.textContent = fehler ? `Fehler: ${fehler}` : "";
  if (!kolonie) return;
  $("ko-laeuft").checked = kolonie.laeuft;
  if (!ungespeichert.has("ko-umbenennen") && kolonie.umbenennen) {
    $("ko-umb-aktiv").checked = kolonie.umbenennen.aktiv;
    $("ko-umb-muster").value = kolonie.umbenennen.muster;
  }
  const ws = kolonie.warteschlange;
  $("ko-liste").innerHTML = ws.length ? `<div class="tabelle-rahmen"><table><thead><tr><th>#</th><th>Ziel</th><th>Status</th>
      <th>Schiff von</th><th class="zahl verstecken-schmal">Fahrzeit</th><th>Hinweis</th><th></th></tr></thead><tbody>${ws.map((e, n) => {
      const [klasse, text] = KO_STATUS[e.status] || ["grau", e.status];
      const ankunft = e.status === "unterwegs" && e.ankunft ? ` · Ankunft in <span data-bis="${e.ankunft}" data-fertig="jetzt">${dauer(e.ankunft - jetzt())}</span>` : "";
      return `<tr><td class="mono">${n + 1}</td>
        <td>${koordLink(e.koordinaten)} <span class="leise">${esc(e.name || "")}</span>${e.bewohnt ? ` <span class="marke warn">bewohnt</span>` : ""}${e.fehlversuche ? ` <span class="marke grau" title="gescheiterte Versuche - die Insel wird erneut angefahren">${e.fehlversuche}× gescheitert</span>` : ""}${begleitungKurz(e)}</td>
        <td><span class="marke ${klasse}"><span class="punkt"></span>${text}</span>${ankunft}</td>
        <td>${esc(e.bau_insel_name || "–")}${e.entfernung != null ? ` <span class="leise klein-text">(${zahl(e.entfernung, 1)} Felder)</span>` : ""}</td>
        <td class="zahl verstecken-schmal">${e.fahrzeit_s ? dauer(e.fahrzeit_s) : "–"}</td>
        <td class="klein-text">${esc(e.hinweis || "")}</td>
        <td style="white-space:nowrap"><button class="klein" data-ko-hoch="${e.id}" ${n === 0 ? "disabled" : ""} title="nach oben">▲</button>
          <button class="klein" data-ko-runter="${e.id}" ${n === ws.length - 1 ? "disabled" : ""} title="nach unten">▼</button>
          ${e.bewohnt && e.status !== "unterwegs" ? `<button class="klein" data-ko-beg="${e.id}" title="Begleitung (weitere Schiffe und Truppen)">Begleitung</button>` : ""}
          <button class="klein" data-ko-weg="${e.id}" title="entfernen">✕</button></td></tr>`;
    }).join("")}</tbody></table></div>` : `<div class="leise klein-text">Die Warteschlange ist leer.</div>`;

  const sch = kolonie.schiff;
  $("ko-schiff").textContent = `Kolonisationsschiff: Hafen ${sch.ab_hafen}, ${mengenText(sch.kosten)}`;
  $("ko-inseln").innerHTML = `<div class="tabelle-rahmen"><table><thead><tr><th>Insel</th><th class="zahl">Hafen</th><th class="zahl">Lager</th>
      <th class="zahl">Schiffe im Hafen</th><th>Kann bauen</th><th>Baut für</th></tr></thead><tbody>${kolonie.inseln.map(i => `<tr>
      <td>${esc(i.name)} <span class="leise mono klein-text">${koordLink(i.koordinaten)}</span></td>
      <td class="zahl">${i.hafen}</td><td class="zahl">${kompakt(i.kapazitaet)}</td><td class="zahl">${i.schiffe_im_hafen}</td>
      <td>${i.ungeeignet ? `<span class="leise klein-text">${esc(i.ungeeignet)}</span>` : `<span class="marke gut"><span class="punkt"></span>ja</span>`}</td>
      <td>${i.baut_fuer ? `${koordLink(i.baut_fuer)}${i.reserviert ? ` <span class="leise klein-text">· reserviert ${mengenText(i.reserviert)}</span>` : ""}` : "–"}</td>
    </tr>`).join("")}</tbody></table></div>`;
  $("ko-verlauf").innerHTML = kolonie.verlauf.length ? kolonie.verlauf.slice(0, 15).map(v =>
    `<div class="klein-text" style="margin:3px 0"><span class="marke ${KO_ERGEBNIS[v.ergebnis] || "grau"}" style="margin-right:6px">${esc(v.ergebnis)}</span>${uhrzeit(v.zeit, true)} · ${koordLink(v.koordinaten)} ${esc(v.text)}</div>`).join("")
    : `<div class="leise klein-text">noch nichts</div>`;
  const dot = $("nav-dot-kolonie");
  if (kolonieFehler) dot.className = "dot schlecht";
}
/** Dialog: weitere Schiffe und Truppen fuer das Kolonisationsschiff. Gibt {schiffe, truppen} zurueck, null bei Abbrechen. */
async function begleitungDialog({ titel, text, aktuell, ok, gefahr }) {
  const inseln = kolonie?.inseln || [];
  const arten = [["schiffe", "Schiffe"], ["truppen", "Truppen"]];
  const typen = (art) => [...new Set([...inseln.flatMap(i => Object.keys(i[art] || {})), ...Object.keys(aktuell?.[art] || {})])]
    .filter(t => t !== "kolonisationsschiff").sort();
  const html = `<div class="formular" id="beg-form" style="margin-top:12px">${arten.map(([art, name]) => typen(art).length
    ? `<div class="feld"><label>${name}</label>${typen(art).map(t => {
        const max = Math.max(0, ...inseln.map(i => (i[art] || {})[t] || 0));
        return `<div class="info-zeile"><span>${esc(sname(t))} <span class="leise klein-text">(max. ${zahl(max)} auf einer Insel)</span></span>
          <input type="number" class="eingabe" min="0" step="1" style="max-width:90px" data-beg="${art}|${t}" value="${aktuell?.[art]?.[t] || 0}"></div>`;
      }).join("")}</div>` : "").join("")}
    <span class="hinweis">Die Schiffe und Truppen müssen beim Start im Hafen der Bau-Insel liegen, sonst wartet das Schiff. Der Bot bildet sie nicht aus.</span></div>`;
  let werte = null;
  const lesen = (e) => {
    if (!e.target.closest("#bestaetigung [data-j]")) return;
    werte = { schiffe: {}, truppen: {} };
    document.querySelectorAll("#beg-form [data-beg]").forEach(inp => {
      const [art, t] = inp.dataset.beg.split("|"), n = Math.floor(Number(inp.value));
      if (n > 0) werte[art][t] = n;
    });
  };
  document.addEventListener("click", lesen, true);
  const ja = await bestaetigen({ titel, text, html, icon: "koloschiff", ok, gefahr });
  document.removeEventListener("click", lesen, true);
  return ja ? werte : null;
}
async function kolonieHinzu(koord, knopf) {
  const r = await aktion(knopf, async () => {
    try { return await post("/api/kolonie/warteschlange", { koordinaten: koord }); }
    catch (err) {
      const besitzer = /^BEWOHNT\|(.*)$/.exec(err.message)?.[1];
      if (besitzer === undefined) throw err;
      const beg = await begleitungDialog({ titel: `${koord} gehört ${besitzer}`, ok: "Eintragen", gefahr: true,
        text: "Das Schiff fährt los, sobald es bereit ist, auch wenn die Insel noch bewohnt ist. Sie wird nur eingenommen, wenn Truppen und Hauptgebäude zerstört sind (sonst 50/50). Optional fahren weitere Schiffe und Truppen mit." });
      if (!beg) return null;
      return post("/api/kolonie/warteschlange", { koordinaten: koord, bewohnt: true, begleitung: beg });
    }
  }, (e) => e && `${e.koordinaten} ist auf Platz ${(kolonie?.warteschlange.length || 0) + 1} der Kolonisations-Warteschlange`);
  if (r) { $("ko-koord").value = ""; kolonieHolen(); }
}
$("ko-umb-speichern").onclick = async (e) => {
  const r = await aktion(e.target, () => post("/api/kolonie/umbenennen", { aktiv: $("ko-umb-aktiv").checked, muster: $("ko-umb-muster").value }),
    "Umbenennen gespeichert");
  if (r) { dirtySetzen("ko-umbenennen", false); kolonieHolen(); }
};
$("ko-hinzu").onclick = (e) => kolonieHinzu($("ko-koord").value.trim(), e.target);
$("ko-koord").addEventListener("keydown", (e) => { if (e.key === "Enter") kolonieHinzu(e.target.value.trim(), $("ko-hinzu")); });
$("ko-laeuft").onchange = async (e) => {
  const an = e.target.checked;
  if (!an && !(await bestaetigen({ titel: "Kolonisations-Bot anhalten?", text: "Er baut keine Schiffe mehr und schickt keine los; die Reservierung wird freigegeben.", icon: "koloschiff", ok: "Anhalten", gefahr: true }))) { e.target.checked = true; return; }
  await aktion(e.target, () => post("/api/kolonie/control", { aktion: an ? "start" : "stop" }), `Kolonisations-Bot ${an ? "aktiv" : "angehalten"}`);
  kolonieHolen();
};
$("ko-liste").addEventListener("click", async (e) => {
  const hoch = e.target.closest("[data-ko-hoch]"), runter = e.target.closest("[data-ko-runter]"), weg = e.target.closest("[data-ko-weg]");
  if (hoch || runter) {
    const id = (hoch || runter).dataset[hoch ? "koHoch" : "koRunter"];
    await aktion(hoch || runter, () => post(`/api/kolonie/warteschlange/${id}/verschieben`, { richtung: hoch ? -1 : 1 }));
    return kolonieHolen();
  }
  if (weg) {
    const eintrag = kolonie.warteschlange.find(x => String(x.id) === weg.dataset.koWeg);
    const zusatz = eintrag?.status === "unterwegs" ? " Das Schiff wird zurückgerufen, solange das noch geht." : "";
    if (!(await bestaetigen({ titel: `${eintrag?.koordinaten} aus der Warteschlange nehmen?`, text: zusatz.trim(), icon: "koloschiff", ok: "Entfernen", gefahr: true }))) return;
    await aktion(weg, () => post(`/api/kolonie/warteschlange/${weg.dataset.koWeg}/entfernen`), (r) => r.text);
    kolonieHolen();
  }
});
document.addEventListener("click", async (e) => {
  const beg = e.target.closest("[data-ko-beg]");
  if (beg) {
    const eintrag = kolonie?.warteschlange.find(x => String(x.id) === beg.dataset.koBeg);
    const neu = await begleitungDialog({ titel: `Begleitung für ${eintrag?.koordinaten || "Ziel"}`, ok: "Speichern", aktuell: eintrag?.begleitung,
      text: "Weitere Schiffe und Truppen, die mit dem Kolonisationsschiff fahren." });
    if (neu) { await aktion(beg, () => post(`/api/kolonie/warteschlange/${beg.dataset.koBeg}/begleitung`, neu), "Begleitung gespeichert"); kolonieHolen(); }
    return;
  }
  const b = e.target.closest("[data-kolonie-hinzu]");
  if (b) kolonieHinzu(b.dataset.kolonieHinzu, b);
});
