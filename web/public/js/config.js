/* config.html — manage LLMs + role overrides */
$(async function () {
  const $msg = $("#msg");
  const setMsg = (txt, cls) => $msg.removeClass("error ok").addClass(cls || "muted").text(txt);
  const $llms = $("#llms"), $defLlm = $("#default-llm"), $roleOv = $("#role-overrides");

  function uid() { return "llm_" + Math.random().toString(36).slice(2, 9); }
  function llmRow(l) {
    const $row = $(`
      <div class="llm-item" data-id="${l.id}">
        <div class="row">
          <div class="field" style="flex:2"><label>名称</label><input class="f-name" type="text" value="${l.name || ""}"></div>
          <div class="field" style="flex:2"><label>模型</label><input class="f-model" type="text" value="${l.model || ""}"></div>
        </div>
        <div class="row">
          <div class="field" style="flex:3"><label>Base URL</label><input class="f-base" type="text" value="${l.base_url || ""}"></div>
          <div class="field" style="flex:3"><label>API Key</label><input class="f-key" type="password" value="${l.api_key || ""}"></div>
        </div>
        <div class="row">
          <span class="tag">id: ${l.id}</span>
          <button class="danger small btn-del">删除</button>
        </div>
      </div>`);
    $row.find(".btn-del").on("click", () => { $row.remove(); refreshSelectors(); });
    ["input"].forEach(ev => $row.find("input").on(ev, refreshSelectors));
    return $row;
  }

  function refreshSelectors() {
    const items = collectLlms();
    const prevDef = $defLlm.val();
    $defLlm.empty();
    items.forEach(l => $defLlm.append(`<option value="${l.id}">${l.name} — ${l.model}</option>`));
    if (items.find(l => l.id === prevDef)) $defLlm.val(prevDef);
    $roleOv.find("select").each(function () {
      const cur = $(this).val();
      const $sel = $(this).empty();
      $sel.append(`<option value="">(用默认)</option>`);
      items.forEach(l => $sel.append(`<option value="${l.id}">${l.name}</option>`));
      $sel.val(cur);
    });
  }

  function collectLlms() {
    return $llms.find(".llm-item").map(function () {
      return {
        id: $(this).data("id"),
        name: $(this).find(".f-name").val().trim(),
        model: $(this).find(".f-model").val().trim(),
        base_url: $(this).find(".f-base").val().trim(),
        api_key: $(this).find(".f-key").val(),
      };
    }).get();
  }

  // load
  let cfg, personas;
  try {
    [cfg, personas] = await Promise.all([$.get("/api/config"), $.get("/api/personas")]);
  } catch (e) { setMsg("无法连接后端", "error"); return; }

  (cfg.llms || []).forEach(l => $llms.append(llmRow(l)));
  // role overrides for non-moderator (moderator can also pick)
  const allRoles = Object.entries(personas);
  allRoles.forEach(([role, p]) => {
    const cur = (cfg.agents?.role_llm || {})[role] || "";
    $roleOv.append(`
      <div class="row" style="margin-bottom:8px">
        <span style="flex:1">${p.emoji} ${p.name} <span class="muted">(${role})</span></span>
        <select class="role-llm" data-role="${role}" style="flex:2"></select>
      </div>`);
    $roleOv.find(`[data-role="${role}"]`).data("preset", cur);
  });
  refreshSelectors();
  // apply preserved values
  $roleOv.find("select").each(function () { $(this).val($(this).data("preset") || ""); });
  $defLlm.val(cfg.default_llm || "");

  $("#add-llm").on("click", () => {
    const l = { id: uid(), name: "新 LLM", model: "", base_url: "", api_key: "" };
    $llms.append(llmRow(l));
    refreshSelectors();
  });

  $("#save").on("click", async () => {
    const llms = collectLlms();
    if (llms.some(l => !l.name || !l.model)) { setMsg("每个 LLM 都要填名称和模型", "error"); return; }
    const role_llm = {};
    $roleOv.find("select").each(function () {
      const v = $(this).val(); if (v) role_llm[$(this).data("role")] = v;
    });
    const patched = {
      ...cfg,
      llms,
      default_llm: $defLlm.val(),
      agents: { ...(cfg.agents || {}), role_llm },
    };
    setMsg("保存中…");
    try {
      await $.ajax({ url: "/api/config", method: "POST", contentType: "application/json", data: JSON.stringify(patched) });
      setMsg("已保存 ✅", "ok");
      cfg = patched;
    } catch (e) {
      setMsg("保存失败:" + (e.responseText || e.statusText), "error");
    }
  });
});
