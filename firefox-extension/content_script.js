// JHP Easy Apply — content script
//
// LinkedIn 2026 wraps the Easy Apply modal inside a Shadow DOM root attached to
// <div id="interop-outlet">. Native document.querySelector* DOES NOT cross
// shadow boundaries, so every helper here uses the deepNodes() generator that
// descends shadowRoots. Same trick the Python Selenium bot uses
// (see app/modules/automation/adapters/selenium_bot.py JS_WALK_PROLOG).
//
// Other LinkedIn-2026 facts:
// - "Easy Apply" is rendered as <A>, not <button>; aria-label="Easy Apply to this job"
// - Modal Submit/Continue/Next/Review buttons live INSIDE the shadow tree
// - "I'm interested" is a soft-signal alternative on jobs without real EA

const log = (...args) => console.log("[JHP]", ...args);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MAX_MODAL_PAGES = 6;
const RATE_LIMIT_MARKERS = [
  "let's confirm it's really you",
  "we've restricted your account",
  "unusual activity",
  "complete this puzzle",
];

// --- Shadow-DOM-aware DOM walker ---

function* deepNodes(root) {
  if (!root) return;
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    if (!node) continue;
    if (node.nodeType === 1) yield node;
    if (node.shadowRoot) stack.push(node.shadowRoot);
    const kids = node.children || node.childNodes || [];
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
  }
}

function isVisible(el) {
  // offsetParent doesn't work in shadow roots — use rect+style
  const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  if (!rect || rect.width < 4 || rect.height < 4) return false;
  const cs = (el.ownerDocument && el.ownerDocument.defaultView)
    ? el.ownerDocument.defaultView.getComputedStyle(el) : null;
  if (cs && (cs.visibility === "hidden" || cs.display === "none" || cs.opacity === "0")) return false;
  return true;
}

function inAnyDialog(el) {
  let p = el;
  while (p) {
    if (p.getAttribute && p.getAttribute("role") === "dialog") return true;
    p = p.parentNode || (p.host /* shadowRoot */);
  }
  return false;
}

// --- Find / click helpers ---

/** Find first VISIBLE clickable (button | <a> | [role=button]) whose text or
 * aria-label matches regex. Walks Shadow DOM. */
function findClickable(regex, opts = {}) {
  const { preferDialog = false } = opts;
  const candidates = [];
  for (const el of deepNodes(document)) {
    if (el.tagName !== "BUTTON" && el.tagName !== "A" &&
        (!el.getAttribute || el.getAttribute("role") !== "button")) continue;
    if (el.disabled || (el.getAttribute && el.getAttribute("aria-disabled") === "true")) continue;
    if (!isVisible(el)) continue;
    const t = (el.textContent || "").trim();
    const a = (el.getAttribute && el.getAttribute("aria-label")) || "";
    if (!regex.test(t) && !regex.test(a)) continue;
    candidates.push(el);
  }
  if (!candidates.length) return null;
  // aria-label "easy apply to ..." wins
  for (const b of candidates) {
    const a = ((b.getAttribute && b.getAttribute("aria-label")) || "").toLowerCase();
    if (a.includes("easy apply to")) return b;
  }
  if (preferDialog) {
    for (const b of candidates) if (inAnyDialog(b)) return b;
  }
  return candidates[0];
}

async function waitForClickable(regex, timeoutMs = 6000, opts = {}) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const b = findClickable(regex, opts);
    if (b) return b;
    await sleep(180);
  }
  return null;
}

async function waitForModalAppear(timeoutMs = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    for (const d of deepNodes(document)) {
      if (d.getAttribute && d.getAttribute("role") === "dialog" && isVisible(d)) {
        const t = (d.textContent || "").toLowerCase().substring(0, 800);
        if (/apply|contact info|first name|easy apply|submit application|review your application|dialog content/.test(t)) {
          return true;
        }
      }
    }
    await sleep(200);
  }
  return false;
}

function robustClick(el, label = "btn") {
  // scroll into view, then try native + JS click
  try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
  try { el.click(); log(label, "native click ok"); return true; }
  catch (e) { log(label, "native click failed:", e.message); }
  try {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    log(label, "synthetic click ok");
    return true;
  } catch (e) { log(label, "synthetic click failed:", e.message); }
  return false;
}

