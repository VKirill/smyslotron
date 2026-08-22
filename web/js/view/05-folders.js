"use strict";
function folderEl(c, col, overlap){
  const div = document.createElement("div");
  div.className = "folder";
  if (c.same === true) div.className += " same";
  else if (c.same === false) div.className += " diff";
  if (state.sel && state.sel.col === col.prov && state.sel.label === c.label) div.className += " sel";
  if (overlap) div.className += " match";
  const name = c.name || Q[c.top];
  const consTitle = c.same === true ? "Состав совпал во всех включённых вариантах"
    : c.same === false ? "Состав отличается в: " + (c.diffWith || []).join(", ") : "";
  const presR = PRES[Q[c.top]];
  div.innerHTML = `<div class="fhead"><span class="icon" title="${esc(consTitle)}">${c.icon || "📁"}</span>
      <span class="name" title="${esc(name)}">${hi(name, col.q || "")}</span>
      ${c.geo ? `<span class="badge geo" title="Гео-кластер">📍 ${esc(c.geo)}</span>` : ""}
      ${overlap ? `<span class="badge ov">${overlap} общ.</span>` : ""}
      <span class="slot sint">${c.dom ? `<span class="badge int ${INT_CLS[c.dom] || ""}${c.mixed ? " mx" : ""}" title="Интент кластера: ${esc(c.dom)}${c.mixed ? " (смешанный — есть второй интент ≥25%)" : ""}">${INT_SHORT[c.dom] || "?"}${c.mixed ? "⚠" : ""}</span>` : ""}</span>
      <span class="slot sq">${c.qn ? `<span class="badge qb" title="Вопросных фраз">❓ ${Math.round(c.qn / c.idxs.length * 100)}%</span>` : ""}</span>
      <span class="slot sp">${presR !== undefined ? `<span class="badge pres">⚙ ${scoreOf(presR) ?? "✓"}</span>` : ""}</span>
      <span class="slot scmp">${isAB() && !state.anOn && !col.tpl ? `<button class="cmp" title="Найти эти фразы в соседней колонке">⇄</button>` : ""}</span>
      <button class="copy" title="Скопировать уникальные фразы кластера (без дублей)">⧉</button>
      <button class="copy peval" title="Оценить кластер текущим промтом (таб «Промты») через DeepSeek">⚙</button>
      <span class="slot scnt"><span class="badge" title="Уникальных смыслов${c.dups ? ` + склеенных дублей` : ""}">${fmt(c.idxs.length)}${c.dups ? `+${fmt(c.dups)}` : ""} фраз</span></span>
      <span class="slot ssum"><span class="badge">Σ ${fmt(c.sum)}</span></span></div>`;
  const head = div.firstElementChild;
  head.querySelector(".copy").addEventListener("click", e => {
    e.stopPropagation();
    copyCluster(c, e.currentTarget);
  });
  const cmp = head.querySelector(".cmp");
  if (cmp) cmp.addEventListener("click", e => {
    e.stopPropagation();
    state.sel = {col: col.prov, label: c.label, idxs: c.idxs};
    render();
  });
  const pe = head.querySelector(".peval");
  if (pe) pe.addEventListener("click", e => { e.stopPropagation(); evalCluster(c, pe); });
  const pb = head.querySelector(".badge.pres");
  if (pb){
    pb.addEventListener("mouseenter", () => { popCancelHide(); showPresPop(pb, PRES[Q[c.top]]); });
    pb.addEventListener("mouseleave", popHideSoon);
    pb.addEventListener("click", e => e.stopPropagation());
  }
  head.addEventListener("click", () => toggleFolder(div, c, col));
  head.addEventListener("contextmenu", e => { e.preventDefault(); showCtx(e, c); });
  return div;
}

