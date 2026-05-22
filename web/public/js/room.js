/* room.html — live debate WS client, room_id-driven, history-aware. */
$(async function () {
  const params = new URLSearchParams(location.search);
  const roomId = params.get("id");
  if (!roomId) { $("#status").text("缺少房间 id,返回首页选择房间"); return; }

  let room = null;
  try { room = await $.get("/api/rooms/" + encodeURIComponent(roomId)); }
  catch (e) { $("#status").text("房间不存在"); return; }

  $("#topic").text("议题:" + (room.topic || room.name));
  $("#meta").text(`房间: ${room.name} · 上限 ${room.max_turns} 轮`);

  const $msgs = $("#msgs");
  const $msgsInner = $("#msgs-inner");
  const $status = $("#status");
  const $thinking = $("#thinking-inner");
  const $start = $("#start");
  const $restart = $("#restart");
  const $finalize = $("#finalize");

  if (window.marked) marked.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
  function renderMarkdown(text) {
    if (!window.marked || !window.DOMPurify) return null;
    return DOMPurify.sanitize(marked.parse(text || ""));
  }

  const streams = {};
  const seenMsgIds = new Set();
  const NEAR_BOTTOM_PX = 80;
  let stickToBottom = true;
  function recomputeStick() {
    const el = $msgs[0];
    stickToBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < NEAR_BOTTOM_PX;
  }
  $msgs.on("scroll", recomputeStick);
  function maybeScrollBottom() {
    if (!stickToBottom) return;
    const el = $msgs[0]; el.scrollTop = el.scrollHeight;
  }

  function fmtTime(ts) { return new Date(ts*1000).toTimeString().slice(0,8); }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

  function setBubbleContent($content, role, text, opts) {
    const isUser = role === "user";
    if (isUser || !window.marked) {
      $content.addClass("plain").text(text || "");
    } else {
      $content.removeClass("plain");
      const html = renderMarkdown(text || "");
      $content.html(html != null ? html : escapeHtml(text || ""));
    }
    if (opts && opts.streaming) $content.append('<span class="cursor"></span>');
  }

  function renderMsg(m, opts) {
    if (m.id && seenMsgIds.has(m.id)) return null;
    if (m.id) seenMsgIds.add(m.id);
    const cssRole = m.role === "user" ? "user" : (m.role === room.moderator_id ? "moderator" : "");
    const $el = $(`
      <div class="msg ${cssRole}" data-id="${m.id}">
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

  function clearAll() {
    $msgsInner.empty();
    $thinking.empty();
    seenMsgIds.clear();
    for (const k of Object.keys(streams)) delete streams[k];
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

  // ---- load history first ----
  let active = false;
  try {
    const h = await $.get(`/api/rooms/${encodeURIComponent(roomId)}/history`);
    active = !!h.active;
    if (h.session && h.session.topic) {
      $("#topic").text("议题:" + h.session.topic);
    }
    if (h.messages && h.messages.length) {
      for (const m of h.messages) renderMsg(m);
      stickToBottom = true; maybeScrollBottom();
    }
    if (active) {
      $status.text("辩论进行中,正在接入…");
    } else if (h.messages && h.messages.length) {
      $status.text("已加载历史 — 可点「重启」开始新一轮,或继续插话");
      $start.show();
    } else {
      $status.text("尚未开始辩论 — 点「开始辩论」启动");
      $start.show();
    }
  } catch (e) {
    $status.text("加载历史失败:" + (e.responseJSON?.detail || e.statusText || e.message));
  }

  // ---- websocket ----
  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${wsProto}//${location.hostname}:8000/ws/debate`);

  ws.onopen = () => {
    if (active) {
      ws.send(JSON.stringify({ type: "attach", room_id: roomId }));
    } else {
      // passive attach so server can route future events for this room without auto-starting
      ws.send(JSON.stringify({ type: "attach", room_id: roomId }));
    }
  };
  ws.onclose = () => { $status.text("连接已关闭"); clearThinking(); };
  ws.onerror = () => { $status.text("连接错误"); };

  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    const t = evt.type, d = evt.data || {};
    if (t === "status") $status.text(d.text || "");
    else if (t === "thinking") { if (d.on) showThinking(d); else clearThinking(d.role); }
    else if (t === "stream_start") {
      clearThinking(d.role);
      if (seenMsgIds.has(d.id)) return;  // already in history
      const $el = renderMsg({ id: d.id, role: d.role, name: d.name, emoji: d.emoji, color: d.color, round: d.round, ts: d.ts, content: "" }, { streaming: true });
      if ($el) streams[d.id] = { $content: $el.find(".content"), text: "", role: d.role };
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
      // user_message or final-verdict snapshot — render unless dup
      renderMsg(d);
    } else if (t === "error") {
      $status.text("错误:" + (d.text || "")); clearThinking();
    } else if (t === "paused") {
      $status.text("⏸ 辩论已达上限 — 继续插话延长讨论,或点「结束辩论」生成总结");
      clearThinking();
    } else if (t === "done") {
      $status.text("辩论结束 ✅"); clearThinking();
      $start.show();
    } else if (t === "started" || t === "restarted") {
      $status.text(t === "restarted" ? "已重启辩论…" : "已启动辩论…");
      $start.hide();
    } else if (t === "attached") {
      if (d.active) { $start.hide(); $status.text("已接入进行中的辩论"); }
    }
  };

  function send() {
    const txt = $("#input").val().trim();
    if (!txt) return;
    if (ws.readyState !== 1) { $status.text("未连接,无法发送"); return; }
    renderMsg({ id: "u-" + Date.now(), role: "user", name: "你", emoji: "🙋", color: "#58a6ff", content: txt, round: 0, ts: Date.now()/1000 });
    stickToBottom = true; maybeScrollBottom();
    ws.send(JSON.stringify({ type: "user_message", text: txt }));
    $("#input").val("");
  }
  $("#send").on("click", send);
  $("#input").on("keydown", e => { if (e.key === "Enter") send(); });
  const goHome = () => { try { ws.close(); } catch(e){} location.href = "/"; };
  $("#back").on("click", goHome);
  $("#home").on("click", goHome);

  $start.on("click", () => {
    if (ws.readyState !== 1) return;
    $start.hide();
    $status.text("正在启动辩论…");
    ws.send(JSON.stringify({ type: "start", room_id: roomId }));
  });

  $restart.on("click", () => {
    const curTopic = $("#topic").text().replace(/^议题:/, "").trim();
    const newTopic = prompt("新议题(留空保持原议题):", curTopic) ;
    if (newTopic === null) return;
    const clear = confirm("清空之前的历史记录?\n确定 = 清空 / 取消 = 保留旧 session,新开一轮");
    if (ws.readyState !== 1) { $status.text("未连接"); return; }
    if (clear) clearAll();
    $status.text("正在重启辩论…");
    $start.hide();
    ws.send(JSON.stringify({
      type: "restart",
      room_id: roomId,
      topic: newTopic.trim() || null,
      clear_history: clear,
    }));
    if (newTopic.trim()) $("#topic").text("议题:" + newTopic.trim());
  });

  $finalize.on("click", () => {
    if (ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "finalize" }));
      $status.text("正在生成最终总结…");
    }
  });
});
