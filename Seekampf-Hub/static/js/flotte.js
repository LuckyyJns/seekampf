"use strict";
// ======================================================================
// Flotten-Manager
// ======================================================================
let flotte = null, verlauf = null, flotteFehler = null;

const FELDER = [
  { gruppe: "Suchbereich" },
  { key: "scan_radius_sektoren", label: "Radius (Sektoren)", typ: "number", schritt: 1, hinweis: "0 = nur eigener Sektor, 1 = 3×3, 2 = 5×5" },
  { key: "scan_intervall_stunden", label: "Neu-Scan alle (h)", typ: "number", schritt: 1, hinweis: "wirft besiedelte Inseln aus der Zielliste" },
  { gruppe: "Flotte" },
  { key: "flotte_kriegsschiffe", label: "Kriegsschiffe je Flotte", typ: "number", schritt: 1, hinweis: "Typ egal: es fährt, was im Hafen liegt" },
  { key: "flotte_handelsschiffe", label: "Handelsschiffe je Flotte", typ: "number", schritt: 1, hinweis: "Beute je Schiff: klein 75, groß 460" },
  { key: "grosse_handelsschiffe", label: "Große Handelsschiffe mitschicken", typ: "checkbox", hinweis: "aus = es fahren nur kleine Handelsschiffe" },
  { key: "flotte_einheiten", label: "Steinewerfer je Flotte", typ: "number", schritt: 1 },
  { key: "max_flotten", label: "Max. Flotten je Insel", typ: "number", schritt: 1, hinweis: "0 = so viele, wie Schiffe da sind" },
  { gruppe: "Angriff" },
  { key: "rohstoff_modus", label: "Beute-Priorisierung", typ: "select",
    optionen: [["knappster", "knappster Rohstoff"], ["gleichmaessig", "gleichmäßig"], ["gold", "immer Gold"], ["stein", "immer Stein"], ["holz", "immer Holz"]],
    hinweis: "rechnet ein, was schon unterwegs ist" },
  { key: "ausgleich_schwelle", label: "Ausgleichsschwelle", typ: "number", schritt: 0.01, hinweis: "Anteil der Lagerkapazität; darunter gleichmäßig" },
  { key: "razzia_ziel", label: "Razzia-Ziel", typ: "text", hinweis: "haupthaus ist auf freien Inseln Stufe 0 → kein Schaden" },
  { gruppe: "Sicherheit" },
  { key: "lager_voll_schwelle", label: "Lager-voll-Schwelle", typ: "number", schritt: 0.01, hinweis: "Pause, wenn alle drei Rohstoffe darüber liegen" },
  { key: "niederlagen_bis_blacklist", label: "Niederlagen bis Blacklist", typ: "number", schritt: 1 },
  { key: "blacklist_tage", label: "Blacklist-Dauer (Tage)", typ: "number", schritt: 1 },
  { key: "rueckruf_bei_bedrohung", label: "Flotten bei Bedrohung zurückrufen", typ: "checkbox" },
  { key: "pruef_intervall_s", label: "Ziel unterwegs prüfen alle (s)", typ: "number", schritt: 10, hinweis: "solange die Flotte zurückgerufen werden kann; besiedelt → Rückruf" },
  { key: "pruef_vorlauf_s", label: "Letzte Prüfung vor Rückrufende (s)", typ: "number", schritt: 5 },
  { gruppe: "Postfach" },
  { key: "berichte_archivieren", label: "Eigene Kampfberichte ausblenden", typ: "checkbox", hinweis: "nur Siege auf freien Inseln; Niederlagen und Angriffe auf besiedelte Inseln bleiben stehen; nicht umkehrbar" },
  { gruppe: "Takt & Telegram" },
  { key: "tick_sekunden", label: "Takt (Sekunden)", typ: "number", schritt: 1 },
  { key: "telegram_report", label: "Telegram-Morgenreport", typ: "checkbox" },
  { key: "report_stunde", label: "Report um (Uhr)", typ: "number", schritt: 1 },
];

