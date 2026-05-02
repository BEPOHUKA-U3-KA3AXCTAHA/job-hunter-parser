// JHP external-form filler — runs on ANY page (manifest <all_urls>).
//
// Triggered by popup "Fill This Page" button. Snapshots the rendered
// form HTML, posts it to the local Python API which asks Claude for an
// action plan, then executes each action via real DOM events. Cloudflare
// Turnstile and similar bot-detectors can't see anything because the
// browser session is the user's real Firefox — no Selenium, no
// navigator.webdriver, no marionette.

const API_BASE = "http://localhost:8765";

function log(...args) {
  console.log("[JHP-fill]", ...args);
}

function* deepNodes(root) {
  if (!root) return;
  const stack = [root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    if (n.nodeType === 1) yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    const k = n.children || n.childNodes || [];
    for (let i = k.length - 1; i >= 0; i--) stack.push(k[i]);
  }
}

function snapshotFormHtml(maxChars = 120000) {
  const root = document.querySelector("form") || document.body;
  let raw = root ? root.outerHTML : "";
  raw = raw.replace(/<script\b[\s\S]*?<\/script>/gi, "");
  raw = raw.replace(/<style\b[\s\S]*?<\/style>/gi, "");
  raw = raw.replace(/<svg\b[\s\S]*?<\/svg>/gi, "<svg/>");
  raw = raw.replace(/\sclass="[^"]{60,}"/g, "");
  raw = raw.replace(/(data:[a-z+/-]+;base64,)[A-Za-z0-9+/=]{100,}/g, "$1<truncated>");
  raw = raw.replace(/\s+/g, " ");
  if (raw.length > maxChars) {
    raw = raw.slice(0, maxChars / 2) + "\n...[truncated]...\n" + raw.slice(-maxChars / 2);
  }
  return raw;
}

