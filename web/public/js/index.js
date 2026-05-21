/* index.html — start a debate */
$(async function () {
  const $msg = $("#msg");
  const setMsg = (txt, cls) => $msg.removeClass("error ok").addClass(cls || "muted").text(txt);

  // load personas + config
  let cfg, personas;
  try {
    [cfg, personas] = await Promise.all([
      $.get("/api/config"),
      $.get("/api/personas"),
    ]);
  } catch (e) {
    setMsg("无法连接后端,请稍候刷新", "error");
    return;
  }

  // role chips (skip moderator — always present)
  const enabled = new Set(cfg.agents?.enabled_roles || ["bull", "bear", "risk"]);
  const $roles = $("#roles");
  Object.entries(personas).forEach(([role, p]) => {
    if (role === "moderator") return;
    const on = enabled.has(role);
    const $chip = $(
      `<label class="role-chip ${on ? "on" : ""}">
        <input type="checkbox" ${on ? "checked" : ""} value="${role}">
        <span class="emoji">${p.emoji}</span><span class="name">${p.name}</span>
      </label>`
    );
    $chip.on("click", function (e) {
      // toggle by clicking anywhere on the chip
      const $cb = $(this).find("input");
      if (e.target.tagName !== "INPUT") $cb.prop("checked", !$cb.prop("checked"));
      $(this).toggleClass("on", $cb.prop("checked"));
    });
    $roles.append($chip);
  });

  // LLM list
  const $llm = $("#llm");
  (cfg.llms || []).forEach((l) => {
    $llm.append(`<option value="${l.id}" ${l.id === cfg.default_llm ? "selected" : ""}>${l.name} — ${l.model}</option>`);
  });
  if (!(cfg.llms || []).length) {
    $llm.append(`<option>(无可用 LLM,请先到配置页添加)</option>`);
    $("#start").prop("disabled", true);
    setMsg("尚未配置任何 LLM,请到 /config 添加", "error");
  }

  // prefill rest
  $("#topic").val("BTC 当前是抄底机会还是诱多?多空对决一下。");
  $("#symbol").val(cfg.data_source?.symbol || "BTCUSDT");
  $("#source").val(cfg.data_source?.primary || "binance");
  $("#rounds").val(cfg.agents?.max_rounds || 3);

  $("#start").on("click", async function () {
    const topic = $("#topic").val().trim();
    if (!topic) { setMsg("议题不能为空", "error"); return; }
    const roles = $("#roles input:checked").map(function () { return this.value; }).get();
    if (!roles.length) { setMsg("至少选 1 个角色", "error"); return; }

    // patch config (we keep llm/role_llm untouched here — config page manages those)
    const patched = {
      ...cfg,
      default_llm: $("#llm").val(),
      data_source: { ...(cfg.data_source || {}), primary: $("#source").val(), symbol: $("#symbol").val() },
      agents: { ...(cfg.agents || {}), enabled_roles: roles, max_rounds: parseInt($("#rounds").val(), 10) || 3 },
    };
    setMsg("保存配置中…");
    try {
      await $.ajax({ url: "/api/config", method: "POST", contentType: "application/json", data: JSON.stringify(patched) });
    } catch (e) {
      setMsg("保存失败:" + (e.responseText || e.statusText), "error");
      return;
    }
    sessionStorage.setItem("debate_topic", topic);
    location.href = "/room";
  });
});
