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

function loadApiConfig() {
  const env = (typeof import.meta !== "undefined" && import.meta.env) || {};

  API_KEY = env.API_KEY || "";
  API_URL = env.API_URL || "http://localhost:8000/api/chat";
  TURNSTILE_SITE_KEY = env.TURNSTILE_SITE_KEY || "";
  TURNSTILE_VERIFY_URL = API_URL.replace(/\/api\/chat$/, "/api/verify-turnstile");
}

function getSessionId() {
  let id = sessionStorage.getItem("session_id");
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem("session_id", id);
  }
  return id;
}

const sessionId = getSessionId();

function appendMessage(role, { text, markdown } = {}) {
  const bubble = document.createElement("div");
  bubble.className = `msg ${role}`;
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
    appendMessage("assistant", { markdown: evt.markdown });
  } else if (evt.type === "error") {
    setStatus("");
    appendMessage("assistant", { text: evt.text });
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
