"use strict";
let _mq = {q: null};
// нормализация запроса: обычные — в нижний регистр, /регэкспы/ — как есть
function normQ(s){
  s = (s || "").trim();
  return /^(!!?)?\/.+\/[a-z]*$/i.test(s) ? s : s.toLowerCase();
}
function _tokenize(s){
  // [{w, ex}] — слово + флаг «точная форма»
  return s.split(/\s+/).filter(Boolean)
          .map(w => ({w: w.replace(/^!/, ""), ex: w.startsWith("!")}))
          .filter(t => t.w);
}
function _compileQ(q){
  if (_mq.q === q) return _mq;
  let neg = false, s = q;
  if (s.startsWith("!!")){ neg = true; s = s.slice(2).trim(); }
  else if (/^!\s*[\["«\/]/.test(s)){ neg = true; s = s.slice(1).trim(); }  // ![...] / !"..." / !/re/ — тоже инверсия
  let seq = null, quote = null, exact = null, subs = null, txt = "", re = null;
  let m;
  if ((m = s.match(/^\/(.+)\/([a-z]*)$/))){
    // регулярное выражение: /паттерн/флаги (без флагов — регистронезависимо)
    try{ re = new RegExp(m[1], m[2] || "i"); }catch(err){ txt = s; }
  } else if ((m = s.match(/^\[(.*)\]$/))){
    seq = _tokenize(m[1]);
    if (!seq.length) seq = null;
  } else if ((m = s.match(/^["«](.*)["»]$/))){
    quote = _tokenize(m[1]);
    if (!quote.length) quote = null;
  } else if (/(^|\s)!\S/.test(s)){
    exact = []; subs = [];
    for (const t of _tokenize(s)) (t.ex ? exact : subs).push(t.w);
    if (!exact.length){ exact = null; txt = subs.join(" "); subs = null; }
  } else txt = s;
  _mq = {q, neg, seq, quote, exact, subs, txt, re};
  return _mq;
}
// слово запроса подходит к слову фразы: точное совпадение, а без «!» — ещё и по лемме
function _wOk(t, raw, lem, k){ return raw[k] === t.w || (!t.ex && lem && lem[k] === t.w); }
function matchQ(i, q){
  const c = _compileQ(q);
  if (!c.seq && !c.quote && !c.exact && !c.txt && !c.re) return true;
  const hitText = (t, lt) => {
    if (c.re) return c.re.test(t);
    const low = t.toLowerCase();
    if (c.txt) return low.includes(c.txt);
    const raw = low.split(/\s+/);
    const lem = lt ? lt.split(/\s+/) : null;
    if (c.seq){
      outer: for (let s = 0; s + c.seq.length <= raw.length; s++){
        for (let j = 0; j < c.seq.length; j++)
          if (!_wOk(c.seq[j], raw, lem, s + j)) continue outer;
        return true;
      }
      return false;
    }
    if (c.quote){
      if (raw.length !== c.quote.length) return false;
      const used = new Array(raw.length).fill(false);
      for (const t2 of c.quote){
        let found = -1;
        for (let k = 0; k < raw.length; k++)
          if (!used[k] && _wOk(t2, raw, lem, k)){ found = k; break; }
        if (found < 0) return false;
        used[found] = true;
      }
      return true;
    }
    // смешанный режим: !точные + обычные подстроки
    return c.exact.every(w => raw.includes(w)) && c.subs.every(x => low.includes(x));
  };
  const hit = hitText(Q[i], L && L[i] || "") || !!(D && D[i] && D[i].some(f => hitText(f, "")));
  return c.neg ? !hit : hit;
}
function hi(s, q){
  if (!q) return esc(s);
  const rm = q.replace(/^!!?/, "").match(/^\/(.+)\/([a-z]*)$/);
  if (rm){
    try{
      const mm = s.match(new RegExp(rm[1], rm[2] || "i"));
      if (mm && mm[0])
        return esc(s.slice(0, mm.index)) + "<mark>" + esc(mm[0]) + "</mark>" +
               esc(s.slice(mm.index + mm[0].length));
    }catch(err){}
    return esc(s);
  }
  q = q.replace(/^!!?(?=[\["«])/, "").trim().replace(/^[\["«]/, "").replace(/[\]"»]$/, "")
       .replace(/^!!/, "").replace(/(^|\s)!(\S)/g, "$1$2").trim();
  if (!q) return esc(s);
  const i = s.toLowerCase().indexOf(q);
  if (i < 0) return esc(s);
  return esc(s.slice(0,i)) + "<mark>" + esc(s.slice(i, i+q.length)) + "</mark>" + esc(s.slice(i+q.length));
}

