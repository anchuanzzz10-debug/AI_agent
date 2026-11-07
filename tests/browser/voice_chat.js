// Browser test for dashboard voice chat. Run by tests/test_voice_browser.py.
// The browser's speech engines are replaced with controlled fakes so the
// page logic can be tested; real recognition quality must be tested by a person.
const { chromium, devices } = require("playwright");

const BASE = process.argv[2];
const PASSWORD = process.argv[3];

function fakes(opts) {
  return `
    (() => {
      const o = ${JSON.stringify(opts)};
      window.__spoken = []; window.__recLangs = [];
      if (o.noRecognition) {
        try { delete window.webkitSpeechRecognition; } catch (e) {}
        window.webkitSpeechRecognition = undefined; window.SpeechRecognition = undefined;
      } else {
        window.SpeechRecognition = class {
          start() {
            window.__recLangs.push(this.lang);
            setTimeout(() => {
              this.onstart && this.onstart();
              if (o.error) { this.onerror && this.onerror({ error: o.error }); this.onend && this.onend(); return; }
              const res = [{ transcript: o.say }]; res.isFinal = true;
              this.onresult && this.onresult({ resultIndex: 0, results: [res] });
              this.onend && this.onend();
            }, 30);
          }
          stop() { this.onend && this.onend(); }
        };
      }
      const voices = o.voices;
      Object.defineProperty(window, "speechSynthesis", { configurable: true, value: {
        getVoices: () => voices, cancel: () => {}, addEventListener: () => {},
        speak: (u) => window.__spoken.push({ text: u.text, lang: u.lang }) } });
      window.SpeechSynthesisUtterance = function (t) { this.text = t; };
    })();`;
}

async function scenario(browser, name, opts, run) {
  const context = await browser.newContext({ ...devices["Pixel 7"] });
  await context.addInitScript(fakes(opts));
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE + "/login");
  await page.fill("#password", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(BASE + "/");
  await page.goto(BASE + "/chat");
  const result = await run(page);
  await context.close();
  return { name, errors, ...result };
}

async function lastBotReply(page) {
  await page.waitForFunction(() => {
    const bots = document.querySelectorAll(".msg.bot");
    return bots.length > 1 && bots[bots.length - 1].textContent !== "…";
  });
  return page.$$eval(".msg.bot", (els) => els[els.length - 1].textContent);
}

(async () => {
  const browser = await chromium.launch();
  const ML = [{ name: "Malayalam", lang: "ml-IN" }, { name: "English India", lang: "en-IN" }];
  const EN_ONLY = [{ name: "English US", lang: "en-US" }];
  const out = [];

  out.push(await scenario(browser, "malayalam_voice_round_trip", { say: "റിപ്പോർട്ട് തരൂ", voices: ML }, async (page) => {
    await page.selectOption("#voice-lang", "ml-IN");
    await page.check("#speak-replies");
    await page.click("#mic");
    const reply = await lastBotReply(page);
    const mine = await page.$$eval(".msg.me", (els) => els.map((e) => e.textContent));
    return { reply, mine, recLangs: await page.evaluate(() => window.__recLangs),
             spoken: await page.evaluate(() => window.__spoken),
             privacyVisible: await page.isVisible("#voice-privacy") };
  }));

  out.push(await scenario(browser, "english_voice", { say: "report", voices: ML }, async (page) => {
    await page.selectOption("#voice-lang", "en-IN");
    await page.check("#speak-replies");
    await page.click("#mic");
    const reply = await lastBotReply(page);
    return { reply, recLangs: await page.evaluate(() => window.__recLangs),
             spoken: await page.evaluate(() => window.__spoken) };
  }));

  out.push(await scenario(browser, "loanword_check", { say: "ചെക്ക് ചെയ്യൂ", voices: ML }, async (page) => {
    await page.selectOption("#voice-lang", "ml-IN");
    await page.click("#mic");
    return { reply: await lastBotReply(page) };
  }));

  out.push(await scenario(browser, "no_malayalam_voice", { say: "റിപ്പോർട്ട് തരൂ", voices: EN_ONLY }, async (page) => {
    await page.selectOption("#voice-lang", "ml-IN");
    await page.check("#speak-replies");
    await page.click("#mic");
    await lastBotReply(page);
    return { spoken: await page.evaluate(() => window.__spoken), note: await page.textContent("#voice-note") };
  }));

  out.push(await scenario(browser, "speaking_off", { say: "report", voices: ML }, async (page) => {
    await page.selectOption("#voice-lang", "en-IN");
    await page.uncheck("#speak-replies");
    await page.click("#mic");
    await lastBotReply(page);
    return { spoken: await page.evaluate(() => window.__spoken) };
  }));

  out.push(await scenario(browser, "mic_permission_denied", { error: "not-allowed", voices: ML }, async (page) => {
    await page.click("#mic");
    await page.waitForFunction(() => !document.getElementById("voice-note").hidden);
    return { note: await page.textContent("#voice-note"),
             userMessages: await page.$$eval(".msg.me", (els) => els.length) };
  }));

  out.push(await scenario(browser, "unsupported_browser", { noRecognition: true, voices: ML }, async (page) => {
    return { micHidden: await page.isHidden("#mic"), note: await page.textContent("#voice-note") };
  }));

  out.push(await scenario(browser, "voice_cannot_approve", { say: "അപ്രൂവ് APR-1A2B3C4D", voices: ML }, async (page) => {
    await page.selectOption("#voice-lang", "ml-IN");
    await page.click("#mic");
    return { reply: await lastBotReply(page) };
  }));

  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
