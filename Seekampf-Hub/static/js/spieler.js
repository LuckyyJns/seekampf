"use strict";
// ======================================================================
// Spieler-Tracker: Rangliste, Veraenderungen je Tag (Stand um 1:00 Uhr),
// Schiffe und Truppen weltweit (GET /api/spieler, siehe spieler.py)
// ======================================================================
let spielerDaten = null;
const SP_SCHIFFE = ["kleines_kriegsschiff", "grosses_kriegsschiff", "kleines_handelsschiff", "grosses_handelsschiff", "spaehschiff", "kolonisationsschiff"];
const SP_TRUPPEN = ["steinewerfer", "speerkaempfer", "bogenschuetze"];
const SP_ICON = { kolonisationsschiff: "koloschiff" };

async function spielerHolen() {
  try {
    spielerDaten = await hole("/api/spieler?tage=60");
    $("sp-fehler").hidden = true;
  } catch (e) {
    $("sp-fehler").textContent = `Spieler-Daten nicht abrufbar: ${e.message}`;
    $("sp-fehler").hidden = false;
    return;
  }
  zeichneSpieler();
}

/** Veraenderung mit Vorzeichen und Farbe. */
function spDiff(n, { leer = "–" } = {}) {
  if (n === null || n === undefined) return `<span class="leise">${leer}</span>`;
  n = Math.round(n);
  if (!n) return `<span class="leise">±0</span>`;
  return `<span style="color:${n > 0 ? "var(--gut)" : "var(--schlecht)"}">${n > 0 ? "+" : "−"}${zahl(Math.abs(n))}</span>`;
}
function spRang(n) {
  if (!n) return "";
  return `<span style="color:${n > 0 ? "var(--gut)" : "var(--schlecht)"}" title="${n > 0 ? "aufgestiegen" : "abgestiegen"}">${n > 0 ? "▲" : "▼"}${Math.abs(n)}</span>`;
}
const spDatum = (t) => new Date(t * 1000).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
/** Zeitraum eines Vergleichs: "Mo 20.10." = Stand Mo 1:00 bis Di 1:00 - genaue Uhrzeiten im Tooltip.
 *  Fehlt ein Stand (Hub war um 1:00 aus), umfasst ein Vergleich mehrere Tage. */
function spZeitraum(tag) {
  const titel = `${uhrzeit(tag.von, true)} – ${uhrzeit(tag.bis, true)}`;
  const tage = (tag.bis - tag.von) / 86400;
  const text = tage > 1.5 ? `${spDatum(tag.von)} – ${spDatum(tag.bis - 7200)}` : spDatum(tag.von);
  return `<span title="${esc(titel)}">${text}</span>`;
}
const spAllianz = (a) => a ? `<span class="marke grau" title="${esc(a.name || "")}">${esc(a.tag || a.name || "?")}</span>` : `<span class="leise">ohne Allianz</span>`;
const spIcon = (t, s = 22) => IK(SP_ICON[t] || t, s);

