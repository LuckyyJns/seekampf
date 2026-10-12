"use strict";
// ======================================================================
// Diagramm: gestapelte Saeulen je Tag (Gold/Stein/Holz)
// ======================================================================
const diagramme = {};   // Container-ID -> Reihe (fuer Neuzeichnen bei Groesse/Thema)
const tabellenAn = new Set();

function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  for (const f of [1, 2, 2.5, 5, 10]) if (f * e >= v) return f * e;
  return 10 * e;
}
function diagramm(id, reihe) {
  const el = $(id);
  if (!el) return;
  diagramme[id] = reihe;
  const erster = reihe.findIndex(d => d.gold + d.stein + d.holz + d.raids > 0);
  if (erster > 0) reihe = reihe.slice(Math.min(erster, Math.max(0, reihe.length - 7)));
  if (tabellenAn.has(id)) { el.innerHTML = diagrammTabelle(reihe); return; }
  const breite = Math.max(280, el.clientWidth || 600);
  const hoehe = 210, links = 46, rechts = 50, oben = 10, unten = 24;
  const innenB = breite - links - rechts, innenH = hoehe - oben - unten;
  const n = reihe.length;
  const summe = (d) => d.gold + d.stein + d.holz;
  const max = niceMax(Math.max(...reihe.map(summe), 1));
  const schritt = innenB / n;
  const saeule = Math.round(Math.min(24, Math.max(4, schritt * 0.62)) / 2) * 2;
  const y = (v) => oben + innenH - (v / max) * innenH;
  const farben = { gold: "var(--gold)", stein: "var(--stein)", holz: "var(--holz)" };
  const flaeche = "var(--flaeche)";

  let svg = `<svg viewBox="0 0 ${breite} ${hoehe}" height="${hoehe}" role="img" aria-label="Beute je Tag">`;
  // Gitter + y-Achse
  svg += `<g class="achse">`;
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4, yy = y(v);
    svg += `<line class="gitter" x1="${links}" x2="${links + innenB}" y1="${yy}" y2="${yy}"/>`;
    svg += `<text x="${links - 6}" y="${yy + 4}" text-anchor="end">${kompakt(v)}</text>`;
  }
  // x-Beschriftung: jeder ~5. Tag
  const jede = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innenB / 64))));
  reihe.forEach((d, i) => {
    if ((n - 1 - i) % jede) return;
    const x = links + schritt * i + schritt / 2;
    const [j, m, t] = d.datum.split("-");
    svg += `<text x="${x}" y="${hoehe - 6}" text-anchor="middle">${t}.${m}.</text>`;
  });
  svg += `</g>`;
  // Saeulen: Segmente von unten nach oben, eckig mit dunkler Pixelkontur
  reihe.forEach((d, i) => {
    const x = Math.round(links + schritt * i + (schritt - saeule) / 2);
    let basis = 0;
    ["gold", "stein", "holz"].filter(k => d[k] > 0).forEach((k) => {
      const y0 = y(basis), y1 = y(basis + d[k]);
      basis += d[k];
      const h = Math.round(y0 - y1);
      if (h < 1) return;
      svg += `<rect fill="${farben[k]}" stroke="var(--ink)" stroke-width="2" x="${x}" y="${Math.round(y1)}" width="${saeule}" height="${h}"/>`;
    });
  });
  // Direkte Beschriftung an der letzten Saeule, nur wo das Segment hoch genug ist
  const letzte = reihe[n - 1];
  if (letzte) {
    let basis = 0;
    const xl = links + schritt * (n - 1) + (schritt + saeule) / 2 + 6;
    for (const [k, name] of ROHSTOFFE) {
      const h = (letzte[k] / max) * innenH;
      if (h >= 14) svg += `<text x="${xl}" y="${y(basis + letzte[k] / 2) + 4}" font-size="11" fill="var(--gedaempft)">${name}</text>`;
      basis += letzte[k];
    }
  }
  svg += `<line x1="${links}" x2="${links + innenB}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)" stroke-width="3"/>`;
  // Trefferflaechen: ganze Spaltenbreite, volle Hoehe
  reihe.forEach((d, i) => {
    svg += `<rect class="treffer" data-i="${i}" x="${links + schritt * i}" y="${oben}" width="${schritt}" height="${innenH}"/>`;
  });
  svg += `</svg>`;

  const legende = `<div class="legende" style="margin-bottom:8px">${ROHSTOFFE.map(([k, name]) =>
    `<span>${ROHSTOFF_ICON(k)}${name}</span>`).join("")}
    <span class="leise" style="margin-left:auto">Summe ${zahl(reihe.reduce((a, d) => a + summe(d), 0))} · ${zahl(reihe.reduce((a, d) => a + d.raids, 0))} Raids</span></div>`;
  el.innerHTML = `${legende}<div class="diagramm">${svg}<div class="tooltip"></div></div>`;

  const box = el.querySelector(".diagramm"), tip = el.querySelector(".tooltip");
  box.querySelectorAll(".treffer").forEach(r => {
    r.addEventListener("mouseenter", () => {
      const d = reihe[+r.dataset.i];
      const [j, m, t] = d.datum.split("-");
      tip.innerHTML = `<div style="margin-bottom:4px"><b>${t}.${m}.${j}</b></div>` +
        ROHSTOFFE.map(([k, name]) => `<div class="zeile"><span class="res-key">${ROHSTOFF_ICON(k)}${name}</span><b>${zahl(d[k])}</b></div>`).join("") +
        `<div class="zeile" style="border-top:1px solid var(--rand);margin-top:4px;padding-top:4px"><span>Summe</span><b>${zahl(summe(d))}</b></div>` +
        `<div class="zeile"><span>Raids</span><b>${zahl(d.raids)}</b></div>`;
      tip.style.display = "block";
      const rb = r.getBoundingClientRect(), bb = box.getBoundingClientRect();
      let left = rb.left - bb.left + rb.width / 2 + 10;
      if (left + tip.offsetWidth > bb.width) left = rb.left - bb.left - tip.offsetWidth - 10 + rb.width / 2;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `10px`;
    });
    r.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}
function diagrammTabelle(reihe) {
  const zeilen = [...reihe].reverse().map(d => {
    const [j, m, t] = d.datum.split("-");
    return `<tr><td>${t}.${m}.${j}</td>${ROHSTOFFE.map(([k]) => `<td class="zahl">${zahl(d[k])}</td>`).join("")}
      <td class="zahl">${zahl(d.gold + d.stein + d.holz)}</td><td class="zahl">${zahl(d.raids)}</td></tr>`;
  }).join("");
  return `<div class="tabelle-rahmen" style="max-height:320px;overflow:auto"><table><thead><tr><th>Tag</th>
    ${ROHSTOFFE.map(([, n]) => `<th class="zahl">${n}</th>`).join("")}<th class="zahl">Summe</th><th class="zahl">Raids</th></tr></thead>
    <tbody>${zeilen}</tbody></table></div>`;
}
function neuZeichnenDiagramme() {
  for (const [id, reihe] of Object.entries(diagramme)) diagramm(id, reihe);
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tabelle-fuer]");
  if (!b) return;
  const id = b.dataset.tabelleFuer;
  if (tabellenAn.has(id)) tabellenAn.delete(id); else tabellenAn.add(id);
  b.textContent = tabellenAn.has(id) ? "Diagramm" : "Tabelle";
  if (diagramme[id]) diagramm(id, diagramme[id]);
});
let groesseTimer = null;
window.addEventListener("resize", () => { clearTimeout(groesseTimer); groesseTimer = setTimeout(neuZeichnenDiagramme, 150); });