/** Kennzahlen-Kacheln (ohne umgebenden Container). */
function kachelnHtml(s) {
  const k = [
    { wert: zahl(s.raids), label: "Raids gesamt" },
    { wert: zahl(s.inseln_besucht), label: "Inseln besucht" },
    { wert: zahl(s.gold), label: "Gold", farbe: "var(--gold)" },
    { wert: zahl(s.stein), label: "Stein", farbe: "var(--stein)" },
    { wert: zahl(s.holz), label: "Holz", farbe: "var(--holz)" },
    { wert: laufzeit(s.laufzeit_s), label: "Laufzeit" },
    { wert: zahl(s.loot_pro_stunde), label: "Loot / Stunde", icon: "⚡", klasse: "signal" },
  ];
  return k.map(x => `<div class="kachel ${x.klasse || ""}" ${x.farbe ? `style="--kachel-farbe:${x.farbe}"` : ""}>
      <div class="beschriftung">${x.farbe ? ROHSTOFF_ICON(x.label.toLowerCase().split(" ")[0]) : ""}${x.icon ? IK("gold", 24) : ""}${x.label}</div>
      <div class="wert">${x.wert}</div></div>`).join("");
}
/** Kuerzel + Farbverlauf je Insel, stabil ueber die ID. */
const AVATAR_FARBEN = [["#2ec5d3", "#3b82f6"], ["#8b5cf6", "#ec4899"], ["#f59e0b", "#ef4444"], ["#10b981", "#0ea5e9"], ["#6366f1", "#22d3ee"]];
function avatar(name, id) {
  const worte = String(name || "?").trim().split(/\s+/);
  const kurz = worte.length > 1 ? (worte[0][0] + worte[worte.length - 1].slice(0, 2)) : worte[0][0];
  const [a1, a2] = AVATAR_FARBEN[Math.abs(Number(id) || 0) % AVATAR_FARBEN.length];
  return `<span class="avatar" title="${esc(name)}">${IK("eigene_insel", 34)}</span>`;
}
const BESTAND_REIHE = ["spaehschiff", "kleines_handelsschiff", "grosses_handelsschiff", "kleines_kriegsschiff", "grosses_kriegsschiff", "kolonisationsschiff", "steinewerfer", "speerkaempfer", "bogenschuetze"];
const BESTAND_ICON = { kolonisationsschiff: "koloschiff" };
/** Schiffe und Truppen einer Insel als Plättchen: Icon + Anzahl */
function bestandPlaettchen(i) {
  const alle = { ...(i.schiffe || {}), ...(i.truppen || {}) };
  const liste = [...BESTAND_REIHE, ...Object.keys(alle).filter(k => !BESTAND_REIHE.includes(k))].filter(k => alle[k] > 0);
  return liste.length
    ? `<div class="bestand">${liste.map(k => `<span class="bpl" title="${esc(sname(k))}">${gico(BESTAND_ICON[k] || k, 28) || ""}<b>${zahl(alle[k])}</b>${gico(BESTAND_ICON[k] || k) ? "" : `<span class="leise klein-text">${esc(sname(k))}</span>`}</span>`).join("")}</div>`
    : `<span class="leise klein-text">kein Bestand im Hafen</span>`;
}
function flottenBestand(i) {
  const s = i.schiffe || {}, t = i.truppen || {};
  const krieg = (s.kleines_kriegsschiff || 0) + (s.grosses_kriegsschiff || 0);
  const handel = (s.kleines_handelsschiff || 0) + (s.grosses_handelsschiff || 0);
  return { krieg, handel, werfer: t.steinewerfer || 0 };
}
function flotteInselStatus(i) {
  if (!i.gehoert_uns) return `<span class="marke grau"><span class="punkt"></span>nicht mehr im Konto</span>`;
  if (!i.aktiv) return `<span class="marke grau"><span class="punkt"></span>aus</span>`;
  if (!flotte.laeuft) return `<span class="marke schlecht"><span class="punkt"></span>gestoppt</span>`;
  if (i.bedrohung) return `<span class="marke warn"><span class="punkt"></span>Bedrohung im Anflug</span>`;
  if (i.pause_grund) return `<span class="marke warn"><span class="punkt"></span>${esc(i.pause_grund)}</span>`;
  return `<span class="marke gut"><span class="punkt"></span>raidet</span>`;
}
function flottenTabelle(liste) {
  if (!liste.length) return `<div class="leise klein-text">keine Flotte unterwegs</div>`;
  const zeilen = liste.map(f => {
    const phase = f.zurueckgerufen && !f.angekommen ? `<span class="marke warn"><span class="punkt"></span>zurückgerufen</span>`
      : f.angekommen ? `<span class="marke gut"><span class="punkt"></span>Rückweg</span>`
      : `<span class="marke"><span class="punkt"></span>Hinweg</span>`;
    const art = f.mission && f.mission !== "attack" ? ` <span class="leise">(${esc(f.mission)})</span>` : "";
    const prio = f.prioritaet ? ` <span class="leise">(${esc(f.prioritaet)})</span>` : "";
    return `<tr>
      <td class="mono">${koordLink(f.koordinaten)}${prio}${art}</td>
      <td class="verstecken-schmal">${esc(f.ziel_name || "–")}</td>
      <td>${phase}</td>
      <td>${f.angekommen ? beuteText(f.loot) : "<span class='leise'>–</span>"}</td>
      <td class="zahl">${f.angekommen ? `<span class="leise">${uhrzeit(f.arrive_at)}</span>` : `<b>${countdown(f.arrive_at, "angekommen")}</b>`}</td>
      <td class="zahl">${f.return_at ? `<b>${countdown(f.return_at, "daheim")}</b> <span class="leise">${uhrzeit(f.return_at)}</span>` : "–"}</td>
    </tr>`;
  }).join("");
  return `<div class="tabelle-rahmen"><table><thead><tr><th>Ziel</th><th class="verstecken-schmal">Insel</th><th>Phase</th>
    <th>Beute</th><th class="zahl">Ankunft</th><th class="zahl">Rückkehr</th></tr></thead><tbody>${zeilen}</tbody></table></div>`;
}
function zieleTabelle(i, unterwegs) {
  if (!i.ziele.length) return `<div class="leise klein-text">${i.letzter_scan ? "keine freien Inseln im Umkreis" : "noch nicht gescannt"}</div>`;
  const t = jetzt();
  const maxBeute = Math.max(1, ...i.ziele.map(z => z.beute_gesamt.gold + z.beute_gesamt.stein + z.beute_gesamt.holz));
  const zeilen = i.ziele.map(z => {
    const summe = z.beute_gesamt.gold + z.beute_gesamt.stein + z.beute_gesamt.holz;
    const gesperrt = z.blacklist_bis && (z.blacklist_bis === "dauerhaft" || z.blacklist_bis > t);
    let status;
    if (gesperrt) status = `<span class="marke schlecht" title="${esc(z.blacklist_grund || "")}"><span class="punkt"></span>gesperrt ${z.blacklist_bis === "dauerhaft" ? "dauerhaft" : countdown(z.blacklist_bis, "frei")}</span>`;
    else if (unterwegs.has(z.koordinaten)) status = `<span class="marke"><span class="punkt"></span>angefahren</span>`;
    else if (z.koordinaten === i.naechstes_ziel) status = `<span class="marke gut"><span class="punkt"></span>als Nächstes</span>`;
    else status = `<span class="leise">bereit</span>`;
    return `<tr>
      <td>${z.niederlagen_gesamt ? `<span class="marke schlecht" title="${z.niederlagen_gesamt} Niederlagen">${z.niederlagen_gesamt}×</span>` : ""}</td>
      <td class="mono">${koordLink(z.koordinaten)}</td>
      <td class="verstecken-schmal">${esc(z.name)}</td>
      <td class="zahl">${dauer(z.fahrzeit_s)}</td>
      <td class="zahl">${zahl(z.raids)}</td>
      <td class="verstecken-schmal">${beuteText(z.letzte_beute)}</td>
      <td class="zahl">${zahl(summe)}<div class="balken"><i style="width:${Math.round(100 * summe / maxBeute)}%"></i></div></td>
      <td>${status}</td>
      <td><button class="klein" data-sperre="${i.id}|${esc(z.koordinaten)}">${gesperrt ? "frei" : "sperren"}</button></td>
    </tr>`;
  }).join("");
  return `<div class="tabelle-rahmen" style="max-height:420px;overflow:auto"><table><thead><tr><th></th><th>Koordinaten</th>
    <th class="verstecken-schmal">Name</th><th class="zahl">Fahrzeit</th><th class="zahl">Raids</th>
    <th class="verstecken-schmal">Letzte Beute</th><th class="zahl">Beute gesamt</th><th>Status</th><th></th></tr></thead>
    <tbody>${zeilen}</tbody></table></div>`;
}