function zeichneSpieler() {
  const d = spielerDaten;
  if (!d) return;
  const st = d.stand;
  $("sp-status").innerHTML = !st
    ? (d.laeuft ? "erster Stand wird geholt …" : (d.letzter_fehler ? `Fehler: ${esc(d.letzter_fehler)}` : "noch kein Stand"))
    : `Stand ${uhrzeit(st.zeit, true)} · täglich um ${d.stunde}:00 Uhr · erfasst seit ${uhrzeit(d.erster, true)}`
      + (d.laeuft ? " · wird gerade aktualisiert" : "") + (d.letzter_fehler ? ` · <span style="color:var(--schlecht)">letzter Versuch fehlgeschlagen: ${esc(d.letzter_fehler)}</span>` : "");
  if (!st) { $("sp-welt").innerHTML = $("sp-allianzen").innerHTML = $("sp-liste").innerHTML = ""; return; }
  const ohneVergleich = !d.tage.length
    ? `<div class="leise klein-text" style="margin-bottom:10px">Ausgangsstand gespeichert – die ersten Veränderungen gibt es nach dem nächsten Stand um ${d.stunde}:00 Uhr.</div>` : "";

  // ---- Schiffe und Truppen weltweit
  const w = st.welt || {};
  const bestand = (t) => (w.schiffe_gesamt?.[t] || 0) + (w.schiffe_auf_see?.[t] || 0) + (w.einheiten_gesamt?.[t] || 0) + (w.einheiten_auf_see?.[t] || 0);
  const typen = [...SP_SCHIFFE, ...SP_TRUPPEN];
  const letzter = d.tage[0];
  $("sp-kacheln").innerHTML = typen.map(t => {
    const diff = letzter ? (letzter.welt.schiffe[t] ?? letzter.welt.truppen[t]) : null;
    return `<div class="kachel"><div class="beschriftung">${spIcon(t, 24)}${esc(sname(t))}</div>
      <div class="wert">${letzter ? spDiff(diff) : "–"}</div><div class="leise klein-text">gesamt ${zahl(bestand(t))}</div></div>`;
  }).join("");
  const kopf = typen.map(t => `<th class="zahl" title="${esc(sname(t))}">${spIcon(t, 22)}</th>`).join("");
  const zeilen = d.tage.map(tag => `<tr><td>${spZeitraum(tag)}</td>
      ${typen.map(t => `<td class="zahl mono">${spDiff(tag.welt.schiffe[t] ?? tag.welt.truppen[t])}</td>`).join("")}
      <td class="zahl mono">${spDiff(tag.welt.spieler_anzahl)}</td><td class="zahl mono">${spDiff(tag.welt.inseln_anzahl)}</td></tr>`).join("");
  $("sp-welt").innerHTML = ohneVergleich + (d.tage.length ? `<div class="tabelle-rahmen" style="max-height:340px;overflow:auto"><table><thead><tr><th>Tag</th>${kopf}
      <th class="zahl">Spieler</th><th class="zahl">Inseln</th></tr></thead><tbody>${zeilen}</tbody></table></div>` : "")
    + `<div class="leise klein-text" style="margin-top:8px">Zuwachs je Tag weltweit (Häfen/Garnisonen + auf See), wie im Spiel unter „Statistiken“.
      Je Spieler gibt das Spiel Schiffe und Truppen nicht heraus. Jetzt: ${zahl(w.spieler_anzahl)} Spieler, ${zahl(w.inseln_anzahl)} bewohnte Inseln, ${zahl(w.flotten_unterwegs)} Flotten unterwegs.</div>`;

  // ---- Allianzen
  const al = letzter?.allianzen || {};
  $("sp-allianzen").innerHTML = `<div class="tabelle-rahmen"><table><thead><tr><th class="zahl">#</th><th>Allianz</th><th class="zahl">Mitglieder</th>
      <th class="zahl">Punkte</th><th class="zahl">letzter Tag</th></tr></thead><tbody>${(st.allianzen || []).map(a => {
        const v = al[String(a.id)] || {};
        return `<tr><td class="zahl mono">${a.rang ?? "–"} ${spRang(v.rang_diff)}</td><td><b>[${esc(a.tag)}]</b> ${esc(a.name)}</td>
          <td class="zahl mono">${zahl(a.mitglieder)} ${v.mitglieder_diff ? spDiff(v.mitglieder_diff) : ""}</td>
          <td class="zahl mono">${zahl(a.punkte)}</td><td class="zahl mono">${letzter ? spDiff(v.punkte_diff) : "–"}</td></tr>`;
      }).join("")}</tbody></table></div>`;

  // ---- Spieler (Akkordeon in Ranglisten-Reihenfolge)
  const container = $("sp-liste");
  const ids = st.spieler.map(s => String(s.id));
  container.querySelectorAll(".insel-karte").forEach(k => { if (!ids.includes(k.dataset.insel)) k.remove(); });
  for (const s of st.spieler) {
    const id = String(s.id);
    const karte = inselKarte(container, id, `<div data-teil="rumpf"></div>`);
    container.appendChild(karte);   // Reihenfolge der Rangliste
    karte.classList.toggle("offen", offeneInseln.spieler.has(id));
    karte.classList.toggle("ich", String(d.ich) === id);
    const e = letzter?.spieler[id] || {};
    const summe = (feld) => d.tage.slice(0, 7).reduce((n, t) => n + (t.spieler[id]?.[feld] || 0), 0);
    karte.querySelector(".insel-kopf").innerHTML = `
      <span class="pfeil">▶</span>
      <span class="mono" style="min-width:2.4em;text-align:right;font-weight:650">#${s.rang ?? "–"}</span>
      <span class="namenblock"><span class="titel">${esc(s.name)}${String(d.ich) === id ? ` <span class="marke gut">du</span>` : ""}</span>
        <span class="koord">${spAllianz(s.allianz)}</span></span>
      <span class="info">
        <span title="Punkte">${IK("haupthaus", 24)}<b class="mono">${zahl(s.punkte)}</b> ${letzter ? spDiff(e.punkte_diff) : ""}</span>
        <span title="Inseln">${IK("eigene_insel", 24)}<b class="mono">${zahl(s.inseln_anzahl)}</b> ${letzter ? spDiff(e.inseln_anzahl_diff) : ""}</span>
        ${spRang(e.rang_diff)}
        ${e.neu ? `<span class="marke gut">neu</span>` : ""}
        ${d.tage.length > 1 ? `<span class="verstecken-schmal leise">7 Tage: ${spDiff(summe("inseln_anzahl_diff"))} Inseln · ${spDiff(summe("punkte_diff"))} Punkte</span>` : ""}
      </span>`;
    if (!karte.classList.contains("offen")) continue;
    karte.querySelector('[data-teil="rumpf"]').innerHTML = spielerRumpf(s, id, d);
  }
}

