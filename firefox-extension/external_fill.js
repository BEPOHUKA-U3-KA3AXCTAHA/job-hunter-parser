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

async function ensureFullFormHydrated() {
  // React forms lazy-hydrate sections on scroll. Scroll to the bottom
  // (and back) so every field-entry has a chance to mount before we
  // serialize the HTML.
  const prev = window.scrollY;
  for (let y = 0; y <= document.body.scrollHeight; y += 400) {
    window.scrollTo(0, y);
    await waitMs(80);
  }
  window.scrollTo(0, document.body.scrollHeight);
  await waitMs(300);
  window.scrollTo(0, prev);
  await waitMs(150);
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

function findOptionByTextWithin(scope, want) {
  const wantLow = want.toLowerCase();
  let exact = null, contains = null;
  for (const el of deepNodes(scope)) {
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

function findDeclineOptionWithin(scope) {
  const re1 = /decline|undisclosed|not provided|prefer not/i;
  const re2 = /\bnot\b[^.]{0,20}\b(disclose|specify|specified|identify|answer|say|tell|share)\b/i;
  for (const el of deepNodes(scope)) {
    if (!el.getAttribute || el.getAttribute("role") !== "option") continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const t = (el.textContent || "").trim();
    if (t && (re1.test(t) || re2.test(t))) return el;
  }
  return null;
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
      // Rippling's div role=combobox listens for pointer events specifically
      // (React Aria use-pointer pattern). Dispatch the full pointer + mouse
      // sequence so the dropdown actually opens.
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const baseOpts = { bubbles: true, cancelable: true, view: window, button: 0, clientX: cx, clientY: cy };
      const ptrOpts = { ...baseOpts, pointerType: "mouse", pointerId: 1, isPrimary: true };
      try { el.dispatchEvent(new PointerEvent("pointerover", ptrOpts)); } catch (e) {}
      try { el.dispatchEvent(new PointerEvent("pointerenter", ptrOpts)); } catch (e) {}
      try { el.dispatchEvent(new PointerEvent("pointerdown", ptrOpts)); } catch (e) {}
      el.dispatchEvent(new MouseEvent("mousedown", baseOpts));
      try { el.dispatchEvent(new PointerEvent("pointerup", ptrOpts)); } catch (e) {}
      el.dispatchEvent(new MouseEvent("mouseup", baseOpts));
      el.dispatchEvent(new MouseEvent("click", baseOpts));
      el.click();
      el.focus();
      // Press Down arrow to ensure listbox opens for keyboard-controllable
      // comboboxes that ignore pure click.
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      await waitMs(1200);
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
      // Scope option search to THIS combobox's listbox to avoid clicking
      // 'Yes' in some other dropdown that happens to be open.
      let scope = null;
      const listboxId = el.getAttribute && el.getAttribute("aria-controls");
      if (listboxId) {
        scope = document.getElementById(listboxId) ||
                document.querySelector(`[id="${listboxId}"]`);
      }
      let opt = scope
        ? findOptionByTextWithin(scope, value)
        : findOptionByText(value);
      if (!opt && /decline|prefer not|don'?t (wish|want)|not (specified|specify|identify|disclose|to)|do not wish|choose not/i.test(value)) {
        opt = scope ? findDeclineOptionWithin(scope) : findDeclineOption();
      }
      if (opt) {
        // For React-Aria-style div comboboxes, clicking the option moves
        // the DOM text but DOES NOT trigger the framework's onChange.
        // Use keyboard navigation: focus the combobox, type the value
        // (combobox has aria-autocomplete="list" → typing filters to
        // matching options + highlights first match), then Enter.
        el.focus();
        await waitMs(150);
        // Type each character so React-Aria's autocomplete filter fires.
        for (const ch of value.slice(0, 8)) {
          el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true, cancelable: true }));
          el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true, cancelable: true }));
          await waitMs(60);
        }
        await waitMs(400);
        // Enter to select the highlighted (filtered) option.
        el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
        el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
        await waitMs(400);
        // Verify by checking if combobox now shows our value (substring).
        const after = (el.textContent || "").trim().toLowerCase();
        if (after.includes(value.toLowerCase().slice(0, 4))) {
          return true;
        }
        log("type-then-enter didn't take, falling back to ArrowDown nav");
        el.focus();
        await waitMs(200);
        // ArrowDown opens listbox if not already open + highlights first.
        const downEvt = (key) => new KeyboardEvent(key, { key: "ArrowDown", code: "ArrowDown", bubbles: true, cancelable: true });
        el.dispatchEvent(downEvt("keydown"));
        el.dispatchEvent(downEvt("keyup"));
        await waitMs(400);
        // Walk down until the highlighted/focused option text matches.
        const wantLow = value.trim().toLowerCase();
        let matched = false;
        for (let step = 0; step < 30; step++) {
          const root = scope || document;
          // React Aria marks the focused option with data-focused="true"
          // or aria-selected="true". Some implementations use a class.
          const active = root.querySelector(
            '[role="option"][data-focused="true"], ' +
            '[role="option"][aria-selected="true"], ' +
            '[role="option"][data-highlighted="true"], ' +
            '[role="option"].focused'
          );
          const txt = active ? (active.textContent || "").trim().toLowerCase() : "";
          const wantDecline = /decline|prefer not|don'?t (wish|want)|not (specified|specify|identify|disclose|to)|do not wish|choose not/i.test(value);
          const isMatch = txt && (
            txt === wantLow ||
            txt.includes(wantLow) ||
            (wantDecline && /decline|undisclosed|not provided|prefer not|\bnot\b[^.]{0,20}\b(disclose|specify|specified|identify|answer|say|tell|share)\b/i.test(active.textContent || ""))
          );
          if (isMatch) {
            const enterEvt = (key) => new KeyboardEvent(key, { key: "Enter", code: "Enter", bubbles: true, cancelable: true });
            el.dispatchEvent(enterEvt("keydown"));
            el.dispatchEvent(enterEvt("keyup"));
            await waitMs(400);
            matched = true;
            break;
          }
          el.dispatchEvent(downEvt("keydown"));
          el.dispatchEvent(downEvt("keyup"));
          await waitMs(140);
        }
        if (!matched) {
          // Fallback: just press Enter on whatever's highlighted.
          el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
          await waitMs(300);
        }
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
  // Also: any input/textarea/select/div with aria-invalid=true and a
  // question label up the tree. This catches Rippling-style 'gray Apply
  // button means a required field is empty' even before the user clicks
  // submit.
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
    if (el.getAttribute && el.getAttribute("aria-invalid") === "true") {
      // Walk up to find the question label.
      let q = el.parentElement, label = "";
      for (let i = 0; i < 6 && q && !label; i++) {
        const lbl = q.querySelector && (
          q.querySelector("label") ||
          q.querySelector("p.css-i4dt0z") ||
          q.querySelector('[class*="QuestionTitle"]')
        );
        if (lbl) label = (lbl.textContent || "").trim();
        q = q.parentElement;
      }
      if (label) out.push(`aria-invalid: ${label.slice(0, 140)}`);
    }
  }
  // Also scan aria-required="true" controls that are STILL empty — many
  // ATSes (Rippling) don't set aria-invalid until the user clicks submit,
  // so empty-required is a better pre-submit signal of what's missing.
  for (const el of deepNodes(document)) {
    if (el.getAttribute && el.getAttribute("aria-required") === "true") {
      const tag = el.tagName;
      let isEmpty = false;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        isEmpty = !((el.value || "").trim());
      } else if (tag === "DIV") {
        const inner = (el.textContent || "").trim();
        isEmpty = !inner || /^(select|choose|please)/i.test(inner);
      }
      if (!isEmpty) continue;
      let q = el.parentElement, label = "";
      for (let i = 0; i < 6 && q && !label; i++) {
        const lbl = q.querySelector && (
          q.querySelector("label") ||
          q.querySelector("p.css-i4dt0z") ||
          q.querySelector('[class*="QuestionTitle"]')
        );
        if (lbl) label = (lbl.textContent || "").trim();
        q = q.parentElement;
      }
      if (label) out.push(`empty-required: ${label.slice(0, 140)}`);
      else if (el.name) out.push(`empty-required[name=${el.name}]`);
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

async function uploadResumeIfPresent() {
  // Find a file input that wants a CV/resume.
  let target = null;
  for (const inp of document.querySelectorAll('input[type="file"]')) {
    const name = (inp.name || "").toLowerCase();
    const id = (inp.id || "").toLowerCase();
    const accept = (inp.accept || "").toLowerCase();
    if (/resume|cv/.test(name + id) || /pdf|doc/.test(accept) || true) {
      target = inp;
      break;
    }
  }
  if (!target) {
    log("no <input type=file> on page");
    return false;
  }
  // Fetch the resume from the local API.
  let payload;
  try {
    const r = await fetch(`${API_BASE}/resume`);
    if (!r.ok) throw new Error("resume API " + r.status);
    payload = await r.json();
    if (!payload.base64) throw new Error("no base64");
  } catch (e) {
    log("resume fetch failed:", e);
    return false;
  }
  // base64 -> Blob -> File
  const bin = atob(payload.base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const file = new File([bytes], payload.filename || "resume.pdf", {
    type: payload.mime || "application/pdf",
  });
  // Inject into the file input via DataTransfer (works in real Firefox).
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    target.files = dt.files;
    target.dispatchEvent(new Event("change", { bubbles: true }));
    target.dispatchEvent(new Event("input", { bubbles: true }));
    log("resume injected via DataTransfer");
    return true;
  } catch (e) {
    log("DataTransfer failed:", e);
    return false;
  }
}

async function fillThisPage() {
  log("starting fill on", location.href);
  // Upload CV first — many ATSes auto-parse it to pre-fill name/email/etc,
  // shrinking the page-filler workload to just the custom questions.
  await uploadResumeIfPresent();
  await waitMs(2500);
  let lastErrors = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    log(`==== attempt ${attempt + 1}/3 ====`);
    await ensureFullFormHydrated();
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
    // Log each action so we can post-mortem which one failed.
    try {
      sessionStorage.setItem(
        `jhp-actions-${attempt + 1}`,
        JSON.stringify(actions, null, 2),
      );
    } catch (e) {}
    let executed = 0;
    const results = [];
    for (const act of actions) {
      const ok = await executeAction(act);
      results.push({ ...act, _ok: ok });
      if (ok) executed++;
      await waitMs(250);
    }
    try {
      sessionStorage.setItem(
        `jhp-results-${attempt + 1}`,
        JSON.stringify(results, null, 2),
      );
    } catch (e) {}
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

// Auto-trigger when the URL hash is #jhp-autofill — used by the
// scripts/test_extension_fill.py harness so the bot can run end-to-end
// without a manual popup click.
async function maybeAutoFill() {
  if (!location.hash.includes("jhp-autofill")) return;
  log("auto-fill triggered by URL hash");
  // Wait a moment for the form to fully render before snapshotting.
  await waitMs(2500);
  const result = await fillThisPage();
  log("auto-fill result:", result);
  // Stash the result so the harness can poll it from the page side.
  try {
    sessionStorage.setItem("jhp-autofill-result", JSON.stringify(result));
  } catch (e) {}
}
maybeAutoFill();

log("external_fill.js ready on", location.href);
