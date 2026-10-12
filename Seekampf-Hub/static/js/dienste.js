"use strict";
// ======================================================================
// Dienste (systemd)
// ======================================================================
let dienste = null;
const DIENST_FUER_TAB = { flotte: "flotte", upgrade: "upgrade", allianz: "allianz", kolonie: "kolonie", ausbildung: "ausbildung" };
const AKTION_TEXT = { start: "Starten", stop: "Stoppen", restart: "Neu starten" };

function dienstMarke(d) {
  if (!d) return `<span class="marke grau"><span class="punkt"></span>unbekannt</span>`;
  if (!d.installiert) return `<span class="marke grau"><span class="punkt"></span>nicht installiert</span>`;
  const z = d.zustand;
  if (z === "active") return `<span class="marke gut"><span class="punkt"></span>läuft</span>`;
  if (z === "activating" || z === "reloading") return `<span class="marke warn"><span class="punkt"></span>startet</span>`;
  if (z === "failed") return `<span class="marke schlecht"><span class="punkt"></span>ausgefallen</span>`;
  if (z === "deactivating") return `<span class="marke warn"><span class="punkt"></span>stoppt</span>`;
  return `<span class="marke schlecht"><span class="punkt"></span>gestoppt</span>`;
}
function dienstKnoepfe(name, d) {
  if (!d) return "";
  return d.aktionen.map(a => {
    const aus = (a === "start" && d.zustand === "active") || (a === "stop" && d.zustand !== "active");
    return `<button class="klein" data-dienst="${name}" data-dienst-aktion="${a}" ${aus ? "disabled" : ""}>${AKTION_TEXT[a]}</button>`;
  }).join("");
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-dienst-aktion]");
  if (!b) return;
  const name = b.dataset.dienst, a = b.dataset.dienstAktion;
  const titel = dienste?.[name]?.titel || name;
  if (a === "stop" && !(await bestaetigen({ titel: `${titel} stoppen?`, text: "Der Dienst bleibt aus, bis du ihn wieder startest.", icon: ({ flotte: "kleines_kriegsschiff", upgrade: "haupthaus", allianz: "allianz_hilfe", kolonie: "koloschiff", ausbildung: "kaserne" })[name] || "wachturm", ok: "Stoppen", gefahr: true }))) return;
  await aktion(b, () => post(`/api/dienste/${name}/${a}`), `${titel}: ${AKTION_TEXT[a]} ausgeführt`);
  if (name === "hub") { melde("Seekampf-Hub startet neu – die Seite lädt gleich neu …"); setTimeout(() => location.reload(), 4000); }
  dienstHolen();
});
async function dienstHolen() {
  try { dienste = await hole("/api/dienste"); } catch (e) { return; }
  for (const name of ["flotte", "upgrade", "allianz", "kolonie", "ausbildung"]) {
    const d = dienste[name], dot = $(`nav-dot-${name}`);
    if (!d || !dot) continue;
    dot.className = "dot " + (d.zustand === "active" ? "gut" : d.zustand === "activating" ? "warn" : "schlecht");
    dot.title = d.zustand;
  }
  zeichneKopfRechts();
  if (tab === "uebersicht") zeichneUebersichtDienste();
}
function zeichneKopfRechts() {
  const name = DIENST_FUER_TAB[tab];
  const el = $("kopf-rechts");
  if (!name || !dienste) { el.innerHTML = ""; return; }
  const d = dienste[name];
  let extra = "";
  if (name === "flotte" && flotte) {
    extra = flotte.laeuft
      ? `<button class="stop" id="fl-anaus">Raiden stoppen</button>`
      : `<button class="haupt" id="fl-anaus">Raiden starten</button>`;
  }
  el.innerHTML = `${extra}<span class="dienst">${dienstMarke(d)}<span class="knoepfe">${dienstKnoepfe(name, d)}</span></span>`;
  const b = $("fl-anaus");
  if (b) b.onclick = () => aktion(b, () => post("/api/flotte/control", { aktion: flotte.laeuft ? "stop" : "start" }))
    .then(flotteHolen);
}
