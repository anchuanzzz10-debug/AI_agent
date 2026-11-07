// Guardian dashboard: confirmations and chat. No inline scripts (strict CSP).
(function () {
  "use strict";

  // Confirm dialogs for forms marked data-confirm (approve, delete).
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.getAttribute && form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) {
      event.preventDefault();
    }
  });

  // Confirm for individual buttons (e.g. remove change login).
  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("[data-confirm-button]");
    if (btn && !window.confirm(btn.getAttribute("data-confirm-button"))) {
      event.preventDefault();
    }
  });

  // Suggest a project ID from the address (e.g. https://www.docs-media.com -> docs-media).
  var urlInput = document.getElementById("url");
  var idInput = document.getElementById("id");
  if (urlInput && idInput) {
    var touched = idInput.value !== "";
    idInput.addEventListener("input", function () { touched = true; });
    urlInput.addEventListener("input", function () {
      if (touched) return;
      try {
        var host = new URL(urlInput.value).hostname.replace(/^www\./, "");
        idInput.value = host.split(".")[0].toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 40);
      } catch (e) { /* not a full URL yet */ }
    });
  }

  // Chat
  var chat = document.querySelector(".chat");
  if (!chat) return;
  var csrf = chat.getAttribute("data-csrf");
  var errorLabel = chat.getAttribute("data-error") || "Error";
  var messages = document.getElementById("messages");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");

  function add(text, cls) {
    var div = document.createElement("div");
    div.className = "msg " + cls;
    div.textContent = text;            // textContent: never interpreted as HTML
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    add(text, "me");
    input.value = "";
    var pending = add("…", "bot");
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ message: text }),
      credentials: "same-origin"
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/login"; return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data) pending.textContent = data.reply;
      })
      .catch(function (err) {
        pending.textContent = errorLabel + ": " + err.message;
        pending.className = "msg bot err";
      });
  });
})();
