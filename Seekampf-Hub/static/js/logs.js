"use strict";
// ======================================================================
// Logs
// ======================================================================
const LOG_ICON_BOT = { flotte: "kleines_kriegsschiff", upgrade: "haupthaus", allianz: "allianz_hilfe", kolonie: "koloschiff", ausbildung: "kaserne" };
const LOG_THEMEN = [[/BEUTE/, "gold"], [/AUSGLEICH|Lieferung/i, "rohstoffausgleich"], [/Kolonis|Koloschiff/i, "koloschiff"],
  [/Ausbildung|ausgebildet|Verlegung|verlegt/i, "kaserne"], [/Allianz|Anfrage|Notruf|Beistand|Forum|Leihe/i, "allianz_hilfe"],
  [/Ausbau|gestartet|Bauauftrag/i, "haupthaus"], [/Flotte|ANKUNFT|Raid|ABFAHRT/i, "kleines_kriegsschiff"]];
let logModus = "ereignisse";
try { logModus = localStorage.getItem("logmodus") || "ereignisse"; } catch (e) { /* egal */ }
const logRoh = {}, logGesehen = {};
/** Logzeilen "2026-10-05 15:02:46 [INFO] Text" zerlegen; Zeilen ohne Zeitstempel (z. B. Fehlerdetails) gehoeren zur vorigen. */
function logParsen(zeilen) {
  const liste = [];
  for (const z of zeilen) {
    const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d:\d\d:\d\d) \[(\w+)\] (.*)$/.exec(z);
    if (m) liste.push({ datum: `${m[3]}.${m[2]}.${m[1]}`, zeit: m[4], stufe: m[5].toUpperCase(), text: m[6], roh: z });
    else if (liste.length) { liste[liste.length - 1].text += "\n" + z; liste[liste.length - 1].roh += "\n" + z; }
    else liste.push({ datum: "", zeit: "", stufe: "INFO", text: z, roh: z });
  }
  return liste;
}
function logZeichnen(bot) {
  const el = document.querySelector(`[data-log="${bot}"]`), d = logRoh[bot];
  if (!el || !d) return;
  const amEnde = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  document.querySelectorAll("[data-logmodus]").forEach(b => b.classList.toggle("an", b.dataset.logmodus === logModus));
  el.classList.toggle("roh", logModus === "roh");
  if (logModus === "roh") {
    el.textContent = (d.zeilen || []).join("\n") || "(noch nichts geloggt)";
  } else {
    let liste = logParsen(d.zeilen || []);
    liste.forEach(e => { e.routine = e.stufe === "INFO" && /^Insel \d+ \| /.test(e.text); });
    const gesehen = logGesehen[bot];                      // was schon einmal da war, leuchtet nicht
    liste.forEach(e => { e.neu = !!gesehen && !gesehen.has(e.roh); });
    logGesehen[bot] = new Set(liste.map(e => e.roh));
    if (logModus === "ereignisse") liste = liste.filter(e => !e.routine);
    else if (logModus === "probleme") liste = liste.filter(e => ["ERROR", "WARNING", "CRITICAL"].includes(e.stufe));
    let tag = "";
    const zeilen = liste.map(e => {
      const kopf = e.datum && e.datum !== tag ? `<div class="lz-tag">${e.datum}</div>` : "";
      tag = e.datum || tag;
      const thema = (LOG_THEMEN.find(([re]) => re.test(e.text)) || [])[1] || LOG_ICON_BOT[bot];
      return `${kopf}<div class="lz ${e.stufe} ${e.routine ? "routine" : ""} ${e.neu ? "neu" : ""}"><time title="${esc(e.datum)}">${esc(e.zeit)}</time>${IK(thema, 22)}<span class="lz-text">${esc(e.text)}</span></div>`;
    });
    el.innerHTML = zeilen.join("") || `<div class="lz-leer">${(d.zeilen || []).length ? "Nichts dabei – oben auf „Alles“ umschalten." : "noch nichts geloggt"}</div>`;
  }
  if (amEnde || !el.dataset.gescrollt) { el.scrollTop = el.scrollHeight; el.dataset.gescrollt = "1"; }
}
function logUiBauen() {
  document.querySelectorAll("[data-log]").forEach(el => {
    const rechts = el.closest("section")?.querySelector(".kopfzeile .rechts");
    if (!rechts) return;
    const seg = document.createElement("div");
    seg.className = "segment"; seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Log anzeigen");
    seg.innerHTML = [["ereignisse", "Ereignisse"], ["alles", "Alles"], ["probleme", "Probleme"], ["roh", "Rohtext"]]
      .map(([k, t]) => `<button type="button" data-logmodus="${k}">${t}</button>`).join("");
    seg.addEventListener("click", (e) => {
      const b = e.target.closest("[data-logmodus]");
      if (!b) return;
      logModus = b.dataset.logmodus;
      try { localStorage.setItem("logmodus", logModus); } catch (err) { /* egal */ }
      logZeichnen(DIENST_FUER_TAB[tab]);
    });
    rechts.prepend(seg);
  });
}
logUiBauen();
async function logsHolen() {
  const bot = DIENST_FUER_TAB[tab];
  if (!bot) return;
  const el = document.querySelector(`[data-log="${bot}"]`);
  try {
    logRoh[bot] = await hole(`/api/log/${bot}?zeilen=300`);
    document.querySelector(`[data-log-datei="${bot}"]`).textContent = logRoh[bot].datei || "";
    logZeichnen(bot);
  } catch (e) { el.textContent = `Log nicht lesbar: ${e.message}`; }
}
