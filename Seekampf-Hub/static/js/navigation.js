"use strict";
// ======================================================================
// Navigation: Hamburger + Tabs ueber den Hash
// ======================================================================
const TABS = { uebersicht: "Übersicht", karte: "Karte", gesundheit: "Gesundheit", flotte: "Flotten-Manager", ausgleich: "Rohstoff-Ausgleich", bilanz: "Rohstoff-Bilanz", spieler: "Spieler", upgrade: "Upgrade-Bot", allianz: "Allianz-Bot", kolonie: "Kolonisations-Bot", ausbildung: "Ausbildungs-Bot" };
const UNTERTITEL = {
  uebersicht: "Alle Bots und Inseln auf einen Blick",
  karte: "Die ganze Welt – Spieler, Allianzen und deine Flotten",
  flotte: "Raids auf freie Inseln – je Insel steuerbar",
  ausgleich: "Überschuss der Hauptinsel an die Ausbauinseln liefern",
  gesundheit: "Läuft alles – und tut es auch etwas?",
  bilanz: "Woher die Rohstoffe jeder Insel kommen und wohin sie gehen",
  spieler: "Andere Spieler Tag für Tag – Stand jeden Morgen um 1:00 Uhr",
  upgrade: "Bau-Warteschlangen, Priorisierung und Ausbau von Hand",
  allianz: "Allianz-Protokoll: Notrufe, Beistand, Rohstoff-Anfragen",
  kolonie: "Freie Inseln aus deiner Warteschlange kolonisieren",
  ausbildung: "Truppen und Schiffe je Insel auf Mindestbestand halten",
};
let tab = null;

function istSchmal() { return window.matchMedia("(max-width: 900px)").matches; }
$("hamburger").onclick = () => {
  if (istSchmal()) document.body.classList.toggle("nav-offen");
  else {
    document.body.classList.toggle("nav-zu");
    try { localStorage.setItem("nav-zu", document.body.classList.contains("nav-zu") ? "1" : ""); } catch (e) { /* egal */ }
  }
};
$("schleier").onclick = () => document.body.classList.remove("nav-offen");
try { if (localStorage.getItem("nav-zu")) document.body.classList.add("nav-zu"); } catch (e) { /* egal */ }

function zeigeTab(ziel) {
  // Direktlink auf eine Insel: #flotte/447 oder #upgrade/447 klappt sie auf.
  let [neu, insel] = String(ziel || "").split("/");
  if (!TABS[neu]) neu = "uebersicht";
  if (insel && offeneInseln[neu]) offeneInseln[neu].add(insel);
  if (tab && neu !== tab) {
    const offen = ungespeichertIn(tab);
    if (offen.length) {
      bestaetigen({ titel: "Ungespeicherte Änderungen", text: "In diesem Bereich gibt es Änderungen, die noch nicht gespeichert sind. Trotzdem wechseln? Sie gehen verloren.", icon: TAB_ICON_NAME[tab], ok: "Trotzdem wechseln", abbrechen: "Hierbleiben", gefahr: true })
        .then((wechseln) => {
          if (!wechseln) { history.replaceState(null, "", `#${tab}`); return; }
          // Verworfen: Formulare beim naechsten Besuch frisch aus dem Stand bauen.
          offen.forEach(f => { dirtySetzen(f, false); formularVerwerfen(f); });
          zeigeTabJetzt(neu);
        });
      return;
    }
  }
  zeigeTabJetzt(neu);
}
const TAB_ICON_NAME = { uebersicht: "eigene_insel", karte: "hinterland", gesundheit: "gesundheit", upgrade: "haupthaus", flotte: "kleines_kriegsschiff", ausbildung: "kaserne", ausgleich: "rohstoffausgleich", spieler: "invasions_verband", kolonie: "koloschiff", allianz: "allianz_hilfe" };
/** Koordinate als Link: Klick oeffnet die Karte mit dieser Insel */
function koordLink(k) {
  if (!k) return `<span class="mono">–</span>`;
  return `<a class="koord-link mono" href="#karte" data-zur-karte="${esc(k)}" title="Auf der Karte zeigen">${esc(k)}</a>`;
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-zur-karte]");
  if (!a) return;
  e.preventDefault(); e.stopPropagation();
  ka.zielKoord = a.dataset.zurKarte;
  zeigeTab("karte");
  if (ka.nachKoord.size) karteZielAnspringen();
}, true);
function zeigeTabJetzt(neu) {
  tab = neu;
  document.querySelectorAll(".tab").forEach(el => el.classList.toggle("sichtbar", el.id === `tab-${tab}`));
  document.querySelectorAll("nav#seite a").forEach(a => a.classList.toggle("aktiv", a.dataset.tab === tab));
  $("titel").textContent = TABS[tab];
  const navIcon = document.querySelector(`nav#seite a[data-tab="${tab}"] img`);
  if (navIcon) $("titel-icon").firstElementChild.src = navIcon.getAttribute("src");
  $("untertitel").textContent = UNTERTITEL[tab];
  document.title = `${TABS[tab]} – Seekampf-Hub`;
  document.body.classList.remove("nav-offen");
  zeichneKopfRechts();
  aktualisieren();
  logsHolen();
}
window.addEventListener("hashchange", () => {
  const [neu] = location.hash.slice(1).split("/");
  if (neu !== tab || location.hash.includes("/")) zeigeTab(location.hash.slice(1));
});

