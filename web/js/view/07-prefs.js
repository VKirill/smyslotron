"use strict";
function viewKey(){ return state.cols.join("+"); }

/* ---------- настройки и шаблоны: хранятся в БД на пользователя,
   localStorage — офлайн-фолбэк и источник миграции старых данных ---------- */
const _pTimers = {};
function dbSet(key, value){
  try{ localStorage.setItem(key, JSON.stringify(value)); }catch(e){}
  clearTimeout(_pTimers[key]);
  _pTimers[key] = setTimeout(() => {
    fetch("api/prefs/" + encodeURIComponent(key),
      {method: "POST", credentials: "same-origin",
       headers: {"Content-Type": "application/json"},
       body: JSON.stringify({value})}).catch(() => {});
  }, 400);
}
async function dbGet(key){
  let v = null;
  try{
    const r = await fetch("api/prefs/" + encodeURIComponent(key), {credentials: "same-origin"});
    if (r.ok) v = (await r.json()).value;
  }catch(e){}
  if (v === null){
    try{ v = JSON.parse(localStorage.getItem(key)); }catch(e){ v = null; }
    if (v) dbSet(key, v);  // миграция старых localStorage-настроек в БД
  }
  return v;
}

async function loadPrefs(){
  const pr = (await dbGet("sem_prefs:" + (PID || "demo"))) || {};
  if (pr.sliders) for (const k in pr.sliders) if (pr.sliders[k] <= 1000) pr.sliders[k] *= 10;
  return pr;
}
function savePrefs(){ dbSet("sem_prefs:" + (PID || "demo"),
  {view: state.cols, search: state.search, sliders: state.sliders,
   targetGeo: state.targetGeo, mode: state.mode,
   anOn: state.anOn, anQ: state.anQ, cmpTpls: state.cmpTpls,
   minusWords: state.minusWords, searchOnly: state.searchOnly,
   presSort: state.presSort, presFlt: state.presFlt,
   fMode: state.fMode, fMin: state.fMin, fMetric: state.fMetric}); }

// ---------- шаблоны фильтров ----------
let TPLS = [];                       // загружаются в boot() из БД
function loadTpls(){ return TPLS; }
function saveTpls(t){ TPLS = t; dbSet("sem_tpl", t); }
const MODE_NAME = {hard: "Hard", avg: "Средний", soft: "Soft"};

function tplLabel(t){
  return `${t.cols.map(k => VARIANT_SHORT[k] || k).join("+")} · ${MODE_NAME[t.mode]} · ${(t.slider / 100).toFixed(1)}%` +
         (t.minSize > 1 ? ` · мин.${t.minSize}` : "") + (t.search ? ` · «${t.search}»` : "");
}

function renderTpls(){
  const list = $("#tplList");
  if (!list) return;
  const tpls = loadTpls();
  list.innerHTML = tpls.length ? "" : '<div class="hint">нажми «+», чтобы сохранить текущие настройки как шаблон</div>';
  tpls.forEach((t, i) => {
    const active = t.mode === state.mode && t.slider === state.slider &&
      t.minSize === state.minSize && (t.search || "") === state.search &&
      JSON.stringify(t.cols) === JSON.stringify(state.cols);
    const row = document.createElement("div");
    row.className = "tpl" + (active ? " on" : "");
    row.innerHTML = `<input type="checkbox" class="tcmp" title="Показать шаблон колонкой (отметь несколько — сравнишь). Сохранённый в шаблоне поиск в колонке не применяется"
        ${state.cmpTpls.includes(t.name) ? "checked" : ""}>
      <span class="tname" title="${esc(tplLabel(t))}">${esc(t.name)}</span>
      <span class="tinfo">${esc(tplLabel(t))}</span>
      <button class="tdel" title="Удалить шаблон">×</button>`;
    row.querySelector(".tcmp").onclick = e => {
      e.stopPropagation();
      state.cmpTpls = e.target.checked
        ? [...state.cmpTpls, t.name]
        : state.cmpTpls.filter(n => n !== t.name);
      state.sel = null; state.shown = {};
      savePrefs(); render();
    };
    row.querySelector(".tdel").onclick = e => {
      e.stopPropagation();
      state.cmpTpls = state.cmpTpls.filter(n => n !== t.name);
      const ts = loadTpls(); ts.splice(i, 1); saveTpls(ts); renderTpls(); render();
    };
    row.onclick = () => applyTpl(t);
    list.appendChild(row);
  });
}

function applyTpl(t){
  const ok = (t.cols || ["openai"]).filter(p => META.provs.includes(p));
  state.cols = ok.length ? ok : [META.provs[0]];
  state.mode = t.mode; state.slider = t.slider; state.minSize = t.minSize;
  state.search = t.search || "";
  state.sel = null; state.shown = {}; state.expanded.clear();
  state.sliders[viewKey()] = state.slider;
  savePrefs();
  syncUI(); render();
}

function syncUI(){
  $("#slider").value = state.slider;
  $("#minSize").value = state.minSize;
  $("#search").value = state.search;
  document.querySelectorAll("#segProvider input").forEach(b => b.checked = state.cols.includes(b.value));
  document.querySelectorAll("#segMode button").forEach(b => b.classList.toggle("on", b.dataset.v === state.mode));
  $("#modeHint").textContent = MODE_HINT[state.mode];
  $("#providerHint").textContent = isAB()
    ? "⇄ у папки — подсветить её фразы в остальных колонках; клик — раскрыть состав"
    : "отметь до 3 вариантов — колонки встанут рядом для сравнения";
}

