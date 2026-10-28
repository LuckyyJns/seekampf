"use strict";
// ======================================================================
// Ozeankarte
// ======================================================================
const brett = new Image();   // Holzbrett als Karten-Hintergrund (liegt in der Welt, bewegt sich mit)
brett.src = "/static/karte-brett.jpg";
brett.addEventListener("load", () => { if (tab === "karte") zeichneKarte(); });
/** Text mit hellem Rand, damit er auf dem Holz lesbar bleibt */
function pixText(ctx, text, x, y) {
  const f = ka.farben, fill = ctx.fillStyle;
  ctx.save(); ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = f["text-halo"] || "rgba(255,255,255,.8)"; ctx.strokeText(text, x, y); ctx.restore();
  ctx.fillStyle = fill; ctx.fillText(text, x, y);
}
const ka = {
  daten: null, inseln: [], nachKoord: new Map(), allianzen: new Map(),
  modus: "spieler", spieler: null, auswahl: null,
  ebenen: { flotten: true, angriffe: true, frei: false, ziele: false, aenderungen: false, gitter: true },
  angriffe: [], spielFlotten: [], ringSchiff: "kleines_kriegsschiff",
  // Ansicht: Pixel je Feld und die Feldkoordinate in der linken oberen Ecke
  massstab: 10, ox: 0, oy: 0, bereit: false, farben: {}, zeiger: new Map(), letzteBewegung: 0,
};
const KNOTEN = { spaehschiff: 20, kleines_kriegsschiff: 12, kleines_handelsschiff: 10, grosses_kriegsschiff: 8,
                 grosses_handelsschiff: 7, kolonisationsschiff: 3 };
const MISSION = { attack: "Angriff", colonize: "Kolonisation", handel: "Handel", transport: "Handel", support: "Unterstützung", spy: "Spionage" };
const RINGE_MIN = [10, 30, 60];
/** Allianz-Kuerzel in eckigen Klammern - ausser es hat schon welche ("[.]"). */
const tagText = (tag) => /^\[.*\]$/.test(tag || "") ? tag : `[${tag || "?"}]`;
// Weltkoordinate -> Feld (Mittelpunkt); wie geo.feld im Flotten-Manager
function feld(x, y, z) { return [x * 5 + (z - 1) % 5 + 0.5, y * 5 + Math.floor((z - 1) / 5) + 0.5]; }
function koordFeld(text) {
  const t = String(text || "").split(":").map(Number);
  return t.length === 3 && t.every(n => !isNaN(n)) ? feld(t[0], t[1], t[2]) : null;
}
function fahrzeitS(a, b, knoten) {
  const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
  return Math.max(d / knoten * 600, 120);
}
function farbenLesen() {
  const cs = getComputedStyle(document.documentElement);
  for (const n of ["meer", "meer-welt", "meer-gitter", "meer-gitter-stark", "insel-neutral", "insel-frei", "insel-hervor",
                   "insel-rand", "brett-schleier", "text-halo", "karte-radius", "karte-radius-fuell", "al1", "al2", "al3", "al4", "al5", "al6", "al7", "al8", "al9", "al10", "al11", "al12", "gildenlos", "glow", "feind", "karte-text", "warn", "akzent", "akzent-2", "gut", "gedaempft"])
    ka.farben[n] = cs.getPropertyValue(`--${n}`).trim();
}

// ---------------------------------------------------------------- Daten
async function karteHolen() {
  let d;
  try { d = await hole("/api/karte"); } catch (e) { $("ka-stand").textContent = `Karte nicht ladbar: ${e.message}`; return; }
  zeitVersatz = d.serverzeit - Date.now() / 1000;
  ka.daten = d;
  ka.inseln = d.inseln.map(i => ({ ...i, f: feld(i.x, i.y, i.z) }));
  ka.nachKoord = new Map(ka.inseln.map(i => [i.k, i]));
  // Jede Allianz eine eigene Farbe (12 Stueck). Zuordnung nach Allianz-ID,
  // damit sich keine Farbe aendert, wenn sich die Inselzahlen verschieben.
  ka.allianzen = new Map([...d.allianzen].sort((a, b) => a.id - b.id).map((a, i) => [a.id, {
    ...a, farbe: `al${(i % 12) + 1}` }]));
  if (ka.spieler === null) ka.spieler = d.ich?.name || "";
  spielerListeFuellen();
  karteSeiteZeichnen();
  scanInfo(d.scan);
  if (!ka.bereit && ka.inseln.length) { einpassen(); ka.bereit = true; }
  zeichneKarte();
  karteZielAnspringen();
}
function karteZielAnspringen() {
  const k = ka.zielKoord;
  if (!k || tab !== "karte") return;
  ka.zielKoord = null;
  const t = k.split(":").map(Number);
  if (ka.nachKoord.has(k)) waehleInsel(k, true);
  else if (t.length === 3 && t.every(n => !isNaN(n))) zentrieren(...feld(t[0], t[1], t[2]), 30);
}
function spielerListeFuellen() {
  const sel = $("ka-spieler");
  const html = `<option value="">– niemand –</option>` + ka.daten.spieler.map(s =>
    `<option value="${esc(s.name)}">${esc(s.name)}${s.name === ka.daten.ich?.name ? " (du)" : ""} · ${s.inseln} Inseln</option>`).join("");
  if (sel.dataset.stand !== html) { sel.innerHTML = html; sel.dataset.stand = html; }
  sel.value = ka.spieler;
  $("ka-suchliste").innerHTML = ka.daten.spieler.map(s => `<option value="${esc(s.name)}">`).join("");
}
function scanInfo(scan) {
  const d = ka.daten;
  $("ka-scan").disabled = !!scan?.laeuft;
  $("ka-scan").textContent = scan?.laeuft ? `scannt … ${Math.round((scan.fortschritt || 0) * 100)} %` : "Karte jetzt scannen";
  $("ka-scan-info").textContent = scan?.fehler ? `Letzter Scan fehlgeschlagen: ${scan.fehler}`
    : d?.zeit ? `Stand ${uhrzeit(d.zeit, true)} · nächster Scan um 1:00 Uhr` : "noch kein Scan";
  $("ka-stand").textContent = d?.zeit ? `${zahl(d.inseln.length)} Inseln · ${d.spieler.length} Spieler · ${d.allianzen.length} Allianzen · Stand ${uhrzeit(d.zeit, true)}` : "noch kein Scan";
}
/** Alle eigenen Flotten direkt aus dem Spiel - auch von Hand geschickte */
async function spielFlottenHolen() {
  try { ka.spielFlotten = (await hole("/api/karte/flotten")).flotten || []; } catch (e) { /* Karte bleibt nutzbar */ }
  zeichneKarte();
}
async function angriffeHolen() {
  try { ka.angriffe = (await hole("/api/karte/angriffe")).angriffe || []; } catch (e) { /* Karte bleibt nutzbar */ }
}
$("ka-scan").onclick = async (e) => {
  const r = await aktion(e.target, () => post("/api/karte/scan"), "Scan gestartet – dauert etwa eine Minute");
  if (!r) return;
  const warten = setInterval(async () => {
    let s; try { s = await hole("/api/karte/status"); } catch (err) { return; }
    scanInfo(s);
    if (!s.laeuft) { clearInterval(warten); karteHolen(); melde(s.fehler ? `Scan fehlgeschlagen: ${s.fehler}` : "Karte aktualisiert", s.fehler ? "schlecht" : "gut"); }
  }, 2000);
};