function setNativeValue(el, val) {
  const ctor = el.tagName === "TEXTAREA" ? HTMLTextAreaElement
             : el.tagName === "SELECT" ? HTMLSelectElement
             : HTMLInputElement;
  const desc = ctor && Object.getOwnPropertyDescriptor(ctor.prototype, "value");
  if (desc && desc.set) desc.set.call(el, val);
  else el.value = val;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function fallbackClickTarget(el) {
  // Hidden radio/checkbox: click the associated label or wrapper instead.
  const root = el.getRootNode ? el.getRootNode() : document;
  if (el.id) {
    const lbl = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (lbl) return lbl;
  }
  let p = el.parentElement;
  for (let i = 0; i < 5 && p; i++) {
    if (p.tagName === "LABEL") return p;
    const role = p.getAttribute && p.getAttribute("role");
    if (role === "radio" || role === "checkbox" || role === "button") return p;
    p = p.parentElement;
  }
  return null;
}

function findOptionByText(want, opts = {}) {
  const wantLow = want.toLowerCase();
  let exact = null, contains = null;
  for (const el of deepNodes(document)) {
    if (!el.getAttribute || el.getAttribute("role") !== "option") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const t = (el.textContent || "").trim().toLowerCase();
    if (!t) continue;
    if (t === wantLow) { exact = el; break; }
    if (!contains && t.includes(wantLow)) contains = el;
  }
  return exact || contains;
}

function findDeclineOption() {
  const re1 = /decline|undisclosed|not provided|prefer not/i;
  const re2 = /\bnot\b[^.]{0,20}\b(disclose|specify|specified|identify|answer|say|tell|share)\b/i;
  for (const el of deepNodes(document)) {
    if (!el.getAttribute || el.getAttribute("role") !== "option") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const t = (el.textContent || "").trim();
    if (t && (re1.test(t) || re2.test(t))) return el;
  }
  return null;
}

function visibleOptions() {
  const out = [];
  for (const el of deepNodes(document)) {
    if (!el.getAttribute || el.getAttribute("role") !== "option") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const t = (el.textContent || "").trim();
    if (t) out.push(t);
  }
  return out.slice(0, 12);
}

async function waitMs(ms) { return new Promise(r => setTimeout(r, ms)); }

async function executeAction(act) {
  const sel = act.selector;
  const value = act.value || "";
  const action = (act.action || "").toLowerCase();
  if (!sel || !action) return false;
  let el = null;
  try { el = document.querySelector(sel); } catch (e) {}
  if (!el) {
    log("selector miss:", sel.slice(0, 80));
    return false;
  }
  el.scrollIntoView({ block: "center" });
  await waitMs(150);
  try {
    if (action === "fill") {
      el.click();
      try { el.value = ""; } catch (e) {}
      setNativeValue(el, value);
      return true;
    }
    if (action === "click") {
      try {
        el.click();
        return true;
      } catch (e) {
        const fb = fallbackClickTarget(el);
        if (fb) { fb.click(); return true; }
        return false;
      }
    }
    if (action === "select_combobox") {
      el.click();
      await waitMs(500);
      // Try filtering inside the combobox if it has a search input.
      try {
        let typer = el;
        const inner = el.querySelector('input[role="combobox"], input[data-input*="search"]');
        if (inner) typer = inner;
        if (typer.tagName === "INPUT") {
          typer.click();
          typer.select();
          setNativeValue(typer, "");
          typer.value = value;
          typer.dispatchEvent(new Event("input", { bubbles: true }));
          await waitMs(700);
        }
      } catch (e) {}
      let opt = findOptionByText(value);
      if (!opt && /decline|prefer not|don'?t (wish|want)|not (specified|specify|identify|disclose|to)|do not wish|choose not/i.test(value)) {
        opt = findDeclineOption();
      }
      if (opt) {
        opt.click();
        return true;
      }
      log("option not found, visible:", visibleOptions(), "for", value);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      return false;
    }
    log("unknown action:", action);
    return false;
  } catch (e) {
    log("action failed:", action, sel.slice(0, 50), e);
    return false;
  }
}

function detectFormErrors() {
  const out = [];
  const re = /missing entry|this field is required|^required\b|please (enter|select|fill|complete)|errors? on the form|needs corrections/i;
  for (const el of deepNodes(document)) {
    if (!el.getBoundingClientRect) continue;
    const role = el.getAttribute && el.getAttribute("role");
    const cls = el.className && (el.className.baseVal !== undefined ? el.className.baseVal : el.className);
    const isAlert = role === "alert" ||
      (typeof cls === "string" && /\b(error|invalid|required-warning|form-error|errorBanner)\b/i.test(cls));
    if (isAlert) {
      const r = el.getBoundingClientRect();
      if (r.width >= 1 && r.height >= 1) {
        const t = (el.textContent || "").trim();
        if (t && re.test(t)) out.push(t.slice(0, 200));
      }
    }
  }
  return [...new Set(out)];
}

function findSubmitButton() {
  const re = /^\s*(submit application|submit|send message|send|apply)\s*$/i;
  for (const el of deepNodes(document)) {
    if (el.tagName !== "BUTTON" && el.tagName !== "A" &&
        (!el.getAttribute || el.getAttribute("role") !== "button")) continue;
    if (el.disabled) continue;
    if (el.getAttribute && el.getAttribute("aria-disabled") === "true") continue;
    const r = el.getBoundingClientRect();
    if (!r || r.width < 4 || r.height < 4) continue;
    try {
      const style = getComputedStyle(el);
      if (style && style.pointerEvents === "none") continue;
    } catch (e) {}
    const t = (el.textContent || "").trim();
    const a = (el.getAttribute && el.getAttribute("aria-label")) || "";
    if (re.test(t) || re.test(a)) return el;
  }
  return null;
}

async function fillThisPage() {
  log("starting fill on", location.href);
  let lastErrors = [];
  for (let attempt = 0; attempt < 4; attempt++) {
    log(`==== attempt ${attempt + 1}/4 ====`);
    const html = snapshotFormHtml();
    const r = await fetch(`${API_BASE}/fill-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: location.href, html, prior_errors: lastErrors }),
    });
    if (!r.ok) {
      log("fill-plan API call failed", r.status);
      return { outcome: "failed", detail: `api ${r.status}` };
    }
    const { actions } = await r.json();
    log(`got ${actions.length} action(s)`);
    let executed = 0;
    for (const act of actions) {
      if (await executeAction(act)) executed++;
      await waitMs(250);
    }
    log(`attempt ${attempt + 1}: executed ${executed}/${actions.length}`);
    await waitMs(1000);
    const submit = findSubmitButton();
    if (!submit) {
      lastErrors = detectFormErrors();
      if (lastErrors.length === 0) {
        lastErrors = ["submit button still disabled — required fields missing"];
      }
      log(`attempt ${attempt + 1}: no enabled submit, errors:`, lastErrors);
      continue;
    }
    log(`attempt ${attempt + 1}: clicking submit`);
    submit.click();
    // Cloudflare Turnstile / hCaptcha may pop up here. Because we're a
    // REAL Firefox session, Turnstile usually solves invisibly within
    // 3-8 seconds. Wait and re-check.
    await waitMs(8000);
    lastErrors = detectFormErrors();
    if (lastErrors.length === 0) {
      log("🎯 form likely accepted (no errors visible)");
      return { outcome: "applied", detail: `${executed} actions, attempt ${attempt + 1}` };
    }
    log(`attempt ${attempt + 1} rejected:`, lastErrors);
  }
  return { outcome: "failed", detail: `4 attempts, last errors: ${lastErrors.join(" | ")}` };
}

browser.runtime.onMessage.addListener(async (msg) => {
  if (msg.type === "fill_this_page") {
    return await fillThisPage();
  }
});

log("external_fill.js ready on", location.href);
