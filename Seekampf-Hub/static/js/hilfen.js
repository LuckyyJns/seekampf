"use strict";
// ======================================================================
// Hilfen
// ======================================================================
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let zeitVersatz = 0;               // Serverzeit - Browserzeit
const jetzt = () => Date.now() / 1000 + zeitVersatz;

function zahl(n, stellen = 0) {
  return Number(n ?? 0).toLocaleString("de-DE", { maximumFractionDigits: stellen });
}
function kompakt(n) {
  n = Number(n || 0);
  if (Math.abs(n) >= 1e6) return (n / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " Mio";
  if (Math.abs(n) >= 1e4) return (n / 1e3).toLocaleString("de-DE", { maximumFractionDigits: 0 }) + "k";
  return zahl(n);
}
function dauer(sek) {
  if (sek === null || sek === undefined || isNaN(sek)) return "–";
  let s = Math.max(0, Math.round(sek));
  const t = Math.floor(s / 86400); s -= t * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  if (t) return `${t}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}:${String(s).padStart(2, "0")}`;
  return `${s}s`;
}
function laufzeit(sek) {
  const s = Math.max(0, Math.round(sek || 0));
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}
/** Zeitpunkt aus Epoch-Sekunden oder ISO-Text in Epoch-Sekunden. */
function ts(wert) {
  if (wert === null || wert === undefined || wert === "") return null;
  if (typeof wert === "number") return wert;
  const t = Date.parse(wert);
  return isNaN(t) ? null : t / 1000;
}
function uhrzeit(wert, mitTag = false) {
  const t = ts(wert);
  if (!t) return "–";
  const d = new Date(t * 1000);
  const heute = new Date().toDateString() === d.toDateString();
  const zeit = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  return (mitTag || !heute) ? `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })} ${zeit}` : zeit;
}
function vor(wert) {
  const t = ts(wert);
  return t ? `vor ${dauer(jetzt() - t)}` : "–";
}
/** Countdown-Span: der Sekundentakt unten zaehlt alle [data-bis] herunter. */
function countdown(bis, fertig = "fertig") {
  const t = ts(bis);
  if (!t) return "–";
  return `<span data-bis="${t}" data-fertig="${esc(fertig)}">${dauer(t - jetzt())}</span>`;
}
const GEBAEUDE_NAMEN = {
  haupthaus: "Haupthaus", goldmine: "Goldmine", steingrube: "Steingrube", saegewerk: "Sägewerk",
  lagerhaus: "Lagerhaus", hafen: "Hafen", kaserne: "Kaserne", steinmauer: "Steinmauer", wachturm: "Wachturm",
  labor: "Labor", marktplatz: "Marktplatz",
};
const gname = (t) => GEBAEUDE_NAMEN[t] || t;
const SCHIFF_NAMEN = {
  kleines_kriegsschiff: "kl. Kriegsschiff", grosses_kriegsschiff: "gr. Kriegsschiff",
  kleines_handelsschiff: "kl. Handelsschiff", grosses_handelsschiff: "gr. Handelsschiff",
  spaehschiff: "Spähschiff", kolonisationsschiff: "Kolonisationsschiff",
  steinewerfer: "Steinewerfer", speerkaempfer: "Speerkämpfer", bogenschuetze: "Bogenschütze",
};
const sname = (t) => SCHIFF_NAMEN[t] || t;
const ICONS_VORHANDEN = new Set(["invasions_verband","haupthaus","goldmine","steingrube","saegewerk","lagerhaus","steinmauer","wachturm","kaserne","hafen","gold","stein","holz","steinewerfer","speerkaempfer","bogenschuetze","kleines_kriegsschiff","grosses_kriegsschiff","kleines_handelsschiff","grosses_handelsschiff","spaehschiff","koloschiff","handelsroute","allianz_hilfe","eigene_insel","hinterland"]);
const IK = (n, s = 22) => `<img class="ict" src="/static/icons/${n}.png" width="${s}" height="${s}" alt="" aria-hidden="true">`;
const gico = (t, s = 24) => ICONS_VORHANDEN.has(t) ? IK(t, s) : "";
const ROHSTOFF_ICON = (k) => IK(k, 24);
const ROHSTOFFE = [["gold", "Gold"], ["stein", "Stein"], ["holz", "Holz"]];
/** Rückfrage im Pixel-Stil (statt des grauen Browser-Fensters). Liefert true/false. */
function bestaetigen({ titel = "Bist du sicher?", text = "", html = "", icon = null, ok = "OK", abbrechen = "Abbrechen", gefahr = false } = {}) {
  return new Promise((fertig) => {
    document.getElementById("bestaetigung")?.remove();
    ton("oeffnen");
    const d = document.createElement("div");
    d.id = "bestaetigung"; d.className = "dialog-schleier";
    d.innerHTML = `<div class="dialog dialog-klein" role="alertdialog" aria-modal="true" aria-label="${esc(titel)}">
      <div class="dialog-kopf">${icon ? IK(icon, 64) : ""}<div><h3>${esc(titel)}</h3>${text ? `<div class="dialog-text">${esc(text)}</div>` : ""}</div></div>
      ${html}
      <div class="dialog-leiste"><button type="button" data-n>${esc(abbrechen)}</button><button type="button" class="${gefahr ? "stop" : "haupt"}" data-j>${esc(ok)}</button></div></div>`;
    document.body.appendChild(d);
    const taste = (e) => { if (e.key === "Escape") zu(false); };
    const zu = (wert) => { d.remove(); document.removeEventListener("keydown", taste); fertig(wert); };
    d.addEventListener("click", (e) => {
      if (e.target === d || e.target.closest("[data-n]")) zu(false);
      else if (e.target.closest("[data-j]")) zu(true);
    });
    document.addEventListener("keydown", taste);
    d.querySelector("[data-j]").focus();
  });
}
const mengenHtml = (m) => `<div class="dialog-summe"><div class="gk-kosten">${ROHSTOFFE.filter(([k]) => m[k] > 0).map(([k]) => `<span>${ROHSTOFF_ICON(k)}${zahl(m[k])}</span>`).join("")}</div></div>`;

function beuteText(b) {
  if (!b) return "–";
  const teile = ROHSTOFFE.filter(([k]) => (b[k] ?? 0) > 0).map(([k, n]) => `${zahl(b[k])} ${n}`);
  return teile.length ? teile.join(" · ") : "nichts";
}
function mengenText(m) {
  if (!m || !Object.keys(m).length) return "–";
  return Object.entries(m).map(([k, v]) => `${zahl(v)} ${sname(k)}`).join(", ");
}

async function hole(pfad, optionen) {
  const antwort = await fetch(pfad, optionen);
  if (!antwort.ok) {
    let text = await antwort.text();
    try { text = JSON.parse(text).detail || text; } catch (e) { /* Rohtext */ }
    throw new Error(text);
  }
  if (optionen?.method && optionen.method !== "GET") { const t = tonFuerAnfrage(pfad, optionen); if (t) ton(t); }
  return antwort.json();
}
const sende = (methode) => (pfad, koerper) => hole(pfad, {
  method: methode, headers: { "Content-Type": "application/json", "X-Seekampf-Hub": "1" }, body: JSON.stringify(koerper ?? {}),
});
const post = sende("POST"), put = sende("PUT");

function melde(text, art = "") {
  if (art === "schlecht" || Date.now() - letzterAktionsTon > 900) ton(art === "gut" ? "ok" : art === "schlecht" ? "fehler" : "klick");
  const el = document.createElement("div");
  el.className = `meldung ${art}`;
  el.textContent = text;
  $("meldungen").appendChild(el);
  setTimeout(() => el.remove(), art === "schlecht" ? 9000 : 5000);
}
/** Knopf waehrend einer Aktion sperren, Fehler als Meldung zeigen. */
async function aktion(knopf, fn, erfolg) {
  if (knopf) knopf.disabled = true;
  try {
    const r = await fn();
    const text = typeof erfolg === "function" ? erfolg(r) : erfolg;
    if (text) melde(text, "gut");
    return r;
  } catch (e) {
    melde(e.message, "schlecht");
  } finally {
    if (knopf) knopf.disabled = false;
  }
}
