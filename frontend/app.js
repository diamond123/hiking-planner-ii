const messagesEl = document.getElementById("messages");
const statusEl = document.getElementById("status");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const appEl = document.getElementById("app");
const gateEl = document.getElementById("turnstile-gate");
const gateErrorEl = document.getElementById("gate-error");

let API_KEY = "";
let API_URL = "";
let TURNSTILE_SITE_KEY = "";
let TURNSTILE_VERIFY_URL = "";
let END_SESSION_URL = "";

function loadApiConfig() {
  const env = (typeof import.meta !== "undefined" && import.meta.env) || {};

  API_KEY = env.API_KEY || "";
  API_URL = env.API_URL || "http://localhost:8000/api/chat";
  TURNSTILE_SITE_KEY = env.TURNSTILE_SITE_KEY || "";
  TURNSTILE_VERIFY_URL = API_URL.replace(/\/api\/chat$/, "/api/verify-turnstile");
  END_SESSION_URL = API_URL.replace(/\/api\/chat$/, "/api/end-session");
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

let inactivityTimer = null;
let historyClearTimer = null;
let lastAssistantText = "";

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

async function endSessionDueToInactivity() {
  clearInactivityTimer();
  appendMessage("assistant", {
    text: "Looks like something might have come up on your end. Bye for now — see you next time!",
    nudge: true,
  });
  inputEl.disabled = true;
  formEl.querySelector("button").disabled = true;

  try {
    await fetch(END_SESSION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch (err) {
    // Best-effort backend cleanup only; still start a fresh session locally either way.
  }

  // Leave the goodbye message visible for a moment before resetting, instead
  // of wiping it away in the same tick it was shown.
  setTimeout(startNewSession, SESSION_RESET_DELAY_MS);
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
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function setStatus(text) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("active", Boolean(text));
}

async function sendMessage(text) {
  clearInactivityTimer();
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
    return;
  }

  appendMessage("user", { text });
  setStatus("Thinking...");
  inputEl.disabled = true;
  formEl.querySelector("button").disabled = true;

  try {
    const resp = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
      },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });

    if (!resp.ok) {
      setStatus("");
      appendMessage("assistant", { text: `Request failed (HTTP ${resp.status}).` });
      return;
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
  } catch (err) {
    setStatus("");
    appendMessage("assistant", { text: "Sorry, something went wrong reaching the server." });
  } finally {
    inputEl.disabled = false;
    formEl.querySelector("button").disabled = false;
    inputEl.focus();
  }
}

function handleEvent(evt) {
  if (evt.type === "status") {
    setStatus(evt.text);
  } else if (evt.type === "final") {
    setStatus("");
    lastAssistantText = evt.markdown || "";
    appendMessage("assistant", { markdown: evt.markdown });
    scheduleInactivityNudge();
  } else if (evt.type === "error") {
    setStatus("");
    lastAssistantText = evt.text || "";
    appendMessage("assistant", { text: evt.text });
    scheduleInactivityNudge();
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  sendMessage(text);
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