async function evalCluster(c, btn){
  const prompt = $("#prText") ? $("#prText").value.trim() : "";
  if (!prompt){ alert("Выбери или напиши промт на табе «Промты»"); return; }
  btn.textContent = "⏳"; btn.disabled = true;
  try{
    const text = [...c.idxs].sort((x, y) => F[y] - F[x]).slice(0, 10).map(i => Q[i]).join("; ");
    const r = await fetch("api/prompt_eval", {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({prompt, schema: $("#prSchema").value.trim(),
                            items: [{id: 0, text}]})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    const it = (d.items || [])[0];
    if (it){
      const {id, ...rest} = it;
      PRES[Q[c.top]] = rest;
      dbSet("sem_pres:" + (PID || "demo"), PRES);
    }
    renderPresFlt();
    render();
  }catch(err){
    btn.textContent = "✗"; btn.disabled = false;
    setTimeout(() => { btn.textContent = "⚙"; }, 1600);
  }
}

let _popT;
function popHideSoon(){ clearTimeout(_popT); _popT = setTimeout(hideCtx, 250); }
function popCancelHide(){ clearTimeout(_popT); }

function showPresPop(anchor, r){
  hideCtx();
  if (!r) return;
  const m = document.createElement("div");
  m.id = "ctxmenu";           // общий id — закрытие кликом/Esc/скроллом уже работает
  m.className = "prespop";
  m.onclick = ev => ev.stopPropagation();
  m.onmouseenter = popCancelHide;
  m.onmouseleave = popHideSoon;
  const sc = scoreOf(r);
  m.innerHTML = `<div class="pscore">⚙ ${sc !== null ? sc : "—"}</div>` +
    Object.entries(r).filter(([k, v]) => k !== "score" && v !== "" && v !== null)
      .map(([k, v]) => `<div class="pfield"><b>${esc(k)}</b>${esc(typeof v === "object" ? JSON.stringify(v) : String(v))}</div>`).join("");
  document.body.appendChild(m);
  const rect = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
  const px = rect ? rect.left : anchor.clientX, py = rect ? rect.bottom + 6 : anchor.clientY;
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.min(px, innerWidth - w - 8) + "px";
  m.style.top = Math.min(py, innerHeight - h - 8) + "px";
}

function toggleFolder(div, c, col){
  const key = (col.key || col.prov) + ":" + c.label;
  const open = div.querySelector(".qlist");
  if (open){ open.remove(); state.expanded.delete(key); return; }
  expandInto(div, c, false, col.q || "");
  state.expanded.add(key);
}

async function copyCluster(c, btn){
  // копируем только уникальные смыслы (репрезентанты), без склеенных дублей
  const lines = [...c.idxs].sort((x, y) => F[y] - F[x]).map(i => Q[i]);
  try{
    await navigator.clipboard.writeText(lines.join("\n"));
    const old = btn.textContent;
    btn.textContent = "✓"; btn.classList.add("ok");
    setTimeout(() => { btn.textContent = old; btn.classList.remove("ok"); }, 1200);
  }catch(err){
    btn.textContent = "✗";
    setTimeout(() => { btn.textContent = "⧉"; }, 1200);
  }
}

function expandInto(div, c, onlyMatches, colQ){
  const ql = document.createElement("div");
  ql.className = "qlist";
  const sq = (colQ !== undefined ? colQ : normQ(state.search));
  let pool = c.idxs;
  if ((onlyMatches || state.searchOnly) && sq) pool = pool.filter(i => matchQ(i, sq));
  const sorted = [...pool].sort((x, y) => {
    if (sq){
      const mx = matchQ(x, sq) ? 1 : 0, my = matchQ(y, sq) ? 1 : 0;
      if (mx !== my) return my - mx;  // совпадения поиска — наверх
    }
    return F[y] - F[x];
  });
  const CAP = 300;
  ql.innerHTML = sorted.slice(0, CAP).map(i => {
    const q = sq;
    const hitDup = q && D && D[i] && D[i].some(f => f.toLowerCase().includes(q)) && !Q[i].toLowerCase().includes(q);
    const dd = D && D[i] && D[i].length
      ? ` <span class="dup${hitDup ? " hit" : ""}" title="${esc(D[i].join(", "))}">+${D[i].length} дубл.${hitDup ? " ✓" : ""}</span>` : "";
    const ib = INT && INT[i]
      ? `<span class="pint ${INT_CLS[INT[i]] || ""}" title="${esc(INT[i])}">${INT_SHORT[INT[i]] || "?"}</span> ` : "";
    const mk = (W && W[i] ? " ❓" : "") + (G && G[i] ? " 📍" : "");
    const eq = encodeURIComponent(Q[i]);
    const se = `<span class="se"><a class="ya" href="https://yandex.ru/search/?text=${eq}" target="_blank" rel="noopener" title="Выдача Яндекса">Я</a><a class="go" href="https://www.google.com/search?q=${eq}" target="_blank" rel="noopener" title="Выдача Google">G</a><a class="tr" data-i="${i}" title="В корзину — исключить из кластеризации">🗑</a></span>`;
    return `<div class="q"><input type="checkbox" class="qsel" data-i="${i}"${SELQ.has(Q[i]) ? " checked" : ""}><span class="qt">${ib}${hi(Q[i], q)}${mk}${dd}</span>${se}<span class="qf">${fmt(F[i])}</span></div>`;
  }).join("") + (sorted.length > CAP ? `<div class="q"><span class="qt" style="color:var(--muted)">… ещё ${fmt(sorted.length - CAP)}</span></div>` : "");
  ql.addEventListener("mousedown", e => { if (e.shiftKey) e.preventDefault(); });
  let lastSel = null;  // индекс строки последнего клика по галочке (для shift-диапазона)
  ql.addEventListener("click", e => {
    const cb = e.target.closest(".qsel");
    if (cb){
      e.stopPropagation();
      const boxes = [...ql.querySelectorAll(".qsel")];
      const pos = boxes.indexOf(cb);
      const on = cb.checked;  // состояние ПОСЛЕ клика — оно же применяется к диапазону
      if (e.shiftKey && lastSel !== null){
        const [a, b] = [Math.min(lastSel, pos), Math.max(lastSel, pos)];
        for (let k = a; k <= b; k++){
          boxes[k].checked = on;
          const t = Q[+boxes[k].dataset.i];
          if (on) SELQ.add(t); else SELQ.delete(t);
        }
      } else {
        const t = Q[+cb.dataset.i];
        if (on) SELQ.add(t); else SELQ.delete(t);
      }
      lastSel = pos;
      updateSelBar();
      return;
    }
    const tr = e.target.closest(".tr");
    if (!tr) return;
    e.preventDefault(); e.stopPropagation();
    TRASH.add(Q[+tr.dataset.i]);
    saveTrash(); render();
  });
  div.appendChild(ql);
}