function isBlocked() {
  const text = (document.body.innerText || "").toLowerCase().substring(0, 4000);
  return RATE_LIMIT_MARKERS.some((m) => text.includes(m));
}

function dumpClickables(limit = 12) {
  const out = [];
  for (const b of deepNodes(document)) {
    if (b.tagName !== "BUTTON" && b.tagName !== "A" &&
        (!b.getAttribute || b.getAttribute("role") !== "button")) continue;
    if (b.disabled || !isVisible(b)) continue;
    out.push({
      tag: b.tagName,
      text: (b.textContent || "").trim().substring(0, 50),
      aria: ((b.getAttribute && b.getAttribute("aria-label")) || "").substring(0, 80),
      inDialog: inAnyDialog(b),
    });
    if (out.length >= limit) break;
  }
  return out;
}

function hasModalErrors() {
  for (const e of deepNodes(document)) {
    if (!isVisible(e)) continue;
    const role = e.getAttribute && e.getAttribute("role");
    const cls = (e.className || "").toString();
    if (role === "alert" || cls.includes("error") || cls.includes("feedback--error")) {
      const t = (e.textContent || "").trim();
      if (t.length > 0 && t.length < 200) return true;
    }
  }
  return false;
}

async function uncheckFollowCompany() {
  for (const cb of deepNodes(document)) {
    if (cb.tagName !== "INPUT" || cb.type !== "checkbox") continue;
    const root = cb.getRootNode ? cb.getRootNode() : document;
    const lbl = (cb.closest && cb.closest("label"))
      || (cb.id && root.querySelector ? root.querySelector(`label[for='${cb.id}']`) : null);
    const t = lbl ? lbl.textContent.toLowerCase() : "";
    if (/follow.*compan/i.test(t) && cb.checked) {
      (lbl || cb).click();
      await sleep(250);
      return true;
    }
  }
  return false;
}

/** Fill first VISIBLE empty input whose label/placeholder/aria matches regex.
 * Walks Shadow DOM. Returns true if filled. */
async function fillTextInputIfEmpty(regex, value) {
  if (!value) return false;
  for (const inp of deepNodes(document)) {
    if (inp.tagName !== "INPUT") continue;
    const type = (inp.type || "").toLowerCase();
    if (type !== "text" && type !== "tel" && type !== "" && type !== "email" && type !== "number") continue;
    if (!isVisible(inp)) continue;
    if (inp.value && inp.value.trim()) continue;
    const id = inp.id || "";
    const name = inp.name || "";
    const placeholder = inp.placeholder || "";
    const aria = (inp.getAttribute && inp.getAttribute("aria-label")) || "";
    let labelText = "";
    if (id) {
      const root = inp.getRootNode ? inp.getRootNode() : document;
      const labelEl = root.querySelector ? root.querySelector(`label[for='${id}']`) : null;
      labelText = labelEl ? labelEl.textContent : "";
    }
    if (!labelText) {
      let p = inp.parentNode;
      for (let i = 0; i < 4 && p; i++) {
        if (p.tagName === "LABEL") { labelText = p.textContent; break; }
        p = p.parentNode;
      }
    }
    const corpus = `${id} ${name} ${placeholder} ${aria} ${labelText}`.toLowerCase();
    if (!regex.test(corpus)) continue;
    inp.focus();
    // Use native value setter so React/Vue see the change
    const proto = Object.getPrototypeOf(inp);
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(inp, value);
    else inp.value = value;
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(300);
    return true;
  }
  return false;
}

// --- Apply flow ---

