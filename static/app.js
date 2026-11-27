"use strict";

/* ---------- Theme toggle ----------
   The saved choice is applied by a tiny script in <head> before paint.
   Without a saved choice the page follows the operating system setting. */
function currentTheme() {
  const saved = document.documentElement.dataset.theme;
  if (saved) return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function labelThemeToggle(button) {
  const next = currentTheme() === "dark" ? "light" : "dark";
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.title = `Switch to ${next} theme`;
}

document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  labelThemeToggle(button);
  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* storage may be blocked */ }
    labelThemeToggle(button);
  });
});

/* ---------- Copy buttons (API key page and code samples) ---------- */
function showCopyStatus(message) {
  const status = document.querySelector("[data-copy-status]");
  if (status) status.textContent = message;
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const temporary = document.createElement("textarea");
  temporary.value = value;
  temporary.setAttribute("readonly", "");
  temporary.style.position = "fixed";
  temporary.style.opacity = "0";
  document.body.appendChild(temporary);
  temporary.select();
  const copied = document.execCommand("copy");
  temporary.remove();
  if (!copied) throw new Error("Copy is not available in this browser.");
}

document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy-target]");
  if (copyButton) {
    const target = document.getElementById(copyButton.dataset.copyTarget);
    if (!target) return;
    try {
      await copyText(target.value);
      showCopyStatus("API key copied to clipboard.");
    } catch {
      showCopyStatus("Copy failed. Select and copy the key manually.");
    }
    return;
  }

  const codeButton = event.target.closest("[data-copy-code]");
  if (codeButton) {
    const code = codeButton.parentElement.querySelector("code");
    if (!code) return;
    const original = codeButton.textContent;
    try {
      await copyText(code.textContent);
      codeButton.textContent = "Copied";
    } catch {
      codeButton.textContent = "Copy failed";
    }
    window.setTimeout(() => { codeButton.textContent = original; }, 1800);
  }
});

/* ---------- Forms: confirmation and loading state ---------- */
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const confirmation = form.dataset.confirm;
  if (confirmation && !window.confirm(confirmation)) {
    event.preventDefault();
    return;
  }
  if (!form.matches("[data-loading-form]")) return;
  const submitter = event.submitter;
  if (!(submitter instanceof HTMLButtonElement)) return;
  submitter.disabled = true;
  const label = submitter.querySelector(".button-label");
  const loading = submitter.querySelector(".button-loading");
  if (label) label.hidden = true;
  if (loading) loading.hidden = false;
});

/* ---------- Admin tabs (keyboard: arrows, Home, End) ---------- */
document.querySelectorAll('[role="tablist"] [role="tab"]').forEach((tab) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = Array.from(tab.parentElement.querySelectorAll('[role="tab"]'));
    const currentIndex = tabs.indexOf(tab);
    const nextIndex = event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
      : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[nextIndex]);
    tabs[nextIndex].focus();
  });
});

function selectTab(selectedTab) {
  const tablist = selectedTab.parentElement;
  tablist.querySelectorAll('[role="tab"]').forEach((tab) => {
    const isSelected = tab === selectedTab;
    tab.setAttribute("aria-selected", String(isSelected));
    tab.tabIndex = isSelected ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) panel.hidden = !isSelected;
  });
}

/* ---------- Recent parcels ----------
   The last few tracking IDs found are kept in this browser's localStorage
   (nothing is sent to the server). Storage can be blocked, so every access
   is wrapped and the page works without it. */
const RECENT_KEY = "recentParcels";
const RECENT_MAX = 5;

function readRecent() {
  try {
    const saved = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    return saved.filter((id) => typeof id === "string" && id.length <= 80).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

function writeRecent(ids) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(ids)); } catch { /* storage blocked */ }
}

function renderRecent() {
  const section = document.querySelector("[data-recent-parcels]");
  const list = document.querySelector("[data-recent-list]");
  if (!section || !list) return;
  const current = document.querySelector("[data-assistant-form]")?.dataset.trackingId;
  const ids = readRecent();
  list.replaceChildren(...ids.map((id) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.className = "tracking-code";
    link.href = `/?tracking_id=${encodeURIComponent(id)}`;
    link.textContent = id;
    if (id === current) link.setAttribute("aria-current", "true");
    item.appendChild(link);
    return item;
  }));
  section.hidden = ids.length === 0;
}

