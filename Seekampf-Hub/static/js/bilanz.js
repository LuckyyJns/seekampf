"use strict";
// ======================================================================
// Rohstoff-Bilanz: woher die Rohstoffe kommen und wohin sie gehen
// (Buchungen der Bots in ~/Seekampf/data/bilanz, GET /api/bilanz) und
// die Reservierungen aller Bots (GET /api/reservierungen)
// ======================================================================
let bilanz = null, reservierungenStand = null;
let biZeitraum = "heute", biInsel = "alle";
try { biZeitraum = localStorage.getItem("bi-zeitraum") || "heute"; biInsel = localStorage.getItem("bi-insel") || "alle"; } catch (e) { /* egal */ }

async function bilanzHolen() {
  try {
    [bilanz, reservierungenStand] = await Promise.all([hole("/api/bilanz?tage=14"), hole("/api/reservierungen")]);
    $("bi-fehler").hidden = true;
  } catch (e) {
    $("bi-fehler").textContent = `Bilanz nicht abrufbar: ${e.message}`;
    $("bi-fehler").hidden = false;
    return;
  }
  zeichneBilanz();
}

/** Kalendertag (Europe/Berlin) als JJJJ-MM-TT, `zurueck` Tage vor der Serverzeit. */
function biTag(zurueck = 0) {
  return new Date((bilanz.serverzeit - zurueck * 86400) * 1000).toLocaleDateString("sv-SE", { timeZone: "Europe/Berlin" });
}
function biTage() {
  if (biZeitraum === "heute") return [biTag(0)];
  if (biZeitraum === "gestern") return [biTag(1)];
  const n = Number(biZeitraum) || 7;
  return Array.from({ length: n }, (_, i) => biTag(i));
}
/** Summe je Kategorie und Rohstoff ueber die gewaehlten Tage und Inseln. */
function biSumme(tage, inseln) {
  const summe = {};
  for (const t of tage) for (const [iid, kat] of Object.entries(bilanz.tage[t] || {})) {
    if (inseln && !inseln.has(iid)) continue;
    for (const [k, werte] of Object.entries(kat)) {
      if (k.startsWith("_")) continue;
      const ziel = summe[k] ||= { gold: 0, stein: 0, holz: 0 };
      for (const [r] of ROHSTOFFE) ziel[r] += Number(werte[r] || 0);
    }
  }
  return summe;
}
const biGesamt = (w) => ROHSTOFFE.reduce((n, [r]) => n + (w?.[r] || 0), 0);
function biZahl(n, vorzeichen = true) {
  n = Math.round(n || 0);
  if (!n) return `<span class="leise">0</span>`;
  const farbe = n > 0 ? "var(--gut)" : "var(--schlecht)";
  return `<span style="color:${farbe}">${vorzeichen && n > 0 ? "+" : ""}${zahl(n)}</span>`;
}
function biZeile(titel, w, klasse = "") {
  return `<tr class="${klasse}"><td>${titel}</td>${ROHSTOFFE.map(([r]) => `<td class="zahl mono">${biZahl(w[r])}</td>`).join("")}
    <td class="zahl mono"><b>${biZahl(biGesamt(w))}</b></td></tr>`;
}

