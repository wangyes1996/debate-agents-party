/* agents.html — CRUD agents */
$(async function () {
  let cfg = null, agents = [];

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

  async function reload() {
    const [c, a] = await Promise.all([$.get("/api/config"), $.get("/api/agents")]);
    cfg = c; agents = a.agents || [];
    render();
  }

  function render() {
    const $list = $("#agents-list").empty();
    if (!agents.length) {
      $list.append('<div class="card muted">还没有 agent。</div>');
      return;
    }
    agents.forEach(a => {
      const llmName = a.llm_id ? (cfg.llm_configs.find(x=>x.id===a.llm_id)?.name || a.llm_id) : "(默认)";
      const $card = $(`
        <div class="card agent-card" style="border-left:4px solid ${a.color}">
          <div class="row" style="align-items:flex-start; gap:14px">
            <div style="flex:1; min-width:0">
              <div class="agent-name">
                <span style="font-size:22px">${a.emoji}</span>
                <strong style="color:${a.color}">${esc(a.name)}</strong>
                ${a.is_moderator ? '<span class="tag" style="border-color:#d29922; color:#d29922">主持人</span>' : ""}
                ${a.web_search !== false ? '<span class="tag" style="border-color:#3fb950; color:#3fb950" title="发言前会联网搜索">🌐 联网</span>' : '<span class="tag muted" title="不联网">🚫 离线</span>'}
                ${a.builtin ? '<span class="tag">预设</span>' : ""}
                <span class="tag">LLM: ${esc(llmName)}</span>
              </div>
              <div class="muted agent-sys">${esc((a.system || "").slice(0, 220))}${(a.system||"").length>220?"…":""}</div>
            </div>
            <div class="room-actions">
              <button class="ghost small btn-edit">编辑</button>
              <button class="danger small btn-del">删除</button>
            </div>
          </div>
        </div>`);
      $card.find(".btn-edit").on("click", () => openModal(a));
      $card.find(".btn-del").on("click", async () => {
        if (!confirm(`删除 agent「${a.name}」?所有引用 ta 的房间会自动移除该角色。`)) return;
        await $.ajax({ url: "/api/agents/" + a.id, method: "DELETE" });
        await reload();
      });
      $list.append($card);
    });
  }

  let editingId = null;
  const $modal = $("#modal");

  function openModal(a) {
    editingId = a ? a.id : null;
    $("#modal-title").text(a ? "编辑 Agent" : "新建 Agent");
    $("#m-name").val(a ? a.name : "");
    $("#m-emoji").val(a ? a.emoji : "💬");
    $("#m-color").val(a ? a.color : "#7c3aed");
    $("#m-moderator").prop("checked", a ? !!a.is_moderator : false);
    $("#m-websearch").prop("checked", a ? (a.web_search !== false) : true);
    $("#m-system").val(a ? a.system : "");
    $("#m-msg").text("");

    const $sel = $("#m-llm").empty();
    $sel.append(`<option value="">(用默认)</option>`);
    (cfg.llm_configs || []).forEach(l => $sel.append(`<option value="${l.id}">${esc(l.name)} — ${esc(l.model)}</option>`));
    if (a) $sel.val(a.llm_id || "");
    $modal.removeClass("hidden");
  }
  function closeModal() { $modal.addClass("hidden"); editingId = null; }

  $("#new-agent").on("click", () => openModal(null));
  $("#m-cancel").on("click", closeModal);
  $modal.on("click", e => { if (e.target === $modal[0]) closeModal(); });

  $("#m-save").on("click", async () => {
    const body = {
      name: $("#m-name").val().trim(),
      emoji: $("#m-emoji").val().trim() || "💬",
      color: $("#m-color").val().trim() || "#888",
      llm_id: $("#m-llm").val() || "",
      is_moderator: $("#m-moderator").is(":checked"),
      web_search: $("#m-websearch").is(":checked"),
      system: $("#m-system").val(),
    };
    if (!body.name) return $("#m-msg").addClass("error").text("请填名称");
    if (!body.system.trim()) return $("#m-msg").addClass("error").text("请填 system prompt");
    try {
      if (editingId) {
        await $.ajax({ url: "/api/agents/" + editingId, method: "PUT", contentType: "application/json", data: JSON.stringify({ id: editingId, ...body }) });
      } else {
        await $.ajax({ url: "/api/agents", method: "POST", contentType: "application/json", data: JSON.stringify(body) });
      }
      closeModal();
      await reload();
    } catch (e) {
      $("#m-msg").addClass("error").text("保存失败:" + (e.responseText || e.statusText));
    }
  });

  try { await reload(); }
  catch (e) { $("#agents-list").html('<div class="card error">无法连接后端</div>'); }
});
