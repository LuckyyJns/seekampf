"use strict";
// ======================================================================
// Upgrade-Bot
// ======================================================================
let upgrade = null;
const wartendeBefehle = new Map();   // Befehl-ID -> Beschreibung

function zeichneUpgrade() {
  if (!upgrade) return;
  const st = upgrade.status;
  const box = $("up-fehler");
  const alt = st ? jetzt() - st.zeit : null;
  const fehler = !st ? "Noch kein Status vom Upgrade-Bot – läuft er schon mit der neuen Version?"
    : st.fehler ? st.fehler
    : alt > (st.poll_s || 300) + 120 ? `Letzter Durchlauf ${vor(st.zeit)} – der Bot scheint zu hängen.` : null;
  box.hidden = !fehler;
  box.textContent = fehler ? `Hinweis: ${fehler}` : "";
  if (!st) return;
  $("up-takt").innerHTML = `letzter Durchlauf ${uhrzeit(st.letzter_tick)} · nächster in ${countdown(st.naechster_tick, "gleich")}`;

  const container = $("up-inseln");
  for (const i of Object.values(st.inseln).sort((a, b) => (a.position ?? 0) - (b.position ?? 0))) {
    const id = String(i.id);
    const steuer = { aktiv: true, modus: "auto", bis_stufe: null, gesperrt: [], ausbauziele: [], nebenher: true, ...(upgrade.steuerung.inseln?.[id] || {}) };
    const karte = inselKarte(container, id, `
      <div class="zwei-spalten">
        <div class="unterteil"><h2>Lager</h2><div data-teil="lager"></div></div>
        <div class="unterteil"><h2>Warteschlange</h2><div data-teil="schlange"></div></div>
      </div>
      <div class="unterteil"><h2>Als Nächstes</h2><div data-teil="plan"></div></div>
      <div class="unterteil"><div class="kopfzeile"><h2>Priorisierung &amp; Sperren</h2>
        <div class="rechts"><span class="ungespeichert-marke" data-dirty-anzeige="up-form-${id}" hidden>● ungespeichert</span>
          <button class="klein" data-up-speichern="${id}">Speichern</button></div></div>
        <div data-form="up-form-${id}" data-teil="form"></div></div>
      <div class="unterteil"><h2>Gebäude ausbauen</h2><div data-teil="gebaeude"></div></div>`);
    karte.classList.toggle("offen", offeneInseln.upgrade.has(id));
    const schlange = i.warteschlange || [];
    const aktiv = schlange.find(o => o.status === "aktiv");
    const modusText = steuer.modus === "auto" ? (steuer.ausbauziele?.length ? `${steuer.ausbauziele.length} Ausbauziele` : "automatisch")
      : `nur ${gname(steuer.modus)}${steuer.bis_stufe ? ` bis ${steuer.bis_stufe}` : ""}`;
    const modusMarke = steuer.modus === "auto" ? (steuer.ausbauziele?.length
        ? `<span class="marke${i.priorisierung_aktiv ? "" : " grau"}" title="${esc(i.priorisierung_hinweis || "")}">${esc(modusText)}${i.priorisierung_aktiv ? "" : " · erreicht"}</span>`
        : `<span class="marke grau">${modusText}</span>`)
      : i.priorisierung_aktiv ? `<span class="marke">${esc(modusText)}</span>`
      : `<span class="marke grau" title="${esc(i.priorisierung_hinweis || "")}">${esc(modusText)} · erreicht</span>`;
    karte.querySelector(".insel-kopf").innerHTML = `
      <span class="pfeil">▶</span>
      ${avatar(i.name, i.id)}
      <span class="namenblock"><span class="titel">${esc(i.name)}</span><span class="koord mono">${esc(i.koordinaten)}</span></span>
      ${i.fehler ? `<span class="marke schlecht"><span class="punkt"></span>Fehler</span>`
        : steuer.aktiv ? `<span class="marke gut"><span class="punkt"></span>an</span>` : `<span class="marke grau"><span class="punkt"></span>aus</span>`}
      ${modusMarke}
      <span class="info">
        ${aktiv ? `<span>baut ${gico(aktiv.gebaeude)}<b>${esc(gname(aktiv.gebaeude))} → ${aktiv.ziel_stufe}</b> · ${countdown(aktiv.finish_at)}</span>` : `<span>nichts im Bau</span>`}
        <span>${schlange.length}/${st.max_warteschlange} in der Schlange</span>
      </span>
      <span class="schalter-platz"><label class="schalter" title="Upgrade-Bot auf dieser Insel">
        <input type="checkbox" data-up-aktiv="${id}" ${steuer.aktiv ? "checked" : ""}>
        <span class="spur"></span><span class="verstecken-schmal">Bot</span></label></span>`;
    if (!karte.classList.contains("offen")) continue;
    if (i.fehler) { karte.querySelector('[data-teil="lager"]').innerHTML = `<div class="fehler">${esc(i.fehler)}</div>`; continue; }

    // Lager
    const r = i.rohstoffe || {}, kap = r.kapazitaet || 1, prod = r.produktion_pro_h || {};
    karte.querySelector('[data-teil="lager"]').innerHTML = `<div class="res">${ROHSTOFFE.map(([k, n]) => `
      <span class="res-key">${ROHSTOFF_ICON(k)}${n}</span>
      <span class="leiste"><i style="width:${Math.min(100, 100 * (r[k] || 0) / kap)}%;background:var(--${k})"></i></span>
      <span class="mono">${zahl(r[k])} <span class="leise">+${zahl(prod[k])}/h</span></span>`).join("")}</div>
      <div class="leise klein-text" style="margin-top:6px">Kapazität ${zahl(kap)} je Rohstoff</div>`;
    // Warteschlange
    karte.querySelector('[data-teil="schlange"]').innerHTML = schlange.length
      ? `<ul class="schlange">${schlange.map(o => `<li>
          <span class="marke ${o.status === "aktiv" ? "" : "grau"}">${o.status === "aktiv" ? "im Bau" : "wartet"}</span>
          ${gico(o.gebaeude)}<span><b>${esc(gname(o.gebaeude))}</b> → Stufe ${o.ziel_stufe}</span>
          <span class="rest">${o.status === "aktiv" ? `fertig in <b>${countdown(o.finish_at)}</b> <span class="leise">(${uhrzeit(o.finish_at)})</span>`
            : `<span class="leise">startet ${uhrzeit(o.start_at)}</span>`}</span></li>`).join("")}</ul>`
      : `<div class="leise klein-text">Warteschlange leer</div>`;
    // Plan
    const p = i.plan;
    let planHtml;
    if (!steuer.aktiv) planHtml = `<span class="leise">Bot ist auf dieser Insel ausgeschaltet – laufende Aufträge werden fertig gebaut.</span>`;
    else if (!p) planHtml = `<span class="leise">aktuell ist kein Ausbau in Sicht</span>`;
    else {
      const fehlt = Object.entries(p.fehlt || {}).map(([k, v]) => `${zahl(v)} ${k[0].toUpperCase() + k.slice(1)}`).join(", ");
      const wann = p.in_s === null ? (p.unerreichbar === "lager" ? "passt nicht ins Lager – Lagerhaus nötig" : "ohne Produktion nicht erreichbar")
        : p.in_s <= 0 ? "sofort" : `in ${dauer(p.in_s)} (ca. ${uhrzeit(jetzt() + p.in_s)})`;
      planHtml = `${gico(p.gebaeude)}<b>${esc(gname(p.gebaeude))} → Stufe ${p.ziel_stufe}</b> · ${wann}${fehlt ? ` <span class="leise">· es fehlen ${fehlt}</span>` : ""}`;
    }
    karte.querySelector('[data-teil="plan"]').innerHTML = `<div class="hinweisbox">${planHtml}
      ${i.hinweis ? `<div class="leise klein-text" style="margin-top:6px">${esc(i.hinweis)}</div>` : ""}</div>`;
    // Formular: nur bauen, wenn nicht gerade bearbeitet
    zeichneUpgradeFormular(karte, id, steuer, i);
    // Gebäude-Kacheln: ein Klick reiht den Ausbau in die Warteschlange ein
    const roh = i.rohstoffe || {};
    const schlangeVoll = schlange.length >= (st.max_warteschlange || 3);
    const MAX_STUFE = 20;
    karte.querySelector('[data-teil="gebaeude"]').innerHTML = `<div class="gkacheln">${(i.gebaeude || []).filter(g => upgrade.gebaeude.includes(g.typ)).map(g => {
        const inSchlange = schlange.filter(o => o.gebaeude === g.typ);
        let grund = "", marke = "";
        if (!g.verfuegbar) { grund = `erst ab Haupthaus ${g.haupthaus_ab}`; marke = `<span class="marke grau">ab HH ${g.haupthaus_ab}</span>`; }
        else if (g.naechste_stufe == null) { grund = "Höchststufe erreicht"; marke = `<span class="marke grau">Maximum</span>`; }
        else if (schlangeVoll) { grund = "Warteschlange ist voll"; marke = `<span class="marke grau">Schlange voll</span>`; }
        else marke = ROHSTOFFE.every(([k]) => (roh[k] || 0) >= (g.kosten?.[k] || 0)) ? `<span class="marke gut">bezahlbar</span>` : `<span class="marke warn">Rohstoffe fehlen</span>`;
        const kosten = g.kosten ? ROHSTOFFE.map(([k]) => `<span class="${(roh[k] || 0) >= g.kosten[k] ? "" : "fehlt"}">${ROHSTOFF_ICON(k)}${zahl(g.kosten[k])}</span>`).join("") : "";
        const gesperrt = steuer.gesperrt.includes(g.typ) ? ` <span class="marke grau" title="gilt nur für die automatische Reihenfolge">gesperrt</span>` : "";
        const nQ = inSchlange.length;   // naechste_stufe der API rechnet die Warteschlange schon mit
        return `<button type="button" class="gkachel" data-up-kachel="${id}" data-geb="${g.typ}" data-stufe="${g.naechste_stufe ?? ""}" ${grund ? "disabled" : ""}
          title="${esc(grund || `${gname(g.typ)} auf Stufe ${g.naechste_stufe} ausbauen – Klick reiht ein${nQ ? ` (hinter ${nQ} in der Warteschlange)` : ""}`)}">
          <span class="gk-kopf">${gico(g.typ, 48)}<span class="gk-name"><b>${esc(gname(g.typ))}${gesperrt}</b>
            <span class="leise">Stufe ${g.stufe}${g.naechste_stufe != null ? ` → ${g.naechste_stufe}` : ""}${nQ ? ` · ${nQ} in Schlange` : ""}</span></span></span>
          <span class="gk-pips" aria-hidden="true">${Array.from({ length: MAX_STUFE }, (_, n) => `<i class="${n < g.stufe ? "f" : n < g.stufe + nQ ? "q" : (g.naechste_stufe != null && n === g.stufe + nQ ? "n" : "")}"></i>`).join("")}</span>
          <span class="gk-kosten">${kosten || `<span class="leise">–</span>`}</span>
          <span class="gk-fuss"><span class="leise">${g.bauzeit_s ? `Bauzeit ${dauer(g.bauzeit_s)}` : ""}</span>${marke}</span></button>`;
      }).join("")}</div>
      <div class="leise klein-text" style="margin-top:8px">Klick auf eine Kachel reiht die nächste Stufe ein – auch hinter Stufen, die schon in der Warteschlange stehen. Läuft auch, wenn der Bot hier aus ist; die Kosten zieht das Spiel sofort ab.</div>`;
  }
  // Befehle
  $("up-befehle").innerHTML = (st.befehle || []).length ? (st.befehle || []).slice(0, 8).map(b =>
    `<div><span class="marke ${b.ok ? "gut" : "schlecht"}" style="margin-right:6px">${b.ok ? "ok" : "Fehler"}</span>${uhrzeit(b.zeit)} · ${esc(b.text)}</div>`).join("") : "noch keine";
  befehleAuswerten(st.befehle || []);
}
function zeichneUpgradeFormular(karte, id, steuer, i) {
  const el = karte.querySelector('[data-teil="form"]');
  const form = `up-form-${id}`;
  const stand = JSON.stringify(steuer);
  if (ungespeichert.has(form) || el.dataset.gebaut === stand) return;
  el.dataset.gebaut = stand;
  const verfuegbar = new Set((i.gebaeude || []).filter(g => g.verfuegbar).map(g => g.typ));
  el.innerHTML = `<div class="formular">
      <div class="feld"><label>Priorisierung</label>
        <select data-feld="modus">
          <option value="auto" ${steuer.modus === "auto" ? "selected" : ""}>Automatisch (normale Reihenfolge)</option>
          ${upgrade.gebaeude.map(g => `<option value="${g}" ${steuer.modus === g ? "selected" : ""}>Nur ${gname(g)}${verfuegbar.has(g) ? "" : " (noch nicht verfügbar)"}</option>`).join("")}
        </select>
        <span class="hinweis">baut dann nur dieses Gebäude und wartet, bis es bezahlbar ist</span></div>
      <div class="feld"><label>Bis Stufe</label>
        <input type="number" min="1" max="20" step="1" data-feld="bis_stufe" value="${steuer.bis_stufe ?? ""}" placeholder="ohne Grenze">
        <span class="hinweis">danach baut der Bot wieder automatisch</span></div>
    </div>
    <div class="feld" style="margin-top:12px"><span class="label">Nie bauen</span>
      <div class="chips">${upgrade.gebaeude.map(g => `<label><input type="checkbox" data-sperre-geb="${g}" ${steuer.gesperrt.includes(g) ? "checked" : ""}>${gname(g)}</label>`).join("")}</div>
      <span class="hinweis">gilt für die automatische Reihenfolge; Ausbauten von Hand gehen trotzdem</span></div>
    <div class="feld" style="margin-top:12px"><span class="label">Ausbauziele (der Reihe nach)</span>
      <div data-teil="ziele-liste">${(steuer.ausbauziele || []).map(z => ausbauzielZeile(z)).join("")}</div>
      <div><button class="klein" data-ziel-neu="${id}">+ Ziel</button></div>
      <span class="hinweis">baut nur das erste noch nicht erreichte Ziel und wartet, bis es bezahlbar ist; nicht verfügbare werden übersprungen;
        gesperrte Gebäude werden hier trotzdem gebaut; sind alle erreicht, baut der Bot wieder automatisch. „Priorisierung“ oben geht vor.</span></div>
    <div class="feld" style="margin-top:12px"><span class="label">Nebenher bauen</span>
      <div class="chips"><label><input type="checkbox" data-feld="nebenher" ${steuer.nebenher !== false ? "checked" : ""}>Während auf ein Ziel gespart wird</label></div>
      <span class="hinweis">gilt für Priorisierung und Ausbauziele: nur Ausbauten, die das Ziel nicht später fertig machen –
        es bleibt trotzdem genauso früh bezahlbar, und die Warteschlange ist bis dahin wieder frei</span></div>`;
}
function ausbauzielZeile(z = { gebaeude: "hafen", stufe: 1 }) {
  return `<div class="formular-leiste" data-ausbauziel style="margin:4px 0">
    <select data-z-geb class="eingabe">${upgrade.gebaeude.map(g => `<option value="${g}" ${z.gebaeude === g ? "selected" : ""}>${gname(g)}</option>`).join("")}</select>
    <span class="leise">auf Stufe</span>
    <input type="number" min="1" max="20" step="1" data-z-stufe class="eingabe" style="width:70px" value="${z.stufe}">
    <button class="klein" data-z-hoch title="nach oben">▲</button><button class="klein" data-z-weg title="entfernen">✕</button></div>`;
}
function formularVerwerfen(form) {
  if (form === "fl-settings") $("fl-formular").dataset.gebaut = "";
  else if (form === "aus-settings") ausgleichFormGebaut = "";
  else if (form === "kasse-settings") kasseFormGebaut = "";
  else if (form.startsWith("fl-insel-")) { const el = document.querySelector(`[data-form="${form}"]`); if (el) el.dataset.gebaut = ""; }
  else if (form.startsWith("aus-insel-")) { const el = document.querySelector(`[data-form="${form}"]`); if (el) el.dataset.gebaut = ""; }
  else if (form === "al-strategie") $("al-strategie").dataset.gebaut = "";
  else if (form === "ab-standard") $("ab-standard").dataset.gebaut = "";
  else if (form.startsWith("ab-insel-")) { const el = document.querySelector(`[data-form="${form}"]`); if (el) el.dataset.gebaut = ""; }
  else if (form === "al-anfrage") ["gold", "stein", "holz"].forEach(k => $(`al-anfrage-${k}`).value = "");
  else if (form.startsWith("up-form-")) {
    const el = document.querySelector(`[data-form="${form}"]`);
    if (el) el.dataset.gebaut = "";
  }
}
akkordeonKlick("upgrade", $("up-inseln"));
$("up-inseln").addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-up-aktiv]");
  if (!cb) return;
  const name = upgrade.status.inseln[cb.dataset.upAktiv]?.name;
  await aktion(cb, () => put(`/api/upgrade/inseln/${cb.dataset.upAktiv}`, { aktiv: cb.checked }),
    `${name}: Upgrade-Bot ${cb.checked ? "eingeschaltet" : "ausgeschaltet"} – greift in wenigen Sekunden`);
  upgradeHolen();
});
$("up-inseln").addEventListener("click", async (e) => {
  const neu = e.target.closest("[data-ziel-neu]"), hoch = e.target.closest("[data-z-hoch]"), weg = e.target.closest("[data-z-weg]");
  if (neu || hoch || weg) {
    const form = e.target.closest("[data-form]");
    if (neu) form.querySelector('[data-teil="ziele-liste"]').insertAdjacentHTML("beforeend", ausbauzielZeile());
    if (hoch) { const z = hoch.closest("[data-ausbauziel]"); if (z.previousElementSibling) z.parentNode.insertBefore(z, z.previousElementSibling); }
    if (weg) weg.closest("[data-ausbauziel]").remove();
    dirtySetzen(form.dataset.form, true);
    return;
  }
  const sp = e.target.closest("[data-up-speichern]");
  if (sp) {
    const id = sp.dataset.upSpeichern, el = document.querySelector(`[data-form="up-form-${id}"]`);
    const nutzlast = {
      modus: el.querySelector('[data-feld="modus"]').value,
      bis_stufe: el.querySelector('[data-feld="bis_stufe"]').value || null,
      gesperrt: [...el.querySelectorAll("[data-sperre-geb]:checked")].map(x => x.dataset.sperreGeb),
      ausbauziele: [...el.querySelectorAll("[data-ausbauziel]")].map(z => ({
        gebaeude: z.querySelector("[data-z-geb]").value, stufe: Number(z.querySelector("[data-z-stufe]").value) })),
      nebenher: el.querySelector('[data-feld="nebenher"]').checked,
    };
    if (nutzlast.modus !== "auto" && nutzlast.gesperrt.includes(nutzlast.modus)) {
      melde(`${gname(nutzlast.modus)} ist gleichzeitig priorisiert und gesperrt – bitte eins von beiden ändern.`, "schlecht");
      return;
    }
    const r = await aktion(sp, () => put(`/api/upgrade/inseln/${id}`, nutzlast), "Gespeichert – greift in wenigen Sekunden");
    if (r) { dirtySetzen(`up-form-${id}`, false); el.dataset.gebaut = ""; upgradeHolen(); }
    return;
  }
  const kachel = e.target.closest("[data-up-kachel]");
  if (kachel && !kachel.disabled) {
    const id = kachel.dataset.upKachel, geb = kachel.dataset.geb;
    const name = upgrade.status.inseln[id]?.name;
    const stufe = kachel.dataset.stufe ? ` auf Stufe ${kachel.dataset.stufe}` : "";
    const g = (upgrade.status.inseln[id]?.gebaeude || []).find(x => x.typ === geb);
    if (!(await bestaetigen({ titel: `${gname(geb)}${stufe} einreihen?`, text: `auf ${name} – die Kosten zieht das Spiel sofort ab.`, icon: geb,
        html: g?.kosten ? `${mengenHtml(g.kosten)}<div class="leise klein-text" style="margin-top:6px">Bauzeit ${dauer(g.bauzeit_s)}</div>` : "", ok: "Einreihen" }))) return;
    const r = await aktion(kachel, () => post("/api/upgrade/befehl", { typ: "auftrag", insel_id: id, gebaeude: geb }));
    if (r) { wartendeBefehle.set(r.id, `${gname(geb)} auf ${name}`); melde("Auftrag an den Bot übergeben …"); setTimeout(upgradeHolen, 4000); }
  }
});
$("up-pruefen").onclick = async (e) => {
  const r = await aktion(e.target, () => post("/api/upgrade/befehl", { typ: "pruefen" }), "Durchlauf angestoßen");
  if (r) setTimeout(upgradeHolen, 6000);
};
function befehleAuswerten(ergebnisse) {
  for (const b of ergebnisse) {
    if (!wartendeBefehle.has(b.id)) continue;
    wartendeBefehle.delete(b.id);
    melde(b.text, b.ok ? "gut" : "schlecht");
  }
}
async function upgradeHolen() {
  try {
    upgrade = await hole("/api/upgrade");
    zeitVersatz = upgrade.serverzeit - Date.now() / 1000;
  } catch (e) { return; }
  if (tab === "upgrade") zeichneUpgrade();
  if (tab === "uebersicht") zeichneUebersichtInseln();
}