function zeichneFlotte() {
  const box = $("fl-fehler");
  const fehler = flotteFehler || flotte?.fehler;
  box.hidden = !fehler;
  box.textContent = fehler ? `Fehler: ${fehler}` : "";
  if (!flotte) return;
  $("fl-kacheln").innerHTML = kachelnHtml(flotte.stats);

  const container = $("fl-inseln");
  const unterwegs = new Set(flotte.flotten.map(f => f.koordinaten));
  for (const i of flotte.inseln) {
    const id = String(i.id);
    const karte = inselKarte(container, id, `
      <div class="unterteil kacheln klein" data-teil="stats"></div>
      <div class="unterteil"><div class="kopfzeile"><h2>Beute je Tag</h2>
        <div class="rechts"><button class="klein" data-tabelle-fuer="fl-diagramm-${id}">Tabelle</button></div></div>
        <div id="fl-diagramm-${id}"></div></div>
      <div class="unterteil"><h2>Im Hafen</h2><div data-teil="hafen" class="klein-text"></div></div>
      <div class="unterteil"><h2>Flotten unterwegs</h2><div data-teil="flotten"></div></div>
      <div class="unterteil"><div class="kopfzeile"><h2>Einstellungen dieser Insel</h2>
        <div class="rechts"><span class="ungespeichert-marke" data-dirty-anzeige="fl-insel-${id}" hidden>● ungespeichert</span>
          <button class="klein" data-fl-insel-speichern="${id}">Speichern</button></div></div>
        <div class="formular" data-form="fl-insel-${id}" data-teil="einstellungen"></div>
        <span class="hinweis">leer = globaler Wert (unten unter Einstellungen)</span></div>
      <div class="unterteil"><div class="kopfzeile"><h2>Zielrotation</h2>
        <div class="rechts"><span class="leise klein-text" data-teil="scaninfo"></span>
          <button class="klein" data-scan="${id}">Jetzt scannen</button></div></div>
        <div data-teil="ziele"></div></div>`);
    karte.classList.toggle("offen", offeneInseln.flotte.has(id));
    const fl = flotte.flotten.filter(f => f.insel_id === i.id);
    const b = flottenBestand(i);
    karte.querySelector(".insel-kopf").innerHTML = `
      <span class="pfeil">▶</span>
      ${avatar(i.name, i.id)}
      <span class="namenblock"><span class="titel">${esc(i.name)}</span><span class="koord mono">${esc(i.koordinaten)}</span></span>
      ${flotteInselStatus(i)}
      <span class="info">
        <span class="bchip" title="Kriegsschiffe">${gico("kleines_kriegsschiff", 28)}<b class="mono">${b.krieg}</b></span>
        <span class="bchip" title="Handelsschiffe">${gico("kleines_handelsschiff", 28)}<b class="mono">${b.handel}</b></span>
        <span class="bchip" title="Steinewerfer">${gico("steinewerfer", 28)}<b class="mono">${b.werfer}</b></span>
        <span><b class="mono">${fl.length}</b> unterwegs</span>
        <span class="verstecken-schmal">${zahl(i.stats.raids)} Raids · ${zahl(i.stats.loot_pro_stunde)}/h</span>
      </span>
      <span class="schalter-platz"><label class="schalter" title="Von dieser Insel raiden">
        <input type="checkbox" data-fl-aktiv="${id}" ${i.aktiv ? "checked" : ""} ${i.gehoert_uns ? "" : "disabled"}>
        <span class="spur"></span><span class="verstecken-schmal">Raiden</span></label></span>`;
    if (!karte.classList.contains("offen")) continue;
    karte.querySelector('[data-teil="stats"]').innerHTML = kachelnHtml(i.stats);
    const hafen = Object.entries({ ...(i.schiffe || {}), ...(i.truppen || {}) }).filter(([, n]) => n > 0);
    karte.querySelector('[data-teil="hafen"]').innerHTML = hafen.length
      ? bestandPlaettchen(i)
        + `<div class="leise" style="margin-top:4px">reicht für ${i.freie_flotten} weitere Flotte(n) nach den Einstellungen</div>`
      : `<span class="leise">nichts daheim</span>`;
    karte.querySelector('[data-teil="flotten"]').innerHTML = flottenTabelle(fl);
    karte.querySelector('[data-teil="scaninfo"]').textContent = i.letzter_scan ? `letzter Scan ${vor(i.letzter_scan)}` : "noch kein Scan";
    const zielBox = karte.querySelector('[data-teil="ziele"]');
    const zielScroll = zielBox.querySelector(".tabelle-rahmen")?.scrollTop || 0;
    zielBox.innerHTML = zieleTabelle(i, unterwegs);
    const zielRahmen = zielBox.querySelector(".tabelle-rahmen");
    if (zielRahmen) zielRahmen.scrollTop = zielScroll;
    zeichneInselEinstellungen(karte, i);
    if (verlauf && verlauf.inseln[id] && !diagramme[`fl-diagramm-${id}`]) diagramm(`fl-diagramm-${id}`, verlauf.inseln[id]);
    else if (verlauf && !verlauf.inseln[id]) $(`fl-diagramm-${id}`).innerHTML = `<div class="leise klein-text">noch keine Beute von dieser Insel</div>`;
  }
  zeichneFlotteFormular(flotte.settings);
}
akkordeonKlick("flotte", $("fl-inseln"));
$("fl-inseln").addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-fl-aktiv]");
  if (!cb) return;
  const i = flotte.inseln.find(x => String(x.id) === cb.dataset.flAktiv);
  if (!cb.checked && !(await bestaetigen({ titel: `Raiden von ${i.name} ausschalten?`, text: "Laufende Flotten fahren zu Ende, neue werden nicht mehr losgeschickt.", icon: "kleines_kriegsschiff", ok: "Ausschalten", gefahr: true }))) { cb.checked = true; return; }
  await aktion(cb, () => post(`/api/flotte/inseln/${cb.dataset.flAktiv}/aktiv`, { aktiv: cb.checked }),
    `${i.name}: Raiden ${cb.checked ? "eingeschaltet" : "ausgeschaltet"}`);
  flotteHolen();
});
$("fl-inseln").addEventListener("click", async (e) => {
  const sperre = e.target.closest("[data-sperre]");
  if (sperre) {
    const [iid, koord] = sperre.dataset.sperre.split("|");
    await aktion(sperre, () => post(`/api/flotte/inseln/${iid}/ziele/${koord}/sperre`));
    return flotteHolen();
  }
  const scan = e.target.closest("[data-scan]");
  if (scan) {
    scan.textContent = "scannt …";
    await aktion(scan, () => post(`/api/flotte/inseln/${scan.dataset.scan}/scan`),
      (r) => `${r.gefunden} freie Inseln gefunden (${r.neu.length} neu)`);
    scan.textContent = "Jetzt scannen";
    flotteHolen();
  }
});
$("fl-blacklist").onclick = (e) => aktion(e.target, () => post("/api/flotte/blacklist/clear"), "Blacklist geleert").then(flotteHolen);
$("fl-stats-reset").onclick = async (e) => {
  if (await bestaetigen({ titel: "Statistik zurücksetzen?", text: "Gesamt- und Inselstatistik gehen auf null. Der Beute-Verlauf bleibt erhalten.", icon: "kampfbericht", ok: "Zurücksetzen", gefahr: true }))
    aktion(e.target, () => post("/api/flotte/stats/reset"), "Statistik zurückgesetzt").then(flotteHolen);
};