// ------------------------------------------------------------ Ansicht
const canvas = $("ka-canvas"), ctx = canvas.getContext("2d");
function groesse() {
  const r = $("ka-rahmen").getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, r.width * dpr); canvas.height = Math.max(1, r.height * dpr);
  return { b: r.width, h: r.height, dpr };
}
function zuBild(fx, fy) { return [(fx - ka.ox) * ka.massstab, (fy - ka.oy) * ka.massstab]; }
function zuFeld(px, py) { return [px / ka.massstab + ka.ox, py / ka.massstab + ka.oy]; }
function bereichZeigen(x0, y0, x1, y1) {
  const { b, h } = groesse();
  ka.massstab = Math.min(b / (x1 - x0), h / (y1 - y0));
  ka.ox = (x0 + x1) / 2 - b / 2 / ka.massstab;
  ka.oy = (y0 + y1) / 2 - h / 2 / ka.massstab;
  zeichneKarte();
}
function einpassen() {
  if (!ka.inseln.length) return welt();
  const xs = ka.inseln.map(i => i.x), ys = ka.inseln.map(i => i.y);
  bereichZeigen((Math.min(...xs) - 1) * 5, (Math.min(...ys) - 1) * 5, (Math.max(...xs) + 2) * 5, (Math.max(...ys) + 2) * 5);
}
function welt() { const w = ka.daten?.welt || { min: 1, max: 100 }; bereichZeigen(w.min * 5 - 5, w.min * 5 - 5, (w.max + 1) * 5 + 5, (w.max + 1) * 5 + 5); }
function zoomen(faktor, px, py) {
  const { b, h } = { b: canvas.clientWidth, h: canvas.clientHeight };
  px ??= b / 2; py ??= h / 2;
  const [fx, fy] = zuFeld(px, py);
  ka.massstab = Math.min(80, Math.max(1.2, ka.massstab * faktor));
  ka.ox = fx - px / ka.massstab; ka.oy = fy - py / ka.massstab;
  zeichneKarte();
}
function zentrieren(fx, fy, massstab) {
  if (massstab) ka.massstab = massstab;
  ka.ox = fx - canvas.clientWidth / 2 / ka.massstab;
  ka.oy = fy - canvas.clientHeight / 2 / ka.massstab;
  zeichneKarte();
}
$("ka-plus").onclick = () => zoomen(1.5);
$("ka-minus").onclick = () => zoomen(1 / 1.5);
$("ka-einpassen").onclick = einpassen;
$("ka-welt").onclick = welt;

// ---------------------------------------------------------- Einfaerben
function inselFarbe(i) {
  const f = ka.farben;
  if (!i.b) return null;                                      // frei/Ruine: nur Umriss
  if (ka.modus === "spieler") return i.b === ka.spieler ? f["insel-hervor"] : f["insel-neutral"];
  const a = ka.allianzen.get(i.a);
  return a ? f[a.farbe] : f.gildenlos;
}
function hervorgehoben(i) {
  return ka.modus === "spieler" && !!ka.spieler && i.b === ka.spieler;
}
/** Platz in der Kolonisations-Warteschlange (1, 2, ...) oder 0 */
function koloPlatz(k) {
  return (kolonie?.warteschlange || []).findIndex(e => e.koordinaten === k) + 1;
}

// --------------------------------------------------------- Flottenlage
function eigeneInselFeld(id) {
  const i = (flotte?.inseln || []).find(x => x.id === id);
  return i ? koordFeld(i.koordinaten) : null;
}
/** Wo steht die Flotte gerade? Hinweg start->ziel, Rueckweg ziel->start.
    Zurueckgerufen vor Ankunft: Wende in der Mitte zwischen Abfahrt und Rueckkehr. */
