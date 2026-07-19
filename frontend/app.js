const messagesEl = document.getElementById("messages");
const statusEl = document.getElementById("status");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const appEl = document.getElementById("app");
const gateEl = document.getElementById("turnstile-gate");
const gateErrorEl = document.getElementById("gate-error");
const examplesEl = document.getElementById("examples");

let API_KEY = "";
let API_URL = "";
let TURNSTILE_SITE_KEY = "";
let TURNSTILE_VERIFY_URL = "";
let END_SESSION_URL = "";
let REGENERATE_URL = "";
let SEND_EMAIL_URL = "";

// Mobile browsers report `100vh`/`100dvh` based on the layout viewport, which
// doesn't shrink when the on-screen keyboard opens - only the *visual*
// viewport does. Without this, opening the keyboard pushes the whole fixed-
// height app column upward instead of shrinking it, shoving the chat history
// off-screen above the input. `--app-height` tracks the real visible height
// and wins over the `dvh` fallback in style.css.
//
// Height alone isn't enough on iOS Safari, though: focusing the input also
// makes Safari *pan* the visual viewport down (to keep the focused input
// clear of the keyboard) independently of any document/element scroll -
// `overflow: hidden` on html/body does nothing to stop it, because it isn't a
// scroll the CSSOM sees, it's a shift of which portion of the layout
// viewport is currently visible (`visualViewport.offsetTop`). `.app`/`.gate`
// used to sit in normal document flow, pinned to the *layout* viewport's
// top - so that pan slid them up and out of view by `offsetTop` pixels,
// leaving the header scrolled off-screen and the input row sitting at the
// very top edge under the status bar (the bug this fixes). They're now
// `position: fixed` in style.css and translated by `--app-offset-top` to
// cancel the pan out, keeping them pinned to the *visual* viewport instead.
function updateViewportMetrics() {
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  const offsetTop = vv ? vv.offsetTop : 0;
  document.documentElement.style.setProperty("--app-height", `${height}px`);
  document.documentElement.style.setProperty("--app-offset-top", `${offsetTop}px`);
}

updateViewportMetrics();
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    updateViewportMetrics();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
  // Resize alone misses the case where the pan (offsetTop) changes without a
  // height change - e.g. the user scrolls while the keyboard is still open.
  window.visualViewport.addEventListener("scroll", updateViewportMetrics);
} else {
  window.addEventListener("resize", updateViewportMetrics);
}

function loadApiConfig() {
  const env = (typeof import.meta !== "undefined" && import.meta.env) || {};

  API_KEY = env.API_KEY || "";
  API_URL = env.API_URL || "http://localhost:8000/api/chat";
  TURNSTILE_SITE_KEY = env.TURNSTILE_SITE_KEY || "";
  TURNSTILE_VERIFY_URL = API_URL.replace(/\/api\/chat$/, "/api/verify-turnstile");
  END_SESSION_URL = API_URL.replace(/\/api\/chat$/, "/api/end-session");
  REGENERATE_URL = API_URL.replace(/\/api\/chat$/, "/api/regenerate-plan");
  SEND_EMAIL_URL = API_URL.replace(/\/api\/chat$/, "/api/send-plan-email");
}

function getSessionId() {
  let id = sessionStorage.getItem("session_id");
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem("session_id", id);
  }
  return id;
}

let sessionId = getSessionId();

// Inactivity handling: if the user doesn't reply for a while after the
// assistant asks something, nudge them once, then end the session and start
// a fresh one if they still don't respond. Purely client-side (timers) plus
// a best-effort call to drop the abandoned session's backend state.
const INACTIVITY_NUDGE_MS = 2 * 60 * 1000;
const INACTIVITY_END_MS = 2 * 60 * 1000;
const REPEAT_QUESTION_MAX_LENGTH = 220;
// How long to leave the goodbye message on screen before minting a new
// session_id and re-enabling the input, so it doesn't get wiped before the
// user has a chance to read it.
const SESSION_RESET_DELAY_MS = 4000;
// How long to leave the *old conversation's messages* visible on screen after
// a session ends, in case the user comes back and wants to see them — much
// longer than SESSION_RESET_DELAY_MS, which only governs when the input
// unlocks. Cleared either when this elapses, or immediately when the user
// sends a new message (whichever comes first), so an old and new conversation
// never visually blend together.
const HISTORY_CLEAR_DELAY_MS = 20 * 60 * 1000;
// How long a completed plan (with its action buttons still unclicked) can sit
// idle before the session ends on its own - same outcome as clicking "I'm all
// set", just without a nudge stage first, since there's no pending question
// to nudge about.
const PLAN_IDLE_END_MS = 20 * 60 * 1000;

