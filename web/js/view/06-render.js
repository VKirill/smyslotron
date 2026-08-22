"use strict";
let renderQueued = false;
function render(){
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(async () => {
    renderQueued = false;
    const cols = await getCols();
    const work = $("#work");
    // запомнить прокрутку каждой колонки — обновление данных не должно кидать вверх
    const scrollPos = {};
    work.querySelectorAll(".col").forEach(el => {
      const l = el.querySelector(".list");
      if (el.dataset.key && l) scrollPos[el.dataset.key] = l.scrollTop;
    });
    work.innerHTML = "";
    work.classList.toggle("tworow", cols.length > 4);
    let rowTop = work, rowBottom = work;
    if (cols.length > 4){
      rowTop = document.createElement("div"); rowTop.className = "wrow";
      rowBottom = document.createElement("div"); rowBottom.className = "wrow";
      work.append(rowTop, rowBottom);
    }
    const topCount = cols.length > 4 ? Math.floor(cols.length / 2) : cols.length;

    // консенсус: папка зелёная, если её состав совпал во ВСЕХ колонках
    if (cols.length > 1 && !state.anOn){
      const sets = cols.map(c => new Set(c.clusters.map(cl => cl.idxs.join(","))));
      for (const c of cols)
        for (const cl of c.clusters){
          const key = cl.idxs.join(",");
          cl.same = sets.every(st => st.has(key));
          cl.diffWith = cl.same ? []
            : cols.filter((_, ci) => !sets[ci].has(key)).map(x => x.tpl ? x.tpl.name : titleOf(x.prov));
        }
    } else {
      for (const cl of cols[0].clusters) cl.same = null;
    }

    // A/B: карта пересечений для второй колонки
    let ovMaps = null;
    if (isAB() && state.sel && !state.anOn){
      if (cols.some(c => c.prov === state.sel.col)){
        ovMaps = {};
        for (const other of cols){
          if (other.prov === state.sel.col) continue;
          const m2 = new Map();
          for (const i of state.sel.idxs){
            const l = other.lab[i];
            m2.set(l, (m2.get(l) || 0) + 1);
          }
          ovMaps[other.prov] = m2;
        }
      } else state.sel = null;
    }

    for (const col of cols){
      const el = document.createElement("div");
      el.className = "col";
      el.dataset.key = col.key || col.prov;
      const q = col.q || "";
      const minS = col.minSize ?? state.minSize;
      const ovMap = ovMaps ? ovMaps[col.prov] : null;
      const isOther = !!ovMap;
      let clusters = col.clusters;
      let small = clusters.filter(c => c.idxs.length < minS);
      clusters = clusters.filter(c => c.idxs.length >= minS);
      if (q) clusters = clusters.filter(c => state.searchScope === "name"
        ? matchQ(c.top, q) : c.idxs.some(i => matchQ(i, q)));
      if (state.presSort && Object.keys(PRES).length)
        clusters = [...clusters].sort((x, y) =>
          (scoreOf(PRES[Q[y.top]]) ?? -1) - (scoreOf(PRES[Q[x.top]]) ?? -1));
      if (presFltActive()) clusters = clusters.filter(c => testPresF(PRES[Q[c.top]]));
      if (isOther && ovMap){
        clusters = clusters.filter(c => ovMap.has(c.label));
        clusters.sort((x, y) => (ovMap.get(y.label)) - (ovMap.get(x.label)));
      }
      const smallCnt = small.reduce((s, c) => s + c.idxs.length, 0);
      // счёт папок «один к одному»: мелкие группы из «Без группы» — тоже папки
      const nSmallF = q ? small.filter(cc => cc.idxs.some(i => matchQ(i, q))).length : small.length;
      const nFolders = clusters.length + nSmallF;
      const cntHtml = `<span title="${fmt(clusters.length)} групп + ${fmt(nSmallF)} в «Без группы»">${fmt(nFolders)} папок</span>
        <button class="copy colcopy" title="Скопировать уникальные фразы всех показанных папок колонки (без дублей и «Без группы»)">⧉</button>` +
        (q ? `<button class="copy coltrash" title="Отправить в корзину ВСЕ фразы проекта, найденные этим фильтром — они выпадут из кластеризации">🗑</button>` : "") +
        `<button class="copy colmove" title="Перенести фразы показанных папок в другой проект (существующий или новый) — здесь они уйдут в корзину, ничего не теряется">📤</button>`;

      const head = document.createElement("div");
      head.className = "colhead";
      if (col.tpl){
        // сравнение шаблонов: заголовок = имя шаблона и его настройки
        head.innerHTML = `<span title="${esc(tplLabel(col.tpl))}"><b>${esc(col.tpl.name)}</b>
            <span style="font-size:11px">${esc(tplLabel(col.tpl))}</span></span>
          <span class="ancnt">${cntHtml}</span>
          <button class="andel" title="Убрать из сравнения">×</button>`;
        head.querySelector(".andel").onclick = () => {
          state.cmpTpls = state.cmpTpls.filter(n => n !== col.tpl.name);
          savePrefs(); renderTpls(); render();
        };
      } else if (col.an != null){
        // режим анализа: вместо названия варианта — поле поиска колонки
        head.innerHTML = `<input class="anq" placeholder="запросы для отслеживания…"
            value="${esc(col.anq || "").replace(/"/g, "&quot;")}">
          <span class="ancnt">${cntHtml}</span>
          ${state.anQ.length > 1 ? `<button class="andel" title="Убрать столбец">×</button>` : ""}`;
        const inp = head.querySelector(".anq");
        inp.addEventListener("input", () => {
          state.anQ[col.an] = inp.value;
          state.anFocus = col.an;
          clearTimeout(inp._t);
          inp._t = setTimeout(() => { savePrefs(); render(); }, 350);
        });
        const del = head.querySelector(".andel");
        if (del) del.onclick = () => {
          state.anQ.splice(col.an, 1);
          if (!state.anQ.length) state.anQ = [""];
          state.anFocus = null; savePrefs(); render();
        };
      } else {
        head.innerHTML = `<span><b>${titleOf(col.prov)}</b></span>
          <span>${isOther && ovMap ? `${fmt(clusters.length)} папок с общими фразами` : cntHtml}
          ${isAB() && state.sel && col.prov === state.sel.col ? ` · <button class="link" id="resetSel">сбросить выбор</button>` : ""}</span>`;
      }
      const ctb = head.querySelector(".coltrash");
      if (ctb) ctb.onclick = e => {
        e.stopPropagation();
        const hits = [];
        for (let i = 0; i < N; i++)
          if (!excluded(i) && matchQ(i, q)) hits.push(i);
        if (!hits.length){ alert("По этому фильтру ничего не найдено"); return; }
        if (!confirm(`Отправить в корзину ${hits.length.toLocaleString("ru-RU")} фраз, найденных фильтром «${q}»?\n\nОни перестанут участвовать в кластеризации; вернуть можно из корзины на табе «Фильтры».`)) return;
        for (const i of hits) TRASH.add(Q[i]);
        saveTrash(); render();
      };
      const cmv = head.querySelector(".colmove");
      if (cmv) cmv.onclick = e => {
        e.stopPropagation();
        // все уникальные фразы показанных папок + их дубли (перенос смысла целиком)
        const phrases = [];
        for (const c of clusters)
          for (const i of c.idxs){ phrases.push(Q[i]); if (D && D[i]) phrases.push(...D[i]); }
        openMoveDialog(phrases, clusters.length);
      };
      const ccb = head.querySelector(".colcopy");
      if (ccb) ccb.onclick = async e => {
        e.stopPropagation();
        const lines = [];
        for (const c of clusters)
          for (const i of [...c.idxs].sort((x, y) => F[y] - F[x])) lines.push(Q[i]);
        try{
          await navigator.clipboard.writeText(lines.join("\n"));
          ccb.textContent = "✓"; ccb.classList.add("ok");
        }catch(err){ ccb.textContent = "✗"; }
        setTimeout(() => { ccb.textContent = "⧉"; ccb.classList.remove("ok"); }, 1200);
      };
      el.appendChild(head);

      const list = document.createElement("div");
      list.className = "list";
      const key = col.key || col.prov;
      const shown = state.shown[key] || 200;
      for (const c of clusters.slice(0, shown)){
        const fel = folderEl(c, col, isOther && ovMap ? ovMap.get(c.label) : 0);
        if (state.expanded.has(key + ":" + c.label)) expandInto(fel, c, false, q);
        list.appendChild(fel);
      }
      if (clusters.length > shown){
        const m = document.createElement("div");
        m.className = "more";
        m.textContent = `Показать ещё (${fmt(clusters.length - shown)} папок скрыто)`;
        m.onclick = () => { state.shown[key] = shown + 300; render(); };
        list.appendChild(m);
      }
      if (smallCnt){
        const idxs = small.flatMap(cc => cc.idxs);
        let show = true, ov = 0;
        if (q) show = state.searchScope === "name" ? false : idxs.some(i => matchQ(i, q));
        if (isOther && ovMap){
          const sl = new Set(small.map(cc => cc.label));
          for (const [l, cnt] of ovMap) if (sl.has(l)) ov += cnt;
          show = ov > 0;
        }
        if (show){
          let ssum = 0, stop = idxs[0];
          for (const i of idxs){ ssum += F[i]; if (F[i] > F[stop]) stop = i; }
          const ng = {label: "__nogroup__", idxs, sum: ssum, top: stop, same: null,
                      name: "Без группы — не объединились при текущей силе", icon: "🗂"};
          const fel = folderEl(ng, col, ov);
          fel.classList.add("nogroup");
          const hitSearch = q && state.searchScope !== "name" && idxs.some(i => matchQ(i, q));
          // при поиске папка остаётся внизу, но раскрыта и показывает ТОЛЬКО найденное
          if (hitSearch || state.expanded.has(key + ":__nogroup__"))
            expandInto(fel, ng, hitSearch, q);
          list.appendChild(fel);
        }
      }
      el.appendChild(list);
      (cols.indexOf(col) < topCount ? rowTop : rowBottom).appendChild(el);
      const sp = scrollPos[el.dataset.key];
      if (sp) requestAnimationFrame(() => { list.scrollTop = sp; });
    }
    const rs = $("#resetSel");
    if (rs) rs.onclick = () => { state.sel = null; render(); };

    // режим анализа: вернуть фокус в поле, где печатал пользователь
    if (state.anOn && state.anFocus != null){
      const inp = work.querySelectorAll(".anq")[state.anFocus];
      if (inp){ inp.focus(); const L = inp.value.length; inp.setSelectionRange(L, L); }
      state.anFocus = null;
    }

    // статистика по первой колонке
    const c0 = cols[0];
    const singles = c0.clusters.filter(c => c.idxs.length === 1).length;
    const thr = c0.k > 0 ? 1 - c0.tree.d[c0.k - 1] : 1;
    $("#thr").textContent = thr.toFixed(3);
    $("#ncl").textContent = fmt(c0.clusters.length);
    $("#stats").innerHTML = `
      <span>Фраз в ядре</span><span>${fmt(TOTAL)}</span>
      <span>Уникальных смыслов</span><span>${fmt(N)}</span>
      <span>Папок всего</span><span>${fmt(c0.clusters.length)}</span>
      <span>Одиночек</span><span>${fmt(singles)}</span>
      <span>Слияний применено</span><span>${fmt(c0.k)} / ${fmt(N - 1)}</span>`;
    // зелёная точка на табе «Фильтры», если хоть один фильтр активен
    $("#tabFilters").classList.toggle("dot",
      !!(state.search.trim() || state.fMode !== "off" || state.targetGeo.trim() ||
         state.anOn || state.minusWords.length || TRASH.size ||
         RULES.must.length || RULES.not.length));
    // счётчики минус-слов и корзины + список корзины
    let mc = 0;
    if (state.minusWords.length) for (let i = 0; i < N; i++) if (minusHit(i)) mc++;
    $("#minusCnt").textContent = fmt(mc);
    $("#trashCnt").textContent = TRASH.size ? `— ${fmt(TRASH.size)} фраз` : "— пусто";
    const tl = $("#trashList");
    if (tl){
      const items = [...TRASH].slice(0, 200);
      tl.innerHTML = items.map(t => `<div class="q" style="cursor:pointer" title="Вернуть из корзины">↩ ${esc(t)}</div>`).join("") +
        (TRASH.size > 200 ? `<div class="q" style="color:var(--muted)">… ещё ${fmt(TRASH.size - 200)}</div>` : "");
      [...tl.children].forEach((el, k) => {
        if (k < items.length) el.onclick = () => { TRASH.delete(items[k]); saveTrash(); render(); };
      });
    }
    renderRules();
    renderTpls();
  });
}

