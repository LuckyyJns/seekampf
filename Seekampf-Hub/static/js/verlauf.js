"use strict";
// ======================================================================
// Upgrade-Bot: Verlauf je Insel (Linien, eine Farbe je Insel)
// ======================================================================
let verlaufUp = null, vlKennzahl = "produktion", vlTabelle = false, vlGeholt = 0;
const VL_KENNZAHLEN = {
  produktion: { titel: "Produktion pro Stunde (Gold + Stein + Holz)", wert: t => t.produktion ? t.produktion.gold + t.produktion.stein + t.produktion.holz : null },
  stufen: { titel: "Summe aller Ausbaustufen", wert: t => t.stufen_summe ?? null },
  punkte: { titel: "Inselpunkte", wert: t => t.punkte ?? null },
  verlust: { titel: "Durch volles Lager verlorene Rohstoffe je Tag", wert: t => t.verlust ? t.verlust.gold + t.verlust.stein + t.verlust.holz : null },
};
const serienFarbe = (i) => `var(--serie-${(i % 8) + 1})`;

function linienDiagramm(el, daten, serien) {
  const breite = Math.max(280, el.clientWidth || 600), hoehe = 240;
  const links = 52, rechts = 16, oben = 12, unten = 24;
  const innenB = breite - links - rechts, innenH = hoehe - oben - unten;
  const n = daten.length;
  const alle = serien.flatMap(s => s.werte).filter(v => v != null);
  const max = niceMax(Math.max(1, ...alle));
  const x = (i) => links + (n <= 1 ? innenB / 2 : innenB * i / (n - 1));
  const y = (v) => oben + innenH - (v / max) * innenH;
  let svg = `<svg viewBox="0 0 ${breite} ${hoehe}" height="${hoehe}" role="img" aria-label="Verlauf"><g class="achse">`;
  for (let k = 0; k <= 4; k++) {
    const v = max * k / 4;
    svg += `<line class="gitter" x1="${links}" x2="${links + innenB}" y1="${y(v)}" y2="${y(v)}"/><text x="${links - 6}" y="${y(v) + 4}" text-anchor="end">${kompakt(v)}</text>`;
  }
  const jede = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innenB / 64))));
  daten.forEach((d, i) => {
    if ((n - 1 - i) % jede) return;
    const [, m, t] = d.split("-");
    svg += `<text x="${x(i)}" y="${hoehe - 6}" text-anchor="middle">${t}.${m}.</text>`;
  });
  svg += `</g>`;
  serien.forEach((s) => {
    // Luecken (Tage ohne Daten) unterbrechen die Linie statt sie zu verbinden
    let pfad = "", stift = false;
    s.werte.forEach((v, i) => {
      if (v == null) { stift = false; return; }
      pfad += `${stift ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      stift = true;
    });
    if (pfad) svg += `<path d="${pfad}" fill="none" stroke="${s.farbe}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;
    s.werte.forEach((v, i) => {   // einzelne Punkte ohne Nachbarn sichtbar machen
      const allein = v != null && s.werte[i - 1] == null && s.werte[i + 1] == null;
      if (allein) svg += `<rect x="${x(i) - 4}" y="${y(v) - 4}" width="8" height="8" fill="${s.farbe}" stroke="var(--ink)" stroke-width="2"/>`;
    });
  });
  svg += `<line class="vl-faden" x1="0" x2="0" y1="${oben}" y2="${oben + innenH}" stroke="var(--ink)" stroke-width="2" stroke-dasharray="3 3" opacity="0"/>`;
  svg += `<g class="vl-punkte"></g><rect class="vl-treffer" x="${links}" y="${oben}" width="${innenB}" height="${innenH}" fill="transparent" tabindex="0"/></svg>`;

  el.innerHTML = `<div class="linien-legende"></div><div class="diagramm">${svg}<div class="tooltip"></div></div>`;
  const legende = el.querySelector(".linien-legende");
  serien.forEach(s => {
    const span = document.createElement("span"), key = document.createElement("i");
    key.style.background = s.farbe;
    span.append(key, document.createTextNode(s.name));
    legende.appendChild(span);
  });
  const box = el.querySelector(".diagramm"), tip = el.querySelector(".tooltip");
  const faden = el.querySelector(".vl-faden"), punkte = el.querySelector(".vl-punkte"), treffer = el.querySelector(".vl-treffer");
  const zeige = (i) => {
    faden.setAttribute("x1", x(i)); faden.setAttribute("x2", x(i)); faden.setAttribute("opacity", ".6");
    punkte.innerHTML = serien.map(s => s.werte[i] == null ? "" :
      `<rect x="${x(i) - 5}" y="${y(s.werte[i]) - 5}" width="10" height="10" fill="${s.farbe}" stroke="var(--ink)" stroke-width="2"/>`).join("");
    const [j, m, t] = daten[i].split("-");
    tip.replaceChildren();
    const kopf = document.createElement("div");
    kopf.style.marginBottom = "4px"; kopf.innerHTML = `<b>${t}.${m}.${j}</b>`;
    tip.appendChild(kopf);
    serien.forEach(s => {
      const zeile = document.createElement("div"); zeile.className = "zeile";
      const name = document.createElement("span"), linie = document.createElement("i"), wert = document.createElement("b");
      linie.className = "linie"; linie.style.background = s.farbe;
      name.append(linie, document.createTextNode(s.name));
      wert.textContent = s.werte[i] == null ? "–" : zahl(s.werte[i]);
      zeile.append(name, wert); tip.appendChild(zeile);
    });
    tip.style.display = "block";
    const bb = box.getBoundingClientRect(), sx = bb.width / breite;
    let left = x(i) * sx + 12;
    if (left + tip.offsetWidth > bb.width) left = x(i) * sx - tip.offsetWidth - 12;
    tip.style.left = `${Math.max(0, left)}px`; tip.style.top = "10px";
  };
  const verstecke = () => { tip.style.display = "none"; faden.setAttribute("opacity", "0"); punkte.innerHTML = ""; };
  let aktuell = n - 1;
  treffer.addEventListener("pointermove", (e) => {
    const bb = treffer.getBoundingClientRect();
    aktuell = Math.round((e.clientX - bb.left) / bb.width * (n - 1));
    zeige(Math.max(0, Math.min(n - 1, aktuell)));
  });
  treffer.addEventListener("pointerleave", verstecke);
  treffer.addEventListener("focus", () => zeige(aktuell));
  treffer.addEventListener("blur", verstecke);
  treffer.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") aktuell = Math.max(0, aktuell - 1);
    else if (e.key === "ArrowRight") aktuell = Math.min(n - 1, aktuell + 1);
    else return;
    e.preventDefault(); zeige(aktuell);
  });
}
function linienTabelle(daten, serien) {
  const kopf = serien.map(s => `<th class="zahl">${esc(s.name)}</th>`).join("");
  const zeilen = daten.map((d, i) => [d, i]).reverse().map(([d, i]) => {
    const [j, m, t] = d.split("-");
    return `<tr><td>${t}.${m}.${j}</td>${serien.map(s => `<td class="zahl">${s.werte[i] == null ? "–" : zahl(s.werte[i])}</td>`).join("")}</tr>`;
  }).join("");
  return `<div class="tabelle-rahmen" style="max-height:320px;overflow:auto"><table><thead><tr><th>Tag</th>${kopf}</tr></thead><tbody>${zeilen}</tbody></table></div>`;
}
function zeichneVerlaufUp() {
  const el = $("vl-diagramm");
  if (!verlaufUp || !el) return;
  const k = VL_KENNZAHLEN[vlKennzahl];
  // Erst ab dem ersten Tag mit Daten zeigen
  const erster = verlaufUp.daten.findIndex((d, i) => verlaufUp.inseln.some(x => verlaufUp.reihen[x.id][i]));
  const ab = Math.max(0, Math.min(erster, verlaufUp.daten.length - 2));
  const daten = verlaufUp.daten.slice(ab);
  const serien = verlaufUp.inseln.map((insel, idx) => ({
    name: insel.name, farbe: serienFarbe(idx),
    werte: verlaufUp.reihen[insel.id].slice(ab).map(t => t ? k.wert(t) : null),
  })).filter(s => s.werte.some(v => v != null));
  $("vl-tabelle").textContent = vlTabelle ? "Diagramm" : "Tabelle";
  if (!serien.length) { el.innerHTML = `<div class="leise klein-text">noch keine Daten</div>`; }
  else if (vlTabelle) el.innerHTML = linienTabelle(daten, serien);
  else linienDiagramm(el, daten, serien);
  const hinweise = {
    produktion: "Stand am Ende des Tages, alle drei Rohstoffe zusammen.",
    stufen: "Summe der Stufen aller Gebäude. Vor dem 01.10. aus den Logs zurückgerechnet.",
    punkte: "Inselpunkte werden erst seit dem 01.10. aufgezeichnet.",
    verlust: "Produktion, die verpufft ist, weil das Lager voll war. Läuft ein Lager regelmäßig über (≥ 2 h in 24 h), zieht der Upgrade-Bot das Lagerhaus vor.",
  };
  $("vl-hinweis").textContent = `${k.titel}. ${hinweise[vlKennzahl]}`;
}
async function verlaufUpHolen() {
  if (Date.now() - vlGeholt < 60000 && verlaufUp) return zeichneVerlaufUp();
  try { verlaufUp = await hole("/api/upgrade/verlauf?tage=60"); vlGeholt = Date.now(); } catch (e) { return; }
  zeichneVerlaufUp();
}
$("vl-kennzahl").addEventListener("click", (e) => {
  const b = e.target.closest("[data-kennzahl]");
  if (!b) return;
  vlKennzahl = b.dataset.kennzahl;
  $("vl-kennzahl").querySelectorAll("button").forEach(x => x.classList.toggle("an", x === b));
  zeichneVerlaufUp();
});
$("vl-tabelle").onclick = () => { vlTabelle = !vlTabelle; zeichneVerlaufUp(); };
window.addEventListener("resize", () => { if (tab === "upgrade" && !vlTabelle) zeichneVerlaufUp(); });