// Thema
/** Bewegung an/aus (gemerkt im Browser; Standard: an, ausser das System wuenscht weniger Bewegung) */
function bewegungSetzen(an) {
  document.body.classList.toggle("ohne-anim", !an);
  $("anim").textContent = an ? "Bewegung: an" : "Bewegung: aus";
}
let bewegungAn = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
try { const m = localStorage.getItem("bewegung"); if (m) bewegungAn = m === "an"; } catch (e) { /* egal */ }
bewegungSetzen(bewegungAn);
$("anim").onclick = () => {
  bewegungAn = !bewegungAn;
  bewegungSetzen(bewegungAn);
  try { localStorage.setItem("bewegung", bewegungAn ? "an" : "aus"); } catch (e) { /* egal */ }
};
/** 8-Bit-Toene (Rechteckwelle), standardmaessig aus */
let tonAn = false, audio = null;
try { tonAn = localStorage.getItem("ton") === "an"; } catch (e) { /* egal */ }
// Leises "Tick": kurzes, gefiltertes Rauschen, das schnell abklingt (kein Piepen). f = Tonhoehe des Klicks, v = Lautstaerke.
const TON_LAUTSTAERKE = 1;
// f = Tonhoehe je Klick (Liste = mehrere Klicks hintereinander), v = Lautstaerke, abstand = Sekunden zwischen den Klicks, laenge = Sekunden je Klick
const TON_ARTEN = {
  klick: { f: 1900, v: .22 },                                               // normaler Knopf
  tab: { f: 1350, v: .24 },                                                 // Tab wechseln
  oeffnen: { f: 1600, v: .2 },                                              // Rueckfrage geht auf
  an: { f: 2500, v: .22 }, aus: { f: 1150, v: .22 },                        // Schalter / Haken
  ok: { f: 2300, v: .2 }, fehler: { f: 750, v: .28 },                       // Meldungen
  alarm: { f: 950, v: .3, mal: 2, abstand: .17 },
  bauen: { f: [650, 1500], v: [.34, .22], abstand: .075, laenge: .04 },     // Ausbau eingereiht: "tock-tick" wie ein Hammer
  ausbilden: { f: [1000, 1250, 1700], v: [.24, .24, .26], abstand: .062 },  // Truppen/Schiffe: drei aufsteigende Klopfer
  senden: { f: [1500, 2300, 3200], v: [.18, .18, .18], abstand: .045, laenge: .02 }, // Lieferung/Anfrage: schneller Wisch nach oben
  speichern: { f: [2700, 3300], v: [.17, .17], abstand: .05, laenge: .02 }, // Speichern: heller Doppelklick
  dienst: { f: [520, 520], v: [.34, .3], abstand: .11, laenge: .045 },      // Dienst starten/stoppen: dumpfer Doppelschlag
};
/** Klicks in den Audio-Kontext legen (auch fuer die Offline-Messung nutzbar). */
function tonErzeugen(ctx, art, t0 = 0) {
  const a = TON_ARTEN[art];
  if (!a) return;
  const fs = [].concat(a.f), vs = [].concat(a.v), mal = fs.length > 1 ? fs.length : (a.mal || 1);
  const laenge = Math.round(ctx.sampleRate * (a.laenge || .028));
  const puffer = ctx.createBuffer(1, laenge, ctx.sampleRate), daten = puffer.getChannelData(0);
  for (let i = 0; i < laenge; i++) daten[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / laenge, 4);   // schnell abklingend
  for (let k = 0; k < mal; k++) {
    const quelle = ctx.createBufferSource(), filter = ctx.createBiquadFilter(), pegel = ctx.createGain();
    quelle.buffer = puffer; filter.type = "bandpass"; filter.frequency.value = fs[k % fs.length]; filter.Q.value = .9;
    pegel.gain.value = vs[k % vs.length] * TON_LAUTSTAERKE;
    quelle.connect(filter); filter.connect(pegel); pegel.connect(ctx.destination);
    quelle.start(t0 + k * (a.abstand || 0));
  }
}
/** Welcher Ton gehoert zu einer erfolgreichen schreibenden Anfrage? null = keiner (Schalter und Knopf klicken schon selbst). */
function tonFuerAnfrage(pfad, optionen) {
  let koerper = {};
  try { koerper = JSON.parse(optionen?.body || "{}"); } catch (e) { /* egal */ }
  if (/\/api\/upgrade\/befehl/.test(pfad)) return koerper.typ === "auftrag" ? "bauen" : null;
  if (/\/ausbilden$/.test(pfad)) return "ausbilden";
  if (/\/ausgleich\/liefern/.test(pfad)) return "senden";
  if (/\/api\/allianz\/befehl/.test(pfad)) return koerper.typ === "anfrage" ? "senden" : null;
  if (/\/api\/dienste\//.test(pfad)) return "dienst";
  if (/\/aktiv$|\/control$|\/faehigkeiten$|\/stats\/reset|\/blacklist\/clear/.test(pfad)) return null;
  if (/\/api\/kolonie\/warteschlange$/.test(pfad)) return "bauen";
  return "speichern";
}
let letzterAktionsTon = 0;
function ton(art) {
  if (!tonAn) return;
  if (!["klick", "tab", "oeffnen", "an", "aus", "ok", "fehler", "alarm"].includes(art)) letzterAktionsTon = Date.now();
  try {
    audio ||= new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
    tonErzeugen(audio, art, audio.currentTime);
  } catch (e) { /* egal */ }
}
// Klick-Ton fuer alles, was man anklicken kann (Schalter und Haken haben eigene Toene beim Umlegen)
const KLICKBAR = "a, button, select, summary, [data-insel], .insel-kopf, .gkachel, .legende-liste button, .zl-balken, .ebenen label";
document.addEventListener("click", (e) => {
  if (!tonAn) return;
  const el = e.target.closest?.(KLICKBAR);
  if (!el || el.disabled || el.closest("[disabled]")) return;
  if (el.matches("label") && el.querySelector("input[type=checkbox]")) return;
  ton(el.matches("nav#seite a") ? "tab" : "klick");
}, true);
document.addEventListener("change", (e) => {
  if (tonAn && e.target.matches?.("input[type=checkbox]")) ton(e.target.checked ? "an" : "aus");
}, true);
function tonSetzen(an) { tonAn = an; $("ton").textContent = an ? "Ton: an" : "Ton: aus"; }
tonSetzen(tonAn);
$("ton").onclick = () => {
  tonSetzen(!tonAn);
  try { localStorage.setItem("ton", tonAn ? "an" : "aus"); } catch (e) { /* egal */ }
  ton("ok");
};
$("thema").onclick = () => {
  const dunkel = getComputedStyle(document.documentElement).colorScheme.includes("dark");
  const neu = dunkel ? "light" : "dark";
  document.documentElement.dataset.theme = neu;
  try { localStorage.setItem("thema", neu); } catch (e) { /* egal */ }
  neuZeichnenDiagramme();
};
try { const t = localStorage.getItem("thema"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* egal */ }
