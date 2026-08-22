"use strict";
/* ---------- корзина и минус-слова: полное исключение из кластеризации ---------- */
let TRASH = new Set();  // тексты фраз в корзине (persist: sem_trash:<pid>)
function saveTrash(){ dbSet("sem_trash:" + (PID || "demo"), [...TRASH]); }
let RULES = {must: [], not: []};   // правила связок (persist: sem_rules:<pid>)
let PRES = {};                     // результаты промт-оценки: маркер папки -> объект
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
    ["⧉", "Копировать фразы кластера", () => copyCluster(c, document.createElement("button"))],
  ];
  for (const [ico, label, fn] of items){
    const d = document.createElement("div");
    d.className = "ctxitem";
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

