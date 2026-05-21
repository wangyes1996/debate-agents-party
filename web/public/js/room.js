/* room.html — live debate WebSocket client with token-by-token streaming */
$(function () {
  const topic = sessionStorage.getItem("debate_topic") || "BTC 行情";
  $("#topic").text("议题:" + topic);

  const $msgs = $("#msgs");
  const $status = $("#status");
  const $thinking = $("#thinking-area");

  // streaming buffers: id -> { $node, content }
  const streams = {};

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return d.toTimeString().slice(0, 8);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function scrollBottom() {
    $msgs.scrollTop($msgs[0].scrollHeight);
  }

  function renderMsg(m, opts) {
    const isUser = m.role === "user";
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
    $el.find(".content").text(m.content || "");
    if (opts && opts.streaming) {
      $el.find(".content").append('<span class="cursor"></span>');
    }
    $msgs.append($el);
    scrollBottom();
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
    scrollBottom();
  }

  // WebSocket — connect direct to backend :8000 (host:8000)
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
      // create empty bubble + remove that role's thinking dots
      clearThinking(d.role);
      const $el = renderMsg({
        id: d.id, role: d.role, name: d.name, emoji: d.emoji, color: d.color,
        round: d.round, ts: d.ts, content: "",
      }, { streaming: true });
      streams[d.id] = { $content: $el.find(".content"), text: "" };
    } else if (t === "stream_chunk") {
      const s = streams[d.id]; if (!s) return;
      s.text += d.delta;
      // re-render text + cursor
      s.$content.text(s.text);
      s.$content.append('<span class="cursor"></span>');
      scrollBottom();
    } else if (t === "stream_end") {
      const s = streams[d.id]; if (!s) return;
      s.$content.text(d.content || s.text);  // final canonical content
      // remove cursor
      s.$content.find(".cursor").remove();
      delete streams[d.id];
    } else if (t === "message") {
      // legacy non-stream path (e.g. user echo, errors)
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
    // optimistic local echo
    renderMsg({
      id: "u-" + Date.now(), role: "user", name: "你", emoji: "🙋", color: "#58a6ff",
      content: txt, round: 0, ts: Date.now() / 1000,
    });
    ws.send(JSON.stringify({ type: "user_message", text: txt }));
    $("#input").val("");
  }
  $("#send").on("click", send);
  $("#input").on("keydown", (e) => { if (e.key === "Enter") send(); });
  $("#back").on("click", () => { ws.close(); location.href = "/"; });
});
