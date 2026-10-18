/*! Ask OLake chat widget. Embed with:
 *  <script src="https://<host>/widget.js" defer></script>
 * Optional attributes: data-endpoint (API base URL, defaults to the script's origin),
 * data-theme ("auto" | "light" | "dark"), data-position ("right" | "left"), data-open ("true").
 */
(function () {
  "use strict";
  if (window.__askOlakeLoaded) return;
  window.__askOlakeLoaded = true;

  var script = document.currentScript || document.querySelector('script[src*="widget.js"]');
  var cfg = (script && script.dataset) || {};
  var API = (cfg.endpoint || (script ? new URL(script.src).origin : "")).replace(/\/$/, "");
  var POSITION = cfg.position === "left" ? "left" : "right";
  var THEME = cfg.theme || "auto";
  var SUGGESTIONS = [
    "How do I set up CDC for Postgres?",
    "Which Iceberg catalogs does OLake support?",
    "What does OLake Fusion compaction do?",
  ];

  var CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { --bg:#ffffff; --fg:#111827; --muted:#6b7280; --border:#e5e7eb; --soft:#f3f4f6; --accent:#2563eb; --accent-fg:#fff; --code:#0f172a; --code-fg:#e2e8f0;
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: var(--fg);
    position: fixed; bottom: 20px; ${POSITION}: 20px; z-index: 2147483000; }
  .wrap.dark { --bg:#111827; --fg:#f3f4f6; --muted:#9ca3af; --border:#374151; --soft:#1f2937; --accent:#3b82f6; --code:#020617; }
  .fab { width: 56px; height: 56px; border-radius: 50%; border: none; background: var(--accent); color: var(--accent-fg); cursor: pointer;
    box-shadow: 0 6px 20px rgba(0,0,0,.25); display: flex; align-items: center; justify-content: center; margin-${POSITION}: 0; margin-left: auto; }
  .wrap.left .fab { margin-left: 0; }
  .fab:focus-visible, button:focus-visible, textarea:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .panel { display: none; flex-direction: column; width: 400px; height: min(620px, calc(100vh - 110px)); background: var(--bg); border: 1px solid var(--border);
    border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,.25); overflow: hidden; margin-bottom: 12px; }
  .open .panel { display: flex; }
  header { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
  header strong { flex: 1; font-size: 15px; }
  header small { color: var(--muted); font-weight: 400; }
  .icon-btn { background: none; border: none; color: var(--muted); cursor: pointer; padding: 4px; border-radius: 6px; font-size: 18px; line-height: 1; }
  .icon-btn:hover { background: var(--soft); color: var(--fg); }
  .log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 92%; padding: 10px 12px; border-radius: 12px; word-wrap: break-word; }
  .user { align-self: flex-end; background: var(--accent); color: var(--accent-fg); border-bottom-right-radius: 4px; white-space: pre-wrap; }
  .bot { align-self: flex-start; background: var(--soft); border-bottom-left-radius: 4px; }
  .bot p { margin: 0 0 8px; } .bot p:last-child { margin-bottom: 0; }
  .bot ul, .bot ol { margin: 4px 0 8px; padding-left: 20px; }
  .bot pre { background: var(--code); color: var(--code-fg); padding: 10px; border-radius: 8px; overflow-x: auto; font-size: 12.5px; margin: 6px 0 8px; position: relative; }
  .bot code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
  .bot :not(pre) > code { background: rgba(127,127,127,.18); padding: 1px 5px; border-radius: 4px; }
  .bot a { color: var(--accent); }
  .copy { position: absolute; top: 6px; right: 6px; font-size: 11px; background: rgba(255,255,255,.12); color: inherit; border: none; border-radius: 4px; padding: 2px 6px; cursor: pointer; }
  sup.cite a { text-decoration: none; font-weight: 600; font-size: 11px; padding: 0 2px; }
  .sources { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; gap: 4px; max-width: 100%; font-size: 12px; text-decoration: none; color: var(--fg); background: var(--bg);
    border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip:hover { border-color: var(--accent); }
  .chip b { color: var(--accent); }
  .meta { display: flex; align-items: center; gap: 4px; margin-top: 8px; color: var(--muted); font-size: 12px; }
  .meta .icon-btn { font-size: 14px; }
  .meta .icon-btn[aria-pressed="true"] { color: var(--accent); background: var(--soft); }
  .typing { color: var(--muted); font-style: italic; }
  .suggest { display: flex; flex-direction: column; gap: 6px; }
  .suggest button { text-align: left; background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; cursor: pointer; font: inherit; }
  .suggest button:hover { border-color: var(--accent); }
  .hello { color: var(--muted); margin: 0 0 4px; }
  form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); }
  textarea { flex: 1; resize: none; height: 42px; max-height: 120px; padding: 10px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); color: var(--fg); font: inherit; }
  .send { background: var(--accent); color: var(--accent-fg); border: none; border-radius: 10px; padding: 0 14px; cursor: pointer; font: inherit; font-weight: 600; }
  .send:disabled { opacity: .5; cursor: default; }
  .foot { text-align: center; font-size: 11px; color: var(--muted); padding: 0 0 8px; }
  @media (max-width: 480px) { .wrap { bottom: 12px; left: 12px; right: 12px; } .panel { width: auto; height: calc(100dvh - 100px); }
    .fab { margin-left: ${POSITION === "left" ? "0" : "auto"}; } }
  `;

  // ---------- tiny, XSS-safe markdown renderer ----------
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s, citeUrl) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:])/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    s = s.replace(/\[(\d+)\]/g, function (m, n) {
      var url = citeUrl(+n);
      return url ? '<sup class="cite"><a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" title="Source ' + n + '">[' + n + "]</a></sup>" : "";
    });
    return s;
  }
  function markdown(src, citeUrl) {
    var out = [], lines = src.split("\n"), i = 0, list = null, para = [];
    function flushPara() { if (para.length) { out.push("<p>" + inline(para.join(" "), citeUrl) + "</p>"); para = []; } }
    function flushList() { if (list) { out.push("</" + list + ">"); list = null; } }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        flushPara(); flushList();
        var code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push('<pre><button class="copy" type="button">Copy</button><code>' + esc(code.join("\n")) + "</code></pre>");
        continue;
      }
      var ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), h = line.match(/^#{1,6}\s+(.*)/);
      if (ul || ol) {
        flushPara();
        var kind = ul ? "ul" : "ol";
        if (list !== kind) { flushList(); out.push("<" + kind + ">"); list = kind; }
        out.push("<li>" + inline((ul || ol)[1], citeUrl) + "</li>");
      } else if (h) {
        flushPara(); flushList();
        out.push("<p><strong>" + inline(h[1], citeUrl) + "</strong></p>");
      } else if (!line.trim()) {
        flushPara(); flushList();
      } else {
        flushList(); para.push(line.trim());
      }
      i++;
    }
    flushPara(); flushList();
    return out.join("");
  }

  // ---------- DOM ----------
  var host = document.createElement("div");
  host.id = "ask-olake-widget";
  document.body.appendChild(host);
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<style>" + CSS + "</style>" +
    '<div class="wrap ' + POSITION + '">' +
    '  <section class="panel" role="dialog" aria-label="Ask OLake assistant">' +
    '    <header><strong>Ask OLake <small>· docs assistant</small></strong>' +
    '      <button class="icon-btn" data-act="clear" title="New chat" aria-label="Start a new chat">↺</button>' +
    '      <button class="icon-btn" data-act="close" title="Close" aria-label="Close chat">✕</button></header>' +
    '    <div class="log" aria-live="polite"></div>' +
    '    <form><textarea rows="1" placeholder="Ask about OLake or OLake Fusion…" aria-label="Your question" maxlength="4000"></textarea>' +
    '      <button class="send" type="submit">Ask</button></form>' +
    '    <div class="foot">Answers are generated from the OLake docs and may be imperfect. Check the cited sources.</div>' +
    "  </section>" +
    '  <button class="fab" aria-label="Open Ask OLake assistant" aria-expanded="false">' +
    '    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' +
    "  </button>" +
    "</div>";

  var wrap = root.querySelector(".wrap"), fab = root.querySelector(".fab"), log = root.querySelector(".log");
  var form = root.querySelector("form"), input = root.querySelector("textarea"), send = root.querySelector(".send");
  var busy = false;

  function applyTheme() {
    var dark = THEME === "dark" || (THEME === "auto" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    wrap.classList.toggle("dark", dark);
  }
  applyTheme();
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  function setOpen(open) {
    wrap.classList.toggle("open", open);
    fab.setAttribute("aria-expanded", String(open));
    if (open) { if (!log.children.length) showWelcome(); setTimeout(function () { input.focus(); }, 30); }
    else fab.focus();
  }
  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function showWelcome() {
    var box = document.createElement("div");
    box.className = "msg bot";
    box.innerHTML = '<p class="hello">Hi! Ask me anything about OLake ingestion or OLake Fusion. I answer from the official docs and cite my sources.</p><div class="suggest"></div>';
    var sg = box.querySelector(".suggest");
    SUGGESTIONS.forEach(function (q) {
      var b = document.createElement("button");
      b.type = "button"; b.textContent = q;
      b.onclick = function () { ask(q); };
      sg.appendChild(b);
    });
    log.appendChild(box);
  }

  function addUser(text) {
    var d = document.createElement("div");
    d.className = "msg user"; d.textContent = text;
    log.appendChild(d); scrollDown();
  }

  function renderBot(el, text, citations) {
    var byN = {};
    (citations || []).forEach(function (c) { byN[c.n] = c; });
    el.innerHTML = markdown(text, function (n) { return byN[n] ? byN[n].section_url || byN[n].url : null; });
    el.querySelectorAll(".copy").forEach(function (btn) {
      btn.onclick = function () {
        var code = btn.parentNode.querySelector("code").textContent;
        (navigator.clipboard ? navigator.clipboard.writeText(code) : Promise.reject()).then(function () {
          btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1200);
        }).catch(function () {});
      };
    });
  }

  function addSources(el, citations) {
    if (!citations || !citations.length) return;
    var box = document.createElement("div");
    box.className = "sources";
    citations.forEach(function (c) {
      var a = document.createElement("a");
      a.className = "chip"; a.href = c.section_url || c.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.title = c.title;
      // "Destinations / … / 1. AWS Glue: AWS Glue Catalog Write Guide > Configuration (part 3)"
      //   -> "AWS Glue Catalog Write Guide › Configuration"
      var parts = c.title.replace(/ \(part \d+\)$/, "").split(" > ");
      var page = parts[0].split(/: | \/ /).pop();
      var b = document.createElement("b"); b.textContent = c.n;
      var t = document.createElement("span"); t.textContent = parts.length > 1 ? page + " › " + parts[parts.length - 1] : page;
      a.appendChild(b); a.appendChild(t); box.appendChild(a);
    });
    el.appendChild(box);
  }

  function addFeedback(el, id, question) {
    if (!id) return;
    var meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = '<span>Helpful?</span><button class="icon-btn" aria-label="Helpful" aria-pressed="false">👍</button><button class="icon-btn" aria-label="Not helpful" aria-pressed="false">👎</button>';
    var btns = meta.querySelectorAll("button");
    [1, -1].forEach(function (vote, i) {
      btns[i].onclick = function () {
        btns.forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
        btns[i].setAttribute("aria-pressed", "true");
        fetch(API + "/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: id, vote: vote, question: question }) }).catch(function () {});
        meta.querySelector("span").textContent = "Thanks for the feedback!";
      };
    });
    el.appendChild(meta);
  }

  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true; send.disabled = true; input.value = "";
    addUser(question);
    var el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = '<span class="typing">Searching the docs…</span>';
    log.appendChild(el); scrollDown();

    var text = "";
    try {
      var resp = await fetch(API + "/ask/stream", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: question }),
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        throw new Error(err.detail || (resp.status === 429 ? "Too many questions, please wait a minute." : "Something went wrong."));
      }
      var reader = resp.body.getReader(), decoder = new TextDecoder(), buf = "";
      while (true) {
        var r = await reader.read();
        if (r.done) break;
        buf += decoder.decode(r.value, { stream: true });
        var parts = buf.split("\n\n");
        buf = parts.pop();
        parts.forEach(function (part) {
          var ev = (part.match(/^event: (.*)$/m) || [])[1], data = (part.match(/^data: (.*)$/m) || [])[1];
          if (!ev || data === undefined) return;
          data = JSON.parse(data);
          if (ev === "delta") {
            text += data;
            renderBot(el, text.replace(/\[\d+(?:\s*,\s*\d+)*\]/g, ""), []);
          } else if (ev === "final") {
            renderBot(el, data.answer, data.citations);
            addSources(el, data.citations);
            addFeedback(el, data.id, question);
          } else if (ev === "error") {
            throw new Error(data);
          }
          scrollDown();
        });
      }
    } catch (e) {
      el.textContent = (e && e.message) || "Sorry, I couldn't reach the assistant. Please try again.";
    } finally {
      busy = false; send.disabled = false; input.focus(); scrollDown();
    }
  }

  fab.onclick = function () { setOpen(!wrap.classList.contains("open")); };
  root.querySelector('[data-act="close"]').onclick = function () { setOpen(false); };
  root.querySelector('[data-act="clear"]').onclick = function () { log.innerHTML = ""; showWelcome(); input.focus(); };
  form.onsubmit = function (e) { e.preventDefault(); ask(input.value); };
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); }
  });
  root.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
  if (cfg.open === "true") setOpen(true);

  window.AskOLake = { open: function () { setOpen(true); }, close: function () { setOpen(false); }, ask: function (q) { setOpen(true); ask(q); } };
})();