let inactivityTimer = null;
let historyClearTimer = null;
let lastAssistantText = "";
let planJustCompleted = false;
let isSending = false;

function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

function scheduleInactivityNudge() {
  clearInactivityTimer();
  inactivityTimer = setTimeout(showInactivityNudge, INACTIVITY_NUDGE_MS);
}

function showInactivityNudge() {
  const canRepeat = lastAssistantText && lastAssistantText.length <= REPEAT_QUESTION_MAX_LENGTH;
  const text = canRepeat
    ? `Are you still there? Just checking back in — ${lastAssistantText.replace(/^Great, got it. /, "")}`
    : "Are you still there?";
  appendMessage("assistant", { text, nudge: true });
  inactivityTimer = setTimeout(endSessionDueToInactivity, INACTIVITY_END_MS);
}

// Armed by renderPlanActions() every time the post-plan action buttons are
// shown (initial plan, a regenerated one, or back from the email form via
// Cancel) - reuses the same inactivityTimer slot as the pending-question flow
// above, since a completed plan never schedules that one, so there's no
// conflict between the two.
function schedulePlanIdleEnd() {
  clearInactivityTimer();
  inactivityTimer = setTimeout(finishSession, PLAN_IDLE_END_MS);
}

// Shared tail of every "this conversation is over" path (inactivity timeout,
// the "I'm all set" button, a completed email send) - disables input and
// leaves the final message visible for a moment before minting a fresh
// session, rather than wiping it away in the same tick it was shown.
function teardownAfterSessionEnd() {
  inputEl.disabled = true;
  formEl.querySelector("button").disabled = true;
  setTimeout(startNewSession, SESSION_RESET_DELAY_MS);
}

