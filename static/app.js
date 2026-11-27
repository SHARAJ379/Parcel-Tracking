"use strict";

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
    return;
  }

  const suggestion = event.target.closest("[data-question]");
  if (suggestion) {
    const input = document.getElementById("assistant-question");
    if (input) {
      input.value = suggestion.dataset.question;
      input.focus();
    }
  }
});

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

document.querySelectorAll("[data-assistant-form]").forEach((form) => {
  const log = document.querySelector("[data-chat-log]");
  const status = document.querySelector("[data-assistant-status]");
  const input = form.querySelector("textarea[name='question']");
  const button = form.querySelector("button[type='submit']");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || !log || !button) return;
    status.textContent = "";
    appendMessage(log, question, "chat-user");
    input.value = "";
    button.disabled = true;
    const label = button.querySelector(".button-label");
    const loading = button.querySelector(".button-loading");
    if (label) label.hidden = true;
    if (loading) loading.hidden = false;
    const pending = appendMessage(log, "Thinking…", "chat-pending");
    try {
      const response = await fetch("/ui/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ tracking_id: form.dataset.trackingId, question }),
      });
      const result = await response.json();
      pending.remove();
      if (!response.ok || !result.success) {
        status.textContent = result.error?.message || "The assistant is unavailable. Please try again.";
        appendMessage(log, status.textContent, "chat-assistant");
        return;
      }
      appendMessage(log, result.data.answer, "chat-assistant");
    } catch {
      pending.remove();
      status.textContent = "We couldn’t reach the assistant. Check your connection and try again.";
      appendMessage(log, status.textContent, "chat-assistant");
    } finally {
      button.disabled = false;
      if (label) label.hidden = false;
      if (loading) loading.hidden = true;
      input.focus();
    }
  });
});

function appendMessage(log, text, className) {
  const message = document.createElement("p");
  message.className = `chat-message ${className}`;
  message.textContent = text;
  log.appendChild(message);
  message.scrollIntoView({ block: "nearest" });
  return message;
}