function flottenLage(f, t) {
  const start = eigeneInselFeld(f.insel_id), ziel = koordFeld(f.koordinaten);
  if (!start || !ziel || !f.arrive_at) return null;
  const dep = f.depart_at || f.arrive_at - fahrzeitS(start, ziel, 10), arr = f.arrive_at, ret = f.return_at;
  const lerp = (a, b, q) => [a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q];
  const q01 = (v) => Math.max(0, Math.min(1, v));
  if (f.zurueckgerufen && !f.angekommen && ret) {
    const wende = (dep + ret) / 2, punktWende = lerp(start, ziel, q01((wende - dep) / (arr - dep)));
    if (t < wende) return { start, ziel, pos: lerp(start, ziel, q01((t - dep) / (arr - dep))), rueck: false };
    return { start, ziel: punktWende, pos: lerp(punktWende, start, q01((t - wende) / (ret - wende))), rueck: true, gewendet: true };
  }
  if (t < arr) return { start, ziel, pos: lerp(start, ziel, q01((t - dep) / (arr - dep))), rueck: false };
  if (ret) return { start, ziel, pos: lerp(ziel, start, q01((t - arr) / (ret - arr))), rueck: true };
  return { start, ziel, pos: ziel, rueck: false };
}
/** Flotten fuer die Karte: Raids des Flotten-Managers plus Kolonisationsschiffe, die der Kolonisations-Bot unterwegs hat */
function karteFlotten() {
  const kolo = (kolonie?.warteschlange || []).filter(e => e.status === "unterwegs" && !e.zurueckgerufen && e.bau_insel && e.ankunft).map(e => ({
    id: e.flotte_id, insel_id: e.bau_insel, koordinaten: e.koordinaten, ziel_name: e.name, mission: "colonize",
    depart_at: e.abfahrt, arrive_at: e.ankunft, return_at: e.rueckkehr || null, angekommen: false, ships: { kolonisationsschiff: 1, ...(e.begleitung?.schiffe || {}) }, units: e.begleitung?.truppen || {} }));
  // Was kein Bot meldet (z. B. von Hand geschickt), kommt aus dem Spiel dazu
  const bekannt = new Set([...(flotte?.flotten || []), ...kolo].map(f => String(f.id)));
  const rest = ka.spielFlotten.filter(f => !bekannt.has(String(f.id)));
  return [...(flotte?.flotten || []), ...kolo, ...rest];
}
/** Punktfarbe meiner Flotten je Mission (Rot bleibt Angriffen auf mich vorbehalten) */
function flottenFarbe(mission) {
  const f = ka.farben;
  if (mission === "colonize") return f["akzent-2"] || f.akzent;
  if (mission === "handel" || mission === "transport") return f.gut || f["karte-text"];
  return f["karte-text"];
}
function missionForm(ctx2, x, y, r, mission) {
  ctx2.beginPath();
  if (mission === "colonize") { ctx2.moveTo(x, y - r * 1.3); ctx2.lineTo(x + r * 1.3, y); ctx2.lineTo(x, y + r * 1.3); ctx2.lineTo(x - r * 1.3, y); ctx2.closePath(); }
  else if (mission === "handel" || mission === "transport") ctx2.rect(x - r, y - r, r * 2, r * 2);
  else ctx2.arc(x, y, r, 0, Math.PI * 2);
}

