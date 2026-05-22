/* config.html — manage LLMs only */
$(async function () {
  const $msg = $("#msg");
  const setMsg = (txt, cls) => $msg.removeClass("error ok").addClass(cls || "muted").text(txt);
  const $llms = $("#llms"), $defLlm = $("#default-llm");

  function uid() { return "llm_" + Math.random().toString(36).slice(2, 9); }
  function esc(s) { return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

  function llmRow(l) {
    const $row = $(`
      <div class="llm-item" data-id="${l.id}">
        <div class="row">
          <div class="field" style="flex:2"><label>名称</label><input class="f-name" type="text" value="${esc(l.name || "")}"></div>
          <div class="field" style="flex:2"><label>模型</label><input class="f-model" type="text" value="${esc(l.model || "")}"></div>
        </div>
        <div class="row">
          <div class="field" style="flex:3"><label>Base URL</label><input class="f-base" type="text" value="${esc(l.base_url || "")}"></div>
          <div class="field" style="flex:3"><label>API Key</label><input class="f-key" type="password" value="${esc(l.api_key || "")}"></div>
        </div>
        <div class="row">
          <span class="tag">id: ${l.id}</span>
          <button class="danger small btn-del">删除</button>
        </div>
      </div>`);
    $row.find(".btn-del").on("click", () => { $row.remove(); refreshDefault(); });
    $row.find("input").on("input", refreshDefault);
    return $row;
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

  function refreshDefault() {
    const items = collectLlms();
    const prev = $defLlm.val();
    $defLlm.empty();
    items.forEach(l => $defLlm.append(`<option value="${l.id}">${esc(l.name)} — ${esc(l.model)}</option>`));
    if (items.find(l => l.id === prev)) $defLlm.val(prev);
  }

  let cfg;
  try { cfg = await $.get("/api/config"); }
  catch (e) { setMsg("无法连接后端", "error"); return; }

  (cfg.llm_configs || []).forEach(l => $llms.append(llmRow(l)));
  refreshDefault();
  $defLlm.val(cfg.default_llm_id || "");

  $("#add-llm").on("click", () => {
    const l = { id: uid(), name: "新 LLM", model: "", base_url: "", api_key: "" };
    $llms.append(llmRow(l));
    refreshDefault();
  });

  $("#save").on("click", async () => {
    const llms = collectLlms();
    if (llms.some(l => !l.name || !l.model)) { setMsg("每个 LLM 都要填名称和模型", "error"); return; }
    setMsg("保存中…");
    try {
      await $.ajax({ url: "/api/config", method: "POST", contentType: "application/json", data: JSON.stringify({ llm_configs: llms, default_llm_id: $defLlm.val() }) });
      setMsg("已保存 ✅", "ok");
    } catch (e) {
      setMsg("保存失败:" + (e.responseText || e.statusText), "error");
    }
  });
});
