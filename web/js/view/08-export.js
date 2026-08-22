"use strict";
async function sliceClusters(){
  // кластеры текущего среза первой колонки (учитывают фильтр частотности и гео)
  const vkey = state.cols[0];
  const tree = await loadTree(vkey + "_" + state.mode);
  return {vkey, clusters: clusterize(cutLabels(tree, kFromSlider()))};
}

function loadXLSX(){
  return window.XLSX ? Promise.resolve() : new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "vendor_xlsx.js?v=2"; s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });
}

async function exportSlice(){
  const {vkey, clusters: allClusters} = await sliceClusters();
  const clusters = presFltActive()
    ? allClusters.filter(c => c.idxs.length < state.minSize || testPresF(PRES[Q[c.top]]))
    : allClusters;
  // все поля оценок → колонки листа «Маркеры»
  const presKeys = [];
  {
    const seen = new Set();
    for (const v of Object.values(PRES))
      if (v && typeof v === "object")
        for (const k of Object.keys(v))
          if (!seen.has(k) && presKeys.length < 8){ seen.add(k); presKeys.push(k); }
  }
  const lines = [["Запрос", "Частотность (Σ группы дублей)", "Базовая (Σ)", "Дубли",
                  "Топоним", "Вопрос", "Интент", "Кластер", "Маркерный запрос",
                  "Интент кластера", "Риск смешения"]];
  const rowMeta = [];   // по одной записи на строку данных: {cl, nogroup}
  const markers = [];
  let ncl = 0;
  const noGroup = [];
  for (const c of clusters){
    if (c.idxs.length < state.minSize){ noGroup.push(...c.idxs); continue; }
    ncl++;
    const name = Q[c.top];
    markers.push([name, c.sum, c.idxs.length, c.dom || "",
                  ...presKeys.map(k => {
                    const v = PRES[name] ? PRES[name][k] : undefined;
                    if (v === undefined || v === null) return "";
                    return typeof v === "object" ? JSON.stringify(v) : v;
                  })]);
    for (const i of [...c.idxs].sort((x, y) => F[y] - F[x])){
      lines.push([Q[i], F[i], B[i], (D && D[i] ? D[i].join(" | ") : ""),
                  (G && G[i]) || "", (W && W[i]) ? "да" : "",
                  (INT && INT[i]) || "", ncl, name,
                  c.dom || "", c.mixed ? "да" : ""]);
      rowMeta.push({cl: ncl, nogroup: false});
    }
  }
  for (const i of noGroup.sort((x, y) => F[y] - F[x])){
    lines.push([Q[i], F[i], B[i], (D && D[i] ? D[i].join(" | ") : ""),
                (G && G[i]) || "", (W && W[i]) ? "да" : "",
                (INT && INT[i]) || "", "", "Без группы", "", ""]);
    rowMeta.push({cl: 0, nogroup: true});
  }
  await loadXLSX();

  const HDR_S = {font: {bold: true, color: {rgb: "FFFFFF"}, sz: 11},
                 fill: {patternType: "solid", fgColor: {rgb: "4A76D0"}},
                 alignment: {vertical: "center", horizontal: "center", wrapText: true}};
  const ZEBRA_FILL = {patternType: "solid", fgColor: {rgb: "EEF2FB"}};

  const wb = XLSX.utils.book_new();
  const ws1 = XLSX.utils.aoa_to_sheet(lines);
  ws1["!cols"] = [{wch: 45}, {wch: 14}, {wch: 11}, {wch: 40}, {wch: 12}, {wch: 8},
                  {wch: 24}, {wch: 9}, {wch: 45}, {wch: 24}, {wch: 9}];
  ws1["!rows"] = [{hpt: 30}];
  ws1["!autofilter"] = {ref: "A1:K" + lines.length};
  for (let r = 0; r < lines.length; r++){
    for (let c = 0; c < 11; c++){
      const cell = ws1[XLSX.utils.encode_cell({r, c})];
      if (!cell) continue;
      if (r === 0){ cell.s = HDR_S; continue; }
      const meta = rowMeta[r - 1];
      if (typeof cell.v === "number") cell.z = "#,##0";
      const st = {};
      if (meta.nogroup) st.font = {color: {rgb: "9AA0AE"}, italic: true};
      else if (meta.cl % 2 === 0) st.fill = ZEBRA_FILL;
      if (!meta.nogroup && c === 8) st.font = {bold: true, color: {rgb: "2F4C8F"}};
      if (c === 10 && cell.v === "да") st.font = {bold: true, color: {rgb: "C0392B"}};
      if (st.font || st.fill) cell.s = st;
    }
  }
  XLSX.utils.book_append_sheet(wb, ws1, "Кластеры");

  const mCols = 4 + presKeys.length;
  const ws2 = XLSX.utils.aoa_to_sheet(
    [["Маркерный запрос", "Σ частотность", "Фраз", "Интент кластера", ...presKeys], ...markers]);
  ws2["!cols"] = [{wch: 52}, {wch: 15}, {wch: 9}, {wch: 24},
                  ...presKeys.map(() => ({wch: 18}))];
  ws2["!rows"] = [{hpt: 30}];
  ws2["!autofilter"] = {ref: "A1:" + XLSX.utils.encode_col(mCols - 1) + (markers.length + 1)};
  for (let r = 0; r <= markers.length; r++){
    for (let c = 0; c < mCols; c++){
      const cell = ws2[XLSX.utils.encode_cell({r, c})];
      if (!cell) continue;
      if (r === 0){ cell.s = {...HDR_S, fill: {patternType: "solid", fgColor: {rgb: "2E9E74"}}}; continue; }
      if (typeof cell.v === "number") cell.z = "#,##0";
      const st = {};
      if (r % 2 === 0) st.fill = {patternType: "solid", fgColor: {rgb: "EDF7F2"}};
      if (c === 0) st.font = {bold: true};
      if (st.font || st.fill) cell.s = st;
    }
  }
  XLSX.utils.book_append_sheet(wb, ws2, "Маркеры");

  const modeName = {hard: "hard", avg: "avg", soft: "soft"}[state.mode];
  XLSX.writeFile(wb, `srez_${vkey}_${modeName}_${(state.slider / 100).toFixed(1)}pct.xlsx`,
                 {compression: true});
}

