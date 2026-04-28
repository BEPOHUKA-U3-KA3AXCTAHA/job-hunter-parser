// JHP Easy Apply — background script
// Polls local Python API for next job, opens it in active tab, asks content
// script to apply, posts result back. Conservative pacing built in.

const API_BASE = "http://localhost:8765";
const MIN_GAP_MS = 90 * 1000;       // 90s between applies
const MAX_PER_BATCH = 5;
const MAX_PER_DAY = 30;

let running = false;
let lastApplyAt = 0;

async function fetchNextJob() {
  try {
    const r = await fetch(`${API_BASE}/next-job`);
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    console.error("[JHP] API fetch failed:", e);
    return null;
  }
}

async function postResult(jobId, result) {
  try {
    await fetch(`${API_BASE}/apply-result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, ...result }),
    });
  } catch (e) {
    console.error("[JHP] result POST failed:", e);
  }
}

async function todayCount() {
  try {
    const r = await fetch(`${API_BASE}/stats`);
    if (!r.ok) return 0;
    const j = await r.json();
    return j.applied_today || 0;
  } catch (e) {
    return 0;
  }
}

/** Given a job URL, navigate active tab to it, wait, message content script */
async function applyOne(job, phone) {
  return new Promise(async (resolve) => {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (!tab) return resolve({ outcome: "failed", detail: "no active tab" });

    // Navigate
    await browser.tabs.update(tab.id, { url: job.url });

    // Wait for tab to finish loading
    const onUpdated = (tabId, info) => {
      if (tabId === tab.id && info.status === "complete") {
        browser.tabs.onUpdated.removeListener(onUpdated);
        // Wait extra for LinkedIn to render
        setTimeout(async () => {
          try {
            const result = await browser.tabs.sendMessage(tab.id, {
              type: "apply_now",
              phone,
            });
            resolve(result || { outcome: "failed", detail: "no response" });
          } catch (e) {
            resolve({ outcome: "failed", detail: e.message });
          }
        }, 4000);
      }
    };
    browser.tabs.onUpdated.addListener(onUpdated);
  });
}

async function runBatch(limit, phone) {
  if (running) return { error: "already_running" };
  running = true;
  console.log("[JHP] Starting batch, limit=", limit);

  const stats = { applied: 0, already: 0, no_ea: 0, too_many: 0, failed: 0, blocked: 0 };
  const todayBefore = await todayCount();
  const headroom = Math.max(0, MAX_PER_DAY - todayBefore);
  const effectiveLimit = Math.min(limit, MAX_PER_BATCH, headroom);

  if (effectiveLimit === 0) {
    running = false;
    return { error: "daily_cap", today: todayBefore };
  }

  for (let i = 0; i < effectiveLimit; i++) {
    const job = await fetchNextJob();
    if (!job || !job.url) {
      console.log("[JHP] No more jobs in queue");
      break;
    }
    console.log(`[JHP] [${i + 1}/${effectiveLimit}] ${job.title || job.url}`);

    // Pace: ensure at least MIN_GAP_MS since last
    const sinceLast = Date.now() - lastApplyAt;
    if (sinceLast < MIN_GAP_MS && i > 0) {
      const wait = MIN_GAP_MS - sinceLast + Math.floor(Math.random() * 30000);
      console.log(`[JHP] Waiting ${Math.round(wait / 1000)}s for pacing`);
      await new Promise((r) => setTimeout(r, wait));
    }

    const result = await applyOne(job, phone);
    lastApplyAt = Date.now();
    await postResult(job.id, result);

    const o = result.outcome || "failed";
    if (o === "applied") stats.applied++;
    else if (o === "already_applied") stats.already++;
    else if (o === "no_easy_apply") stats.no_ea++;
    else if (o === "too_many_questions") stats.too_many++;
    else if (o === "blocked") {
      stats.blocked++;
      console.error("[JHP] BLOCKED — stopping batch");
      break;
    } else stats.failed++;
  }

  running = false;
  console.log("[JHP] Batch done", stats);
  return { stats };
}

// Popup talks to background
browser.runtime.onMessage.addListener((msg) => {
  if (msg.type === "start_batch") {
    return runBatch(msg.limit || 1, msg.phone || "");
  }
  if (msg.type === "status") {
    return Promise.resolve({ running, lastApplyAt });
  }
});