async function applyToCurrentJob(profilePhone = "") {
  log("apply on", location.href);
  if (isBlocked()) return { outcome: "blocked", detail: "warning page" };

  // Already applied? Indicator shown + no Apply button
  if (/\b(?:applied|submitted resume|application sent)\b/i.test((document.body.innerText || "").substring(0, 4000))) {
    if (!findClickable(/easy apply\b/i)) {
      return { outcome: "already_applied" };
    }
  }

  // Try Easy Apply first (full apply with modal)
  const eaBtn = await waitForClickable(/easy apply\b/i, 6000);
  if (eaBtn) {
    await sleep(800 + Math.random() * 800);
    if (!robustClick(eaBtn, "easy_apply")) {
      return { outcome: "failed", detail: "click strategies failed on Easy Apply" };
    }
    log("clicked Easy Apply, waiting for modal");
    if (!await waitForModalAppear(8000)) {
      return { outcome: "failed", detail: "Easy Apply clicked but modal never appeared" };
    }
    log("modal appeared, walking pages");
    return await walkModal(profilePhone);
  }

  // Fallback: "I'm interested"
  const interested = await waitForClickable(/i.?m interested/i, 2000);
  if (interested) {
    await sleep(800 + Math.random() * 800);
    if (!robustClick(interested, "interested")) {
      return { outcome: "failed", detail: "I'm interested click failed" };
    }
    log("clicked I'm interested (soft-signal)");
    await sleep(1500 + Math.random() * 1500);
    const confirm = findClickable(/^(?:submit|confirm|done|got it)$/i, { preferDialog: true });
    if (confirm) {
      robustClick(confirm, "interested_confirm");
      await sleep(1000);
    }
    return { outcome: "interest_signaled" };
  }

  const dump = dumpClickables();
  log("No Apply or I'm interested. Clickables:", dump);
  return { outcome: "no_easy_apply", detail: JSON.stringify(dump).substring(0, 800) };
}

async function walkModal(profilePhone) {
  for (let pageIdx = 0; pageIdx < MAX_MODAL_PAGES; pageIdx++) {
    await sleep(600 + Math.random() * 800);
    if (isBlocked()) return { outcome: "blocked", detail: `page ${pageIdx}` };

    // Phone fill
    await fillTextInputIfEmpty(/phone|mobile|tel/i, profilePhone);

    // Submit?
    const submit = findClickable(/^submit application$/i, { preferDialog: true });
    if (submit) {
      log(`Submit at page ${pageIdx + 1}`);
      await uncheckFollowCompany();
      await sleep(700 + Math.random() * 600);
      if (!robustClick(submit, "submit")) {
        return { outcome: "failed", detail: "submit click failed", pages: pageIdx + 1 };
      }
      await sleep(2500 + Math.random() * 1500);
      return { outcome: "applied", pages: pageIdx + 1 };
    }

    // Review?
    const review = findClickable(/^review( your application)?$/i, { preferDialog: true });
    if (review) {
      log(`Review at page ${pageIdx + 1}`);
      robustClick(review, "review");
      await sleep(1200 + Math.random() * 800);
      continue;
    }

    // Continue / Next?
    const cont = findClickable(/^(?:continue( to next step)?|next)$/i, { preferDialog: true });
    if (cont) {
      if (hasModalErrors()) {
        log(`Unfilled required at page ${pageIdx + 1}, closing`);
        await closeModal();
        return { outcome: "too_many_questions", detail: `red errors at page ${pageIdx + 1}`, pages: pageIdx + 1 };
      }
      log(`Continue/Next at page ${pageIdx + 1}`);
      robustClick(cont, "continue");
      await sleep(1200 + Math.random() * 800);
      continue;
    }

    log(`No nav button at page ${pageIdx + 1}`, dumpClickables(8));
    return { outcome: "failed", detail: `no nav at p${pageIdx + 1}`, pages: pageIdx + 1 };
  }

  await closeModal();
  return { outcome: "too_many_questions", detail: `>${MAX_MODAL_PAGES} modal pages` };
}

async function closeModal() {
  const close = findClickable(/^(?:dismiss|close)$/i, { preferDialog: true });
  if (close) {
    robustClick(close, "close_modal");
    await sleep(700);
    const discard = findClickable(/^discard$/i);
    if (discard) {
      robustClick(discard, "discard");
      await sleep(500);
    }
  }
}

// Listen for commands from background.js / popup.js
browser.runtime.onMessage.addListener(async (msg) => {
  if (msg.type === "apply_now") {
    return await applyToCurrentJob(msg.phone || "");
  }
});

log("JHP content_script ready on", location.href);
