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

  // AI status line
  var aiStatus = document.getElementById("ai-status");
  fetch("/api/ai/status", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (st) {
      if (!st || !aiStatus) return;
      aiStatus.textContent = !st.enabled ? text("ai-off")
        : st.ready ? text("ai-ready").replace("{model}", st.model)
        : text("ai-down").replace("{message}", st.message);
      aiStatus.hidden = false;
    })
    .catch(function () { /* status is informational only */ });

  function send(message) {
    add(message, "me");
    var pending = add("…", "bot");
    var thinkingTimer = setTimeout(function () {
      if (pending.textContent === "…") pending.textContent = text("ai-thinking");
    }, 2500);
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ message: message, lang: voiceLang.value.indexOf("ml") === 0 ? "ml" : "en" }),
      credentials: "same-origin"
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = "/login"; return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        clearTimeout(thinkingTimer);
        if (!data) return;
        pending.textContent = data.reply;
        if (data.source === "ai") {
          var badge = document.createElement("div");
          badge.className = "ai-badge";
          badge.textContent = (data.tools && data.tools.length)
            ? text("ai-badge") + " " + data.tools.join(", ")
            : text("ai-badge-none");
          pending.appendChild(badge);
        }
        speak(data.reply, data.lang);
      })
      .catch(function (err) {
        clearTimeout(thinkingTimer);
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
  var SILENCE_MS = 1500;     // stop this long after the last recognised words
  var MAX_MS = 20000;        // never listen longer than this

  function stopListening() {
    listening = false;
    micBtn.classList.remove("listening");
    input.placeholder = input.getAttribute("data-placeholder") || input.placeholder;
  }

  micBtn.addEventListener("click", function () {
    if (listening && recognition) { recognition.stop(); return; }   // press again = done
    if (synth) synth.cancel();             // don't let the agent hear itself
    showNote("");
    recognition = new Recognition();
    var rec = recognition;
    rec.lang = voiceLang.value;
    rec.interimResults = true;
    rec.continuous = true;                 // keep listening while the user speaks
    rec.maxAlternatives = 1;
    var finalText = "";
    var interimText = "";
    var sent = false;
    var silenceTimer = null;
    var maxTimer = null;

    function finish() { try { rec.stop(); } catch (e) { /* already stopped */ } }

    rec.onstart = function () {
      listening = true;
      micBtn.classList.add("listening");
      if (!input.getAttribute("data-placeholder")) input.setAttribute("data-placeholder", input.placeholder);
      input.placeholder = text("voice-listening");
      maxTimer = setTimeout(finish, MAX_MS);
    };
    rec.onresult = function (event) {
      interimText = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) { finalText += piece + " "; } else { interimText += piece; }
      }
      input.value = (finalText + interimText).trim();
      clearTimeout(silenceTimer);
      silenceTimer = setTimeout(finish, SILENCE_MS);
    };
    rec.onerror = function (event) {
      if (event.error === "aborted") return;
      var msg = chat.getAttribute("data-voice-err-" + event.error);
      showNote(msg || (text("voice-err-other") + " (" + event.error + ")"));
    };
    rec.onend = function () {
      clearTimeout(silenceTimer);
      clearTimeout(maxTimer);
      stopListening();
      // Use words still pending as interim if the user pressed stop mid-sentence.
      var said = (finalText + interimText).trim();
      if (said && !sent) {
        sent = true;
        input.value = "";
        send(said);                       // the transcript is shown as your message
      }
    };
    try { rec.start(); } catch (e) { showNote(text("voice-err-other")); stopListening(); }
  });

  // ------------------------------------------------------ microphone test
  var testBtn = document.getElementById("mic-test");
  var meter = document.getElementById("mic-meter");
  var meterFill = document.getElementById("mic-meter-fill");
  if (testBtn && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    testBtn.hidden = false;
    testBtn.addEventListener("click", function () {
      testBtn.disabled = true;
      showNote(text("voice-test-running"));
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        var track = stream.getAudioTracks()[0];
        var label = (track && track.label) || "?";
        var Ctx = window.AudioContext || window.webkitAudioContext;
        var ctx = new Ctx();
        var analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        ctx.createMediaStreamSource(stream).connect(analyser);
        var buf = new Uint8Array(analyser.fftSize);
        var peak = 0;
        meter.hidden = false;
        var timer = setInterval(function () {
          analyser.getByteTimeDomainData(buf);
          var sum = 0;
          for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
          var level = Math.min(1, Math.sqrt(sum / buf.length) * 4);
          peak = Math.max(peak, level);
          meterFill.style.width = Math.round(level * 100) + "%";
        }, 50);
        setTimeout(function () {
          clearInterval(timer);
          stream.getTracks().forEach(function (t) { t.stop(); });
          try { ctx.close(); } catch (e) { /* ignore */ }
          meter.hidden = true;
          testBtn.disabled = false;
          navigator.mediaDevices.enumerateDevices().then(function (devices) {
            var mics = devices.filter(function (d) { return d.kind === "audioinput"; }).length;
            var key = peak > 0.05 ? "voice-test-ok" : "voice-test-silent";
            showNote(text(key).replace("{mic}", label) + " " + text("voice-test-devices").replace("{n}", mics));
            chat.setAttribute("data-mic-test-peak", peak.toFixed(3));
          });
        }, 5000);
      }).catch(function (err) {
        testBtn.disabled = false;
        var name = err && err.name;
        showNote(name === "NotAllowedError" ? text("voice-err-not-allowed")
               : name === "NotFoundError" ? text("voice-test-nomic")
               : text("voice-err-other") + " (" + name + ")");
      });
    });
  }
})();
