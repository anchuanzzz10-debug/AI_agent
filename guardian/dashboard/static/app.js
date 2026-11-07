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
