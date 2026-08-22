"use strict";
function clusterize(lab){
  const map = new Map();
  for (let i = 0; i < N; i++){
    if (excluded(i)) continue;
    if (state.fMode !== "off" && (state.fMetric === "b" ? B[i] : F[i]) < state.fMin){
      if (state.fMode === "hide") continue;
      map.set("f" + i, [i]);  // «в Без группы»: одиночка уйдёт под мин. размер папки
      continue;
    }
    // при quesSplit вопросительные фразы уходят в парный «вопросный» кластер
    const key = state.quesSplit && W && W[i] ? lab[i] + "?q" : lab[i];
    let arr = map.get(key);
    if (!arr) map.set(key, arr = []);
    arr.push(i);
  }
  // фразы с разными городами не сливаем: кластер с >1 гео-ключом режется по гео;
  // целевое гео приравнивается к «без гео» и не отрезается
  const tset = new Set(state.targetGeo.toLowerCase().split(",").map(x => x.trim()).filter(Boolean));
  const geoOf = i => {
    const g = G ? G[i] : "";
    if (!g || !tset.size) return g;
    return g.split(" ").filter(w => !tset.has(w)).sort().join(" ");
  };
  const parts = [];
  for (const [label, idxs] of map){
    if (G && new Set(idxs.map(geoOf)).size > 1){
      const by = new Map();
      for (const i of idxs){
        const g = geoOf(i);
        let a = by.get(g);
        if (!a) by.set(g, a = []);
        a.push(i);
      }
      for (const [g, a] of by) parts.push([label + "|" + g, a]);
    } else parts.push([label, idxs]);
  }
  const cl = [];
  for (const [label, idxs] of parts){
    let sum = 0, top = idxs[0], qn = 0, dups = 0;
    const iw = {};
    for (const i of idxs){
      sum += F[i]; if (F[i] > F[top]) top = i;
      if (W && W[i]) qn++;
      if (D && D[i]) dups += D[i].length;
      if (INT && INT[i]) iw[INT[i]] = (iw[INT[i]] || 0) + Math.max(1, F[i]);
    }
    let dom = "", mixed = false;
    const tops = Object.entries(iw).sort((a, b) => b[1] - a[1]);
    if (tops.length){
      const tot = tops.reduce((a, x) => a + x[1], 0);
      dom = tops[0][0];
      mixed = tops.length > 1 && tops[1][1] >= 0.25 * tot;
    }
    const geos = G ? [...new Set(idxs.map(i => G[i]).filter(Boolean))] : [];
    cl.push({label, idxs, sum, top, qn, dups, dom, mixed, geo: geos.join("+")});
  }
  cl.sort((x, y) => y.sum - x.sum);
  return cl;
}

function kFromSlider(s){ return Math.round((s ?? state.slider) / 10000 * (N - 1)); }
function cmpTpls(){ return TPLS.filter(t => state.cmpTpls.includes(t.name)); }

async function getCols(){
  const cmp = cmpTpls();
  if (cmp.length){
    // сравнение шаблонов: колонка = шаблон со СВОИМИ вариантом/режимом/срезом.
    // Поиск, зашитый в шаблон, не применяем (он «прячет» папки в чужом проекте),
    // а вот общий «Поиск по фразам» из сайдбара действует на все колонки
    const gq = normQ(state.search);
    const cols = [];
    for (const [ci, t] of cmp.entries()){
      const p = (t.cols || []).find(x => META.provs.includes(x)) || META.provs[0];
      const tree = await loadTree(p + "_" + t.mode);
      const k = kFromSlider(t.slider);
      const lab = cutLabels(tree, k);
      cols.push({prov: p, tree, k, clusters: clusterize(lab), lab, q: gq,
                 key: "t#" + ci, tpl: t, minSize: t.minSize || 1});
    }
    return cols;
  }
  if (state.anOn){
    // режим анализа: N колонок одной и той же кластеризации, у каждой свой поиск
    const p = state.cols[0];
    const tree = await loadTree(p + "_" + state.mode);
    const k = kFromSlider();
    const lab = cutLabels(tree, k);
    const clusters = clusterize(lab);
    return state.anQ.map((aq, ai) => ({prov: p, tree, k, clusters, lab,
      an: ai, anq: aq, q: normQ(aq), key: p + "#a" + ai}));
  }
  const provs = state.cols;
  const cols = [];
  const gq = normQ(state.search);
  for (const p of provs){
    const tree = await loadTree(p + "_" + state.mode);
    const k = kFromSlider();
    const lab = cutLabels(tree, k);
    cols.push({prov: p, tree, k, clusters: clusterize(lab), lab, q: gq, key: p});
  }
  return cols;
}

function esc(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
/* Поиск с операторами (по мотивам Вордстата):
   [слова]   — слова идут подряд именно в этом порядке (форма любая — по леммам);
   "слова"   — фраза состоит ровно из этих слов (число слов зафиксировано, порядок любой);
   !слово    — ровно такая форма слова (работает и внутри [ ] и " ");
   !!запрос  — инверсия: показать фразы, НЕ соответствующие фильтру. */
