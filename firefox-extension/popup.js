// Save inputs to storage so they persist
async function loadSaved() {
  const v = await browser.storage.local.get(["phone", "limit"]);
  if (v.phone) document.getElementById("phone").value = v.phone;
  if (v.limit) document.getElementById("limit").value = v.limit;
}
loadSaved();

document.getElementById("start").addEventListener("click", async () => {
  const limit = parseInt(document.getElementById("limit").value, 10) || 1;
  const phone = document.getElementById("phone").value.trim();
  await browser.storage.local.set({ phone, limit });

  const status = document.getElementById("status");
  status.textContent = "running…";
  status.className = "stats";

  try {
    const result = await browser.runtime.sendMessage({
      type: "start_batch",
      limit,
      phone,
    });
    if (result.error) {
      status.textContent = "Error: " + result.error;
      status.className = "stats err";
    } else {
      const s = result.stats || {};
      status.textContent = `Done: ${s.applied || 0} applied, ${s.no_ea || 0} no-EA, ${s.too_many || 0} skipped, ${s.failed || 0} failed${s.blocked ? ", BLOCKED!" : ""}`;
      status.className = "stats ok";
    }
  } catch (e) {
    status.textContent = "Crashed: " + e.message;
    status.className = "stats err";
  }
});

// "Fill THIS page" — sends fill_this_page to the active tab's content
// script. Works on ANY URL (manifest <all_urls>): real Firefox session
// means no Cloudflare/Turnstile detection, no automation flag.
document.getElementById("fillpage").addEventListener("click", async () => {
  const status = document.getElementById("status");
  status.textContent = "filling this page…";
  status.className = "stats";
  try {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error("no active tab");
    const result = await browser.tabs.sendMessage(tab.id, { type: "fill_this_page" });
    if (!result) {
      status.textContent = "no response from content script";
      status.className = "stats err";
      return;
    }
    if (result.outcome === "applied") {
      status.textContent = "✅ " + (result.detail || "submitted");
      status.className = "stats ok";
    } else {
      status.textContent = "✗ " + (result.detail || "failed");
      status.className = "stats err";
    }
  } catch (e) {
    status.textContent = "Crashed: " + e.message;
    status.className = "stats err";
  }
});