// ------------------------------------------------------------- Zeichnen
let zeichnenGeplant = false;
function zeichneKarte() {
  if (zeichnenGeplant) return;
  zeichnenGeplant = true;
  requestAnimationFrame(() => { zeichnenGeplant = false; zeichneJetzt(); });
}
function zeichneJetzt() {
  if (tab !== "karte") return;
  const { b, h, dpr } = groesse();
  const f = ka.farben, m = ka.massstab, t = jetzt();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = f.meer; ctx.fillRect(0, 0, b, h);
  const w = ka.daten?.welt || { min: 1, max: 100 };
  const [wx0, wy0] = zuBild(w.min * 5, w.min * 5), [wx1, wy1] = zuBild((w.max + 1) * 5, (w.max + 1) * 5);
  const bw = wx1 - wx0, bh = wy1 - wy0;
  if (brett.complete && brett.naturalWidth) {
    // nur den sichtbaren Ausschnitt des Bretts zeichnen (bei starkem Zoom sonst riesig)
    const vx0 = Math.max(0, wx0), vy0 = Math.max(0, wy0), vx1 = Math.min(b, wx1), vy1 = Math.min(h, wy1);
    if (vx1 > vx0 && vy1 > vy0) {
      const nw = brett.naturalWidth, nh = brett.naturalHeight;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(brett, (vx0 - wx0) / bw * nw, (vy0 - wy0) / bh * nh, (vx1 - vx0) / bw * nw, (vy1 - vy0) / bh * nh, vx0, vy0, vx1 - vx0, vy1 - vy0);
      ctx.fillStyle = f["brett-schleier"] || "rgba(246,234,208,.4)"; ctx.fillRect(vx0, vy0, vx1 - vx0, vy1 - vy0);
    }
  } else { ctx.fillStyle = f["meer-welt"]; ctx.fillRect(wx0, wy0, bw, bh); }

  // Sektorgitter (alle 5 Felder), staerker alle 10 Sektoren
  if (ka.ebenen.gitter && m * 5 >= 6) {
    const [fx0, fy0] = zuFeld(0, 0), [fx1, fy1] = zuFeld(b, h);
    const s0x = Math.max(w.min, Math.floor(fx0 / 5)), s1x = Math.min(w.max + 1, Math.ceil(fx1 / 5));
    const s0y = Math.max(w.min, Math.floor(fy0 / 5)), s1y = Math.min(w.max + 1, Math.ceil(fy1 / 5));
    ctx.lineWidth = 1;
    for (let s = s0x; s <= s1x; s++) {
      ctx.strokeStyle = s % 10 === 0 ? f["meer-gitter-stark"] : f["meer-gitter"]; ctx.lineWidth = s % 10 === 0 ? 2 : 1;
      const x = Math.round(zuBild(s * 5, 0)[0]) + .5;
      ctx.beginPath(); ctx.moveTo(x, Math.max(0, wy0)); ctx.lineTo(x, Math.min(h, wy1)); ctx.stroke();
    }
    for (let s = s0y; s <= s1y; s++) {
      ctx.strokeStyle = s % 10 === 0 ? f["meer-gitter-stark"] : f["meer-gitter"]; ctx.lineWidth = s % 10 === 0 ? 2 : 1;
      const y = Math.round(zuBild(0, s * 5)[1]) + .5;
      ctx.beginPath(); ctx.moveTo(Math.max(0, wx0), y); ctx.lineTo(Math.min(b, wx1), y); ctx.stroke();
    }
    if (m * 5 >= 60) {
      ctx.fillStyle = f.gedaempft; ctx.font = "11px 'Pixelify Sans', 'IBM Plex Sans', sans-serif"; ctx.textBaseline = "top";
      for (let sx = s0x; sx < s1x; sx++) for (let sy = s0y; sy < s1y; sy++) {
        const [x, y] = zuBild(sx * 5, sy * 5);
        ctx.globalAlpha = .7; pixText(ctx, `${sx}:${sy}`, x + 4, y + 3); ctx.globalAlpha = 1;
      }
    }
  }

  // Kleine Wellen auf dem Brett (feste Plaetze, nur beim Heranzoomen)
  if (m >= 5) {
    const u = Math.max(2, Math.round(m * .22));
    const wellenVersatz = document.body.classList.contains("ohne-anim") ? 0 : (Math.floor(t / 2.5) % 2) * u;
    const [fx0, fy0] = zuFeld(0, 0), [fx1, fy1] = zuFeld(b, h);
    ctx.fillStyle = f["karte-text"]; ctx.globalAlpha = .16;
    for (let cx = Math.floor(fx0 / 3); cx <= Math.ceil(fx1 / 3); cx++) for (let cy = Math.floor(fy0 / 3); cy <= Math.ceil(fy1 / 3); cy++) {
      const hash = (Math.imul(cx, 73856093) ^ Math.imul(cy, 19349663)) >>> 0;
      if (hash % 9 !== 0) continue;
      const [wxp, wyp] = zuBild(cx * 3 + (hash >> 8) % 3, cy * 3 + (hash >> 12) % 3);
      const x0 = Math.round(wxp) + wellenVersatz, y0 = Math.round(wyp);
      for (const [dx, dy] of [[0, 1], [1, 0], [2, 0], [3, 1], [4, 2], [5, 2], [6, 1]]) ctx.fillRect(x0 + dx * u, y0 + dy * u, u, u);
    }
    ctx.globalAlpha = 1;
  }

  // Raid-Radius und Ziele (K4): je raidender Insel ihr eigener Radius (sonst
  // der globale). Deckungsgleiche Rahmen (gleicher Sektor, gleicher Radius)
  // werden einmal gezeichnet und tragen alle Inselnamen.
  if (ka.ebenen.ziele && flotte) {
    const rahmen = new Map();
    for (const i of flotte.inseln.filter(x => x.aktiv)) {
      const r = Number(i.einstellungen?.scan_radius_sektoren ?? flotte.settings?.scan_radius_sektoren ?? 1);
      const [sx, sy] = i.koordinaten.split(":").map(Number);
      const schluessel = `${sx}:${sy}:${r}`;
      if (!rahmen.has(schluessel)) rahmen.set(schluessel, { sx, sy, r, namen: [] });
      rahmen.get(schluessel).namen.push(i.name);
    }
    ctx.font = "600 12px 'Pixelify Sans', 'IBM Plex Sans', sans-serif"; ctx.textBaseline = "bottom";
    for (const { sx, sy, r, namen } of rahmen.values()) {
      const [x0, y0] = zuBild((sx - r) * 5, (sy - r) * 5), [x1, y1] = zuBild((sx + r + 1) * 5, (sy + r + 1) * 5);
      const bx = Math.round(x0) + .5, by = Math.round(y0) + .5, bw = Math.round(x1 - x0), bh = Math.round(y1 - y0);
      ctx.fillStyle = f["karte-radius-fuell"] || "rgba(17,169,189,.14)"; ctx.fillRect(bx, by, bw, bh);   // Flaeche: wo gesucht wird
      ctx.setLineDash([10, 6]);
      ctx.lineWidth = 6; ctx.strokeStyle = f["insel-rand"]; ctx.strokeRect(bx, by, bw, bh);               // dunkle Kontur
      ctx.lineWidth = 3; ctx.strokeStyle = f["karte-radius"] || f.akzent; ctx.strokeRect(bx, by, bw, bh); // helle Linie
      ctx.setLineDash([]);
      ctx.fillStyle = f["karte-text"];
      pixText(ctx, `${namen.join(", ")} · Radius ${r}`, bx + 4, by - 4);
    }
  }

  // Fahrzeit-Ringe (K6) um die ausgewaehlte eigene Insel
  const auswahl = ka.auswahl && ka.nachKoord.get(ka.auswahl);
  if (auswahl && auswahl.b === ka.daten?.ich?.name) {
    const [cx, cy] = zuBild(...auswahl.f), knoten = KNOTEN[ka.ringSchiff];
    ctx.lineWidth = 1.5; ctx.font = "600 11px 'Pixelify Sans', 'IBM Plex Sans', sans-serif"; ctx.textBaseline = "bottom";
    for (const min of RINGE_MIN) {
      const rad = knoten * min / 10 * m;
      ctx.setLineDash([8, 6]);
      ctx.beginPath(); ctx.arc(cx, cy, rad, 0, Math.PI * 2);
      ctx.lineWidth = 5; ctx.strokeStyle = f["insel-rand"]; ctx.stroke();
      ctx.lineWidth = 2.5; ctx.strokeStyle = f["karte-radius"] || f.akzent; ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = f["karte-text"]; pixText(ctx, `${min} min`, cx + 4, cy - rad - 2);
    }
  }

  // Inseln
  const r = inselRadius();
  const labels = m >= 26;
  const kolo = new Set((kolonie?.warteschlange || []).map(e => e.koordinaten));
  for (const i of ka.inseln) {
    const [x, y] = zuBild(...i.f);
    if (x < -20 || y < -20 || x > b + 20 || y > h + 20) continue;
    const farbe = inselFarbe(i), hervor = hervorgehoben(i);
    const al = ka.modus === "allianz" && i.a ? ka.allianzen.get(i.a) : null;
    const leuchtet = !!al || hervor;
    const rr = Math.max(2, Math.round(hervor ? r * 1.2 : (farbe ? r : r * .8)));
    ctx.beginPath(); ctx.rect(Math.round(x) - rr, Math.round(y) - rr, rr * 2, rr * 2);
    if (farbe) {
      // Leuchtende Punkte wie auf map.seekampf.brckr.de; im hellen Thema dezenter
      if (leuchtet) { ctx.shadowColor = farbe; ctx.shadowBlur = Math.max(5, r * 1.5) * Number(f.glow || 1); }
      ctx.fillStyle = farbe; ctx.globalAlpha = ka.modus === "spieler" && !hervor ? .55 : 1;
      ctx.fill(); ctx.globalAlpha = 1; ctx.lineWidth = 2; ctx.strokeStyle = f["insel-rand"]; ctx.stroke(); ctx.shadowBlur = 0; ctx.shadowColor = "transparent";
    } else {
      // Frei/Ruine: dunkler, kaum sichtbarer Punkt - mit Ebene "Freie Inseln" als heller Ring
      ctx.fillStyle = f["insel-frei"]; ctx.globalAlpha = ka.ebenen.frei ? .25 : .14; ctx.fill();
      ctx.lineWidth = ka.ebenen.frei ? 2 : 1; ctx.strokeStyle = f["insel-frei"];
      ctx.globalAlpha = ka.ebenen.frei ? 1 : .3; ctx.setLineDash(i.r ? [2, 2] : []); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
    if (hervor) { { const q = Math.round(r * 1.25 + 3.5); ctx.beginPath(); ctx.rect(Math.round(x) - q, Math.round(y) - q, q * 2, q * 2); } ctx.lineWidth = 2; ctx.strokeStyle = f["insel-hervor"]; ctx.globalAlpha = .45; ctx.stroke(); ctx.globalAlpha = 1; }
    // Kolonisations-Warteschlange: dezenter gestrichelter Rahmen
    if (kolo.has(i.k)) { { const q = Math.round(r * 1.25 + 3.5); ctx.beginPath(); ctx.rect(Math.round(x) - q, Math.round(y) - q, q * 2, q * 2); } ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]); ctx.strokeStyle = f["akzent-2"]; ctx.globalAlpha = .6; ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1; }
    if (i.k === ka.auswahl) { { const q = Math.round(r * 1.25 + 7); ctx.beginPath(); ctx.rect(Math.round(x) - q, Math.round(y) - q, q * 2, q * 2); } ctx.lineWidth = 2.5; ctx.strokeStyle = f["karte-text"]; ctx.stroke(); }
    if (labels) {
      ctx.font = "500 11px 'Pixelify Sans', 'IBM Plex Sans', sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = f["karte-text"];
      // Allianz-Modus: nur das Kuerzel (passt immer); sonst der Besitzer.
      // Auf die Feldbreite kuerzen, damit Nachbarn sich nicht ueberschreiben.
      let text = ka.modus === "allianz"
        ? (i.a ? (ka.allianzen.get(i.a)?.tag || "?") : (i.b ? "–" : (i.r ? "Ruine" : "frei")))
        : (i.b || (i.r ? "Ruine" : "frei"));
      while (text.length > 3 && ctx.measureText(text).width > m * 0.94) text = text.slice(0, -2) + "…";
      ctx.globalAlpha = hervor || ka.modus === "allianz" ? 1 : .75; pixText(ctx, text, x, y + r + 3); ctx.globalAlpha = 1; ctx.textAlign = "start";
    }
  }

  // Raid-Ziele markieren
  if (ka.ebenen.ziele && flotte) {
    for (const i of flotte.inseln.filter(x => x.aktiv)) for (const z of i.ziele) {
      const p = koordFeld(z.koordinaten); if (!p) continue;
      const [x, y] = zuBild(...p), s = r + 3;
      ctx.beginPath(); ctx.moveTo(x - s, y - s); ctx.lineTo(x + s, y + s); ctx.moveTo(x + s, y - s); ctx.lineTo(x - s, y + s);
      ctx.lineWidth = 6; ctx.strokeStyle = f["insel-rand"]; ctx.stroke();
      ctx.lineWidth = 3; ctx.strokeStyle = f["karte-radius"] || f.akzent; ctx.stroke();
    }
  }

  // Aenderungen (K9)
  if (ka.ebenen.aenderungen && ka.daten) {
    ctx.strokeStyle = f.warn; ctx.lineWidth = 2;
    for (const a of ka.daten.aenderungen) {
      const p = koordFeld(a.k); if (!p) continue;
      const [x, y] = zuBild(...p), s = r + 6;
      ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x, y + s); ctx.lineTo(x - s, y); ctx.closePath(); ctx.stroke();
    }
  }

  // Meine Flotten (Strecke + wandernder Punkt)
  ka.flottenPunkte = [];
  if (ka.ebenen.flotten) {
    for (const fl of karteFlotten()) {
      const lage = flottenLage(fl, t); if (!lage) continue;
      const [ax, ay] = zuBild(...lage.start), [zx, zy] = zuBild(...lage.ziel), [px, py] = zuBild(...lage.pos);
      ctx.strokeStyle = flottenFarbe(fl.mission); ctx.globalAlpha = .2; ctx.lineWidth = 1; ctx.setLineDash(lage.rueck ? [4, 4] : []);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(zx, zy); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
      // kleiner Punkt: Form und Farbe je Mission, auf dem Rueckweg blasser
      ctx.globalAlpha = lage.rueck ? .6 : 1;
      missionForm(ctx, px, py, 3.5, fl.mission);
      ctx.fillStyle = flottenFarbe(fl.mission); ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = f["insel-rand"]; ctx.stroke();
      ctx.globalAlpha = 1;
      ka.flottenPunkte.push({ x: px, y: py, fl, lage });
    }
  }

  // Angriffe auf mich (K5)
  ka.angriffsPunkte = [];
  if (ka.ebenen.angriffe) {
    for (const a of ka.angriffe) {
      const ziel = eigeneInselFeld(a.ziel_insel_id) || koordFeld(a.ziel_koordinaten); if (!ziel) continue;
      const [zx, zy] = zuBild(...ziel);
      const herkunft = koordFeld(a.herkunft_koordinaten);
      let sx, sy;
      if (herkunft) [sx, sy] = zuBild(...herkunft);
      else {
        const richt = { Norden: [0, -1], Sueden: [0, 1], "Süden": [0, 1], Osten: [1, 0], Westen: [-1, 0] }[a.richtung] || [0, -1];
        sx = zx + richt[0] * 70; sy = zy + richt[1] * 70;
      }
      ctx.strokeStyle = f.feind; ctx.lineWidth = 2.5; ctx.setLineDash([7, 4]);
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(zx, zy); ctx.stroke(); ctx.setLineDash([]);
      const wink = Math.atan2(zy - sy, zx - sx);
      ctx.fillStyle = f.feind; ctx.beginPath();
      ctx.moveTo(zx - Math.cos(wink) * (r + 4), zy - Math.sin(wink) * (r + 4));
      ctx.lineTo(zx - Math.cos(wink - .45) * (r + 16), zy - Math.sin(wink - .45) * (r + 16));
      ctx.lineTo(zx - Math.cos(wink + .45) * (r + 16), zy - Math.sin(wink + .45) * (r + 16)); ctx.closePath(); ctx.fill();
      // angegriffene Insel rot umranden, Angreifer als roter Punkt
      ctx.beginPath(); ctx.arc(zx, zy, r + 7, 0, Math.PI * 2); ctx.lineWidth = 3; ctx.strokeStyle = f.feind; ctx.stroke();
      ctx.beginPath(); ctx.arc(sx, sy, 6, 0, Math.PI * 2); ctx.fillStyle = f.feind; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = f["insel-rand"]; ctx.stroke();
      const eta = ts(a.arrive_at);
      ctx.font = "700 12px 'Pixelify Sans', 'IBM Plex Sans', sans-serif"; ctx.fillStyle = f.feind; ctx.textBaseline = "bottom";
      pixText(ctx, `⚠ ${a.absender || "Angriff"}${eta ? " · " + dauer(eta - t) : ""}`, sx + 6, sy - 4);
      ka.angriffsPunkte.push({ x: sx, y: sy, a });
    }
  }

  // Animation nur, solange sich etwas bewegt
  if ((ka.ebenen.flotten && karteFlotten().length) || (ka.ebenen.angriffe && ka.angriffe.length)) {
    clearTimeout(ka.animTimer);
    ka.animTimer = setTimeout(zeichneKarte, 250);
  }
}