/** Eigene Einstellungen einer Insel: je Feld leer = globaler Wert. */
function zeichneInselEinstellungen(karte, i) {
  const el = karte.querySelector('[data-teil="einstellungen"]');
  const form = `fl-insel-${i.id}`;
  const eigene = i.einstellungen || {}, global = flotte.settings || {};
  const stand = JSON.stringify([eigene, global]);
  if (ungespeichert.has(form) || el.dataset.gebaut === stand) return;
  el.dataset.gebaut = stand;
  const erlaubt = new Set(flotte.insel_einstellungen || []);
  const anzeige = (f, w) => f.typ === "checkbox" ? (w ? "an" : "aus")
    : f.typ === "select" ? (f.optionen.find(([v]) => v === w)?.[1] ?? w) : w;
  el.innerHTML = FELDER.filter(f => f.gruppe || erlaubt.has(f.key)).map((f, n, liste) => {
    if (f.gruppe) return liste[n + 1] && !liste[n + 1].gruppe ? `<div class="gruppe">${f.gruppe}</div>` : "";
    const hat = Object.prototype.hasOwnProperty.call(eigene, f.key);
    const glob = `global: ${esc(anzeige(f, global[f.key]))}`;
    if (f.typ === "checkbox" || f.typ === "select") {
      const optionen = f.typ === "checkbox" ? [["true", "an"], ["false", "aus"]] : f.optionen;
      const wert = hat ? String(eigene[f.key]) : "";
      return `<div class="feld"><label>${f.label}</label><select data-ifeld="${f.key}">
          <option value="" ${wert === "" ? "selected" : ""}>(${glob})</option>
          ${optionen.map(([w, t]) => `<option value="${w}" ${wert === w ? "selected" : ""}>${t}</option>`).join("")}
        </select></div>`;
    }
    return `<div class="feld"><label>${f.label}</label>
      <input type="${f.typ}" step="${f.schritt ?? "any"}" data-ifeld="${f.key}" value="${hat ? esc(eigene[f.key]) : ""}" placeholder="${glob}"></div>`;
  }).join("");
}
$("fl-inseln").addEventListener("click", async (e) => {
  const knopf = e.target.closest("[data-fl-insel-speichern]");
  if (!knopf) return;
  const id = knopf.dataset.flInselSpeichern, el = document.querySelector(`[data-form="fl-insel-${id}"]`);
  const nutzlast = {};
  el.querySelectorAll("[data-ifeld]").forEach(f => nutzlast[f.dataset.ifeld] = f.value === "" ? null : f.value);
  const name = flotte.inseln.find(x => String(x.id) === id)?.name;
  const r = await aktion(knopf, () => post(`/api/flotte/inseln/${id}/einstellungen`, nutzlast), `${name}: Einstellungen gespeichert`);
  if (r) { dirtySetzen(`fl-insel-${id}`, false); el.dataset.gebaut = ""; flotteHolen(); }
});

