// JHP Easy Apply — content script
// Strategy: find buttons by VISIBLE TEXT / ARIA-LABEL, not by CSS classes.
// LinkedIn rotates class names weekly; text & aria are stable.

const log = (...args) => console.log("[JHP]", ...args);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MAX_MODAL_PAGES = 4;

const RATE_LIMIT_MARKERS = [
  "let's confirm it's really you",
  "we've restricted your account",
  "unusual activity",
  "complete this puzzle",
];

/** Find first VISIBLE button whose text or aria-label matches regex */
function findButton(regex, root = document) {
  for (const b of root.querySelectorAll("button")) {
    if (b.offsetParent === null) continue;        // hidden
    if (b.disabled) continue;
    const text = (b.textContent || "").trim();
    const aria = (b.getAttribute("aria-label") || "").trim();
    if (regex.test(text) || regex.test(aria)) return b;
  }
  return null;
}

/** Wait up to timeoutMs for a button matching regex */
async function waitForButton(regex, timeoutMs = 4000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const b = findButton(regex);
    if (b) return b;
    await sleep(180);
  }
  return null;
}

/** Find first VISIBLE element matching CSS selector */
function findVisible(sel) {
  for (const el of document.querySelectorAll(sel)) {
    if (el.offsetParent !== null) return el;
  }
  return null;
}

function isBlocked() {
  const text = (document.body.innerText || "").toLowerCase().substring(0, 4000);
  return RATE_LIMIT_MARKERS.some((m) => text.includes(m));
}

function dumpButtons(limit = 15) {
  return Array.from(document.querySelectorAll("button"))
    .filter((b) => b.offsetParent !== null && !b.disabled)
    .slice(0, limit)
    .map((b) => ({
      text: (b.textContent || "").trim().substring(0, 50),
      aria: (b.getAttribute("aria-label") || "").substring(0, 80),
    }));
}

async function uncheckFollowCompany() {
  // Find a checkbox related to "Follow ... company"
  for (const cb of document.querySelectorAll("input[type='checkbox']")) {
    const lbl = cb.closest("label") || document.querySelector(`label[for='${cb.id}']`);
    const lblText = lbl ? lbl.textContent.toLowerCase() : "";
    if (/follow.*compan/i.test(lblText) && cb.checked) {
      lbl.click();
      await sleep(250);
      return;
    }
  }
}

async function fillTextInputIfEmpty(regex, value) {
  if (!value) return;
  for (const inp of document.querySelectorAll("input[type='text'], input[type='tel'], input:not([type])")) {
    if (inp.offsetParent === null) continue;
    if (inp.value && inp.value.trim()) continue;
    const id = inp.id || "";
    const name = inp.name || "";
    const placeholder = inp.placeholder || "";
    const aria = inp.getAttribute("aria-label") || "";
    const labelEl = document.querySelector(`label[for='${id}']`);
    const labelText = labelEl ? labelEl.textContent : "";
    const corpus = `${id} ${name} ${placeholder} ${aria} ${labelText}`.toLowerCase();
    if (regex.test(corpus)) {
      inp.focus();
      for (const ch of value) {
        inp.value += ch;
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        await sleep(40 + Math.random() * 50);
      }
      inp.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(300);
      return true;
    }
  }
  return false;
}

async function applyToCurrentJob(profilePhone = "") {
  log("apply on", location.href);

  if (isBlocked()) {
    return { outcome: "blocked", detail: "warning page" };
  }

  // Already applied? LinkedIn shows a "Applied" pill or "Submitted resume"
  if (/\b(?:applied|submitted resume|application sent)\b/i.test(document.body.innerText.substring(0, 4000))) {
    // But only if the Easy Apply button is missing (otherwise it's just historic data elsewhere)
    const reapply = findButton(/^easy apply/i);
    if (!reapply) {
      log("Already applied indicator + no Apply btn");
      return { outcome: "already_applied" };
    }
  }

  // Find Easy Apply button by text/aria
  const eaBtn = await waitForButton(/easy apply\b/i, 6000);
  if (!eaBtn) {
    const dump = dumpButtons();
    log("No Easy Apply. Buttons:", dump);
    return { outcome: "no_easy_apply", detail: JSON.stringify(dump).substring(0, 800) };
  }

  await sleep(800 + Math.random() * 800);
  eaBtn.click();
  log("clicked Easy Apply");
  await sleep(1500 + Math.random() * 1500);

  // Walk modal pages
  for (let pageIdx = 0; pageIdx < MAX_MODAL_PAGES; pageIdx++) {
    if (isBlocked()) return { outcome: "blocked", detail: `page ${pageIdx}` };

    // Try to fill phone if present + empty
    await fillTextInputIfEmpty(/phone|mobile|tel/i, profilePhone);

    // Submit available? Last page.
    const submit = findButton(/^submit application$/i);
    if (submit) {
      log(`Submit at page ${pageIdx + 1}`);
      await uncheckFollowCompany();
      await sleep(700 + Math.random() * 600);
      submit.click();
      await sleep(2500 + Math.random() * 1500);
      return { outcome: "applied", pages: pageIdx + 1 };
    }

    // Review available? Pre-final page.
    const review = findButton(/^review( your application)?$/i);
    if (review) {
      log(`Review at page ${pageIdx + 1}`);
      review.click();
      await sleep(1200 + Math.random() * 800);
      continue;
    }

    // Continue / Next
    const cont = findButton(/^(?:continue( to next step)?|next)$/i);
    if (cont) {
      // Check for unfilled red error fields
      const errs = Array.from(document.querySelectorAll("[role='alert'], .artdeco-inline-feedback--error"))
        .filter((e) => e.offsetParent !== null && (e.textContent || "").trim());
      if (errs.length > 0) {
        log(`Unfilled required at page ${pageIdx + 1}, closing`);
        await closeModal();
        return {
          outcome: "too_many_questions",
          detail: `unfilled: ${errs.slice(0, 3).map((e) => e.textContent.trim().substring(0, 60)).join(" | ")}`,
        };
      }
      log(`Continue at page ${pageIdx + 1}`);
      cont.click();
      await sleep(1200 + Math.random() * 800);
      continue;
    }

    log(`No nav button at page ${pageIdx + 1}`);
    const dump = dumpButtons(10);
    return { outcome: "failed", detail: `no nav at p${pageIdx + 1}: ${JSON.stringify(dump).substring(0, 500)}` };
  }

  await closeModal();
  return { outcome: "too_many_questions", detail: `>${MAX_MODAL_PAGES} modal pages` };
}

async function closeModal() {
  const close = findButton(/^(?:dismiss|close)$/i) || findVisible("button[aria-label='Dismiss']");
  if (close) {
    close.click();
    await sleep(700);
    const discard = findButton(/^discard$/i);
    if (discard) {
      discard.click();
      await sleep(500);
    }
  }
}

// Listen for commands
browser.runtime.onMessage.addListener(async (msg) => {
  if (msg.type === "apply_now") {
    return await applyToCurrentJob(msg.phone || "");
  }
});

log("JHP content_script ready on", location.href);
