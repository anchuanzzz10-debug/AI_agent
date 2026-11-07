// Guardian dashboard: confirmations, connect form helper, chat and voice.
// No inline scripts (strict CSP).
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

  // ---------------------------------------------------------------- chat
  var chat = document.querySelector(".chat");
  if (!chat) return;
  var csrf = chat.getAttribute("data-csrf");
  var errorLabel = chat.getAttribute("data-error") || "Error";
  var messages = document.getElementById("messages");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var micBtn = document.getElementById("mic");
  var voiceLang = document.getElementById("voice-lang");
  var speakBox = document.getElementById("speak-replies");
  var note = document.getElementById("voice-note");
  var privacy = document.getElementById("voice-privacy");

  function text(key) { return chat.getAttribute("data-" + key) || key; }
  function showNote(msg) { note.textContent = msg; note.hidden = !msg; }

  // Small per-viewer preferences (not security-relevant); storage may be blocked.
  function load(key, fallback) {
    try { var v = window.localStorage.getItem("guardian." + key); return v === null ? fallback : v; }
    catch (e) { return fallback; }
  }
  function save(key, value) {
    try { window.localStorage.setItem("guardian." + key, value); } catch (e) { /* ignore */ }
  }

  function add(content, cls) {
    var div = document.createElement("div");
    div.className = "msg " + cls;
    div.textContent = content;            // textContent: never interpreted as HTML
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function send(message) {
    add(message, "me");
    var pending = add("…", "bot");
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ message: message }),
      credentials: "same-origin"
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/login"; return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        pending.textContent = data.reply;
        speak(data.reply, data.lang);
      })
      .catch(function (err) {
        pending.textContent = errorLabel + ": " + err.message;
        pending.className = "msg bot err";
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var message = input.value.trim();
    if (!message) return;
    input.value = "";
    send(message);
  });

  // ------------------------------------------------------- speech output
  var synth = window.speechSynthesis;
  speakBox.checked = load("speak", "0") === "1";
  speakBox.addEventListener("change", function () {
    save("speak", speakBox.checked ? "1" : "0");
    if (!speakBox.checked && synth) synth.cancel();
  });
  if (!synth) { speakBox.disabled = true; }

  function pickVoice(lang) {
    if (!synth) return null;
    var voices = synth.getVoices() || [];
    var prefix = lang === "ml" ? "ml" : "en";
    var exact = lang === "ml" ? "ml-IN" : "en-IN";
    var match = voices.filter(function (v) { return (v.lang || "").replace("_", "-") === exact; })[0] ||
                voices.filter(function (v) { return (v.lang || "").toLowerCase().indexOf(prefix) === 0; })[0];
    return match || null;
  }

  function cleanForSpeech(s) {
    // Drop icons and bullet symbols so they are not read out.
    return s.replace(/[☀-➿\u{1F300}-\u{1FAFF}•·]/gu, " ").replace(/\s+/g, " ").trim();
  }

  function speak(reply, lang) {
    if (!speakBox.checked || !synth || !reply) return;
    var voice = pickVoice(lang);
    if (!voice) {
      // Never read Malayalam text with an English voice.
      if (lang === "ml") { showNote(text("voice-no-ml-voice")); }
      return;
    }
    synth.cancel();
    var utter = new SpeechSynthesisUtterance(cleanForSpeech(reply));
    utter.voice = voice;
    utter.lang = voice.lang;
    synth.speak(utter);
  }
  if (synth && typeof synth.addEventListener === "function") {
    synth.addEventListener("voiceschanged", function () { /* voices load lazily */ });
  }

  // -------------------------------------------------------- speech input
  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  voiceLang.value = load("voiceLang", voiceLang.value);
  voiceLang.addEventListener("change", function () { save("voiceLang", voiceLang.value); });

  if (!window.isSecureContext) {
    showNote(text("voice-insecure"));
    return;
  }
  if (!Recognition) {
    showNote(text("voice-unsupported"));
    return;
  }
  micBtn.hidden = false;
  privacy.hidden = false;

  var recognition = null;
  var listening = false;

  function stopListening() {
    listening = false;
    micBtn.classList.remove("listening");
    input.placeholder = input.getAttribute("data-placeholder") || input.placeholder;
  }

  micBtn.addEventListener("click", function () {
    if (listening && recognition) { recognition.stop(); return; }
    if (synth) synth.cancel();             // don't let the agent hear itself
    showNote("");
    recognition = new Recognition();
    recognition.lang = voiceLang.value;
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;
    var finalText = "";

    recognition.onstart = function () {
      listening = true;
      micBtn.classList.add("listening");
      if (!input.getAttribute("data-placeholder")) input.setAttribute("data-placeholder", input.placeholder);
      input.placeholder = text("voice-listening");
    };
    recognition.onresult = function (event) {
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) { finalText += piece; } else { interim += piece; }
      }
      input.value = (finalText + interim).trim();
    };
    recognition.onerror = function (event) {
      var key = "voice-err-" + event.error;
      var msg = chat.getAttribute("data-" + key);
      showNote(msg || (text("voice-err-other") + " (" + event.error + ")"));
      stopListening();
    };
    recognition.onend = function () {
      stopListening();
      var said = finalText.trim();
      if (said) {
        input.value = "";
        send(said);                       // the transcript is shown as your message
      }
    };
    try { recognition.start(); } catch (e) { showNote(text("voice-err-other")); stopListening(); }
  });
})();
