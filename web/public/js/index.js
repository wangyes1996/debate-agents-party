/* index.html — list rooms + create/edit/delete */
$(async function () {
  const $msg = $("#msg");
  const setMsg = (txt, cls) => $msg.removeClass("error ok").addClass(cls || "muted").text(txt);

  let cfg = null;
  let agents = [];
  let rooms = [];

  async function reload() {
    const [c, a, r] = await Promise.all([
      $.get("/api/config"),
      $.get("/api/agents"),
      $.get("/api/rooms"),
    ]);
    cfg = c;
    agents = a.agents || [];
    rooms = r.rooms || [];
    render();
  }

  function agentById(id) { return agents.find(a => a.id === id); }

  function render() {
    const $g = $("#rooms").empty();
    if (!rooms.length) {
      $g.append('<div class="card muted">还没有辩论室,点上面的「新建辩论室」开一场吧。</div>');
      return;
    }
    rooms.forEach(r => {
      const mod = agentById(r.moderator_id);
      const parts = (r.agent_ids || []).map(id => agentById(id)).filter(Boolean);
      const $card = $(`
        <div class="card room-card" data-id="${r.id}">
          <div class="row" style="align-items:flex-start; gap:14px">
            <div style="flex:1; min-width:0">
              <div class="room-name">${escapeHtml(r.name || "(未命名)")}</div>
              <div class="room-topic muted">${escapeHtml(r.topic || "")}</div>
              <div class="room-meta">
                <span class="tag">🎤 ${mod ? mod.emoji + " " + escapeHtml(mod.name) : "(未指定主持人)"}</span>
                ${parts.map(p => `<span class="tag" style="border-color:${p.color}">${p.emoji} ${escapeHtml(p.name)}</span>`).join("")}
                <span class="tag">${parts.length} 位 agent · 上限 ${r.max_turns} 轮</span>
              </div>
            </div>
            <div class="room-actions">
              <button class="btn-enter">进入 →</button>
              <button class="ghost small btn-edit">编辑</button>
              <button class="danger small btn-del">删除</button>
            </div>
          </div>
        </div>`);
      $card.find(".btn-enter").on("click", () => { location.href = "/room?id=" + encodeURIComponent(r.id); });
      $card.find(".btn-edit").on("click", () => openModal(r));
      $card.find(".btn-del").on("click", async () => {
        if (!confirm(`删除房间「${r.name}」?`)) return;
        await $.ajax({ url: "/api/rooms/" + r.id, method: "DELETE" });
        await reload();
      });
      $g.append($card);
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  // ---- modal ----
  const $modal = $("#modal");
  let editingId = null;

  function openModal(room) {
    editingId = room ? room.id : null;
    $("#modal-title").text(room ? "编辑辩论室" : "新建辩论室");
    $("#m-name").val(room ? room.name : "");
    $("#m-topic").val(room ? room.topic : "");
    $("#m-turns").val(room ? room.max_turns : 16);
    $("#m-msg").text("");

    const moderators = agents.filter(a => a.is_moderator);
    const $modSel = $("#m-moderator").empty();
    moderators.forEach(a => $modSel.append(`<option value="${a.id}">${a.emoji} ${escapeHtml(a.name)}</option>`));
    if (room) $modSel.val(room.moderator_id);

    const $ag = $("#m-agents").empty();
    const picked = new Set(room ? room.agent_ids : agents.filter(a=>!a.is_moderator).slice(0,5).map(a=>a.id));
    agents.filter(a => !a.is_moderator).forEach(a => {
      const on = picked.has(a.id);
      const $chip = $(`<label class="role-chip ${on ? "on" : ""}" data-id="${a.id}">
        <input type="checkbox" ${on ? "checked" : ""} value="${a.id}">
        <span class="emoji">${a.emoji}</span><span class="name">${escapeHtml(a.name)}</span>
      </label>`);
      $chip.on("click", function (e) {
        const $cb = $(this).find("input");
        if (e.target.tagName !== "INPUT") $cb.prop("checked", !$cb.prop("checked"));
        $(this).toggleClass("on", $cb.prop("checked"));
      });
      $ag.append($chip);
    });
    $modal.removeClass("hidden");
  }
  function closeModal() { $modal.addClass("hidden"); editingId = null; }

  $("#new-room").on("click", () => {
    if (!agents.length) { setMsg("请先在 Agents 页添加 agent", "error"); return; }
    if (!agents.some(a => a.is_moderator)) { setMsg("没有主持人 agent,请先到 Agents 页添加一个 is_moderator=true 的", "error"); return; }
    openModal(null);
  });
  $("#m-cancel").on("click", closeModal);
  $modal.on("click", (e) => { if (e.target === $modal[0]) closeModal(); });

  $("#m-save").on("click", async () => {
    const name = $("#m-name").val().trim();
    const topic = $("#m-topic").val().trim();
    const moderator_id = $("#m-moderator").val();
    const agent_ids = $("#m-agents input:checked").map(function(){return this.value;}).get();
    const max_turns = parseInt($("#m-turns").val(), 10) || 16;
    if (!name) return $("#m-msg").addClass("error").text("请填名称");
    if (!moderator_id) return $("#m-msg").addClass("error").text("请选主持人");
    if (agent_ids.length < 2) return $("#m-msg").addClass("error").text("至少选 2 个参与 agent");
    const body = { name, topic, moderator_id, agent_ids, max_turns };
    try {
      if (editingId) {
        await $.ajax({ url: "/api/rooms/" + editingId, method: "PUT", contentType: "application/json", data: JSON.stringify({ id: editingId, ...body }) });
      } else {
        await $.ajax({ url: "/api/rooms", method: "POST", contentType: "application/json", data: JSON.stringify(body) });
      }
      closeModal();
      await reload();
    } catch (e) {
      $("#m-msg").addClass("error").text("保存失败:" + (e.responseText || e.statusText));
    }
  });

  try { await reload(); }
  catch (e) { setMsg("无法连接后端", "error"); }
});