// ------------------------------------------------------------ Seitenleiste
function karteSeiteZeichnen() {
  const d = ka.daten; if (!d) return;
  $("ka-spieler-wahl").hidden = ka.modus !== "spieler";
  document.querySelectorAll("[data-modus]").forEach(b => b.classList.toggle("an", b.dataset.modus === ka.modus));
  // Legende (K8)
  let html;
  const frei = ka.inseln.filter(i => !i.b).length;
  if (ka.modus === "spieler") {
    const s = d.spieler.find(x => x.name === ka.spieler);
    $("ka-legende-titel").textContent = "Legende";
    html = `<div class="legende-liste">
      ${s ? `<div class="info-zeile" style="border:0"><span class="res-key"><i class="kreis" style="background:var(--insel-hervor)"></i>${esc(s.name)}</span><span>${s.inseln} Inseln · ${zahl(s.punkte)} P.</span></div>` : ""}
      <div class="info-zeile"><span class="res-key"><i class="kreis" style="background:var(--insel-neutral)"></i>andere Spieler</span><span>${ka.inseln.length - frei - (s?.inseln || 0)}</span></div>
      <div class="info-zeile"><span class="res-key"><i class="kreis" style="background:transparent;border:2px solid var(--insel-frei)"></i>frei / Ruine</span><span>${frei}</span></div>
      ${kolonie?.warteschlange?.length ? `<div class="info-zeile"><span class="res-key"><i class="kreis" style="background:transparent;border:2px dashed var(--akzent-2);border-radius:0;opacity:.8"></i>Kolonisations-Warteschlange</span><span>${kolonie.warteschlange.length}</span></div>` : ""}
      <div class="info-zeile"><span class="res-key"><i class="symbol-form" style="border-radius:50%;background:var(--karte-text)"></i>Raid · <i class="symbol-form" style="background:var(--gut)"></i>Handel · <i class="symbol-form" style="transform:rotate(45deg) scale(.85);background:var(--akzent-2)"></i>Kolonisation</span><span></span></div>
      <div class="info-zeile"><span class="res-key"><i class="symbol-form" style="background:var(--feind);border-radius:50%"></i>Angriff auf dich</span><span></span></div>
    </div>`;
  } else {
    $("ka-legende-titel").textContent = "Allianzen";
    const liste = [...ka.allianzen.values()].sort((x, y) => y.inseln - x.inseln);
    html = `<div class="legende-liste">${liste.map(a => `<div class="info-zeile" style="border-top:0;align-items:center">
        <span class="res-key" style="color:var(--text)"><i class="kreis leuchtend" style="background:var(--${a.farbe});--c:var(--${a.farbe})"></i><span><b>${esc(tagText(a.tag))}</b> ${esc(a.name)}${a.id === d.ich?.allianz?.id ? " · deine" : ""}</span></span>
        <span class="leise klein-text" style="white-space:nowrap">${a.inseln} Inseln · ${a.spieler} Sp.</span></div>`).join("")}
      <div class="info-zeile"><span class="res-key"><i class="kreis" style="background:var(--gildenlos)"></i>Gildenlos</span><span>${ka.inseln.filter(i => i.b && !i.a).length}</span></div>
      <div class="info-zeile"><span class="res-key"><i class="kreis" style="background:transparent;border:2px solid var(--insel-frei)"></i>frei / Ruine</span><span>${ka.inseln.filter(i => !i.b).length}</span></div>
      <div class="leise klein-text" style="margin-top:6px">Nah herangezoomt steht das Kürzel an jeder Insel.</div>
    </div>`;
  }
  $("ka-legende").innerHTML = html;
  // Aenderungen (K9)
  const karte = $("ka-aenderungen-karte");
  karte.hidden = !ka.ebenen.aenderungen;
  if (ka.ebenen.aenderungen) {
    $("ka-aenderungen-info").textContent = d.vorher_zeit ? `seit ${uhrzeit(d.vorher_zeit, true)}` : "noch kein Vergleich";
    $("ka-aenderungen").innerHTML = d.aenderungen.length ? d.aenderungen.map(a =>
      `<button class="aenderung" data-springe="${esc(a.k)}"><span class="mono">${esc(a.k)}</span> · ${esc(a.text)}</button>`).join("")
      : `<div class="leise klein-text">${d.vorher_zeit ? "keine Änderungen" : "Erst nach dem zweiten Scan gibt es etwas zu vergleichen."}</div>`;
  }
  inselInfo();
}
function inselInfo() {
  const i = ka.auswahl && ka.nachKoord.get(ka.auswahl);
  if (!i) { $("ka-info").innerHTML = `<span class="leise klein-text">Klick auf eine Insel für Details.</span>`; return; }
  const a = ka.allianzen.get(i.a), ich = ka.daten.ich?.name, eigen = i.b === ich;
  const zeilen = [
    ["Koordinaten", `<span class="mono">${esc(i.k)}</span>`],
    ["Besitzer", i.b ? `<button class="klein" data-waehle-spieler="${esc(i.b)}">${esc(i.b)}</button>` : (i.r ? "Ruine" : "frei")],
    ["Allianz", a ? `<button class="klein" data-waehle-allianz="${a.id}">${esc(tagText(a.tag))} ${esc(a.name)}</button>` : "–"],
    ["Punkte", zahl(i.p)],
  ];
  // Fahrzeit von den eigenen Inseln mit dem gewaehlten Schiffstyp
  const eigene = (flotte?.inseln || []).filter(x => x.koordinaten !== i.k);
  const knoten = KNOTEN[ka.ringSchiff];
  const fahrt = eigene.map(x => `<div class="info-zeile"><span>ab ${esc(x.name)}</span><span>${dauer(fahrzeitS(koordFeld(x.koordinaten), i.f, knoten))}</span></div>`).join("");
  const eigeneId = (flotte?.inseln || []).find(x => x.koordinaten === i.k)?.id;
  $("ka-info").innerHTML = `
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">${avatar(i.n, eigeneId || i.x * 1000 + i.y)}
      <div class="namenblock" style="display:flex;flex-direction:column;line-height:1.25"><b>${esc(i.n || "Insel")}</b>
      <span class="leise klein-text">${eigen ? "deine Insel" : i.b ? "bewohnt" : i.r ? "Ruine" : "unbewohnt"}</span></div></div>
    ${zeilen.map(([k, v]) => `<div class="info-zeile"><span>${k}</span><span>${v}</span></div>`).join("")}
    ${eigeneId ? `<div class="formular-leiste" style="margin-top:10px"><a href="#flotte/${eigeneId}"><button class="klein">Flotten-Manager</button></a>
      <a href="#upgrade/${eigeneId}"><button class="klein">Upgrade-Bot</button></a></div>` : ""}
    ${koloPlatz(i.k) ? `<div class="leise klein-text" style="margin-top:10px">Platz ${koloPlatz(i.k)} der Kolonisations-Warteschlange</div>`
      : !i.b || !eigen ? `<div class="formular-leiste" style="margin-top:10px"><button class="klein" data-kolonie-hinzu="${esc(i.k)}">Zur Kolonisation</button></div>` : ""}
    <div class="feld" style="margin-top:12px"><label for="ka-ring-schiff">${eigen ? "Fahrzeit-Ringe für" : "Fahrzeit mit"}</label>
      <select id="ka-ring-schiff" class="eingabe">${Object.entries(KNOTEN).map(([k, v]) => `<option value="${k}" ${k === ka.ringSchiff ? "selected" : ""}>${esc(sname(k))} (${v} Knoten)</option>`).join("")}</select></div>
    ${fahrt ? `<div style="margin-top:8px">${fahrt}</div>` : ""}`;
  $("ka-ring-schiff").onchange = (e) => { ka.ringSchiff = e.target.value; inselInfo(); zeichneKarte(); };
}
function waehleInsel(koord, springen) {
  ka.auswahl = koord;
  const i = ka.nachKoord.get(koord);
  if (i && springen) zentrieren(...i.f, Math.max(ka.massstab, 22));
  inselInfo(); zeichneKarte();
}

