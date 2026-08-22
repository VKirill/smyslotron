"use strict";
function segInit(el, values, cur, cb){
  el.querySelectorAll("button").forEach(b => {
    b.classList.toggle("on", b.dataset.v === cur);
    b.onclick = () => { cb(b.dataset.v);
      el.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); };
  });
}

async function boot(){
  if (PID){
    try{
      const pr = await (await fetch(`api/projects/${PID}`, {credentials:"same-origin"})).json();
      $("#ptitle").textContent = pr.name || "Проект";
      $("#psub").textContent = `${(pr.rows||0).toLocaleString("ru-RU")} фраз · ` +
        (pr.uniq ? `${pr.uniq.toLocaleString("ru-RU")} уникальных смыслов · ` : "") +
        `создан ${pr.created || ""}`;
    }catch(e){}
  }
  META = await (await fetch(DATA_BASE + "meta.json", {cache: "no-cache", credentials: "same-origin"})).json();
  META.provs = Object.keys(META.providers);
  $("#loadmsg").textContent = "Загрузка ядра запросов…";
  const qd = await (await fetch(DATA_BASE + "queries.json", {cache: "no-cache", credentials: "same-origin"})).json();
  INT = null;
  try{
    const ir = await fetch(DATA_BASE + "intents.json", {cache: "no-cache", credentials: "same-origin"});
    if (ir.ok) INT = (await ir.json()).i;
  }catch(e){}
  Q = qd.q; F = qd.f; B = qd.b; D = qd.d || null; G = qd.g || null; W = qd.w || null; L = qd.l || null; N = Q.length; TOTAL = qd.total || N;
  QIDX = new Map(Q.map((t, i) => [t, i]));

  // варианты эмбеддингов: галочки, до 3 колонок одновременно
  state.cols = [META.provs.includes("openai") ? "openai" : META.provs[0]];
  const seg = $("#segProvider");
  seg.classList.add("vlist");
  seg.innerHTML = META.provs.map(p =>
    `<label class="vchk"><input type="checkbox" value="${p}"> ${titleOf(p)}</label>`).join("")
    + (META.provs.length > 2 ? `<div style="margin-top:4px"><button class="link" id="vAll">все разом</button>
       · <button class="link" id="vOne">только первый</button></div>` : "");
  function applyCols(cols){
    state.cols = cols;
    state.sel = null; state.shown = {}; state.expanded.clear();
    state.slider = state.sliders[viewKey()] ?? state.slider;
    $("#slider").value = state.slider;
    savePrefs(); syncUI(); render();
  }
  seg.addEventListener("change", e => {
    const checked = [...seg.querySelectorAll("input:checked")].map(x => x.value);
    if (!checked.length){ e.target.checked = true; return; }
    applyCols(META.provs.filter(p => checked.includes(p)));
  });
  const vAll = $("#vAll"), vOne = $("#vOne");
  if (vAll) vAll.onclick = () => applyCols([...META.provs]);
  if (vOne) vOne.onclick = () => applyCols([META.provs[0]]);

  segInit($("#segMode"), null, state.mode, v => {
    state.mode = v; state.sel = null; state.shown = {};
    $("#modeHint").textContent = MODE_HINT[v];
    savePrefs();
    render();
  });
  $("#modeHint").textContent = MODE_HINT[state.mode];

  $("#sliderGrp").addEventListener("wheel", e => {
    e.preventDefault();
    const step = e.shiftKey ? 100 : 2;
    const sl = $("#slider");
    sl.value = Math.max(0, Math.min(10000, +sl.value + (e.deltaY < 0 ? step : -step)));
    sl.dispatchEvent(new Event("input"));
  }, {passive: false});
  $("#slider").addEventListener("input", e => {
    state.slider = +e.target.value;
    state.sliders[viewKey()] = state.slider;
    state.sel = null; savePrefs(); render();
  });
  $("#minSize").addEventListener("input", e => { state.minSize = Math.max(1, +e.target.value || 1); state.sel = null; render(); });
  $("#anOn").addEventListener("change", e => {
    state.anOn = e.target.checked;
    $("#anAdd").style.display = state.anOn ? "" : "none";
    $("#search").disabled = state.anOn;
    state.sel = null; state.shown = {}; state.expanded.clear();
    savePrefs(); render();
  });
  $("#anAdd").onclick = () => {
    state.anQ.push("");
    state.anFocus = state.anQ.length - 1;
    savePrefs(); render();
  };
  $("#prRun").onclick = e => { e.preventDefault(); runPromptEval(); };
  $("#prJson").onclick = e => { e.preventDefault(); exportPresJSON(); };
  $("#presSort").addEventListener("change", e => { state.presSort = e.target.checked; savePrefs(); render(); });
  $("#pfField").addEventListener("change", () => {
    const v = $("#pfField").value;
    if (!v) state.presFlt = null;
    else {
      const kind = v.startsWith("n:") ? "num" : "txt";
      state.presFlt = kind === "num"
        ? {field: v.slice(2), kind, op: ">=", num: +$("#pfNum").value || 50}
        : {field: v.slice(2), kind, vals: []};
    }
    state.shown = {};
    savePrefs(); renderPresFlt(); render();
  });
  $("#pfOp").addEventListener("change", () => {
    if (state.presFlt){ state.presFlt.op = $("#pfOp").value; savePrefs(); render(); }
  });
  let pfnT;
  $("#pfNum").addEventListener("input", () => {
    clearTimeout(pfnT);
    pfnT = setTimeout(() => {
      if (state.presFlt){ state.presFlt.num = +$("#pfNum").value || 0; savePrefs(); render(); }
    }, 350);
  });
  $("#prSel").addEventListener("change", () => {
    const p = PRLIB[+$("#prSel").value];
    if (p){ $("#prText").value = p.prompt; $("#prSchema").value = p.schema || ""; }
  });
  $("#prSave").onclick = () => {
    const prompt = $("#prText").value.trim();
    if (!prompt){ alert("Промт пустой"); return; }
    const cur = PRLIB[+$("#prSel").value];
    const name = window.prompt("Название промта:", cur ? cur.name : "Мой промт");
    if (!name) return;
    const entry = {name: name.trim(), prompt, schema: $("#prSchema").value.trim()};
    const ex = PRLIB.findIndex(p => p.name === entry.name);
    if (ex >= 0) PRLIB[ex] = entry; else PRLIB.push(entry);
    dbSet("sem_prompts", PRLIB); renderPrLib();
  };
  $("#prDel").onclick = () => {
    const i = +$("#prSel").value;
    if (!PRLIB[i]){ alert("Выбери сохранённый промт"); return; }
    if (!confirm(`Удалить промт «${PRLIB[i].name}»?`)) return;
    PRLIB.splice(i, 1); dbSet("sem_prompts", PRLIB); renderPrLib();
  };

  $("#selTrash").onclick = () => {
    for (const t of SELQ) TRASH.add(t);
    SELQ.clear(); saveTrash(); updateSelBar(); render();
  };
  $("#selMust").onclick = () => {
    if (SELQ.size < 2){ alert("Выбери минимум две фразы"); return; }
    RULES.must.push([...SELQ]);
    SELQ.clear(); saveRules(); updateSelBar(); render();
  };
  $("#selNot").onclick = () => {
    if (SELQ.size < 2){ alert("Выбери минимум две фразы"); return; }
    RULES.not.push([...SELQ]);
    SELQ.clear(); saveRules(); updateSelBar(); render();
  };
  $("#selClear").onclick = () => { SELQ.clear(); updateSelBar(); render(); };

  let mwT;
  $("#minusW").addEventListener("input", e => {
    clearTimeout(mwT);
    mwT = setTimeout(() => {
      state.minusWords = e.target.value.toLowerCase().split(/[,\n]/).map(w => w.trim()).filter(Boolean);
      state.sel = null; state.shown = {};
      savePrefs(); render();
    }, 350);
  });
  $("#trashClear").onclick = () => {
    if (!TRASH.size) return;
    if (!confirm(`Вернуть все ${TRASH.size.toLocaleString("ru-RU")} фраз из корзины в кластеризацию?`)) return;
    TRASH.clear(); saveTrash(); render();
  };
  $("#fMode").addEventListener("change", () => { state.fMode = $("#fMode").value; state.sel = null; state.shown = {}; savePrefs(); render(); });
  $("#fMin").addEventListener("input", e => { state.fMin = Math.max(0, +e.target.value || 0); savePrefs(); if (state.fMode !== "off"){ state.sel = null; render(); } });
  $("#fMetric").addEventListener("change", () => { state.fMetric = $("#fMetric").value; savePrefs(); if (state.fMode !== "off"){ state.sel = null; render(); } });
  $("#search").addEventListener("input", e => { state.search = e.target.value; state.shown = {}; savePrefs(); render(); });
  $("#searchOnly").addEventListener("change", e => { state.searchOnly = e.target.checked; savePrefs(); render(); });
  $("#searchScope").addEventListener("change", () => {
    state.searchScope = $("#searchScope").value;
    state.shown = {};
    savePrefs(); render();
  });
  let tgTimer = null;
  $("#targetGeo").addEventListener("input", e => {
    state.targetGeo = e.target.value;
    state.sel = null; savePrefs(); render();
    if (PID){
      clearTimeout(tgTimer);
      tgTimer = setTimeout(() => fetch(`api/projects/${PID}/target_geo`, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({target_geo: state.targetGeo.trim().toLowerCase()})
      }).catch(() => {}), 800);
    }
  });

  const prefs = await loadPrefs();
  TPLS = (await dbGet("sem_tpl")) || [];
  TPLS.forEach(x => {
    if (x.slider <= 1000) x.slider *= 10;
    if (!x.cols) x.cols = x.ab ? ["openai", "gemini"] : [x.provider || "openai"];
  });
  state.sliders = prefs.sliders || {};
  if (typeof prefs.search === "string") state.search = prefs.search;
  if (typeof prefs.targetGeo === "string") state.targetGeo = prefs.targetGeo;
  if (["hard", "avg", "soft"].includes(prefs.mode)){
    state.mode = prefs.mode;
    document.querySelectorAll("#segMode button").forEach(b => b.classList.toggle("on", b.dataset.v === state.mode));
    $("#modeHint").textContent = MODE_HINT[state.mode];
  }
  if (Array.isArray(prefs.anQ) && prefs.anQ.length) state.anQ = prefs.anQ.map(String);
  if (Array.isArray(prefs.cmpTpls))
    state.cmpTpls = prefs.cmpTpls.filter(n => TPLS.some(t => t.name === n));
  if (Array.isArray(prefs.minusWords)){
    state.minusWords = prefs.minusWords.map(String);
    $("#minusW").value = state.minusWords.join("\n");
  }
  if (prefs.searchOnly){ state.searchOnly = true; $("#searchOnly").checked = true; }
  if (["all", "name"].includes(prefs.searchScope)){
    state.searchScope = prefs.searchScope;
    $("#searchScope").value = state.searchScope;
  }
  if (["off", "hide", "nogroup"].includes(prefs.fMode)){
    state.fMode = prefs.fMode;
    state.fMin = Math.max(0, +prefs.fMin || 0);
    if (["f", "b"].includes(prefs.fMetric)) state.fMetric = prefs.fMetric;
    $("#fMode").value = state.fMode;
    $("#fMin").value = state.fMin;
    $("#fMetric").value = state.fMetric;
  }
  TRASH = new Set((await dbGet("sem_trash:" + (PID || "demo"))) || []);
  const rl = await dbGet("sem_rules:" + (PID || "demo"));
  if (rl && Array.isArray(rl.must) && Array.isArray(rl.not)) RULES = rl;
  PRES = (await dbGet("sem_pres:" + (PID || "demo"))) || {};
  PRLIB = (await dbGet("sem_prompts")) || [];
  renderPrLib();
  if (PRLIB.length){
    $("#prText").value = PRLIB[0].prompt;
    $("#prSchema").value = PRLIB[0].schema || "";
    $("#prSel").value = "0";
  }
  renderPresFlt();
  // применить сохранённый пользователем порядок блоков сайдбара
  const lay = await dbGet("sem_layout");
  if (lay) document.querySelectorAll(".tabpane").forEach(pane => {
    const order = lay[pane.id];
    if (!Array.isArray(order)) return;
    const items = [...pane.children].filter(x => x.dataset.g);
    const map = new Map(items.map(x => [x.dataset.g, x]));
    const seq = order.filter(g => map.has(g)).map(g => map.get(g))
      .concat(items.filter(x => !order.includes(x.dataset.g)));
    for (const el of seq) pane.appendChild(el);
  });
  if (prefs.presSort){ state.presSort = true; $("#presSort").checked = true; }
  if (prefs.presFlt && prefs.presFlt.field) state.presFlt = prefs.presFlt;
  if (prefs.anOn){
    state.anOn = true;
    $("#anOn").checked = true;
    $("#anAdd").style.display = "";
    $("#search").disabled = true;
  }
  $("#targetGeo").value = state.targetGeo;
  // даталист: топ-30 гео по числу фраз
  if (G){
    const cnt = {};
    for (const g of G) if (g) cnt[g] = (cnt[g] || 0) + 1;
    $("#geoList").innerHTML = Object.entries(cnt).sort((a, b) => b[1] - a[1])
      .slice(0, 30).map(([g, n]) => `<option value="${esc(g)}">${n} фраз</option>`).join("");
  }
  let pv = prefs.view;
  if (pv === "ab") pv = ["openai", "gemini"];
  else if (typeof pv === "string") pv = [pv];
  if (Array.isArray(pv)){
    const ok = pv.filter(x => META.provs.includes(x));
    if (ok.length) state.cols = ok;
  }
  state.slider = state.sliders[viewKey()] ?? state.slider;
  syncUI();

  $("#slicebtn").onclick = e => { e.preventDefault(); exportSlice(); };
  $("#markbtn").onclick = e => { e.preventDefault(); copyMarkers(e.currentTarget); };

  document.querySelectorAll("#sideTabs button").forEach(b => b.onclick = () => {
    document.querySelectorAll("#sideTabs button").forEach(x => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("on", p.id === "pane-" + b.dataset.v));
  });

  // персональный порядок блоков: перетаскивание за заголовок, сохранение в БД
  function saveLayout(){
    const lay = {};
    document.querySelectorAll(".tabpane").forEach(p => {
      lay[p.id] = [...p.children].filter(x => x.dataset.g).map(x => x.dataset.g);
    });
    dbSet("sem_layout", lay);
  }
  let dragEl = null;
  document.querySelectorAll(".tabpane > [data-g]").forEach(el => {
    const handle = el.querySelector(":scope > label") ||
                   el.querySelector(":scope > summary") || el;
    handle.setAttribute("draggable", "true");
    handle.addEventListener("dragstart", e => {
      dragEl = el; el.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", el.dataset.g);
    });
    handle.addEventListener("dragend", () => {
      el.classList.remove("dragging"); dragEl = null; saveLayout();
    });
    el.addEventListener("dragover", e => {
      if (!dragEl || dragEl === el || dragEl.parentElement !== el.parentElement) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      el.parentElement.insertBefore(dragEl, e.clientY < r.top + r.height / 2 ? el : el.nextSibling);
    });
  });

  $("#savesl").onclick = async e => {
    e.preventDefault();
    if (!PID){ alert("Демо-режим: сохранение среза недоступно"); return; }
    const v = state.cols[0];
    if (!confirm(`Зафиксировать срез: ${titleOf(v)} · ${MODE_NAME[state.mode]} · ${(state.slider / 100).toFixed(1)}% · мин.${state.minSize}?`)) return;
    try{
      await fetch(`api/projects/${PID}/slice`, {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({variant: v, mode: state.mode,
                              slider: state.slider, min_size: state.minSize})});
      alert("Срез зафиксирован. Экспорт — кнопка «⬇ Excel»: она всегда отдаёт актуальное состояние (срез, корзину, минус-слова, правила).");
    }catch(err){ alert("Не удалось сохранить: " + err.message); }
  };

  $("#tplAdd").onclick = () => {
    const def = `${state.cols.map(k => VARIANT_SHORT[k] || k).join("+")} ${(state.slider / 100).toFixed(1)}%`;
    const name = prompt("Название шаблона:", def);
    if (!name) return;
    const ts = loadTpls();
    ts.push({name: name.trim(), cols: [...state.cols], mode: state.mode,
             slider: state.slider, minSize: state.minSize, search: state.search});
    saveTpls(ts); renderTpls();
  };
  renderTpls();

  await loadTree(state.cols[0] + "_" + state.mode);
  $("#loader").style.display = "none";
  render();
}
boot().catch(e => { $("#loadmsg").textContent = "Ошибка загрузки: " + e; });