function zeichneBilanz() {
  if (!bilanz) return;
  // Auswahl
  const sel = $("bi-insel");
  const optionen = `<option value="alle">Alle Inseln</option>` + bilanz.inseln.map(i => `<option value="${esc(i.id)}">${esc(i.name)}</option>`).join("");
  if (sel.dataset.stand !== optionen) { sel.innerHTML = optionen; sel.dataset.stand = optionen; }
  if (biInsel !== "alle" && !bilanz.inseln.some(i => String(i.id) === biInsel)) biInsel = "alle";
  sel.value = biInsel;
  $("bi-zeitraum").querySelectorAll("button").forEach(b => b.classList.toggle("an", b.dataset.zeitraum === biZeitraum));

  const tage = biTage();
  const summe = biSumme(tage, biInsel === "alle" ? null : new Set([biInsel]));
  const bekannt = new Set([...bilanz.zugaenge, ...bilanz.abgaenge, bilanz.rest]);
  const sonstige = Object.keys(summe).filter(k => !bekannt.has(k));
  const zu = [...bilanz.zugaenge, ...sonstige.filter(k => biGesamt(summe[k]) >= 0)].filter(k => summe[k]);
  const ab = [...bilanz.abgaenge, ...sonstige.filter(k => biGesamt(summe[k]) < 0)].filter(k => summe[k]);
  const leer = { gold: 0, stein: 0, holz: 0 };
  const saldo = { ...leer };
  for (const w of Object.values(summe)) for (const [r] of ROHSTOFFE) saldo[r] += w[r];
  if (!Object.keys(summe).length) {
    $("bi-tabelle").innerHTML = `<div class="leise klein-text">Für diesen Zeitraum ist noch nichts gebucht.</div>`;
  } else {
    const zwischen = (titel, liste) => {
      const s = { ...leer };
      for (const k of liste) for (const [r] of ROHSTOFFE) s[r] += summe[k][r];
      return biZeile(`<b>${titel}</b>`, s, "zwischensumme");
    };
    $("bi-tabelle").innerHTML = `<div class="tabelle-rahmen"><table><thead><tr><th></th>${ROHSTOFFE.map(([r, n]) =>
        `<th class="zahl">${ROHSTOFF_ICON(r)} ${n}</th>`).join("")}<th class="zahl">Summe</th></tr></thead><tbody>
      ${zu.map(k => biZeile(esc(k), summe[k])).join("")}
      ${zu.length ? zwischen("Zugänge", zu) : ""}
      ${ab.map(k => biZeile(esc(k), summe[k])).join("")}
      ${ab.length ? zwischen("Abgänge", ab) : ""}
      ${summe[bilanz.rest] ? biZeile(`<span class="leise">${esc(bilanz.rest)}</span>`, summe[bilanz.rest]) : ""}
      ${biZeile("<b>Saldo</b>", saldo, "zwischensumme")}
      </tbody></table></div>`;
  }

  // Je Insel
  const zeilen = bilanz.inseln.map((i) => {
    const s = biSumme(tage, new Set([String(i.id)]));
    let zug = 0, abg = 0, rest = 0, quelle = null, posten = null;
    for (const [k, w] of Object.entries(s)) {
      const g = biGesamt(w);
      if (k === bilanz.rest) { rest += g; continue; }
      if (g >= 0) { zug += g; if (!quelle || g > quelle[1]) quelle = [k, g]; } else { abg += g; if (!posten || g < posten[1]) posten = [k, g]; }
    }
    return { i, zug, abg, rest, saldo: zug + abg + rest, quelle, posten, leer: !Object.keys(s).length };
  }).filter(z => !z.leer);
  $("bi-inseln").innerHTML = zeilen.length ? `<div class="tabelle-rahmen"><table><thead><tr><th>Insel</th><th class="zahl">Zugänge</th>
      <th class="zahl">Abgänge</th><th class="zahl verstecken-schmal">nicht zugeordnet</th><th class="zahl">Saldo</th>
      <th class="verstecken-schmal">größte Quelle</th><th class="verstecken-schmal">größter Posten</th></tr></thead><tbody>
    ${zeilen.map(z => `<tr data-bi-insel="${esc(z.i.id)}" style="cursor:pointer" class="${String(z.i.id) === biInsel ? "aktiv" : ""}">
      <td>${esc(z.i.name)}</td><td class="zahl mono">${biZahl(z.zug)}</td><td class="zahl mono">${biZahl(z.abg)}</td>
      <td class="zahl mono verstecken-schmal">${biZahl(z.rest)}</td><td class="zahl mono"><b>${biZahl(z.saldo)}</b></td>
      <td class="verstecken-schmal">${z.quelle ? esc(z.quelle[0]) : "–"}</td><td class="verstecken-schmal">${z.posten ? esc(z.posten[0]) : "–"}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="leise klein-text">Für diesen Zeitraum ist noch nichts gebucht.</div>`;

  // Reservierungen
  const res = reservierungenStand?.eintraege || [];
  $("bi-reservierungen").innerHTML = res.length ? `<div class="tabelle-rahmen"><table><thead><tr><th>Insel</th><th>Bot</th>
      <th>Rohstoffe</th><th>Einheiten</th><th class="verstecken-schmal">Grund</th><th>Stand</th></tr></thead><tbody>
    ${res.map(e => `<tr${e.aktuell ? "" : ' class="leise"'}><td>${esc(e.insel)}</td><td>${esc(e.bot_titel)}</td>
      <td>${Object.keys(e.rohstoffe).length ? beuteText(e.rohstoffe) : "–"}</td><td>${mengenText(e.einheiten)}</td>
      <td class="verstecken-schmal">${esc(e.grund || "")}</td>
      <td>${e.aktuell ? `<span class="marke"><span class="punkt"></span>gilt</span>` : `<span class="marke schlecht" title="Bot läuft nicht – gilt nicht mehr">veraltet</span>`}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="leise klein-text">Gerade hält kein Bot etwas zurück.</div>`;
}

$("bi-zeitraum").addEventListener("click", (e) => {
  const b = e.target.closest("[data-zeitraum]");
  if (!b) return;
  biZeitraum = b.dataset.zeitraum;
  try { localStorage.setItem("bi-zeitraum", biZeitraum); } catch (err) { /* egal */ }
  zeichneBilanz();
});
$("bi-insel").addEventListener("change", (e) => {
  biInsel = e.target.value;
  try { localStorage.setItem("bi-insel", biInsel); } catch (err) { /* egal */ }
  zeichneBilanz();
});
$("bi-inseln").addEventListener("click", (e) => {
  const z = e.target.closest("[data-bi-insel]");
  if (!z) return;
  biInsel = biInsel === z.dataset.biInsel ? "alle" : z.dataset.biInsel;
  try { localStorage.setItem("bi-insel", biInsel); } catch (err) { /* egal */ }
  zeichneBilanz();
});