function aktualisieren() {
  if (tab === "karte") { farbenLesen(); flotteHolen(); kolonieHolen(); spielFlottenHolen(); angriffeHolen(); karteHolen(); zeichneKarte(); }
  if (tab === "flotte") { flotteHolen(); if (!verlauf) verlaufHolen(); else { diagramm("fl-diagramm", verlauf.gesamt); } }
  if (tab === "upgrade") { upgradeHolen(); verlaufUpHolen(); }
  if (tab === "allianz") allianzHolen();
  if (tab === "ausgleich") ausgleichHolen();
  if (tab === "bilanz") bilanzHolen();
  if (tab === "spieler") spielerHolen();
  if (tab === "gesundheit") gesundheitHolen();
  if (tab === "kolonie") kolonieHolen();
  if (tab === "ausbildung") ausbildungHolen();
  if (tab === "uebersicht") {
    flotteHolen(); upgradeHolen(); allianzHolen(); ausbildungHolen();
    if (!verlauf) verlaufHolen(); else diagramm("ue-diagramm", verlauf.gesamt);
    zeichneUebersichtDienste();
  }
}
function sekundentakt() {
  const t = jetzt();
  document.querySelectorAll("[data-bis]").forEach(el => {
    const rest = Number(el.dataset.bis) - t;
    el.textContent = rest > 0 ? dauer(rest) : el.dataset.fertig;
    el.classList.toggle("bald", rest > 0 && rest <= 60);
  });
  $("uhr").textContent = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

dienstHolen();
