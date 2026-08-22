"use strict";
"use strict";
const PID = new URLSearchParams(location.search).get("p");
const DATA_BASE = PID ? `api/projects/${PID}/data/` : "data/";
const $ = s => document.querySelector(s);
const fmt = n => n.toLocaleString("ru-RU");
const MODE_HINT = {
  hard: "complete linkage: фраза в группе, только если близка ко всем её участникам — чистые мелкие папки",
  avg:  "average linkage: баланс чистоты и размера (режим итогового CSV)",
  soft: "single linkage: достаточно близости к одной фразе — крупные папки-цепочки",
};
const VARIANT_TITLES = {openai: "OpenAI · 3-large", gemini: "Gemini · CLUSTERING",
  gem_sim: "Gemini · SEM_SIMILARITY", gem_query: "Gemini · RETRIEVAL_QUERY",
  ensemble: "Ансамбль OpenAI+Gemini", openai1536: "OpenAI · 1536d",
  gemini768: "Gemini · 768d", lemma: "OpenAI · леммы", intent: "OpenAI · интент-префикс",
  voyage: "Voyage · 3-large", qwen: "Qwen3 · text-embedding-v4"};
const VARIANT_SHORT = {openai: "OA", gemini: "GE", gem_sim: "GE-sim", gem_query: "GE-q",
  ensemble: "АНС", openai1536: "OA-1536", gemini768: "GE-768", lemma: "OA-лем", intent: "OA-инт",
  voyage: "VOY", qwen: "QW"};
function titleOf(k){ const v = META && META.providers && META.providers[k];
  return (v && v.title) || VARIANT_TITLES[k] || k; }
function isAB(){ return state.cols.length > 1; }

let META, Q, F, B, D, G, W, L, INT, TOTAL, N;
const INT_SHORT = {"информационный": "И", "коммерческое исследование": "К",
                   "транзакционный": "Т", "навигационный": "Н"};
const INT_CLS = {"информационный": "ii", "коммерческое исследование": "ik",
                 "транзакционный": "it", "навигационный": "in"};
const TREES = {};                    // "openai_hard" -> {a,b,d}
const state = {cols: ["openai"], mode: "hard",
               slider: 8200, minSize: 2, search: "",
               fMode: "off", fMin: 10, fMetric: "f",   // фильтр по частотности: off|hide|nogroup
               minusWords: [],       // минус-слова: фразы с ними (в любой форме) вне кластеризации
               searchOnly: false,    // при поиске показывать в папках только совпавшие фразы
               searchScope: "all",   // где искать: all (фразы+дубли) | name (маркер кластера)
               presSort: false,      // сортировать папки по баллам промт-оценки
               presFlt: null,        // фильтр по оценкам: {field, kind, op/num | vals[]}
               anOn: false, anQ: [""], anFocus: null,  // режим анализа: столбцы-поиски
               cmpTpls: [],          // имена шаблонов, включённых в сравнение колонками
               targetGeo: "",        // целевой регион: его гео = «без гео»
               sliders: {},          // сила объединения на каждый вид: openai/gemini/ab
               sel: null,            // папка для сравнения в A/B: {col, label, idxs}
               expanded: new Set(),  // раскрытые папки "prov:label"
               shown: {}};           // сколько папок показано в колонке

async function loadTree(key){
  if (TREES[key]) return TREES[key];
  $("#loadmsg").textContent = "Загрузка дерева " + key + "…";
  $("#loader").style.display = "flex";
  const buf = await (await fetch(DATA_BASE + key + ".bin", {cache: "no-cache", credentials: "same-origin"})).arrayBuffer();
  const m = buf.byteLength / 12, dv = new DataView(buf);
  const a = new Int32Array(m), b = new Int32Array(m), d = new Float32Array(m);
  for (let i = 0; i < m; i++){
    a[i] = dv.getInt32(i * 12, true);
    b[i] = dv.getInt32(i * 12 + 4, true);
    d[i] = dv.getFloat32(i * 12 + 8, true);
  }
  $("#loader").style.display = "none";
  return TREES[key] = {a, b, d};
}

// срез дендрограммы: первые k слияний, union-find по листьям
function cutLabels(tree, k){
  const uf = new Int32Array(N);
  for (let i = 0; i < N; i++) uf[i] = i;
  const find = x => { let r = x; while (uf[r] !== r) r = uf[r];
                      while (uf[x] !== r){ const nx = uf[x]; uf[x] = r; x = nx; } return r; };
  const rep = new Int32Array(N + k);
  for (let i = 0; i < N; i++) rep[i] = i;
  // правила «никогда вместе»: корень -> Set(id правил), конфликтные слияния пропускаются
  const notOf = new Map();
  for (let r = 0; r < RULES.not.length; r++)
    for (const t of RULES.not[r]){
      const i = QIDX.get(t);
      if (i === undefined) continue;
      let s = notOf.get(i);
      if (!s) notOf.set(i, s = new Set());
      s.add(r);
    }
  const conflict = (ra, rb) => {
    const A = notOf.get(ra), Bs = notOf.get(rb);
    if (!A || !Bs) return false;
    for (const r of A) if (Bs.has(r)) return true;
    return false;
  };
  for (let i = 0; i < k; i++){
    const ra = find(rep[tree.a[i]]), rb = find(rep[tree.b[i]]);
    if (ra !== rb && conflict(ra, rb)){ rep[N + i] = ra; continue; }
    uf[rb] = ra;
    const Bs = notOf.get(rb);
    if (Bs){
      const A = notOf.get(ra);
      if (!A) notOf.set(ra, Bs);
      else for (const r of Bs) A.add(r);
      notOf.delete(rb);
    }
    rep[N + i] = ra;
  }
  // правила «всегда вместе»: принудительно склеить папки участников
  for (const g of RULES.must){
    let first = -1;
    for (const t of g){
      const i = QIDX.get(t);
      if (i === undefined) continue;
      const r2 = find(i);
      if (first < 0) first = r2;
      else if (r2 !== first) uf[r2] = first;
    }
  }
  const lab = new Int32Array(N);
  for (let i = 0; i < N; i++) lab[i] = find(i);
  return lab;
}