function rememberParcel(id) {
  if (!id) return;
  writeRecent([id, ...readRecent().filter((saved) => saved !== id)].slice(0, RECENT_MAX));
  renderRecent();
}

document.querySelector("[data-recent-clear]")?.addEventListener("click", () => {
  writeRecent([]);
  renderRecent();
});
// A parcel shown on page load (search or link) counts as found.
rememberParcel(document.querySelector("[data-assistant-form]")?.dataset.trackingId);
renderRecent();

/* ---------- Chat ----------
   The browser keeps the conversation and sends the last few turns with
   each question, so the server stays stateless. All text from the user
   and the assistant is inserted with textContent, never as HTML. */
const MAX_HISTORY_TURNS = 10;

function timeLabel(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function setTime(element, date) {
  element.dateTime = date.toISOString();
  element.textContent = timeLabel(date);
}

document.querySelectorAll("[data-time-now]").forEach((element) => setTime(element, new Date()));

function scrollToEnd(log) {
  // Scroll only the message list, never the whole page.
  log.scrollTop = log.scrollHeight;
}

function appendMessage(log, text, role, isError = false) {
  const message = document.createElement("div");
  message.className = `msg msg-${role}${isError ? " msg-error" : ""}`;
  const bubble = document.createElement("p");
  bubble.className = "bubble";
  bubble.textContent = text;
  const time = document.createElement("time");
  time.className = "msg-time";
  setTime(time, new Date());
  message.append(bubble, time);
  log.appendChild(message);
  scrollToEnd(log);
  return message;
}

function appendTyping(log) {
  const message = document.createElement("div");
  message.className = "msg msg-assistant msg-typing";
  const bubble = document.createElement("p");
  bubble.className = "bubble";
  const label = document.createElement("span");
  label.className = "sr-only";
  label.textContent = "Assistant is checking the tracking records";
  bubble.appendChild(label);
  for (let i = 0; i < 3; i += 1) {
    const dot = document.createElement("span");
    dot.className = "typing-dot";
    dot.setAttribute("aria-hidden", "true");
    bubble.appendChild(dot);
  }
  message.appendChild(bubble);
  log.appendChild(message);
  scrollToEnd(log);
  return message;
}

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
}

document.querySelectorAll("[data-assistant-form]").forEach((form) => {
  const log = document.querySelector("[data-chat-log]");
  const status = document.querySelector("[data-assistant-status]");
  const panel = document.querySelector("[data-parcel-panel]");
  const input = form.querySelector("textarea[name='question']");
  const button = form.querySelector("button[type='submit']");
  const history = [];
  if (!log || !input || !button) return;

  input.addEventListener("input", () => autoGrow(input));
  // Enter sends; Shift+Enter adds a new line.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  document.querySelectorAll("[data-question]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (button.disabled) return;
      input.value = chip.dataset.question;
      form.requestSubmit();
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || button.disabled) return;
    if (status) status.textContent = "";
    appendMessage(log, question, "user");
    input.value = "";
    autoGrow(input);
    button.disabled = true;
    const typing = appendTyping(log);
    try {
      const response = await fetch("/ui/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          tracking_id: form.dataset.trackingId || null,
          question,
          history: history.slice(-MAX_HISTORY_TURNS),
        }),
      });
      const result = await response.json();
      typing.remove();
      if (!response.ok || !result.success) {
        appendMessage(log, result.error?.message || "The assistant is unavailable. Please try again.", "assistant", true);
        return;
      }
      const data = result.data;
      appendMessage(log, data.answer, "assistant");
      history.push({ role: "user", text: question }, { role: "assistant", text: data.answer });
      // Keep the side panel on the parcel the conversation is about. The panel
      // HTML is rendered and escaped by the server's template.
      if (data.tracking_id && data.tracking_id !== form.dataset.trackingId) {
        form.dataset.trackingId = data.tracking_id;
        if (panel && data.panel_html) panel.innerHTML = data.panel_html;
        const searchInput = document.getElementById("tracking_id");
        if (searchInput) searchInput.value = data.tracking_id;
        window.history.replaceState(null, "", `/?tracking_id=${encodeURIComponent(data.tracking_id)}`);
        if (status) status.textContent = `Showing details for ${data.tracking_id}.`;
        rememberParcel(data.tracking_id);
      }
    } catch {
      typing.remove();
      appendMessage(log, "We couldn’t reach the assistant. Check your connection and try again.", "assistant", true);
    } finally {
      button.disabled = false;
      input.focus({ preventScroll: true });
    }
  });
});