async function endSessionOnBackend() {
  try {
    await fetch(END_SESSION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch (err) {
    // Best-effort backend cleanup only; the frontend tears down either way.
  }
}

async function endSessionDueToInactivity() {
  clearInactivityTimer();
  appendMessage("assistant", {
    text: "Looks like something might have come up on your end. Bye for now — see you next time!",
    nudge: true,
  });
  await endSessionOnBackend();
  teardownAfterSessionEnd();
}

function clearHistoryClearTimer() {
  if (historyClearTimer) {
    clearTimeout(historyClearTimer);
    historyClearTimer = null;
  }
}

function clearMessageHistory() {
  clearHistoryClearTimer();
  messagesEl.innerHTML = "";
  examplesEl.hidden = false;
}

function startNewSession() {
  sessionStorage.removeItem("session_id");
  sessionId = getSessionId();
  lastAssistantText = "";
  setStatus("");
  inputEl.disabled = false;
  formEl.querySelector("button").disabled = false;
  inputEl.focus();

  // Old messages stay visible for a while rather than being wiped immediately.
  clearHistoryClearTimer();
  historyClearTimer = setTimeout(clearMessageHistory, HISTORY_CLEAR_DELAY_MS);
}

function appendMessage(role, { text, markdown, nudge } = {}) {
  const bubble = document.createElement("div");
  bubble.className = `msg ${role}`;
  if (nudge) bubble.classList.add("nudge");
  if (markdown) {
    bubble.classList.add("plan");
    bubble.innerHTML = marked.parse(markdown);
  } else {
    bubble.textContent = text;
  }
  messagesEl.appendChild(bubble);
  if (markdown) {
    // Plans can be taller than the viewport - scroll so the top of the new
    // plan bubble is visible instead of jumping straight to its bottom
    // (which scrollTop = scrollHeight would do), so small screens don't
    // land mid-plan with the summary already scrolled out of view.
    bubble.scrollIntoView({ behavior: "smooth", block: "start" });
  } else {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  return bubble;
}

// If a real status message (e.g. "Checking trails...") sits unchanged for a
// while - the graph can spend several seconds in a single node, e.g. an LLM
// call or the trail-retry loop - swap in a rotating carousel of generic
// "still here" filler messages so the status line doesn't look frozen.
// Cleared and restarted every time a new real status arrives; fully cleared
// (no filler scheduled) when the status is blanked out at turn end.
const FILLER_MESSAGES = [
  "Just a moment...",
  "Still working on it...",
  "Almost there...",
  "Thanks for your patience...",
  "Be right with you...",
];
const FILLER_START_DELAY_MS = 4000;
const FILLER_ROTATE_MS = 3500;

let fillerStartTimer = null;
let fillerRotateTimer = null;
let fillerIndex = 0;

function clearFillerTimers() {
  if (fillerStartTimer) {
    clearTimeout(fillerStartTimer);
    fillerStartTimer = null;
  }
  if (fillerRotateTimer) {
    clearInterval(fillerRotateTimer);
    fillerRotateTimer = null;
  }
}

function rotateFillerMessage() {
  statusEl.textContent = FILLER_MESSAGES[fillerIndex % FILLER_MESSAGES.length];
  fillerIndex += 1;
}

function setStatus(text) {
  clearFillerTimers();
  statusEl.textContent = text || "";
  statusEl.classList.toggle("active", Boolean(text));

  if (text) {
    fillerIndex = 0;
    fillerStartTimer = setTimeout(() => {
      rotateFillerMessage();
      fillerRotateTimer = setInterval(rotateFillerMessage, FILLER_ROTATE_MS);
    }, FILLER_START_DELAY_MS);
  }
}

// Shared by sendMessage() and regeneratePlan() - POSTs `body` to `url`, reads
// the streamed NDJSON response line-by-line, and dispatches each line through
// handleEvent. Throws an Error (with `isHttpError: true` for a non-2xx
// response) on failure so callers can report it their own way.
async function streamRequest(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const err = new Error(`Request failed (HTTP ${resp.status}).`);
    err.isHttpError = true;
    throw err;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.trim()) continue;
      handleEvent(JSON.parse(line));
    }
  }

  if (buffer.trim()) {
    handleEvent(JSON.parse(buffer));
  }
}

async function sendMessage(text) {
  if (isSending) return;
  isSending = true;
  examplesEl.hidden = true;
  clearInactivityTimer();
  planJustCompleted = false;
  if (historyClearTimer) {
    // A prior session ended and its messages were just left on screen for
    // reference; now that the user is actually chatting again, clear them so
    // the old and new conversations don't visually blend together.
    clearMessageHistory();
  }

  if (!API_KEY) {
    appendMessage("assistant", {
      text: "Missing API key. Set API_KEY in environment or .env.",
    });
    isSending = false;
    return;
  }

  appendMessage("user", { text });
  setStatus("Thinking...");
  // Deliberately don't disable/blur inputEl itself here: disabling a
  // focused element closes the mobile virtual keyboard immediately, then
  // re-focusing it in the `finally` block below reopens it - two
  // viewport-resize jumps per turn on iOS Safari. Only the button is
  // disabled (to block duplicate taps); `isSending` above guards against a
  // duplicate submit via the keyboard's "Go"/"Send" key while the input
  // stays enabled and focused throughout the request.
  formEl.querySelector("button").disabled = true;

  try {
    await streamRequest(API_URL, { session_id: sessionId, message: text });
  } catch (err) {
    setStatus("");
    appendMessage("assistant", {
      text: err.isHttpError ? err.message : "Sorry, something went wrong reaching the server.",
    });
  } finally {
    formEl.querySelector("button").disabled = false;
    isSending = false;
    if (planJustCompleted) {
      // A completed hike plan is the end of this request, not a pending
      // question - keeping/reopening the keyboard would cover the plan the
      // user just asked to read.
      inputEl.blur();
    } else {
      // Input was never blurred, so this is a no-op on most browsers, but
      // cheap insurance in case something else (e.g. tapping the send
      // button) stole focus during the request.
      inputEl.focus();
    }
  }
}

