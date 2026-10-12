"use strict";
// ======================================================================
// Uebersicht
// ======================================================================
function zeichneUebersichtDienste() {
  if (!dienste) return;
  const extra = {
    flotte: () => flotte ? `${flotte.flotten.length} Flotten unterwegs` : "",
    upgrade: () => upgrade?.status ? `letzter Durchlauf ${uhrzeit(upgrade.status.letzter_tick)}` : "",
    allianz: () => allianz?.status ? `${allianz.status.faehigkeiten.wirksam.length} Fähigkeiten gemeldet` : "",
    kolonie: () => kolonie ? `${kolonie.warteschlange.length} Ziele in der Warteschlange` : "",
    ausbildung: () => "",
    hub: () => "diese Seite",
  };
  $("ue-dienste").innerHTML = Object.entries(dienste).map(([name, d]) => `
    <section class="karte dienst-karte" style="margin:0">
      <div class="oben">
        ${name !== "hub" ? `${IK(({flotte:"kleines_kriegsschiff",upgrade:"haupthaus",allianz:"allianz_hilfe",kolonie:"koloschiff",ausbildung:"kaserne"})[name] || "wachturm", 30)}<a href="#${name}">${esc(d.titel)} →</a>` : `<b>${esc(d.titel)}</b>`}
        ${dienstMarke(d)}</div>
      <div class="leise klein-text">${d.seit && d.zustand === "active" ? `läuft seit ${uhrzeit(d.seit, true)}` : esc(d.ergebnis || "")}
        ${d.neustarts ? ` · ${d.neustarts} Neustarts` : ""}${extra[name]?.() ? ` · ${extra[name]()}` : ""}</div>
      <div class="dienst"><span class="knoepfe">${dienstKnoepfe(name, d)}</span></div>
    </section>`).join("");
}
const HELD_ICONS = { loot: "gold", flotte: "kleines_kriegsschiff", bau: "haupthaus", allianz: "allianz_hilfe" };
function heldKachel(icon, farbe, label, wert, unter) {
  return `<div class="kachel" style="--kachel-farbe:${farbe}">
    <div class="beschriftung"><span class="icon">${IK(HELD_ICONS[icon], 30)}</span>${label}</div>
    <div class="wert">${wert}</div><div class="unter">${unter}</div></div>`;
}
function zeichneUebersichtHeld() {
  const inselnUp = Object.values(upgrade?.status?.inseln || {});
  const imBau = inselnUp.filter(i => (i.warteschlange || []).some(o => o.status === "aktiv")).length;
  const auftraege = inselnUp.reduce((n, i) => n + (i.warteschlange || []).length, 0);
  const raidend = (flotte?.inseln || []).filter(i => i.aktiv).length;
  const st = allianz?.status;
  const vorgaenge = st ? st.notrufe.length + st.anfragen.length + st.schulden.filter(x => x.status === "offen").length : null;
  $("ue-held").innerHTML = [
    heldKachel("loot", "var(--akzent)", "Loot pro Stunde", flotte ? zahl(flotte.stats.loot_pro_stunde) : "–",
      flotte ? `${zahl(flotte.stats.raids)} Raids insgesamt` : "Flotten-Manager nicht erreichbar"),
    heldKachel("flotte", "var(--stein)", "Flotten unterwegs", flotte ? zahl(flotte.flotten.length) : "–",
      flotte ? `${raidend} von ${flotte.inseln.length} Inseln raiden` : ""),
    heldKachel("bau", "var(--gold)", "Bauaufträge", upgrade?.status ? zahl(auftraege) : "–",
      upgrade?.status ? `auf ${imBau} Inseln wird gebaut` : ""),
    heldKachel("allianz", st && st.notrufe.length ? "var(--schlecht)" : "var(--gut)", "Allianz-Vorgänge", vorgaenge ?? "–",
      st ? (st.notrufe.length ? `${st.notrufe.length} Notruf aktiv` : `${st.anfragen.length} Anfragen offen`) : ""),
  ].join("");
}