function zeichneFlotteFormular(s) {
  const el = $("fl-formular");
  if (el.dataset.gebaut && ungespeichert.has("fl-settings")) return;   // nie beim Tippen ueberschreiben
  if (el.dataset.gebaut === JSON.stringify(s)) return;
  el.dataset.gebaut = JSON.stringify(s);
  el.innerHTML = FELDER.map(f => {
    if (f.gruppe) return `<div class="gruppe">${f.gruppe}</div>`;
    const hinweis = f.hinweis ? `<span class="hinweis">${f.hinweis}</span>` : "";
    if (f.typ === "checkbox") return `<div class="feld check"><input type="checkbox" id="s_${f.key}" ${s[f.key] ? "checked" : ""}>
        <label for="s_${f.key}">${f.label}</label></div>`;
    if (f.typ === "select") return `<div class="feld"><label for="s_${f.key}">${f.label}</label><select id="s_${f.key}">${
        f.optionen.map(([w, t]) => `<option value="${w}" ${s[f.key] === w ? "selected" : ""}>${t}</option>`).join("")}</select>${hinweis}</div>`;
    return `<div class="feld"><label for="s_${f.key}">${f.label}</label>
      <input type="${f.typ}" step="${f.schritt ?? "any"}" id="s_${f.key}" value="${esc(s[f.key])}">${hinweis}</div>`;
  }).join("");
}
$("fl-speichern").onclick = async (e) => {
  const nutzlast = {};
  for (const f of FELDER) {
    if (f.gruppe) continue;
    const el = $(`s_${f.key}`);
    nutzlast[f.key] = f.typ === "checkbox" ? el.checked : el.value;
  }
  const r = await aktion(e.target, () => post("/api/flotte/settings", nutzlast));
  if (!r) return;
  dirtySetzen("fl-settings", false);
  const n = Object.keys(r.geaendert).length;
  $("fl-settings-info").textContent = n ? `${n} Änderung(en) übernommen` : "nichts geändert";
  setTimeout(() => ($("fl-settings-info").textContent = ""), 4000);
  $("fl-formular").dataset.gebaut = "";
  flotteHolen();
};

async function flotteHolen() {
  try {
    flotte = await hole("/api/flotte/status");
    flotteFehler = null;
    zeitVersatz = flotte.serverzeit - Date.now() / 1000;
  } catch (e) {
    flotteFehler = `Flotten-Manager nicht erreichbar: ${e.message}`;
  }
  if (tab === "flotte") { zeichneFlotte(); zeichneKopfRechts(); }
  if (tab === "uebersicht") zeichneUebersichtInseln();
  if (tab === "karte") { zeichneKarte(); inselInfo(); }
}
async function verlaufHolen() {
  try { verlauf = await hole("/api/flotte/verlauf?tage=30"); } catch (e) { return; }
  for (const id of Object.keys(diagramme)) delete diagramme[id];
  if (tab === "flotte") {
    diagramm("fl-diagramm", verlauf.gesamt);
    for (const id of Object.keys(verlauf.inseln)) if (offeneInseln.flotte.has(id)) diagramm(`fl-diagramm-${id}`, verlauf.inseln[id]);
  }
  if (tab === "uebersicht") diagramm("ue-diagramm", verlauf.gesamt);
}