function handleEvent(evt) {
  if (evt.type === "status") {
    setStatus(evt.text);
    // Backend "status" events (as opposed to the client-set "Thinking..."
    // placeholder set synchronously in sendMessage()) only ever fire from
    // search_qdrant/check_weather/check_trail/generate_plan - i.e. once
    // slot-filling is done and the graph has everything it needs and is
    // working autonomously for the rest of this turn. There's no reason to
    // keep the keyboard open through that (possibly multi-second) work, so
    // blur here instead of waiting for the final event right before the
    // plan renders. If the turn actually ends up needing another answer
    // (e.g. the weather check rejects the date), sendMessage()'s `finally`
    // block already refocuses since plan_complete will be false.
    inputEl.blur();
  } else if (evt.type === "final") {
    setStatus("");
    lastAssistantText = evt.markdown || "";
    const bubble = appendMessage("assistant", { markdown: evt.markdown });
    if (evt.plan_complete) {
      // The hike plan is done - there's no pending question to nudge about,
      // so don't arm the "Are you still there?" timer. renderPlanActions()
      // arms its own idle-end timer instead (see schedulePlanIdleEnd()).
      planJustCompleted = true;
      renderPlanActions(bubble, evt.regenerate_remaining);
    } else {
      scheduleInactivityNudge();
    }
  } else if (evt.type === "error") {
    setStatus("");
    lastAssistantText = evt.text || "";
    appendMessage("assistant", { text: evt.text });
    scheduleInactivityNudge();
  }
}

// --- Post-plan action buttons: email / regenerate / done ---------------

let currentRegenerateRemaining = 0;

function removeExistingPlanActions() {
  const existing = document.getElementById("plan-actions");
  if (existing) existing.remove();
}

function makeActionButton(label, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "plan-action-btn";
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function renderPlanActions(afterBubble, regenerateRemaining) {
  removeExistingPlanActions();
  currentRegenerateRemaining = regenerateRemaining || 0;

  const row = document.createElement("div");
  row.id = "plan-actions";
  row.className = "plan-actions";

  row.appendChild(makeActionButton("📧 Email me this plan", () => startEmailFlow(row)));

  if (regenerateRemaining > 0) {
    row.appendChild(makeActionButton("🔄 Not quite — show me another", () => regeneratePlan(row)));
  }

  row.appendChild(makeActionButton("✅ I'm all set, thanks!", () => finishSession()));

  afterBubble.insertAdjacentElement("afterend", row);
  schedulePlanIdleEnd();
}

function startEmailFlow(row) {
  clearInactivityTimer();
  row.innerHTML = "";

  const form = document.createElement("form");
  form.className = "email-form";

  const emailInput = document.createElement("input");
  emailInput.type = "email";
  emailInput.placeholder = "Enter your email here";
  emailInput.required = true;
  emailInput.autocomplete = "email";

  const sendBtn = document.createElement("button");
  sendBtn.type = "submit";
  sendBtn.textContent = "Send";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.textContent = "Cancel";
  cancelBtn.className = "email-form-cancel";
  cancelBtn.addEventListener("click", () => {
    renderPlanActions(row.previousElementSibling, currentRegenerateRemaining);
  });

  const errorEl = document.createElement("p");
  errorEl.className = "plan-action-error";

  form.appendChild(emailInput);
  form.appendChild(sendBtn);
  form.appendChild(cancelBtn);
  row.appendChild(form);
  row.appendChild(errorEl);
  emailInput.focus();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    emailInput.disabled = true;
    sendBtn.disabled = true;
    cancelBtn.disabled = true;
    sendBtn.textContent = "Sending...";

    try {
      const resp = await fetch(SEND_EMAIL_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({ session_id: sessionId, email: emailInput.value.trim() }),
      });
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok) {
        throw new Error(data.detail || "Couldn't send the email. Please try again.");
      }

      row.remove();
      appendMessage("assistant", {
        text: "✅ Sent! Check your inbox — and your spam folder, just in case.",
      });
      await endSessionOnBackend();
      teardownAfterSessionEnd();
    } catch (err) {
      const reason = err.message || "Couldn't send the email.";
      errorEl.textContent = `${reason} Please re-enter your email address and try again.`;
      emailInput.value = "";
      emailInput.disabled = false;
      sendBtn.disabled = false;
      cancelBtn.disabled = false;
      sendBtn.textContent = "Send";
      emailInput.focus();
    }
  });
}

