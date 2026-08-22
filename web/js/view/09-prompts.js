"use strict";
/* ---------- прогон пользовательского промта по папкам среза ---------- */
/* фильтр папок по оценкам: поля берутся из фактических JSON-ответов */
function presFields(){
  const num = new Set(), txt = new Set();
  for (const v of Object.values(PRES)){
    if (!v || typeof v !== "object") continue;
    for (const [k, val] of Object.entries(v)){
      if (typeof val === "number") num.add(k);
      else if (typeof val === "string" && val) txt.add(k);
    }
  }
  return {num: [...num], txt: [...txt].filter(k => !num.has(k))};
}
function presVals(field){
  const cnt = {};
  for (const v of Object.values(PRES)){
    const x = v && v[field];
    if (typeof x === "string" && x) cnt[x] = (cnt[x] || 0) + 1;
  }
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 30);
}
function presFltActive(){ return !!(state.presFlt && state.presFlt.field); }
function testPresF(r){
  const f = state.presFlt;
  if (!presFltActive()) return true;
  if (!r || typeof r !== "object") return false;
  const v = r[f.field];
  if (f.kind === "num"){
    if (typeof v !== "number") return false;
    switch (f.op){
      case ">=": return v >= f.num;
      case "<=": return v <= f.num;
      case ">": return v > f.num;
      case "<": return v < f.num;
      default: return v === f.num;
    }
  }
  return !f.vals.length || f.vals.includes(String(v));
}
function renderPresFlt(){
  const sel = $("#pfField");
  if (!sel) return;
  const {num, txt} = presFields();
  const cur = state.presFlt || {};
  sel.innerHTML = '<option value="">— фильтр по оценкам —</option>' +
    num.map(k => `<option value="n:${esc(k)}">${esc(k)} (число)</option>`).join("") +
    txt.map(k => `<option value="t:${esc(k)}">${esc(k)} (текст)</option>`).join("");
  sel.value = cur.field ? (cur.kind === "num" ? "n:" : "t:") + cur.field : "";
  if (sel.value === "" && cur.field) sel.value = "";  // поле исчезло из данных
  const isNum = cur.field && cur.kind === "num";
  $("#pfOp").style.display = isNum ? "" : "none";
  $("#pfNum").style.display = isNum ? "" : "none";
  if (isNum){ $("#pfOp").value = cur.op || ">="; $("#pfNum").value = cur.num ?? 50; }
  const pv = $("#pfVals");
  if (cur.field && cur.kind === "txt"){
    pv.innerHTML = presVals(cur.field).map(([v, n]) =>
      `<label style="display:flex; align-items:center; gap:7px; padding:2px 0; cursor:pointer">
        <input type="checkbox" class="pfv" value="${esc(v).replace(/"/g, "&quot;")}"
          ${cur.vals.includes(v) ? "checked" : ""}> ${esc(v)}
        <span style="color:var(--muted); margin-left:auto">${n}</span></label>`).join("");
    pv.querySelectorAll(".pfv").forEach(cb => cb.onchange = () => {
      const set = new Set(state.presFlt.vals);
      cb.checked ? set.add(cb.value) : set.delete(cb.value);
      state.presFlt.vals = [...set];
      savePrefs(); render();
    });
  } else pv.innerHTML = "";
}

