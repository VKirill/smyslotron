"use strict";
/* ---------- корзина и минус-слова: полное исключение из кластеризации ---------- */
let TRASH = new Set();  // тексты фраз в корзине (persist: sem_trash:<pid>)
function saveTrash(){ dbSet("sem_trash:" + (PID || "demo"), [...TRASH]); }
let RULES = {must: [], not: []};   // правила связок (persist: sem_rules:<pid>)
let PRES = {};                     // результаты промт-оценки: маркер папки -> объект
let DONE = new Set();              // «проработанные» кластеры: якорь (текст топ-фразы); persist sem_done:<pid>
function saveDone(){ dbSet("sem_done:" + (PID || "demo"), [...DONE]); }
let PRLIB = [];                    // сохранённые промты: [{name, prompt, schema}]
function scoreOf(r){
  if (!r || typeof r !== "object") return null;
  if (typeof r.score === "number") return r.score;
  for (const v of Object.values(r)) if (typeof v === "number") return v;
  return null;
}
let QIDX = new Map();              // текст фразы -> индекс
let SELQ = new Set();              // выделенные галочками фразы (тексты)
let SELC = new Set();              // отмеченные для связки кластеры (якорь = маркер-фраза)
function saveRules(){ dbSet("sem_rules:" + (PID || "demo"), RULES); }
let _mwCache = {key: "", parsed: []};
function _mwParsed(){
  const key = state.minusWords.join("|");
  if (_mwCache.key !== key)
    _mwCache = {key, parsed: state.minusWords.map(e => e.split(/\s+/).filter(Boolean))};
  return _mwCache.parsed;
}
function minusHit(i){
  const entries = _mwParsed();
  if (!entries.length) return false;
  const raw = Q[i].toLowerCase().split(/\s+/);
  const lem = L && L[i] ? L[i].split(/\s+/) : null;
  // запись из нескольких слов («кухня стиль») минусует фразу, если ВСЕ слова
  // встречаются в ней — в любом месте и любой форме (по леммам)
  for (const words of entries)
    if (words.every(w => raw.includes(w) || (lem && lem.includes(w)))) return true;
  return false;
}
function excluded(i){ return TRASH.has(Q[i]) || minusHit(i); }

function updateSelBar(){
  $("#selbar").classList.toggle("on", SELQ.size > 0);
  $("#selCnt").textContent = SELQ.size;
}

/* ---------- контекстное меню кластера (правая кнопка по шапке папки) ---------- */
function showCtx(e, c){
  hideCtx();
  const m = document.createElement("div");
  m.id = "ctxmenu";
  const n = c.idxs.length;
  const anchor = Q[c.top];
  const linkCnt = SELC.size + (SELC.has(anchor) ? 0 : 1);
  const items = [
    ["⧉", "Копировать фразы кластера", () => copyCluster(c, document.createElement("button"))],
    [DONE.has(anchor) ? "✅" : "🟢",
     DONE.has(anchor) ? "Снять пометку «проработан»" : "Проработан",
     () => { DONE.has(anchor) ? DONE.delete(anchor) : DONE.add(anchor); saveDone(); render(); }, "done"],
    [SELC.has(anchor) ? "✖" : "☑",
     SELC.has(anchor) ? "Снять отметку связки" : "Отметить для связки (Ctrl+клик; Shift+клик — диапазон)",
     () => { SELC.has(anchor) ? SELC.delete(anchor) : SELC.add(anchor); render(); }],
    ...(linkCnt >= 2 ? [
      ["🔗", `Слить отмеченные кластеры в один (${linkCnt})`, () => {
        RULES.must.push([...new Set([...SELC, anchor])]);
        SELC.clear(); saveRules(); render();
      }],
      ["⛓", `Отмеченные — никогда вместе (${linkCnt})`, () => {
        RULES.not.push([...new Set([...SELC, anchor])]);
        SELC.clear(); saveRules(); render();
      }],
    ] : []),
    ...(SELC.size ? [["🧹", `Сбросить отметки связки (${SELC.size})`, () => { SELC.clear(); render(); }]] : []),
    ["☑", `Выделить фразы кластера (${fmt(n)})`, () => {
      for (const i of c.idxs) SELQ.add(Q[i]);
      updateSelBar(); render();
    }],
    ["✖", `Снять выделение с кластера`, () => {
      for (const i of c.idxs) SELQ.delete(Q[i]);
      updateSelBar(); render();
    }],
    ["🗑", `Кластер в корзину (${fmt(n)} фраз)`, () => {
      for (const i of c.idxs) TRASH.add(Q[i]);
      saveTrash(); render();
    }],
    ["📤", `Перенести кластер в другой проект`, () => {
      const ph = [];
      for (const i of c.idxs){ ph.push(Q[i]); if (D && D[i]) ph.push(...D[i]); }
      openMoveDialog(ph, 1);
    }],
  ];
  for (const [ico, label, fn, cls] of items){
    const d = document.createElement("div");
    d.className = "ctxitem" + (cls ? " " + cls : "");
    d.innerHTML = `<span>${ico}</span><span>${label}</span>`; // guardian: allow статические строки из кода
    d.onclick = () => { hideCtx(); fn(); };
    m.appendChild(d);
  }
  document.body.appendChild(m);
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.min(e.clientX, innerWidth - w - 8) + "px";
  m.style.top = Math.min(e.clientY, innerHeight - h - 8) + "px";
}
function hideCtx(){ const m = document.getElementById("ctxmenu"); if (m) m.remove(); }
addEventListener("click", hideCtx);
addEventListener("scroll", hideCtx, true);
addEventListener("keydown", e => { if (e.key === "Escape") hideCtx(); });
function renderRules(){
  const el = $("#rulesList");
  if (!el) return;
  const rows = [];
  RULES.must.forEach((g, i) => rows.push({icon: "🔗", g, kind: "must", i}));
  RULES.not.forEach((g, i) => rows.push({icon: "⛓", g, kind: "not", i}));
  el.innerHTML = rows.length ? "" : '<div style="color:var(--muted)">правил пока нет</div>'; // guardian: allow статическая строка
  for (const r of rows){
    const d = document.createElement("div");
    d.className = "q";
    d.style.cursor = "pointer";
    d.title = r.g.join("\n") + "\n\nКлик — удалить правило";
    d.innerHTML = `<span class="qt">${r.icon} ${esc(r.g[0])} + ${r.g.length > 2 ? `ещё ${r.g.length - 1}` : esc(r.g[1] || "")}</span><span class="qf">×</span>`; // guardian: allow значения проходят esc()
    d.onclick = () => { RULES[r.kind].splice(r.i, 1); saveRules(); render(); };
    el.appendChild(d);
  }
}