async function regeneratePlan(row) {
  // Remove immediately rather than just disabling - if this regenerate
  // attempt exhausts all candidates (plan_complete: false), handleEvent()
  // won't call renderPlanActions() again, so a merely-disabled row would be
  // left stuck on screen forever.
  row.remove();
  clearInactivityTimer();
  planJustCompleted = false;
  appendMessage("user", { text: "Show me a different trail" });
  setStatus("Looking for another option...");

  try {
    await streamRequest(REGENERATE_URL, { session_id: sessionId });
  } catch (err) {
    setStatus("");
    appendMessage("assistant", {
      text: err.isHttpError ? err.message : "Sorry, something went wrong reaching the server.",
    });
  } finally {
    if (planJustCompleted) {
      inputEl.blur();
    } else {
      inputEl.focus();
    }
  }
}

// Doubles as the plan-idle-end timeout callback (schedulePlanIdleEnd()) and
// the "I'm all set" button handler - both end the session the same way, so
// there's no `row` param; it just removes whatever action row is currently shown.
function finishSession() {
  removeExistingPlanActions();
  clearInactivityTimer();
  appendMessage("assistant", { text: "Glad I could help — happy hiking! 🥾" });
  endSessionOnBackend();
  teardownAfterSessionEnd();
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isSending) return;
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  // Tapping the on-screen Send button (rather than the keyboard's own "Go"/
  // "Send" key) can steal focus to the button in some browsers, closing the
  // virtual keyboard. Re-focus synchronously, still inside this user-gesture
  // handler, so iOS Safari treats it as gesture-connected and keeps/reopens
  // the keyboard - a `.focus()` called later from an async response handler
  // isn't reliably treated the same way.
  inputEl.focus();
  sendMessage(text);
});

inputEl.addEventListener("focus", () => {
  // Give the keyboard's open animation (and the resulting visualViewport
  // resize) a moment to finish before re-checking scroll position.
  setTimeout(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }, 300);
});

document.querySelectorAll(".example-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    sendMessage(chip.textContent);
  });
});

function setGateError(text) {
  gateErrorEl.textContent = text || "";
}

function unlockApp() {
  gateEl.hidden = true;
  appEl.hidden = false;
  runApp();
}

async function verifyTurnstileToken(token) {
  setGateError("");
  try {
    const resp = await fetch(TURNSTILE_VERIFY_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
      },
      body: JSON.stringify({ token }),
    });
    const data = await resp.json().catch(() => ({}));

    if (resp.ok && data.success) {
      sessionStorage.setItem("human_verified", "true");
      unlockApp();
    } else {
      setGateError("Verification failed. Please try again.");
      if (window.turnstile) window.turnstile.reset();
    }
  } catch (err) {
    setGateError("Could not reach the server to verify. Please try again.");
    if (window.turnstile) window.turnstile.reset();
  }
}

// Called by the Cloudflare Turnstile script once it has loaded (see the
// `?onload=onTurnstileLoad` query param on its <script> tag in index.html).
window.onTurnstileLoad = function () {
  if (sessionStorage.getItem("human_verified") === "true") {
    unlockApp();
    return;
  }

  if (!TURNSTILE_SITE_KEY) {
    setGateError("Configuration error: Turnstile site key was not found.");
    return;
  }

  window.turnstile.render("#turnstile-container", {
    sitekey: TURNSTILE_SITE_KEY,
    callback: verifyTurnstileToken,
    "error-callback": () => setGateError("Verification failed. Please try again."),
    "expired-callback": () => setGateError("Verification expired. Please try again."),
  });
};

function runApp() {
  if (!API_KEY) {
    setStatus("Configuration error");
    appendMessage("assistant", {
      text: "Startup config error: API key was not found. Set API_KEY in environment or .env.",
    });
  }
}

loadApiConfig();