async function runPromptEval(){
  const prompt = $("#prText").value.trim();
  if (!prompt){ alert("Напиши промт"); return; }
  const {clusters} = await sliceClusters();
  const all = clusters.filter(c => c.idxs.length >= state.minSize);
  const todo = all.filter(c => PRES[Q[c.top]] === undefined);
  if (!all.length){ alert("В текущем срезе нет сформированных папок"); return; }
  let list = todo;
  if (!todo.length){
    if (!confirm(`Все ${all.length.toLocaleString("ru-RU")} папок уже оценены. Переоценить заново (старые оценки перезапишутся)?`)) return;
    list = all;
  } else if (todo.length < all.length){
    if (!confirm(`Оценено ${(all.length - todo.length).toLocaleString("ru-RU")} из ${all.length.toLocaleString("ru-RU")} папок. Дооценить оставшиеся ${todo.length.toLocaleString("ru-RU")}?\n(Отмена — ничего не делать)`)) return;
  } else if (!confirm(`Прогнать промт по ${list.length.toLocaleString("ru-RU")} папкам через DeepSeek?\n«Без группы» и папки меньше мин. размера не участвуют.`)) return;
  const schema = $("#prSchema").value.trim();
  const items = list.map((c, k) => ({id: k,
    text: [...c.idxs].sort((x, y) => F[y] - F[x]).slice(0, 10).map(i => Q[i]).join("; ")}));
  const prog = $("#prProg");
  const usage = {in: 0, out: 0};
  let done = 0, failed = 0;
  const batches = [];
  for (let i = 0; i < items.length; i += 20) batches.push(items.slice(i, i + 20));
  let saveT = null;
  const saveSoon = () => {  // промежуточное сохранение: обновление страницы ничего не теряет
    clearTimeout(saveT);
    saveT = setTimeout(() => dbSet("sem_pres:" + (PID || "demo"), PRES), 1500);
  };
  const runBatch = async b => {
    try{
      const r = await fetch("api/prompt_eval", {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({prompt, schema, items: b})});
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      for (const it of d.items || []){
        const c = list[+it.id];
        if (!c) continue;
        const {id, ...rest} = it;
        PRES[Q[c.top]] = rest;
      }
      usage.in += d.usage?.prompt_tokens || 0;
      usage.out += d.usage?.completion_tokens || 0;
      saveSoon();
    }catch(e){ failed++; }
    done++;
    const evaluated = list.filter(c => PRES[Q[c.top]] !== undefined).length;
    prog.textContent = `Оценка: проверено ${fmt(evaluated)} из ${fmt(list.length)}, осталось ${fmt(list.length - evaluated)}${failed ? ` · ошибок ${failed}` : ""}…`;
    updateRunBtn(evaluated, list.length);
  };
  // семафор: до 500 одновременных запросов (лимит DeepSeek — 2500 соединений)
  const LIMIT = 500;
  let idx = 0;
  const worker = async () => { while (idx < batches.length) await runBatch(batches[idx++]); };
  await Promise.all(Array.from({length: Math.min(LIMIT, batches.length)}, worker));
  clearTimeout(saveT);
  dbSet("sem_pres:" + (PID || "demo"), PRES);
  const usd = (usage.in * 0.28 + usage.out * 0.42) / 1e6;
  const evaluated = list.filter(c => PRES[Q[c.top]] !== undefined).length;
  prog.textContent = `Готово: проверено ${fmt(evaluated)} из ${fmt(list.length)} папок за этот прогон` +
    (list.length - evaluated ? `, без оценки осталось ${fmt(list.length - evaluated)} (нажми ▶ ещё раз)` : "") +
    ` · ~$${usd.toFixed(3)}${failed ? ` · ошибок батчей: ${failed}` : ""}`;
  updateRunBtn();
  renderPresFlt();
  render();
}

function updateRunBtn(done, total){
  const b = $("#prRun");
  if (!b) return;
  b.textContent = total === undefined ? "▶ Прогнать по папкам среза"
    : `⏳ ${fmt(done)} / ${fmt(total)} папок…`;
  b.disabled = total !== undefined;
}

function renderPrLib(){
  const sel = $("#prSel");
  const cur = sel.value;
  sel.innerHTML = '<option value="">— новый промт —</option>' +
    PRLIB.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
  sel.value = cur && PRLIB[+cur] ? cur : sel.value;
}

async function copyMarkers(btn){
  const {clusters} = await sliceClusters();
  const markers = clusters.filter(c => c.idxs.length >= state.minSize).map(c => Q[c.top]);
  const old = btn.textContent;
  try{
    await navigator.clipboard.writeText(markers.join("\n"));
    btn.textContent = `✓ ${markers.length}`;
  }catch(err){ btn.textContent = "✗"; }
  setTimeout(() => { btn.textContent = old; }, 1400);
}


async function exportPresJSON(){
  // кластеры текущего среза с учётом мин. размера и активного фильтра по оценкам
  const {vkey, clusters} = await sliceClusters();
  const out = [];
  for (const c of clusters){
    if (c.idxs.length < state.minSize) continue;
    const ev = PRES[Q[c.top]];
    if (presFltActive() && !testPresF(ev)) continue;
    out.push({
      cluster: Q[c.top],
      sum_freq: c.sum,
      phrases: [...c.idxs].sort((x, y) => F[y] - F[x]).map(i => Q[i]),
      eval: ev ?? null,
    });
  }
  if (!out.length){ alert("Под фильтр не попала ни одна папка"); return; }
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  const modeName = {hard: "hard", avg: "avg", soft: "soft"}[state.mode];
  a.download = `clusters_${vkey}_${modeName}_${out.length}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
