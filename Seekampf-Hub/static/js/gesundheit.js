"use strict";
// ======================================================================
// Gesundheit
// ======================================================================
let gesundheit = null;
const GES_BOTS = [["flotte", "Flotten-Manager"], ["upgrade", "Upgrade-Bot"], ["allianz", "Allianz-Bot"],
                  ["kolonie", "Kolonisations-Bot"], ["ausbildung", "Ausbildungs-Bot"]];

/** Ampel je Bot: [Klasse, Text]. Ein Bot "hängt", wenn sein letzter Takt länger als
 *  das 5-Fache seines Takts zurückliegt (Lebenszeichen, das der Bot selbst schreibt). */
function gesBewertung(name, g) {
  const d = g.dienste[name], lauf = g.durchlauf[name] || {}, log = g.logs[name] || {};
  const lz = (g.lebenszeichen || {})[name];
  if (!d || d.zustand !== "active") return ["schlecht", "Dienst läuft nicht"];
  if (lz) {
    if (lz.haengt) return ["schlecht", `hängt – kein Takt seit ${dauer(g.serverzeit - lz.letzter_takt)}`];
  } else {
    const grenze = 5 * (lauf.takt_s || 60);
    if (!lauf.letzter) return ["schlecht", lauf.fehler || "noch kein Durchlauf"];
    if (g.serverzeit - lauf.letzter > grenze) return ["schlecht", `kein Durchlauf seit ${dauer(g.serverzeit - lauf.letzter)}`];
  }
  if (lz && lz.fehler_in_folge > 1) return ["warn", `${lz.fehler_in_folge} Takte in Folge mit Fehler`];
  if (lz && (lz.wiederholt_abgelehnt || []).length) return ["warn", "Aktion wiederholt abgelehnt"];
  if (lauf.fehler) return ["warn", "letzter Durchlauf mit Fehler"];
  if (name === "flotte" && lauf.laeuft === false) return ["warn", "Raiden gestoppt"];
  if ((log.fehler || 0) > 0) return ["warn", `${log.fehler} Fehler in 24 h`];
  return ["gut", "alles in Ordnung"];
}
function zeichneGesundheit() {
  const g = gesundheit;
  if (!g) return;
  const sys = g.system, sich = g.sicherung;
  const sichAlter = sich.letzte ? g.serverzeit - sich.letzte.zeit : null;
  const k = [
    { label: "Temperatur", wert: sys.temperatur != null ? `${zahl(sys.temperatur, 1)} °C` : "–", schlecht: sys.temperatur > 75 },
    { label: "Drosselung", wert: sys.gedrosselt === "0x0" ? "keine" : (sys.gedrosselt || "–"), schlecht: sys.gedrosselt && sys.gedrosselt !== "0x0" },
    { label: "Speicherkarte frei", wert: `${zahl(sys.platte.frei / 1e9, 1)} GB`, schlecht: sys.platte.frei / sys.platte.gesamt < 0.1 },
    { label: "RAM frei", wert: sys.speicher.frei ? `${zahl(sys.speicher.frei / 1e9, 1)} GB` : "–" },
    { label: "Pi läuft seit", wert: sys.laufzeit_s ? dauer(sys.laufzeit_s) : "–" },
    { label: "Letzte Sicherung", wert: sichAlter != null ? `vor ${dauer(sichAlter)}` : "keine", schlecht: sichAlter == null || sichAlter > 30 * 3600 },
  ];
  $("ges-kacheln").innerHTML = k.map(x => `<div class="kachel" ${x.schlecht ? 'style="--kachel-farbe:var(--schlecht)"' : ""}>
      <div class="beschriftung">${x.label}</div><div class="wert" ${x.schlecht ? 'style="color:var(--schlecht)"' : ""}>${x.wert}</div></div>`).join("");

  const tunnel = g.dienste.cloudflared;
  $("ges-bots").innerHTML = GES_BOTS.map(([name, titel]) => {
    const [klasse, text] = gesBewertung(name, g);
    const lauf = g.durchlauf[name] || {}, log = g.logs[name] || {};
    const inselFehler = Object.entries(lauf.inseln_fehler || {}).map(([iid, f]) =>
      `<li><b>${esc(lauf.namen?.[iid] || iid)}:</b> ${esc(f)}</li>`).join("");
    const haeufig = (log.haeufig || []).map(h => `<li><b>${h.anzahl}×</b> ${esc(h.text)}</li>`).join("");
    const abgelehnt = (((g.lebenszeichen || {})[name] || {}).wiederholt_abgelehnt || []).map(a =>
      `<li><b>${a.anzahl}× in Folge abgelehnt</b> (seit ${uhrzeit(a.seit, true)}): <span class="mono">${esc(a.aktion)}</span> – ${esc(a.code)}: ${esc(a.text)}</li>`).join("");
    return `<section class="karte dienst-karte" style="margin:0">
      <div class="oben"><a href="#${name}">${titel} →</a><span class="marke ${klasse}"><span class="punkt"></span>${esc(text)}</span></div>
      <div class="klein-text">Letzter Durchlauf: <b>${lauf.letzter ? vor(lauf.letzter) : "–"}</b>${
        (g.lebenszeichen || {})[name] ? ` · Lebenszeichen ${vor(g.lebenszeichen[name].letzter_takt)}` : ""}${
        name === "upgrade" && lauf.naechster ? ` · nächster in ${countdown(lauf.naechster, "jetzt")}` : ""}${
        name === "flotte" && lauf.flotten != null ? ` · ${lauf.flotten} Flotten unterwegs` : ""}</div>
      ${lauf.fehler ? `<div class="fehler" style="margin:0">${esc(lauf.fehler)}</div>` : ""}
      <div class="klein-text"><span style="color:${log.fehler ? "var(--schlecht)" : "inherit"}"><b>${log.fehler || 0}</b> Fehler</span> ·
        <span style="color:${log.warnungen ? "var(--warn)" : "inherit"}"><b>${log.warnungen || 0}</b> Warnungen</span></div>
      ${abgelehnt ? `<ul class="ges-liste" style="color:var(--warn)">${abgelehnt}</ul>` : ""}
      ${inselFehler ? `<ul class="ges-liste">${inselFehler}</ul>` : ""}
      ${haeufig ? `<ul class="ges-liste leise">${haeufig}</ul>` : ""}
    </section>`;
  }).join("") + `<section class="karte dienst-karte" style="margin:0">
      <div class="oben"><b>Seekampf-Hub &amp; Tunnel</b>${dienstMarke(g.dienste.hub)}</div>
      <div class="klein-text">Cloudflare-Tunnel (seekampf-hub.com): ${dienstMarke(tunnel)}</div>
      <div class="leise klein-text">Hub läuft seit ${uhrzeit(g.dienste.hub?.seit, true)}${g.dienste.hub?.neustarts ? ` · ${g.dienste.hub.neustarts} Neustarts` : ""}</div>
    </section>`;

  const leer = g.logs.upgrade?.leerlauf || {}, namen = g.durchlauf.upgrade?.namen || {}, verlust = g.lager_verlust || {};
  const summe = (v) => v ? (v.gold || 0) + (v.stein || 0) + (v.holz || 0) : null;
  const zeilen = Object.entries(leer).sort((a, b) => b[1].anteil - a[1].anteil).map(([iid, x]) => {
    const p = Math.round(x.anteil * 100);
    const farbe = p >= 50 ? "var(--schlecht)" : p >= 20 ? "var(--warn)" : "var(--gut)";
    const v = verlust[iid] || {}, heute = summe(v.heute), gestern = summe(v.gestern);
    const ueber = v.ueberlauf?.aktiv ? ` <span class="marke warn" title="Lagerhaus wird vorgezogen"><span class="punkt"></span>läuft über</span>` : "";
    return `<tr><td>${esc(namen[iid] || iid)}${ueber}</td>
      <td style="min-width:120px"><div class="balken"><i style="width:${p}%;background:${farbe}"></i></div></td>
      <td class="zahl"><b>${p} %</b></td><td class="zahl leise verstecken-schmal">${dauer(x.leer_s)} von ${dauer(x.erfasst_s)}</td>
      <td class="zahl" style="${gestern > 0 ? "color:var(--warn)" : ""}">${gestern == null ? "–" : zahl(gestern)}</td>
      <td class="zahl" style="${heute > 0 ? "color:var(--warn)" : ""}">${heute == null ? "–" : zahl(heute)}</td></tr>`;
  }).join("");
  $("ges-leerlauf").innerHTML = zeilen
    ? `<div class="tabelle-rahmen"><table><thead><tr><th>Insel</th><th>Warteschlange leer</th><th class="zahl">Anteil</th><th class="zahl verstecken-schmal">Dauer</th>
        <th class="zahl">Verlust gestern</th><th class="zahl">heute</th></tr></thead><tbody>${zeilen}</tbody></table></div>
       <div class="leise klein-text" style="margin-top:8px">Leer = kein Bauauftrag in der Warteschlange (letzte 24 h), meist weil Rohstoffe fehlen – da setzt der Rohstoff-Ausgleich an. Verlust = Produktion, die wegen eines vollen Lagers verpufft ist; läuft ein Lager regelmäßig über, zieht der Upgrade-Bot das Lagerhaus vor.</div>`
    : `<div class="leise klein-text">keine Daten</div>`;

  const lauf = sich.lauf || {};
  $("ges-sicherung").innerHTML = sich.letzte
    ? `<div class="klein-text">Letzte: <b class="mono">${esc(sich.letzte.datei)}</b> (${zahl(sich.letzte.groesse / 1e6, 1)} MB, ${uhrzeit(sich.letzte.zeit, true)}) ·
        ${sich.anzahl} Sicherungen, zusammen ${zahl(sich.gesamt_groesse / 1e6, 1)} MB</div>
       ${lauf.ok === false ? `<div class="fehler" style="margin-top:8px">Letzter Lauf fehlgeschlagen (${uhrzeit(lauf.zeit, true)}): ${esc(lauf.meldung || "")}</div>` : ""}
       <div class="leise klein-text" style="margin-top:6px">Jede Nacht um 3:30 nach ~/Seekampf-Sicherungen (Code, Einstellungen, Zustände, .env – ohne Logs und .venv), die letzten 7 bleiben.</div>`
    : `<div class="leise klein-text">Noch keine Sicherung vorhanden. Sie läuft jede Nacht um 3:30.</div>`;
}
async function gesundheitHolen() {
  try { gesundheit = await hole("/api/gesundheit"); } catch (e) { return; }
  const stufen = GES_BOTS.map(([n]) => gesBewertung(n, gesundheit)[0]);
  const dot = $("nav-dot-gesundheit");
  dot.className = "dot " + (stufen.includes("schlecht") ? "schlecht" : stufen.includes("warn") ? "warn" : "gut");
  if (tab === "gesundheit") zeichneGesundheit();
}