// ---------- Alarmband und Zeitleiste der Übersicht ----------
let letzteAlarme = 0;
function zeichneAlarme() {
  const a = [];
  for (const i of flotte?.inseln || []) if (i.bedrohung && i.gehoert_uns !== false)
    a.push({ text: `${esc(i.name)} wird bedroht${flotte.settings?.rueckruf_bei_bedrohung ? " – Flotten werden zurückgerufen" : ""}`, ziel: `flotte/${i.id}` });
  for (const n of allianz?.status?.notrufe || [])
    a.push({ text: `Notruf in der Allianz${n.insel_name || n.insel ? `: ${esc(n.insel_name || n.insel)}` : ""}`, ziel: "allianz" });
  for (const [name, d] of Object.entries(dienste || {}))
    if (d && d.installiert && d.zustand !== "active") a.push({ text: `${esc(d.titel)} läuft nicht (${esc(d.zustand)})`, ziel: name === "hub" ? "gesundheit" : name });
  if (a.length > letzteAlarme) ton("alarm");
  letzteAlarme = a.length;
  $("ue-alarm").innerHTML = a.map(x => `<a class="alarmband" href="#${x.ziel}">${IK("invasions_verband", 32)}<span>${x.text}</span><span class="mehr">ansehen →</span></a>`).join("");
}
const ZEIT_H = 4 * 3600;
function zeichneZeitleiste() {
  const el = $("ue-zeitleiste");
  if (!el) return;
  const t0 = jetzt(), t1 = t0 + ZEIT_H;
  const pos = (t) => Math.max(0, Math.min(100, 100 * (t - t0) / ZEIT_H));
  const zeilen = [];
  const ausb = new Map((ausbildung?.inseln || []).map(i => [String(i.id), i]));
  for (const i of Object.values(upgrade?.status?.inseln || {}).sort((a, b) => (a.position ?? 0) - (b.position ?? 0))) {
    const balken = [];
    for (const o of i.warteschlange || []) {
      const ende = o.finish_at ? Date.parse(o.finish_at) / 1000 : null;
      if (o.status === "aktiv" && ende && ende < t1)
        balken.push({ von: t0, bis: ende, art: "bau", icon: gico(o.gebaeude, 20), text: `${gname(o.gebaeude)} → ${o.ziel_stufe}`, rest: ende - t0 });
      else if (o.status !== "aktiv" && o.start_at) {
        const start = Date.parse(o.start_at) / 1000;
        if (start < t1) balken.push({ von: Math.max(start, t0), bis: Math.max(start, t0), art: "wartet", text: `${gname(o.gebaeude)} → ${o.ziel_stufe} startet`, rest: start - t0 });
      }
    }
    for (const a of ausb.get(String(i.id))?.auftraege || []) {
      const ende = a.finish_at ? Date.parse(a.finish_at) / 1000 : null;
      if (ende && ende < t1) balken.push({ von: t0, bis: ende, art: "ausb", icon: gico(a.item, 20), text: `${a.anzahl - a.abgeschlossen}× ${abName(a.item)}`, rest: ende - t0 });
    }
    if (balken.length) zeilen.push({ label: i.name, balken });
  }
  const ticks = (flotte?.flotten || []).map(f => {
    const hin = f.state === "outbound", t = hin ? f.arrive_at : (f.return_at || f.arrive_at);
    return { t, hin, text: `${f.ziel_name || f.koordinaten}: ${hin ? "Ankunft" : "Rückkehr"} in ${dauer(t - t0)}` };
  }).filter(x => x.t && x.t < t1 && x.t > t0 - 60);
  if (!zeilen.length && !ticks.length) { el.innerHTML = `<div class="leise klein-text">In den nächsten 4 Stunden läuft nichts aus.</div>`; return; }
  const zeile = (label, inhalt) => `<div class="zl-zeile"><div class="zl-label">${label}</div><div class="zl-spur">${inhalt}</div></div>`;
  const balkenHtml = (b) => b.art === "wartet"
    ? `<span class="zl-marke" style="left:${pos(b.von)}%" title="${esc(b.text)} in ${dauer(b.rest)}"></span>`
    : `<span class="zl-balken ${b.art}" style="left:${pos(b.von)}%;width:${Math.max(1.5, pos(b.bis) - pos(b.von))}%" title="${esc(b.text)} – fertig in ${dauer(b.rest)}">${pos(b.bis) - pos(b.von) > 9 ? `${b.icon}<span>${esc(b.text)}</span>` : ""}</span>`;
  el.innerHTML = `<div class="zl-zeile zl-achse"><div class="zl-label"></div><div class="zl-skala">${["jetzt", "+1 h", "+2 h", "+3 h", "+4 h"].map(t => `<span>${t}</span>`).join("")}</div></div>`
    + zeilen.map(z => zeile(esc(z.label), z.balken.map(balkenHtml).join(""))).join("")
    + (ticks.length ? zeile(`Flotten <span class="leise">${ticks.length}</span>`, ticks.map(x => `<span class="zl-tick ${x.hin ? "hin" : "rueck"}" style="left:${pos(x.t)}%" title="${esc(x.text)}"></span>`).join("")) : "");
}
function zeichneUebersichtInseln() {
  if (tab !== "uebersicht") return;
  zeichneUebersichtHeld();
  zeichneAlarme();
  zeichneZeitleiste();
  const inseln = new Map();
  const eintrag = (id, name, koord) => {
    if (!inseln.has(id)) inseln.set(id, { id, name, koord });
    return inseln.get(id);
  };
  // Reihenfolge wie im Spiel: die Flotten-Liste kommt in dieser Reihenfolge
  for (const i of flotte?.inseln || []) Object.assign(eintrag(String(i.id), i.name, i.koordinaten), { fl: i });
  for (const i of Object.values(upgrade?.status?.inseln || {}).sort((a, b) => (a.position ?? 0) - (b.position ?? 0)))
    Object.assign(eintrag(String(i.id), i.name, i.koordinaten), { up: i });
  for (const i of allianz?.status?.inseln || []) Object.assign(eintrag(String(i.id), i.name, i.koordinaten), { al: i });
  if (!inseln.size) return;
  const notrufe = new Set((allianz?.status?.notrufe || []).map(n => String(n.insel_id)));
  const anfragen = new Set((allianz?.status?.anfragen || []).map(n => String(n.insel_id)));
  $("ue-inseln").innerHTML = [...inseln.values()].map(x => {
    const r = x.up?.rohstoffe || x.fl?.rohstoffe || x.al?.rohstoffe || {};
    const kap = r.kapazitaet || 1, prod = r.produktion_pro_h || {};
    const bau = x.up?.warteschlange?.find(o => o.status === "aktiv");
    const steuerUp = { aktiv: true, ...(upgrade?.steuerung?.inseln?.[x.id] || {}) };
    const bedroht = x.fl?.bedrohung || x.al?.bedrohung;
    const fl = (flotte?.flotten || []).filter(f => String(f.insel_id) === x.id).length;
    const chips = [
      x.fl ? (x.fl.aktiv ? `<span class="marke gut"><span class="punkt"></span>raidet</span>` : `<span class="marke grau">Raiden aus</span>`) : "",
      x.up ? (steuerUp.aktiv ? `<span class="marke">Upgrade an</span>` : `<span class="marke grau">Upgrade aus</span>`) : "",
      notrufe.has(x.id) ? `<span class="marke schlecht">Notruf</span>` : "",
      anfragen.has(x.id) ? `<span class="marke warn">Anfrage offen</span>` : "",
      bedroht ? `<span class="marke schlecht"><span class="punkt"></span>Bedrohung</span>` : "",
    ].join("");
    return `<div class="insel-kachel">
      <div class="oben">${avatar(x.name, x.id)}
        <span class="namenblock"><b>${esc(x.name)}</b><span class="leise mono klein-text">${esc(x.koord)}</span></span></div>
      <div class="chips-zeile">${chips}</div>
      <div class="res">${ROHSTOFFE.map(([k, n]) => `
        <span class="res-key">${ROHSTOFF_ICON(k)}${n}</span>
        <span class="leiste"><i style="width:${Math.min(100, 100 * (r[k] || 0) / kap)}%;background:var(--${k})"></i></span>
        <span class="mono klein-text">${kompakt(r[k])}${prod[k] ? ` <span class="leise">+${kompakt(prod[k])}/h</span>` : ""}</span>`).join("")}</div>
      ${x.fl ? bestandPlaettchen(x.fl) : ""}
      <div class="fakten">
        <div><span class="etikett">Baut gerade</span><b>${bau ? `${gico(bau.gebaeude, 20)} ${esc(gname(bau.gebaeude))} → ${bau.ziel_stufe}` : "–"}</b>${bau ? `<div class="leise klein-text">${countdown(bau.finish_at)}</div>` : ""}</div>
        <div><span class="etikett">Flotten</span><b>${fl} unterwegs</b><div class="leise klein-text">Lager ${kompakt(kap)}</div></div>
      </div>
    </div>`;
  }).join("");
  $("ue-inseln-info").textContent = `${inseln.size} Inseln`;
  zeichneUebersichtDienste();
}