// ------------------------------------------------------------ Bedienung
document.querySelectorAll("[data-modus]").forEach(b => b.onclick = () => { ka.modus = b.dataset.modus; karteSeiteZeichnen(); zeichneKarte(); });
$("ka-spieler").onchange = (e) => { ka.spieler = e.target.value; karteSeiteZeichnen(); zeichneKarte(); };
document.querySelectorAll("[data-ebene]").forEach(cb => cb.onchange = () => {
  ka.ebenen[cb.dataset.ebene] = cb.checked;
  try { localStorage.setItem("karte-ebenen", JSON.stringify(ka.ebenen)); } catch (e) { /* egal */ }
  karteSeiteZeichnen(); zeichneKarte();
});
try {
  Object.assign(ka.ebenen, JSON.parse(localStorage.getItem("karte-ebenen") || "{}"));
  document.querySelectorAll("[data-ebene]").forEach(cb => cb.checked = !!ka.ebenen[cb.dataset.ebene]);
} catch (e) { /* egal */ }
$("tab-karte").addEventListener("click", (e) => {
  const sp = e.target.closest("[data-waehle-spieler]");
  if (sp) { ka.modus = "spieler"; ka.spieler = sp.dataset.waehleSpieler; spielerListeFuellen(); karteSeiteZeichnen(); zeichneKarte(); return; }
  if (e.target.closest("[data-waehle-allianz]")) { ka.modus = "allianz"; karteSeiteZeichnen(); zeichneKarte(); return; }
  const sprung = e.target.closest("[data-springe]");
  if (sprung) waehleInsel(sprung.dataset.springe, true);
});
$("ka-suche-form").onsubmit = (e) => {
  e.preventDefault();
  const q = $("ka-suche").value.trim();
  if (!q) return;
  const t = q.split(":").map(Number);
  if (t.length === 3 && t.every(n => !isNaN(n))) {
    const k = t.join(":");
    if (ka.nachKoord.has(k)) return waehleInsel(k, true);
    return zentrieren(...feld(t[0], t[1], t[2]), 30);
  }
  if (t.length === 2 && t.every(n => !isNaN(n))) return zentrieren(t[0] * 5 + 2.5, t[1] * 5 + 2.5, 24);
  const s = ka.daten?.spieler.find(x => x.name.toLowerCase() === q.toLowerCase())
         || ka.daten?.spieler.find(x => x.name.toLowerCase().includes(q.toLowerCase()));
  if (!s) return melde(`Nichts gefunden zu „${q}“`, "schlecht");
  ka.modus = "spieler"; ka.spieler = s.name; spielerListeFuellen(); karteSeiteZeichnen();
  const ihre = ka.inseln.filter(i => i.b === s.name);
  const xs = ihre.map(i => i.f[0]), ys = ihre.map(i => i.f[1]);
  bereichZeigen(Math.min(...xs) - 8, Math.min(...ys) - 8, Math.max(...xs) + 8, Math.max(...ys) + 8);
};