/* ---------- перенос фраз в другой проект ---------- */
async function openMoveDialog(phrases, nFolders, singles){
  hideCtx();
  singles = singles || [];
  if (!phrases.length && !singles.length){ alert("Нечего переносить"); return; }
  let projects = [];
  try{
    const r = await fetch("api/projects", {credentials: "same-origin"});
    projects = (await r.json()).filter(p => p.id !== PID);
  }catch(e){}
  const m = document.createElement("div");
  m.id = "ctxmenu";
  m.className = "prespop";
  m.style.minWidth = "360px";
  m.onclick = ev => ev.stopPropagation();
  const opts = projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)} (${fmt(p.rows)} фраз)</option>`).join("");
  m.innerHTML = `<div class="pscore" style="font-size:16px">📤 Перенести ${fmt(phrases.length)} фраз из ${fmt(nFolders)} папок${singles.length ? ` + одиночки` : ""}</div>
    <div class="pfield"><b>куда</b>
      <select id="mvTarget" style="width:100%; margin-top:4px"><option value="">— новый проект —</option>${opts}</select></div>
    <div class="pfield" id="mvNameRow"><b>название нового проекта</b>
      <input id="mvName" style="width:100%; margin-top:4px; padding:6px 8px; background:var(--panel2); border:1px solid var(--border); border-radius:7px; color:var(--text); font:inherit" value="${esc(($("#ptitle") && $("#ptitle").textContent) || "Проект")} — ${esc(state.search.trim() || "выборка")}"></div>
    ${singles.length ? `<div class="pfield"><label style="display:flex; align-items:center; gap:7px; cursor:pointer"><input type="checkbox" id="mvSingles" checked> включить одиночки из «Без группы» (${fmt(singles.length)} фраз)</label></div>` : ""}
    <div class="pfield" style="color:var(--muted); font-size:12.5px">Фразы уйдут с частотностями и дублями; здесь они попадут в корзину (вернуть можно в любой момент). Целевой проект пересчитается.</div>
    <div style="display:flex; gap:8px; margin-top:10px; justify-content:flex-end">
      <button class="ctxitem" id="mvCancel" style="border:1px solid var(--border)">Отмена</button>
      <button class="ctxitem" id="mvGo" style="background:var(--accent); color:#fff">Перенести</button></div>`;  // guardian: allow значения через esc()
  document.body.appendChild(m);
  m.style.left = Math.max(8, (innerWidth - m.offsetWidth) / 2) + "px";
  m.style.top = Math.max(8, (innerHeight - m.offsetHeight) / 3) + "px";
  const sel = m.querySelector("#mvTarget");
  sel.onchange = () => { m.querySelector("#mvNameRow").style.display = sel.value ? "none" : ""; };
  m.querySelector("#mvCancel").onclick = hideCtx;
  m.querySelector("#mvGo").onclick = async () => {
    const target = sel.value || null;
    const name = m.querySelector("#mvName").value.trim();
    const cbS = m.querySelector("#mvSingles");
    if (cbS && cbS.checked) phrases = phrases.concat(singles);
    if (!phrases.length){ alert("Нечего переносить"); return; }
    m.querySelector("#mvGo").textContent = "⏳";
    try{
      const r = await fetch(`api/projects/${PID}/move`, {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({phrases, target, name})});
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      // в источнике — в корзину (представители; дубли едут вместе с ними)
      for (const p of phrases) if (QIDX.has(p)) TRASH.add(p);
      saveTrash(); hideCtx(); render();
      alert(d.new
        ? `Создан новый проект (${fmt(d.added)} фраз), кластеризация запущена. Здесь перенесённые фразы в корзине.`
        : `Перенесено ${fmt(d.moved)} фраз (новых для проекта: ${fmt(d.added)}${d.restored ? `, возвращено из его корзины: ${fmt(d.restored)}` : ""}). ${d.added ? "Пересчёт запущен." : "Пересчёт не нужен — фразы там уже есть."} Здесь они в корзине.`);
    }catch(e){ alert("Ошибка переноса: " + e.message); hideCtx(); }
  };
}
