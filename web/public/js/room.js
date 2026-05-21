/* room.html — live debate WebSocket client with token-by-token streaming + markdown */
$(function () {
  const topic = sessionStorage.getItem("debate_topic") || "BTC 行情";
  $("#topic").text("议题:" + topic);

  const $msgs = $("#msgs");                 // scroll container (full width)
  const $msgsInner = $("#msgs-inner");      // centered content
  const $status = $("#status");
  const $thinking = $("#thinking-inner");

  // configure marked: GFM tables, line breaks
  if (window.marked) {
    marked.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
  }
  function renderMarkdown(text) {
    if (!window.marked || !window.DOMPurify) return null;
    const html = marked.parse(text || "");
    return DOMPurify.sanitize(html);
  }

  // streaming buffers: id -> { $node, content }
  const streams = {};

  // --- auto-scroll only when user is already near the bottom ---
  // If user has scrolled up to read history, DON'T yank them down.
  const NEAR_BOTTOM_PX = 80;
  let stickToBottom = true;
  function recomputeStick() {
    const el = $msgs[0];
    stickToBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < NEAR_BOTTOM_PX;
  }
  $msgs.on("scroll", recomputeStick);
  function maybeScrollBottom() {
    if (!stickToBottom) return;
    const el = $msgs[0];
    el.scrollTop = el.scrollHeight;
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return d.toTimeString().slice(0, 8);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // Set bubble content. For user msgs use plain text; for agents render markdown.
  function setBubbleContent($content, role, text, opts) {
    const isUser = role === "user";
    if (isUser || !window.marked) {
      $content.addClass("plain").text(text || "");
    } else {
      $content.removeClass("plain");
      const html = renderMarkdown(text || "");
      $content.html(html != null ? html : escapeHtml(text || ""));
    }
    if (opts && opts.streaming) {
      $content.append('<span class="cursor"></span>');
    }
  }

  function renderMsg(m, opts) {
    const $el = $(`
      <div class="msg ${m.role}" data-id="${m.id}">
        <div class="avatar" style="border-color:${m.color || "#2a313b"}">${m.emoji || "💬"}</div>
        <div class="body">
          <div class="head">
            <span class="name" style="color:${m.color || ""}">${escapeHtml(m.name || m.role)}</span>
            ${m.round ? `<span class="round">R${m.round}</span>` : ""}
            <span class="ts">${fmtTime(m.ts || Date.now() / 1000)}</span>
          </div>
          <div class="content"></div>
        </div>
      </div>`);
    setBubbleContent($el.find(".content"), m.role, m.content, opts);
    $msgsInner.append($el);
    maybeScrollBottom();
    return $el;
  }

  function clearThinking(role) {
    if (!role) $thinking.empty();
    else $thinking.find(`[data-role="${role}"]`).remove();
  }

  function showThinking(d) {
    clearThinking(d.role);
    const $t = $(`
      <div class="thinking" data-role="${d.role}">
        <div class="avatar" style="border-color:${d.color}">${d.emoji}</div>
        <span style="color:${d.color}">${escapeHtml(d.name)}</span>
        <span>正在思考</span>
        <span class="dots"><span></span><span></span><span></span></span>
      </div>`);
    $thinking.append($t);
    maybeScrollBottom();
  }

  // WebSocket — connect direct to backend :8000
  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsHost = location.hostname;
  const ws = new WebSocket(`${wsProto}//${wsHost}:8000/ws/debate`);

  ws.onopen = () => {
    $status.text("已连接,启动辩论…");
    ws.send(JSON.stringify({ type: "start", topic: topic }));
  };
  ws.onclose = () => { $status.text("连接已关闭"); clearThinking(); };
  ws.onerror = () => { $status.text("连接错误"); };

  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    const t = evt.type, d = evt.data || {};
    if (t === "status") {
      $status.text(d.text || "");
    } else if (t === "thinking") {
      if (d.on) showThinking(d);
      else clearThinking(d.role);
    } else if (t === "stream_start") {
      clearThinking(d.role);
      const $el = renderMsg({
        id: d.id, role: d.role, name: d.name, emoji: d.emoji, color: d.color,
        round: d.round, ts: d.ts, content: "",
      }, { streaming: true });
      streams[d.id] = { $content: $el.find(".content"), text: "", role: d.role };
    } else if (t === "stream_chunk") {
      const s = streams[d.id]; if (!s) return;
      s.text += d.delta;
      setBubbleContent(s.$content, s.role, s.text, { streaming: true });
      maybeScrollBottom();
    } else if (t === "stream_end") {
      const s = streams[d.id]; if (!s) return;
      setBubbleContent(s.$content, s.role, d.content || s.text, { streaming: false });
      delete streams[d.id];
    } else if (t === "message") {
      // server-side echoes for user messages: skip — we already rendered locally.
      if (d.role === "user") return;
      renderMsg(d);
    } else if (t === "error") {
      $status.text("错误:" + (d.text || ""));
      clearThinking();
    } else if (t === "done") {
      $status.text("辩论结束 ✅");
      clearThinking();
    }
  };

  function send() {
    const txt = $("#input").val().trim();
    if (!txt) return;
    if (ws.readyState !== 1) { $status.text("未连接,无法发送"); return; }
    // optimistic local echo (the server-side "user" message will be ignored)
    renderMsg({
      id: "u-" + Date.now(), role: "user", name: "你", emoji: "🙋", color: "#58a6ff",
      content: txt, round: 0, ts: Date.now() / 1000,
    });
    // sending is a "user action" → re-stick to bottom
    stickToBottom = true;
    maybeScrollBottom();
    ws.send(JSON.stringify({ type: "user_message", text: txt }));
    $("#input").val("");
  }
  $("#send").on("click", send);
  $("#input").on("keydown", (e) => { if (e.key === "Enter") send(); });
  $("#back").on("click", () => { ws.close(); location.href = "/"; });
});