// Ziehen, Zoomen (Mausrad, zwei Finger), Klick, Tooltip
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  zoomen(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });
canvas.addEventListener("pointerdown", (e) => {
  canvas.setPointerCapture(e.pointerId);
  ka.zeiger.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY });
  canvas.classList.add("zieht");
});
canvas.addEventListener("pointermove", (e) => {
  const r = canvas.getBoundingClientRect();
  const p = ka.zeiger.get(e.pointerId);
  if (p) {
    if (ka.zeiger.size === 2) {
      const [a, b2] = [...ka.zeiger.values()];
      const vorher = Math.hypot(a.x - b2.x, a.y - b2.y);
      p.x = e.clientX; p.y = e.clientY;
      const nachher = Math.hypot(a.x - b2.x, a.y - b2.y);
      if (vorher > 0) zoomen(nachher / vorher, (a.x + b2.x) / 2 - r.left, (a.y + b2.y) / 2 - r.top);
    } else {
      ka.ox -= (e.clientX - p.x) / ka.massstab; ka.oy -= (e.clientY - p.y) / ka.massstab;
      p.x = e.clientX; p.y = e.clientY; zeichneKarte();
    }
    $("ka-tip").style.display = "none";
    return;
  }
  tooltipAn(e.clientX - r.left, e.clientY - r.top);
});
function zeigerEnde(e) {
  const p = ka.zeiger.get(e.pointerId);
  ka.zeiger.delete(e.pointerId);
  if (!ka.zeiger.size) canvas.classList.remove("zieht");
  if (p && Math.hypot(e.clientX - p.sx, e.clientY - p.sy) < 5 && e.type === "pointerup") {
    const r = canvas.getBoundingClientRect();
    const i = inselBei(e.clientX - r.left, e.clientY - r.top);
    waehleInsel(i ? i.k : null, false);
  }
}
canvas.addEventListener("pointerup", zeigerEnde);
canvas.addEventListener("pointercancel", zeigerEnde);
canvas.addEventListener("pointerleave", () => { $("ka-tip").style.display = "none"; });
canvas.addEventListener("dblclick", (e) => { const r = canvas.getBoundingClientRect(); zoomen(2, e.clientX - r.left, e.clientY - r.top); });

