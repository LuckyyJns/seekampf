"use strict";
// ======================================================================
// Pixel-Feinschliff: Zahlenfelder mit Plus/Minus, Hinweise im Pixel-Stil
// ======================================================================
/** Zahlenfelder bekommen eigene Minus/Plus-Knoepfe (statt der kleinen Browser-Pfeile). */
function stepperBauen(wurzel) {
  wurzel.querySelectorAll('input[type="number"]:not([data-stepper])').forEach((inp) => {
    inp.dataset.stepper = "1";
    if (inp.closest(".dialog-zahl") || inp.type !== "number") return;
    const huelle = document.createElement("span");
    huelle.className = "stepper" + (inp.style.width ? " schmal" : "");
    const knopf = (zeichen, vorzeichen, name) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "stepper-b"; b.textContent = zeichen; b.tabIndex = -1; b.setAttribute("aria-label", name);
      b.addEventListener("click", () => {
        if (inp.disabled) return;
        const schritt = Number(inp.step) > 0 ? Number(inp.step) : 1;
        let v = (Number(inp.value) || 0) + vorzeichen * schritt;
        if (inp.min !== "" && v < Number(inp.min)) v = Number(inp.min);
        if (inp.max !== "" && v > Number(inp.max)) v = Number(inp.max);
        inp.value = String(Math.round(v * 1e6) / 1e6);
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        inp.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return b;
    };
    inp.parentNode.insertBefore(huelle, inp);
    huelle.append(knopf("−", -1, "weniger"), inp, knopf("+", 1, "mehr"));
  });
}
stepperBauen(document);
new MutationObserver((liste) => {
  for (const m of liste) for (const n of m.addedNodes) {
    if (n.nodeType !== 1) continue;
    stepperBauen(n.matches?.('input[type="number"]') ? n.parentNode || document : n);
  }
}).observe(document.body, { childList: true, subtree: true });

/** Hinweise (title) als Pixel-Kaestchen statt des grauen Browser-Hinweises. */
const pixtip = document.createElement("div");
pixtip.id = "pixtip"; pixtip.setAttribute("role", "tooltip"); document.body.appendChild(pixtip);
let tipEl = null;
function tipPlatzieren(e) {
  const b = pixtip.offsetWidth, h = pixtip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY + 20;
  if (x + b > window.innerWidth - 8) x = Math.max(8, e.clientX - b - 10);
  if (y + h > window.innerHeight - 8) y = Math.max(8, e.clientY - h - 12);
  pixtip.style.left = `${x}px`; pixtip.style.top = `${y}px`;
}
function tipWeg() {
  if (tipEl && tipEl.dataset.pixtitel != null) { tipEl.setAttribute("title", tipEl.dataset.pixtitel); delete tipEl.dataset.pixtitel; }
  pixtip.style.display = "none"; tipEl = null;
}
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest?.("[title]");
  if (!el || el === tipEl) return;
  tipWeg();
  const text = el.getAttribute("title");
  if (!text) return;
  el.dataset.pixtitel = text; el.removeAttribute("title");
  tipEl = el; pixtip.textContent = text; pixtip.style.display = "block"; tipPlatzieren(e);
});
document.addEventListener("mousemove", (e) => { if (tipEl) tipPlatzieren(e); });
document.addEventListener("mouseout", (e) => { if (tipEl && !tipEl.contains(e.relatedTarget)) tipWeg(); });
document.addEventListener("mousedown", tipWeg);

zeigeTab(location.hash.slice(1) || "uebersicht");
setInterval(() => {
  if (tab === "flotte") flotteHolen();
  if (tab === "upgrade") upgradeHolen();
  if (tab === "allianz") allianzHolen();
  if (tab === "uebersicht") { flotteHolen(); upgradeHolen(); allianzHolen(); }
  if (tab === "karte") { flotteHolen(); kolonieHolen(); spielFlottenHolen(); }
  if (tab === "ausgleich") ausgleichHolen();
  if (tab === "kolonie") kolonieHolen();
  if (tab === "ausbildung") ausbildungHolen();
}, 3000);
setInterval(() => { gesundheitHolen(); if (tab !== "ausgleich") ausgleichHolen(); }, 20000);
gesundheitHolen();
ausgleichHolen();
setInterval(() => { if (tab === "karte") angriffeHolen(); }, 20000);
setInterval(() => { if (tab === "karte") karteHolen(); }, 300000);
setInterval(dienstHolen, 5000);
setInterval(logsHolen, 5000);
setInterval(verlaufHolen, 60000);
setInterval(sekundentakt, 1000);