function spInselListe(liste, richtung) {
  return liste.map(i => {
    const wer = richtung === "dazu" ? (i.von ? ` <span class="leise">von ${esc(i.von)}</span>` : "") : (i.an ? ` <span class="leise">an ${esc(i.an)}</span>` : "");
    return `<span style="white-space:nowrap">${koordLink(i.k)} <span class="leise">${esc(i.name || "")}</span>${wer}</span>`;
  }).join(", ");
}

function spielerRumpf(s, id, d) {
  const zeilen = d.tage.filter(t => t.spieler[id]).map(t => {
    const e = t.spieler[id];
    if (e.weg) return `<tr><td>${spZeitraum(t)}</td><td colspan="4" class="leise">nicht mehr in der Rangliste</td></tr>`;
    const details = [];
    if (e.neu) details.push(`<span class="marke gut">neu in der Rangliste</span>`);
    if (e.inseln_dazu) details.push(`<div><b style="color:var(--gut)">+${e.inseln_dazu.length} Insel${e.inseln_dazu.length > 1 ? "n" : ""}:</b> ${spInselListe(e.inseln_dazu, "dazu")}</div>`);
    if (e.inseln_weg) details.push(`<div><b style="color:var(--schlecht)">−${e.inseln_weg.length} Insel${e.inseln_weg.length > 1 ? "n" : ""}:</b> ${spInselListe(e.inseln_weg, "weg")}</div>`);
    if (e.allianz_vorher !== undefined) details.push(`<div>Allianz: ${spAllianz(e.allianz_vorher)} → ${spAllianz(s.allianz)}</div>`);
    if (e.quests_neu) details.push(`<div>Quests erfüllt: ${e.quests_neu.map(q => `<span class="marke">${esc(q)}</span>`).join(" ")}</div>`);
    if (e.inseln_punkte) details.push(`<div class="leise">ausgebaut: ${e.inseln_gewachsen_anzahl} Insel${e.inseln_gewachsen_anzahl > 1 ? "n" : ""} – meiste ${e.inseln_punkte.slice(0, 3).map(i => `${esc(i.name || i.k)} ${spDiff(i.diff)}`).join(", ")}</div>`);
    return `<tr><td style="white-space:nowrap">${spZeitraum(t)}</td><td class="zahl mono">${spRang(e.rang_diff) || `<span class="leise">±0</span>`}</td>
      <td class="zahl mono">${spDiff(e.punkte_diff)}</td><td class="zahl mono">${spDiff(e.inseln_anzahl_diff)}</td>
      <td class="klein-text">${details.join("") || `<span class="leise">keine Veränderung</span>`}</td></tr>`;
  }).join("");
  const quests = s.quests?.length ? `<div class="klein-text" style="margin-top:10px"><span class="leise">Erfüllte Quests:</span> ${s.quests.map(q => `<span class="marke grau">${esc(q)}</span>`).join(" ")}</div>` : "";
  return `<div class="unterteil">${zeilen
      ? `<div class="tabelle-rahmen" style="max-height:480px;overflow:auto"><table><thead><tr><th>Tag</th><th class="zahl">Rang</th><th class="zahl">Punkte</th>
          <th class="zahl">Inseln</th><th>Was passiert ist</th></tr></thead><tbody>${zeilen}</tbody></table></div>`
      : `<div class="leise klein-text">Noch keine Veränderungen – die gibt es nach dem nächsten Stand um ${d.stunde}:00 Uhr.</div>`}${quests}</div>`;
}

akkordeonKlick("spieler", $("sp-liste"));
$("sp-liste").addEventListener("click", (e) => {
  if (e.target.closest(".insel-kopf") && spielerDaten) zeichneSpieler();
});