function inselRadius() { return Math.max(3, Math.min(0.32 * ka.massstab, 12)); }
function inselBei(px, py) {
  const r = inselRadius() + 5;
  let best = null, bd = r;
  for (const i of ka.inseln) {
    const [x, y] = zuBild(...i.f), d = Math.hypot(x - px, y - py);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}
function tooltipAn(px, py) {
  const tip = $("ka-tip"), t = jetzt();
  let html = null;
  const fp = (ka.flottenPunkte || []).find(p => Math.hypot(p.x - px, p.y - py) < 15);
  const ap = (ka.angriffsPunkte || []).find(p => Math.hypot(p.x - px, p.y - py) < 14);
  if (fp) {  // K7
    const fl = fp.fl;
    html = `<b>${esc(MISSION[fl.mission] || fl.mission || "Flotte")}</b> → <span class="mono">${esc(fl.koordinaten)}</span> ${esc(fl.ziel_name || "")}
      <div class="leise">${fp.lage.rueck ? (fp.lage.gewendet ? "zurückgerufen, auf dem Heimweg" : "auf dem Rückweg") : "auf dem Hinweg"}</div>
      <div class="info-zeile"><span>Ankunft</span><span>${fl.angekommen ? uhrzeit(fl.arrive_at) : `${uhrzeit(fl.arrive_at)} (${dauer(fl.arrive_at - t)})`}</span></div>
      ${fl.return_at ? `<div class="info-zeile"><span>Rückkehr</span><span>${uhrzeit(fl.return_at)} (${dauer(fl.return_at - t)})</span></div>` : ""}
      <div class="info-zeile"><span>Schiffe</span><span>${mengenText(fl.ships)}</span></div>
      ${Object.keys(fl.units || {}).length ? `<div class="info-zeile"><span>Einheiten</span><span>${mengenText(fl.units)}</span></div>` : ""}
      ${fl.angekommen ? `<div class="info-zeile"><span>Beute</span><span>${beuteText(fl.loot)}</span></div>` : ""}`;
  } else if (ap) {
    const a = ap.a, eta = ts(a.arrive_at);
    html = `<b style="color:var(--feind)">Angriff von ${esc(a.absender || "?")}</b>
      <div class="info-zeile"><span>Ziel</span><span>${esc((flotte?.inseln || []).find(x => x.id === a.ziel_insel_id)?.name || a.ziel_koordinaten || "?")}</span></div>
      ${eta ? `<div class="info-zeile"><span>Ankunft</span><span>${uhrzeit(eta)} (${dauer(eta - t)})</span></div>` : ""}
      ${a.mission ? `<div class="info-zeile"><span>Mission</span><span>${esc(MISSION[a.mission] || a.mission)}</span></div>` : ""}`;
  } else {
    const i = inselBei(px, py);
    if (i) {
      const a = ka.allianzen.get(i.a);
      html = `<b>${esc(i.n || "Insel")}</b> <span class="mono leise">${esc(i.k)}</span>
        <div>${i.b ? esc(i.b) : (i.r ? "Ruine" : "frei")}${a ? ` · ${esc(tagText(a.tag))}` : ""} · ${zahl(i.p)} P.</div>`;
    }
  }
  if (!html) { tip.style.display = "none"; return; }
  tip.innerHTML = html; tip.style.display = "block";
  const rb = $("ka-rahmen").getBoundingClientRect();
  let x = px + 14, y = py + 14;
  if (x + tip.offsetWidth > rb.width) x = px - tip.offsetWidth - 14;
  if (y + tip.offsetHeight > rb.height) y = py - tip.offsetHeight - 14;
  tip.style.left = `${Math.max(4, x)}px`; tip.style.top = `${Math.max(4, y)}px`;
}
window.addEventListener("resize", () => { if (tab === "karte") zeichneKarte(); });
new MutationObserver(() => { farbenLesen(); zeichneKarte(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { farbenLesen(); zeichneKarte(); });
farbenLesen();
