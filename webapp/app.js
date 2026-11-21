/* Mini app (the page is webapp/index.html; rules: docs/DESIGN.md). One classic script: its functions and
   top-level names are shared globals, as they were inline. */
/* ================= Data layer =================
   Inside Telegram: real data from the bot's API (/api/...), authorised by initData.
   In a plain browser: demo data, so the design can be viewed without Telegram. */
const tgRaw = window.Telegram?.WebApp;
const inTg = !!(tgRaw && tgRaw.initData);
/* On the site (a plain browser) the person signs in through the bot; the session works like Telegram's initData.
   Demo data only on localhost or with ?demo. Outside Telegram «tg» is a stand-in with browser fallbacks. */
const DEMO = !inTg && (/^(localhost|127\.0\.0\.1)$/.test(location.hostname) || new URLSearchParams(location.search).has("demo"));
if(DEMO && window.parent !== window) document.documentElement.classList.add("embedded-demo");
let siteSession = null; try{ siteSession = localStorage.getItem("siteSession"); }catch(_){}
function siteTg(){
  const dark = matchMedia("(prefers-color-scheme: dark)");
  return {web: true, initData: "", platform: "web", get colorScheme(){ return dark.matches ? "dark" : "light"; },
    isVersionAtLeast: ()=>false, ready(){}, expand(){}, close(){}, BackButton: {show(){}, hide(){}, onClick(){}},
    onEvent(ev, f){ if(ev === "themeChanged") dark.addEventListener?.("change", f); if(ev === "activated") window.addEventListener("focus", f); },
    openLink(u){ window.open(u, "_blank", "noopener"); }, openTelegramLink(u){ window.open(u, "_blank", "noopener"); },
    showConfirm(m, cb){ cb(confirm(m)); }, showPopup(p){ alert(p.message); },
    enableClosingConfirmation(){}, disableClosingConfirmation(){}, addToHomeScreen(){}};
}
const tg = inTg ? tgRaw : DEMO ? null : siteTg();
const LIVE = !!tg;
const authHeader = () => inTg ? "tma " + tg.initData : "Session " + (siteSession || "");

let apiBusy = 0, apiGen = 0;   // requests the user caused: in flight / started so far — the background refresh must not race them
/* GET answers are kept with their ETag: the next poll asks «changed since?», and a 304 costs a few bytes.
   The text is kept, not the object, so each answer is a fresh copy nobody has changed on the screen yet. */
const apiCache = new Map();
/* The server restarts for a couple of seconds on every release: nginx answers 502/503 (the request never got in) or
   the connection drops. Such a request is sent again, for up to ~8 s, instead of «Не сохранилось». A dropped
   connection is retried only when a repeat is harmless: reading, PUT/PATCH/DELETE (the same result twice), and
   POSTs that carry X-Request-Id — the server makes a task once per id. */
const API_RETRY = [300, 600, 900, 1200, 1500, 1800, 2000];
async function apiFetch(path, opt, safe){
  for(let i = 0; ; i++){
    let r;
    try{ r = await fetch(path, opt); }
    catch(e){ if(!safe || i >= API_RETRY.length) throw e; await sleep(API_RETRY[i]); continue; }
    if((r.status === 502 || r.status === 503) && i < API_RETRY.length){ await sleep(API_RETRY[i]); continue; }
    return r;
  }
}
async function api(path, method="GET", body, background=false){
  if(tg?.web && !siteSession) throw new Error("not signed in");   // the site before signing in: nothing to ask the server
  if(!background){ apiBusy++; apiGen++; }
  try{
    const get = method === "GET", hit = get ? apiCache.get(path) : null;
    const rid = method === "POST" ? (crypto.randomUUID?.() || String(Math.random()).slice(2) + Date.now()) : null;
    const r = await apiFetch(path, {method, cache:"no-store", headers:{"Authorization":authHeader(), ...(body?{"Content-Type":"application/json"}:{}), ...(hit?{"If-None-Match":hit.tag}:{}), ...(rid?{"X-Request-Id":rid}:{})}, body: body?JSON.stringify(body):undefined},
                             method !== "POST" || /^\/api\/projects\/-?\d+\/tasks$/.test(path));
    if(r.status === 304 && hit) return JSON.parse(hit.text);
    if(r.status === 401 && tg?.web){ signedOut(); throw new Error("401 signed out"); }   // the site session ended: sign in again
    if(r.status === 403 && r.headers.get("Content-Type")?.includes("application/json")){
      const denied = await r.clone().json();
      if(denied.error === "alpha_required"){
        hideSplash(); $("#alphaGate").hidden = false; $("#alphaJoin").href = denied.link;
        throw new Error("alpha_required");
      }
    }
    if(!r.ok){ const t = await r.text().catch(()=> ""); throw new Error(r.status+" "+t); }
    const text = await r.text(), tag = r.headers.get("ETag");
    if(get && tag){ apiCache.set(path, {tag, text}); if(apiCache.size > 60) apiCache.delete(apiCache.keys().next().value); }
    return JSON.parse(text);
  }finally{ if(!background) apiBusy--; }
}

/* demo screenshots drawn with CSS */
const demoShots = {
  a:{bg:"linear-gradient(160deg,#0f172a 0 18%,#f8fafc 18% 100%)", ui:[["#22c55e",.62,.18,.22],["#e2e8f0",.2,.06,.6],["#e2e8f0",.32,.06,.45]]},
  b:{bg:"linear-gradient(180deg,#fef3c7 0 30%,#ffffff 30%)", ui:[["#f59e0b",.12,.1,.5],["#e5e7eb",.45,.05,.8],["#e5e7eb",.55,.05,.6]]},
  c:{bg:"linear-gradient(180deg,#ffffff 0 75%,#111827 75%)", ui:[["#e5e7eb",.2,.06,.7],["#6b7280",.83,.04,.5],["#6b7280",.9,.04,.35]]},
  d:{bg:"linear-gradient(135deg,#ddd6fe,#fbcfe8)", ui:[["#ffffff",.3,.25,.8],["#7c3aed",.65,.07,.4]]},
  e:{bg:"linear-gradient(135deg,#a7f3d0,#bae6fd)", ui:[["#ffffff",.15,.3,.8],["#0f766e",.55,.06,.55]]},
  f:{bg:"linear-gradient(180deg,#1f2937,#111827)", ui:[["#374151",.2,.2,.85],["#60a5fa",.5,.06,.5]]},
};
function shotHTML(src){
  if(!src) return "";
  // shimmer under the picture until it has loaded, then the picture fades in over it
  const st = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover";
  // already shown once → the browser has it cached: draw it at once, no shimmer and no fade (no blink on re-render)
  if(src.includes("/") && SEEN.has(src)) return `<img class="lazy-img ok" src="${src}" alt="" style="${st}">`;
  if(src.includes("/")) return `<i class="sk img-sk"></i><img class="lazy-img" src="${src}" alt="" decoding="async" onload="imgLoaded(this)" onerror="imgFailed(this)" style="${st}">`;
  const s = demoShots[src];
  return `<div style="position:absolute;inset:0;background:${s.bg}">${s.ui.map(([c,t,h,w])=>`<i style="position:absolute;left:10%;top:${t*100}%;height:${h*100}%;width:${w*100}%;background:${c};border-radius:4px"></i>`).join("")}</div>`;
}
// chat avatar: grey shimmer while loading; the coloured letter only if there is no picture
function avaLoaded(img){ SEEN.add(img.getAttribute("src")); img.classList.add("ok"); setTimeout(()=>img.parentElement?.classList.remove("sk"), 300); }
function avaFailed(img){ const a = img.parentElement; a.classList.remove("sk"); a.style.background = a.dataset.color; a.querySelector("span").hidden = false; img.remove(); }
const SEEN = new Set();   // pictures that have loaded at least once in this session
/* After a re-render: pictures the browser already has are shown at once, without shimmer or fade */
function settleImgs(root=document){
  root.querySelectorAll("img.lazy-img:not(.ok)").forEach(img=>{
    if(img.complete && img.naturalWidth){
      img.classList.add("ok", "instant"); SEEN.add(img.getAttribute("src"));
      const sk = img.previousElementSibling; if(sk?.classList.contains("img-sk")) sk.remove();
      img.parentElement?.classList.contains("ava") && img.parentElement.classList.remove("sk");
    }
  });
}
function imgLoaded(img){ SEEN.add(img.getAttribute("src")); img.classList.add("ok"); const sk = img.previousElementSibling; if(sk?.classList.contains("img-sk")) setTimeout(()=>sk.remove(), 300); }
function imgFailed(img){ const sk = img.previousElementSibling; if(sk?.classList.contains("img-sk")) sk.classList.add("failed"); img.remove(); }
const esc = s => String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
/* Long URLs in item text → short clickable label: host/…/last-part */
function shortUrl(u){
  try{
    const x = new URL(u), parts = x.pathname.split("/").filter(Boolean);
    let tail = parts.length ? parts.at(-1) : "";
    if(tail.length > 24) tail = tail.slice(0, 22) + "…";
    const host = x.host.replace(/^www\./, "");
    return parts.length > 1 ? `${host}/…/${tail}` : parts.length ? `${host}/${tail}` : host;
  }catch(e){ return u; }
}
function linkify(text){
  return esc(text).replace(/https?:\/\/[^\s<]+/g, m=>{
    const trail = (m.match(/[).,;:!?»"]+$/)||[""])[0], url = m.slice(0, m.length - trail.length);
    return `<a class="ln" href="${url}" data-url="${url}">${esc(shortUrl(url.replace(/&amp;/g,"&")))}</a>${trail}`;
  });
}

function ymdDemo(d){ return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; }
let projects = [
  {id:1,name:"Сайт клиники",color:"#e11d48",members:5},
  {id:2,name:"Лендинг «Кофейня»",color:"#f59e0b",members:3},
  {id:3,name:"Магазин сантехники",color:"#0ea5e9",members:4},
  {id:4,name:"Студия йоги",color:"#16a34a",members:2,bot_admin:false},
];
let tasks = [
  {id:14,pid:1,title:"Исправления на главной",author:"Анна",date:"7 ноя, 14:32",shots:["a","b","c"],videos:[],archived:false,
    due:(()=>{ const d = new Date(Date.now()+2*864e5); d.setHours(18,0,0,0); return {at:d.getTime()/1000, allday:false, date:ymdDemo(d), time:"18:00", text:d.toLocaleDateString("ru-RU",{day:"numeric",month:"long"})+", 18:00"}; })(), reminders:["day"], task_reminders:["day"], assignee:{id:2,name:"Олег Петров"},
    files:[{name:"Прайс-лист ноябрь.xlsx",size:48213,author:"Анна",url:"#"},{name:"Макет главной.pdf",size:31457280,author:"Олег",link:"https://t.me/c/1/1"}],
    transcripts:[{author:"Анна",text:"Так, и ещё на главной — кнопку «Купить» давайте сделаем зелёной, как на макете. И телефон в футере старый, новый — плюс семь девятьсот, ноль-ноль-ноль, ноль-ноль, ноль-ноль.",
      summary:{actions:["Сделать кнопку «Купить» на главной зелёной.", "Заменить телефон в футере на +7 900 000-00-00."], purpose:null, details:["Цвет кнопки взять из макета."]}}],
    items:[{t:"Поменять цвет кнопки «Купить» на зелёный",d:false,s:1},{t:"Убрать отступ под шапкой",d:true,s:2},{t:"Заменить телефон в футере на +7 900 000-00-00",d:false,s:3},{t:"Добавить ссылку на WhatsApp в шапку",d:false,s:null}],
    history:[{ts:Date.now()/1000-1500,actor:"Олег",kind:"done",text:"Убрать отступ под шапкой"},
             {ts:Date.now()/1000-3600*3,actor:"Олег",kind:"status",extra:{to:"progress"}},
             {ts:Date.now()/1000-3600*5,actor:"Анна",kind:"created",text:"Исправления на главной"}]},
  {id:13,pid:1,title:"Страница «Врачи»",author:"Олег",date:"6 ноя, 18:05",shots:["d","e"],videos:[{src:"f"}],archived:false,progress:true,
    history:[{ts:Date.now()/1000-1800,actor:"Олег",kind:"status",extra:{to:"review",from:"progress"}},
             {ts:Date.now()/1000-3600*2,actor:"Агент",agent:true,kind:"status",extra:{to:"progress"}},
             {ts:Date.now()/1000-3600*30,actor:"Олег",kind:"created",text:"Страница «Врачи»"}],
    items:[{t:"Фото врачей сделать одного размера",d:true,s:1},{t:"Подписать стаж под каждым",d:true,s:null},{t:"Кнопка «Записаться» у каждой карточки",d:false,s:2},{t:"Убрать раздел «Отзывы» до запуска",d:false,s:null},{t:"Шрифт заголовков крупнее",d:true,s:null}]},
  {id:12,pid:1,title:"Форма записи",author:"Анна",date:"5 ноя, 11:20",shots:["f"],archived:false,
    history:[{ts:Date.now()/1000-3600*26,actor:"Мария",kind:"closed"},
             {ts:Date.now()/1000-3600*26-60,actor:"Мария",kind:"done",text:"Текст согласия на обработку данных"},
             {ts:Date.now()/1000-3600*70,actor:"Анна",kind:"created",text:"Форма записи"}],
    closed:true, result_url:"https://example.com/zapis", hours:2.5, result_note:"Форма записи работает, маска телефона и согласие на главной и в карточках врачей",
    comments:[{id:901,author:"Анна",user_id:2,text:"Проверьте форму на телефоне, кнопка на главной съезжает",date:"5 ноя, 15:10",ts:Date.now()/1000-3600*60},
              {id:902,author:"Анна",user_id:2,question:true,resolved:false,text:"Форма должна уходить на почту или в CRM?",date:"5 ноя, 15:12",ts:Date.now()/1000-3600*59}],
    descr:"Обновить форму записи на главной. Макет: https://figma.com/file/clinic-form\nПочта для заявок — та же, что сейчас.",
    items:[{t:"Маска телефона в поле",d:true,s:1},{t:"Текст согласия на обработку данных",d:true,s:null}]},
  {id:11,pid:1,title:"Тексты на «О клинике»",author:"Мария",date:"2 ноя, 09:47",shots:[],archived:true,
    history:[{ts:Date.now()/1000-3600*50,actor:"Анна",kind:"archived"}],
    items:[{t:"Заменить первый абзац",d:false,s:null}]},
  {id:10,pid:1,title:"Мобильная версия меню",author:"Олег",date:"31 окт, 16:10",shots:["b"],archived:false,backlog:true,
    history:[{ts:Date.now()/1000-3600*75,actor:"Олег",kind:"status",extra:{to:"backlog"}}],
    items:[{t:"Бургер справа",d:false,s:1},{t:"Закрывать меню по тапу вне",d:false,s:null},{t:"Увеличить пункты до 48px",d:false,s:null}]},
  {id:9,pid:2,title:"Меню и цены",author:"Кирилл",date:"7 ноя, 10:02",shots:["e"],archived:false,items:[{t:"Обновить цены на капучино",d:false,s:1},{t:"Добавить сезонное меню",d:false,s:null}]},
  {id:8,pid:3,title:"Карточка товара",author:"Света",date:"4 ноя, 13:40",shots:["c"],archived:false,items:[{t:"Показать наличие на складе",d:true,s:1},{t:"Кнопка «Купить в 1 клик»",d:false,s:null}]},
];
if(LIVE){ projects = []; tasks = []; }

/* API task → screen model */
function fromApi(t){
  const photos = (t.media||[]).filter(m=>m.kind==="photo");
  return {...t,
    shots: photos.map(m=>m.url),
    shotIdx: photos.map(m=>m.idx),   // the server's number of each screenshot (videos share the numbering)
    videos: (t.media||[]).filter(m=>m.kind==="video").map(m=>({src:m.url, link:m.link})),
    items: (t.items||[]).map(i=>({id:i.id, t:i.t, d:i.d, s:i.s, m:i.m||[]})),
  };
}

/* ---------- Helpers ---------- */
const $ = s => document.querySelector(s);
const done = t => t.items ? t.items.filter(i=>i.d).length : t.done;
const total = t => t.items ? t.items.length : t.total;
// «Закрыта» is the task's own status (the server keeps it); demo tasks without it are closed when every item is ticked
const isClosed = t => t.closed !== undefined ? !!t.closed : !!(total(t) && done(t)===total(t));
/* the same rule as the server: the last open item ticked closes the task, an open item appearing reopens it */
function flipClosed(t, openBefore){
  if(t.closed === undefined) return;
  const openAfter = t.items.some(i=>!i.d);
  if(t.closed && openAfter && !openBefore) t.closed = false;
  else if(!t.closed && t.items.length && !openAfter && openBefore) t.closed = true;
}
let curProj = null, curTask = null, filter = "open";
const rk = t => t.rank ?? t.id;   // place in the project list: higher = upper
let hapticAt = 0;   // two taps within a moment (a button plus the pop-up it opens) feel like one
const haptic = (k="light") => { const now = Date.now(); if(now - hapticAt < 80) return; hapticAt = now; tg?.HapticFeedback?.impactOccurred?.(k); };

const TOAST_IC = {
  ok:  '<path d="M6 12.5L10 16.5L18 8"/>',
  warn:'<path d="M12 7V13"/><path d="M12 17.2V17"/>',
  err: '<path d="M8 8L16 16"/><path d="M16 8L8 16"/>',
};
function toast(msg, kind, o={}){
  kind = kind || (/^Не сохранилось|^Не удалось/.test(msg) ? "err" : /^Без разрешения/.test(msg) ? "warn" : "ok");
  const el = $("#toast"), was = el.classList.contains("show");
  const dur = Math.max(o.undo ? 5000 : 2000, msg.length * 55);   // long text stays longer; with «Отменить» — time to tap it
  const u = $("#toastUndo"); u.hidden = !o.undo;
  u.onclick = o.undo ? e=>{ e.stopPropagation(); hideToast(); haptic(); o.undo(); } : null;
  $("#toastTxt").textContent = msg;
  $("#toastIc").innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">${TOAST_IC[kind]}</svg>`;
  el.classList.remove("ok", "warn", "err", "bump"); el.classList.add(kind);
  el.style.setProperty("--dur", dur + "ms"); el.style.removeProperty("--dy");
  // restart the icon, text and countdown animations; a toast already shown gives a little bump instead of dropping again
  el.querySelectorAll(".ti,.ti svg,#toastTxt,.bar").forEach(x=>{ x.style.animation = "none"; void x.offsetWidth; x.style.animation = ""; });
  if(was){ void el.offsetWidth; el.classList.add("bump"); } else el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(hideToast, dur);
}
function hideToast(){ clearTimeout(toast._t); const el = $("#toast"); el.classList.remove("show", "bump", "drag"); el.style.removeProperty("--dy"); }
(()=>{   // tap — away; drag up — follows the finger, past a little — away
  const el = $("#toast"); let y0 = null, dy = 0;
  el.addEventListener("pointerdown", e=>{ if(e.target.closest(".tundo")) return; y0 = e.clientY; dy = 0; el.classList.add("drag"); clearTimeout(toast._t); el.setPointerCapture?.(e.pointerId); });
  el.addEventListener("pointermove", e=>{ if(y0 === null) return; dy = e.clientY - y0; el.style.setProperty("--dy", (dy < 0 ? dy : dy / 4) + "px"); });
  const end = ()=>{ if(y0 === null) return; y0 = null; el.classList.remove("drag");
    if(dy < -18 || Math.abs(dy) < 4) hideToast();
    else { el.style.removeProperty("--dy"); toast._t = setTimeout(hideToast, 1500); } };
  el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
})();
function synced(undo){ toast(LIVE ? "Чеклист в чате обновлён" : "Демо: в чате обновится чеклист", null, undo ? {undo} : {}); }
function fail(e){ if(e.message === "alpha_required") return; console.error(e); toast("Не сохранилось, попробуйте ещё раз"); }

/* Save a change of the current task: live → API, then re-render from the server answer */
/* Server answers are applied to the SAME objects the rows point at (no swapping curTask under them),
   only the newest answer counts, and a failed save reloads the task so the screen never lies. */
let saveSeq = 0;
/* undo: what «Отменить» in the toast does (the change goes back the same way it was made) */
async function save(call, rerender=true, taskId=curTask?.id, undo=null){
  if(!LIVE){ synced(undo); if(rerender){ renderItems(); updRing(); } return; }
  const my = ++saveSeq;
  try{
    const fresh = fromApi(await call());
    if(my !== saveSeq || !curTask || curTask.id !== taskId) return;   // a newer change is on its way, or the user left
    applyFresh(fresh, rerender); synced(undo);
  }catch(e){
    fail(e);
    if(curTask && curTask.id === taskId){ try{ applyFresh(fromApi(await api(`/api/tasks/${taskId}`)), true); }catch(_){} }
  }
}
function applyFresh(fresh, rerender){
  const cur = curTask.items;
  const same = fresh.items.length === cur.length && fresh.items.every((x,i)=>cur[i].id === x.id);
  const {items, ...rest} = fresh;
  Object.assign(curTask, rest);
  renderLinks(); renderHistory();
  if(same){
    items.forEach((x,i)=>{ if(cur[i]._t) delete x.d;  Object.assign(cur[i], x); });   // a tick still waiting to be sent wins
    if(rerender) renderItems(); else syncRows();
  } else { curTask.items = items; renderItems(); }
  updRing(true);
}
function syncRows(){   // only the done state of the rows, so running animations are not restarted
  document.querySelectorAll("#items .item").forEach(row=>{
    const it = curTask.items[+row.dataset.i]; if(it) row.classList.toggle("done", !!it.d);
  });
}

/* ---------- Theme: «Авто» follows Telegram; «Светлая» / «Тёмная» — our own palette, whatever Telegram uses ----------
   Kept in localStorage too, so the first frame already has the right colours (the cloud answers a moment later). */
function themePref(){ try{ const v = localStorage.getItem("themePref"); return v === "light" || v === "dark" ? v : null; }catch(_){ return null; } }
function applyTheme(){
  const pref = themePref(), root = document.documentElement;
  if(inTg){
    const own = !!pref && pref !== tg.colorScheme;
    root.classList.toggle("own-theme", own);
    root.dataset.theme = pref || tg.colorScheme;
    if(own){ const bg = getComputedStyle(root).getPropertyValue("--bg").trim(); tg.setHeaderColor?.(bg); tg.setBackgroundColor?.(bg); tg.setBottomBarColor?.(bg); }
    else { tg.setHeaderColor?.("bg_color"); tg.setBackgroundColor?.("bg_color"); tg.setBottomBarColor?.("bottom_bar_bg_color"); }
  } else if(pref) root.dataset.theme = pref; else root.removeAttribute("data-theme");
  const t = pref || "auto";
  document.querySelectorAll("#themePick button, #themeSw button").forEach(b=>{ b.classList.toggle("on", b.dataset.t === t); b.getAttribute("role") && b.setAttribute("aria-checked", b.dataset.t === t); });
}
function setTheme(t, fromCloud){
  try{ t === "auto" ? localStorage.removeItem("themePref") : localStorage.setItem("themePref", t); }catch(_){}
  if(!fromCloud) cloud.set("theme", t === "auto" ? null : t);
  applyTheme();
}
document.querySelectorAll("#themePick button").forEach(b=>b.onclick = ()=>{ haptic(); setTheme(b.dataset.t); });
applyTheme();

if(tg?.web || (DEMO && new URLSearchParams(location.search).has("desk"))) document.documentElement.classList.add("site", "wide");   // the site: our own header, a centred column (?desk — the same for the demo)
if(inTg){
  document.documentElement.classList.add("tg", "wide");   // wide: two panes once the window is ≥960 px (Telegram Desktop stretched out)
  applyTheme(); tg.onEvent("themeChanged", applyTheme);
  tg.ready(); tg.expand();
  tg.disableVerticalSwipes?.();
  tg.BackButton.onClick(()=>back());
}

/* ---------- Navigation ---------- */
const stack = ["s-projects"];
// screens are moved by transform only; the browser may still scroll the container (focus, scrollIntoView) — undo it
const screensBox = document.querySelector(".screens");
screensBox.addEventListener("scroll", ()=>{ if(screensBox.scrollLeft || screensBox.scrollTop) screensBox.scrollTo(0, 0); });
function go(id){
  if(stack.at(-1) === id) return;          // a double tap must not stack the same screen twice
  stopVoice();
  const cur = document.getElementById(stack.at(-1));
  cur.classList.add("off-left");
  const nxt = document.getElementById(id);
  nxt.classList.remove("off-right","off-left"); nxt.scrollTop = 0;
  stack.push(id); updBack();
}
function closeLB(){
  const lb = $("#lb"); lb.classList.remove("show");
  setTimeout(()=>{ if(!lb.classList.contains("show")) lb.classList.remove("open"); }, 230);
  updBack();
}
/* Back always closes what is open right now, and only then goes to the previous screen */
function layerOpen(){
  return $("#itemSheet").classList.contains("open") || $("#newSheet").classList.contains("open") || $("#renameSheet").classList.contains("open") || $("#projNameSheet").classList.contains("open") || $("#timeSheet").classList.contains("open") || $("#rangeSheet").classList.contains("open") || $("#picSheet").classList.contains("open") || $("#statusSheet").classList.contains("open") || $("#dueSheet").classList.contains("open") || $("#assignSheet").classList.contains("open") || $("#projSheet").classList.contains("open") || $("#srcSheet").classList.contains("open") || $("#commSheet").classList.contains("open") || $("#closeSheet").classList.contains("open") || $("#resSheet").classList.contains("open") || $("#linkSheet").classList.contains("open") || $("#descrSheet").classList.contains("open") || $("#commEditSheet").classList.contains("open")
      || $("#lb").classList.contains("open") || !!window.VibeLightbox?.isOpen() || !!openRow;
}
function back(){
  if($("#srcSheet").classList.contains("open")){ $("#srcSheet").classList.remove("open"); return; }
  if($("#commSheet").classList.contains("open")){ $("#commClose").click(); return; }
  if($("#descrSheet").classList.contains("open")){ $("#descrCancel").click(); return; }
  if($("#commEditSheet").classList.contains("open")){ $("#commEditCancel").click(); return; }
  if($("#closeSheet").classList.contains("open")){ $("#closeSheet").click(); return; }
  if($("#resSheet").classList.contains("open")){ $("#resSheet").classList.remove("open"); return; }
  if($("#linkSheet").classList.contains("open")){ $("#linkSheet").classList.remove("open"); return; }
  if($("#projSheet").classList.contains("open")){ $("#projSheet").classList.remove("open"); return; }
  if($("#assignSheet").classList.contains("open")){ $("#assignSheet").classList.remove("open"); return; }
  if($("#itemSheet").classList.contains("open")){ $("#itemCancel").click(); return; }
  if($("#renameSheet").classList.contains("open")){ $("#renameCancel").click(); return; }
  if($("#projNameSheet").classList.contains("open")){ $("#projNameCancel").click(); return; }
  if($("#timeSheet").classList.contains("open")){ $("#timeCancel").click(); return; }
  if($("#rangeSheet").classList.contains("open")){ $("#rangeCancel").click(); return; }
  if($("#picSheet").classList.contains("open")){ $("#picCancel").click(); return; }
  if($("#newSheet").classList.contains("open")){ $("#newCancel").click(); return; }
  if($("#statusSheet").classList.contains("open")){ $("#statusSheet").classList.remove("open"); return; }
  if($("#dueSheet").classList.contains("open")){ $("#dueCancel").click(); return; }
  if(window.VibeLightbox?.isOpen()){ VibeLightbox.close(); return; }
  if($("#lb").classList.contains("open")){ closeLB(); return; }
  if(openRow){ closeRow(); updBack(); return; }
  if(stack.at(-1) === "s-task" && taskTrail.length){ openTask(taskTrail.pop(), true); return; }   // back through the linked tasks
  if(stack.at(-1) === "s-people" && !ppl.mine && ppl.fromMine){ ppl.fromMine = false; openPeople(true, "slide-back"); updBack(); return; }   // «Все задачи» opened from «Мои»
  if(stack.length===1){ if(tg) tg.close(); else toast("Здесь мини-приложение закроется"); return; }
  stopVoice();
  const cur = document.getElementById(stack.pop());
  if(cur.id === "s-task"){ openSeq++; if(ghosting) ghostOff(); }      // a task still loading must not pop up after Back
  if(cur.id === "s-task" && curTask && opened.id === curTask.id){
    const now = groupOf(curTask);
    if(stack.at(-1) === "s-tasks" && opened.group === filter && now !== filter) leaving = {id: curTask.id, to: now};   // animate it out of the list
  }
  cur.classList.add("off-right");
  document.getElementById(stack.at(-1)).classList.remove("off-left");
  if(stack.at(-1)==="s-tasks") loadTasks();
  if(stack.at(-1)==="s-history") refreshFeed();   // back from a task: it may have just changed
  if(stack.at(-1)==="s-notif") loadNotif(false, true);
  if(stack.at(-1)==="s-people") loadPeople();
  if(stack.at(-1)==="s-comments") loadCommentsFeed();
  if(stack.at(-1)==="s-time") loadTime();
  if(stack.at(-1)==="s-projects") loadProjects();
  updBack();
}
const twoPanes = () => document.documentElement.classList.contains("wide") && matchMedia("(min-width:960px)").matches;
/* two panes: the task open on the right is highlighted in the list on the left */
function markSel(){
  document.querySelectorAll(".task.sel").forEach(x=>x.classList.remove("sel"));
  if(twoPanes() && stack.at(-1) === "s-task" && curTask) document.querySelectorAll(`.task[data-t="${curTask.id}"]`).forEach(x=>x.classList.add("sel"));
}
function updBack(){
  updTabs();
  // wide site: under an open task its list stays on the left
  document.querySelectorAll(".screen.under").forEach(x=>x.classList.remove("under"));
  if(stack.at(-1) === "s-task" && stack.length > 1) document.getElementById(stack.at(-2))?.classList.add("under");
  markSel();
  const canBack = stack.length > 1 || layerOpen();
  $("#backLbl").textContent = canBack ? "Назад" : "Закрыть";
  if(tg?.web) $("#backBtn").style.visibility = canBack ? "" : "hidden";   // the site: nothing to close
  if(tg){ canBack ? tg.BackButton.show() : tg.BackButton.hide(); }
}
/* Every pop-up from the bottom closes with a swipe down: the card follows the finger; past a point (or a flick) it goes
   away the same way a tap on the dimmed background closes it, otherwise it springs back */
(()=>{
  const CLOSE_AT = 90;
  let st = null;
  const scrolled = (el, card) => { for(let e = el; e && e !== card.parentElement; e = e.parentElement) if(e.scrollTop > 0) return true; return false; };
  document.addEventListener("touchstart", e=>{
    st = null;
    const card = e.target.closest?.(".sheet.open > .card"); if(!card || e.touches.length !== 1) return;
    if(scrolled(e.target, card)) return;   // a list inside scrolled down: the swipe scrolls it back first
    st = {card, sh: card.parentElement, y0: e.touches[0].clientY, x0: e.touches[0].clientX, on: false, dy: 0, t0: e.timeStamp, lt: e.timeStamp, ly: e.touches[0].clientY, v: 0};
  }, {passive: true});
  document.addEventListener("touchmove", e=>{
    if(!st) return;
    const y = e.touches[0].clientY, dy = y - st.y0, dx = e.touches[0].clientX - st.x0;
    if(!st.on){
      if(Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)){ st = null; return; }   // sideways: a chip strip, a slider
      if(dy < -6){ st = null; return; }
      if(dy < 8) return;
      st.on = true; st.y0 += 8; st.card.style.transition = "none";
    }
    if(e.cancelable) e.preventDefault();
    const d = Math.max(0, y - st.y0); st.dy = d;
    st.v = (y - st.ly) / Math.max(1, e.timeStamp - st.lt); st.ly = y; st.lt = e.timeStamp;
    st.card.style.transform = `translateY(${d.toFixed(1)}px)`;
  }, {passive: false});
  const end = ()=>{
    if(!st?.on){ st = null; return; }
    const {card, sh, dy, v} = st; st = null;
    card.style.transition = "transform .28s cubic-bezier(.32,.72,0,1), opacity .2s";
    if(dy > CLOSE_AT || (dy > 24 && v > .5)){   // away: down off the screen, the pop-up closes as from a tap outside
      haptic();
      card.style.transform = `translateY(${card.offsetHeight + 40}px)`; card.style.opacity = "0";
      document.activeElement?.blur?.();
      sh.dispatchEvent(new MouseEvent("click", {bubbles: true}));   // the sheet's own «tap outside» close (keeps drafts)
      if(sh.classList.contains("open")) sh.classList.remove("open");
      setTimeout(()=>{ card.style.transition = card.style.transform = card.style.opacity = ""; }, 320);
    } else {
      card.style.transform = "";
      setTimeout(()=>{ card.style.transition = ""; }, 300);
    }
  };
  document.addEventListener("touchend", end); document.addEventListener("touchcancel", end);
})();
/* Pull to refresh: at the very top of a screen a strong pull down reloads what the screen shows;
   if a newer version of the app is out, the whole page reloads instead */
/* BUILD — the page's version: set inline in index.html (the server writes it there) */
async function newVersionOut(){
  if(BUILD.startsWith("__")) return false;   // not served by our server (demo, local file)
  try{
    const ctl = new AbortController(), tm = setTimeout(()=>ctl.abort(), 2500);
    const r = await fetch("/api/version", {cache: "no-store", signal: ctl.signal}); clearTimeout(tm);
    return r.ok && (await r.json()).v !== BUILD;
  }catch(_){ return false; }
}
async function refreshScreen(){
  if(await newVersionOut()){ location.reload(); return new Promise(()=>{}); }   // the spinner keeps turning until the new page is here
  const top = stack.at(-1);
  if(top === "s-projects") return loadProjects();
  if(top === "s-tasks") return loadTasks();
  if(top === "s-people") return loadPeople();
  if(top === "s-history") return refreshFeed();
  if(top === "s-notif") return loadNotif(false, true);
  if(top === "s-comments") return loadCommentsFeed();
  if(top === "s-time") return loadTime();
  if(top === "s-task" && curTask && LIVE){ const id = curTask.id, f = fromApi(await api(`/api/tasks/${id}`)); if(curTask?.id === id) applyFresh(f, true); }
}
(()=>{
  const PULL_AT = 84, MAX = 150, HOLD = 56, ind = $("#ptr"), SZ = 34;   // px of the screen's way down
  let st = null, busy = false;
  const ease = "cubic-bezier(.32,.72,0,1)";
  // d: how far the screen is down. The icon stays in the middle of the gap, grows and shows as the gap opens
  const set = (sc, d, anim) => {
    const k = Math.min(1, d / PULL_AT);
    [sc, ind].forEach(x=>x.style.transition = anim ? `transform .35s ${ease}, opacity .2s, background .15s, color .15s` : "none");
    sc.style.transform = d ? `translateY(${d.toFixed(1)}px)` : "";
    const armed = ind.classList.contains("armed");
    ind.style.transform = `translateY(${Math.max(0, d / 2 - SZ / 2).toFixed(1)}px) scale(${(armed ? 1.12 : .6 + .4 * k).toFixed(3)})`;
    ind.style.opacity = d < 16 ? 0 : Math.min(1, (d - 16) / 30);
  };
  const resist = dy => MAX * (1 - Math.exp(-dy / (MAX * 1.1)));   // follows the finger, ever more slowly
  document.addEventListener("touchstart", e=>{
    st = null;
    if(busy || e.touches.length !== 1 || lifted || layerOpen()) return;
    const sc = e.target.closest?.(".screen"); if(!sc || sc.scrollTop > 0) return;
    st = {sc, x0: e.touches[0].clientX, y0: e.touches[0].clientY, on: false, d: 0};
  }, {passive: true});
  document.addEventListener("touchmove", e=>{
    if(!st) return;
    const dx = e.touches[0].clientX - st.x0, dy = e.touches[0].clientY - st.y0;
    if(!st.on){
      if(Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)){ st = null; return; }   // sideways: a row swipe, not a pull
      if(dy < -8 || st.sc.scrollTop > 0 || lifted){ st = null; return; }
      if(dy < 6) return;
      st.on = true; st.y0 += 6; st.sc.classList.add("pulling");
    }
    if(e.cancelable) e.preventDefault();   // our pull, not the page's own bounce
    const d = resist(Math.max(0, dy - 6)); st.d = d; set(st.sc, d);
    const armed = d >= PULL_AT;
    if(armed !== ind.classList.contains("armed")){ ind.classList.toggle("armed", armed); haptic(armed ? "medium" : "light"); set(st.sc, d); }
  }, {passive: false});
  const end = async ()=>{
    if(!st?.on){ st = null; return; }
    const {sc, d} = st, armed = d >= PULL_AT; st = null;
    sc.classList.remove("pulling");
    if(!armed){ ind.classList.remove("armed"); set(sc, 0, true); return; }
    busy = true; ind.classList.remove("armed"); set(sc, HOLD, true); ind.classList.add("spin");   // it stays a little down while the screen reloads
    try{ await Promise.all([refreshScreen(), sleep(600)]); }catch(_){}
    ind.classList.remove("spin", "armed"); set(sc, 0, true);
    setTimeout(()=>{ sc.style.transition = ""; busy = false; }, 360);
  };
  document.addEventListener("touchend", end); document.addEventListener("touchcancel", end);
})();
/* Swipe from the left edge goes back, as in iOS apps: the screen follows the finger, the previous one shows from under it.
   Android has its own system back gesture (Telegram turns it into the Back button), so no second one there. */
(()=>{
  if(tg?.platform === "android") return;
  const EDGE = 22;
  let sw = null;
  const killClick = e=>{ e.stopPropagation(); e.preventDefault(); };
  document.addEventListener("pointerdown", e=>{
    sw = null;
    if(!e.isPrimary || e.clientX > EDGE || stack.length < 2 || layerOpen()) return;
    sw = {x0: e.clientX, y0: e.clientY, cur: document.getElementById(stack.at(-1)), prev: document.getElementById(stack.at(-2)),
          on: false, x: 0, v: 0, lx: e.clientX, lt: e.timeStamp};
    e.stopPropagation();   // rows must not start their own sideways swipe from the edge
  }, true);
  document.addEventListener("pointermove", e=>{
    if(!sw) return;
    const dx = e.clientX - sw.x0, dy = e.clientY - sw.y0;
    if(!sw.on){
      if(Math.abs(dy) > 10 && Math.abs(dy) > dx){ sw = null; return; }   // a scroll, not a swipe
      if(dx < 10) return;
      sw.on = true; stopVoice();
      sw.cur.classList.add("edge-drag");
      [sw.cur, sw.prev].forEach(el=>el.style.transition = "none");
    }
    const w = sw.cur.offsetWidth, x = Math.max(0, Math.min(w, dx)), p = x / w;
    sw.v = (e.clientX - sw.lx) / Math.max(1, e.timeStamp - sw.lt); sw.lx = e.clientX; sw.lt = e.timeStamp; sw.x = x;
    sw.cur.style.transform = `translateX(${x}px)`;
    sw.prev.style.transform = `translateX(${-28 * (1 - p)}%)`;
    sw.prev.style.opacity = Math.min(1, .3 + p);
  }, true);
  const end = e=>{
    const s = sw; sw = null;
    if(!s?.on) return;
    const done = e.type === "pointerup" && (s.x > s.cur.offsetWidth * .35 || s.v > .45);
    // inline styles off and classes switched in one go: the screens glide on from where the finger left them
    [s.cur, s.prev].forEach(el=>{ el.style.transition = el.style.transform = el.style.opacity = ""; });
    s.cur.classList.remove("edge-drag");
    if(done){ haptic(); back(); }
    document.addEventListener("click", killClick, true);   // the lifted finger must not tap whatever is under it
    setTimeout(()=>document.removeEventListener("click", killClick, true), 350);
  };
  document.addEventListener("pointerup", end, true);
  document.addEventListener("pointercancel", end, true);
  document.addEventListener("touchmove", e=>{ if(sw?.on) e.preventDefault(); }, {passive: false});   // no page scroll while dragging
})();
// popups and the viewer open/close in many places: keep the Back button in sync automatically
const backSync = new MutationObserver(()=>updBack());
["linkSheet","itemSheet","renameSheet","projNameSheet","timeSheet","rangeSheet","picSheet","newSheet","statusSheet","dueSheet","assignSheet","lb"].forEach(id=>backSync.observe(document.getElementById(id), {attributes:true, attributeFilter:["class"]}));
$("#backBtn").onclick = back;
$("#moreBtn").onclick = ()=>tg?.web ? $("#openSettings").click() : toast("Меню Telegram: обновить, настройки");

const plural = (n, one, few, many) => { const a=n%10, b=n%100; return a===1&&b!==11?one : a>=2&&a<=4&&(b<12||b>14)?few : many; };

/* ---------- Motion helpers ---------- */
const sleep = ms => new Promise(r=>setTimeout(r, ms));
const DEMO_DELAY = 450;   // demo mode pretends to load, so the skeletons can be seen
const skLine = (w, h=12, extra="") => `<div class="sk sk-line" style="width:${w};height:${h}px;${extra}"></div>`;
function skRows(n, kind){
  return Array.from({length:n}, (_,i)=> kind==="proj"
    ? `<div class="row" style="opacity:${1-i*.18}"><div class="ava sk"></div><div class="main">${skLine("58%",14)}${skLine("38%",11,"margin-top:8px")}</div><div class="sk" style="width:58px;height:22px;border-radius:999px"></div></div>`
    : `<div class="task" style="opacity:${1-i*.16}"><div class="thumb sk" style="border:0"></div><div class="main">${skLine("72%",14)}${skLine("44%",11,"margin-top:8px")}${skLine("100%",4,"margin-top:12px")}</div></div>`).join("");
}
function countUp(el, to, suffix=""){
  const t0 = performance.now(), dur = 600;
  const step = now=>{ const k = Math.min(1,(now-t0)/dur), e = 1-Math.pow(1-k,3); el.firstChild.nodeValue = Math.round(to*e) + suffix; if(k<1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
// one-shot animation classes must not keep holding transform (drag & swipe set it inline)
document.addEventListener("animationend", e=>e.target.classList?.remove("enter","pop-in","celebrate","fade-swap","bump","tick","flash"));
const replay = (el, cls) => { if(!el) return; el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); };
/* A task that left the current tab (closed, archived, sent to backlog): wait until the list has slid in,
   hide the row under a mask from the bottom up, let the rows below move up, then bump the tab it went to. */
let leaving = null;
let opened = {id: null, group: null};
const groupOf = t => t.archived ? "archive" : t.backlog ? "backlog" : isClosed(t) ? "closed" : t.progress ? "progress" : "open";
const stOf = t => t.backlog ? "backlog" : t.progress ? (t.review ? "review" : "progress") : "open";   // the status a task is in when not closed; «На проверке» is still in work (its tab is «В работе»)
const pickOf = t => isClosed(t) && !t.backlog ? "closed" : stOf(t);   // what the status picker shows as chosen
const STATUSES = {
  open:     {name:"Открыта",   tab:"Открытые", color:"var(--st-open)", sub:"Новая, ещё никто не взял",
             icon:'<path d="M18.364 5.636C21.879 9.151 21.879 14.849 18.364 18.364C14.849 21.879 9.151 21.879 5.636 18.364C2.121 14.849 2.121 9.151 5.636 5.636C9.151 2.121 14.849 2.121 18.364 5.636"/>'},
  progress: {name:"В работе",  tab:"В работе", color:"var(--st-progress)", sub:"Кто-то уже делает",
             icon:'<path d="M11.835 14.995L16.835 19.989C17.663 20.808 19.007 20.808 19.835 19.989V19.989C20.663 19.171 20.663 17.82 19.835 17.001L14.835 12.007"/><path fill-rule="evenodd" clip-rule="evenodd" d="M20.916 5.866C20.94 5.866 20.958 5.907 20.967 5.948C21.437 7.503 21.073 9.264 19.835 10.492C18.584 11.761 16.797 12.129 15.218 11.597L6.871 19.948C6.058 20.767 4.732 20.849 3.891 20.071C3.007 19.212 2.989 17.861 3.835 17.001L12.218 8.609C11.726 7.053 12.084 5.252 13.335 3.983C14.573 2.755 16.336 2.386 17.903 2.878C17.935 2.878 17.965 2.878 17.988 2.918L18.15 3.082C18.228 3.164 18.228 3.287 18.15 3.369L15.515 5.989L17.835 8.322L20.471 5.702C20.549 5.62 20.676 5.62 20.754 5.702L20.916 5.866V5.866Z"/><path fill-rule="evenodd" clip-rule="evenodd" d="M2.933 4.31L3.607 6.316C3.743 6.726 4.125 7.012 4.556 7.012H6.835V4.72C6.835 4.31 6.56 3.901 6.151 3.778L4.128 3.082C3.948 3.041 3.75 3.082 3.616 3.205L3.054 3.778C2.92 3.901 2.873 4.106 2.933 4.31V4.31Z"/><path d="M6.84 7.012L10.34 10.492"/>'},
  backlog:  {name:"В бэклоге", tab:"Бэклог",   color:"var(--st-backlog)", sub:"Отложена на потом",
             icon:'<path d="M12 10.98V3.325"/><path fill-rule="evenodd" clip-rule="evenodd" d="M12 10.98L15.001 7.992L12 10.98Z"/><path d="M2.996 14.992H7.38C7.759 14.992 8.105 15.197 8.274 15.565L8.722 16.466C8.891 16.793 9.238 16.998 9.617 16.998H14.382C14.761 16.998 15.108 16.793 15.277 16.466L15.725 15.565C15.894 15.197 16.241 14.992 16.62 14.992H21.004"/><path d="M15.001 2.997H18.003C19.66 2.997 21.004 4.348 21.004 5.986V18.021C21.004 19.659 19.66 21.01 18.003 21.01H5.998C4.34 21.01 2.996 19.659 2.996 18.021V5.986C2.996 4.348 4.34 2.997 5.998 2.997H8.999"/><path fill-rule="evenodd" clip-rule="evenodd" d="M8.999 7.992L12 10.98L8.999 7.992Z"/>'},
};
const REVIEW = {name:"На проверке", color:"var(--st-review)", sub:"Сделана — автор задачи проверит, ему придёт напоминание",
  icon:'<circle cx="11" cy="11" r="6.5"/><path d="M20 20L15.7 15.7"/><path d="M8.4 11.2L10.2 13L13.6 9.5"/>'};
const PICK = {open: STATUSES.open, progress: STATUSES.progress, review: REVIEW, backlog: STATUSES.backlog, closed: {name:"Закрыта", color:"var(--st-closed)", sub:"Сделана — даже если отмечены не все пункты",
  icon:'<circle cx="12" cy="12" r="8.5"/><path d="M8.6 12.3L10.9 14.6L15.4 9.9"/>'}};
/* Task badges: one set — same height, a 13px icon, the kind's colour as text and a soft fill */
const BDG = {   // one drawing style: 24-grid, 2px stroke, round ends; circles are r 8.5 so they all weigh the same
  open:     {c:"var(--st-open)", i:'<circle cx="12" cy="12" r="8.5"/>'},
  progress: {c:"var(--st-progress)", i:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5A4.5 4.5 0 0 1 12 16.5Z" fill="currentColor" stroke="none"/>'},
  review:   {c:"var(--st-review)", i:'<circle cx="11" cy="11" r="6.5"/><path d="M20 20L15.7 15.7"/>'},
  backlog:  {c:"var(--st-backlog)", i:'<circle cx="12" cy="12" r="8.5" stroke-dasharray="3.2 3.5"/>'},
  closed:   {c:"var(--st-closed)", i:'<circle cx="12" cy="12" r="8.5"/><path d="M8.6 12.3L10.9 14.6L15.4 9.9"/>'},
  archive:  {c:"var(--st-archive)", i:'<rect x="3.5" y="4.5" width="17" height="5" rx="1.5"/><path d="M5.5 9.5V17.5C5.5 18.6 6.4 19.5 7.5 19.5H16.5C17.6 19.5 18.5 18.6 18.5 17.5V9.5"/><path d="M10 13.5H14"/>'},
  due:      {c:"var(--k-due)", i:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.8V12L14.8 13.8"/>'},
  late:     {c:"var(--k-late)", i:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.8V12.6"/><path d="M12 16.2H12.01"/>'},
  who:      {c:"var(--k-who)", i:'<circle cx="12" cy="8.5" r="3.8"/><path d="M4.8 20C4.8 16.3 8 13.8 12 13.8C16 13.8 19.2 16.3 19.2 20"/>'},
  items:    {c:"var(--st-open)", i:'<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.6 12.3L10.9 14.6L15.4 9.9"/>'},
  itemsDone:{c:"var(--st-closed)", i:'<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.6 12.3L10.9 14.6L15.4 9.9"/>'},
  ask:      {c:"var(--k-ask)", i:'<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6C9.8 8.3 10.8 7.4 12 7.4C13.2 7.4 14.2 8.3 14.2 9.5C14.2 11.2 12 11.4 12 13.2"/><path d="M12 16.4H12.01"/>'},
  comm:     {c:"var(--k-comm)", i:'<path d="M19.5 11.5C19.5 15.4 16.1 18.5 12 18.5C11 18.5 10 18.3 9.1 18L4.5 19.5L5.7 15.8C4.9 14.6 4.5 13.1 4.5 11.5C4.5 7.6 7.9 4.5 12 4.5C16.1 4.5 19.5 7.6 19.5 11.5Z"/>'},
  time:     {c:"var(--k-time)", i:'<circle cx="12" cy="13.2" r="7.3"/><path d="M12 9.6V13.2L14.2 14.6"/><path d="M10 3.5H14"/>'},
  res:      {c:"var(--st-closed)", i:'<path d="M7 3.5H14L18.5 8V19C18.5 19.8 17.8 20.5 17 20.5H7C6.2 20.5 5.5 19.8 5.5 19V5C5.5 4.2 6.2 3.5 7 3.5Z"/><path d="M9 13.2L11.1 15.3L15 11.3"/>'},
  bell:     {c:"", i:'<path d="M6.5 16.5V11C6.5 8 9 5.5 12 5.5C15 5.5 17.5 8 17.5 11V16.5"/><path d="M4.5 16.5H19.5"/><path d="M10.2 19.5H13.8"/>'},
};
const bIc = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${BDG[k].i}</svg>`;
/* kind: a BDG key; opts: {tag:"button", id, none:true (an empty slot, grey), extra: html after the text} */
function badge(kind, text, o={}){
  const tag = o.tag || "span";
  return `<${tag} class="bdg${o.none ? " none" : ""}${o.plain ? " plain" : ""}"${o.id ? ` id="${o.id}"` : ""} style="--bc:${o.none || o.plain ? "var(--hint)" : o.color || BDG[kind].c}">${bIc(kind)}<span>${esc(text)}</span>${o.extra || ""}</${tag}>`;
}
const itemsBadge = (d, n) => n ? badge(d === n ? "itemsDone" : "items", `${d}/${n}`, {plain: true}) : "";   // the checklist's progress, last in the row: plain grey text
const openQs = t => (t.comments || []).filter(c=>c.question && !c.resolved).length;
const qOf = t => t.comments ? openQs(t) : (t.questions || 0);   // a full task counts them, a row has the server's number
const qBadge = t => qOf(t) ? badge("ask", `${qOf(t)} ${plural(qOf(t), "вопрос", "вопроса", "вопросов")}`) : "";
const statusOf = t => t.archived ? "archive" : isClosed(t) ? "closed" : stOf(t);
const STATUS_TXT = {open:"Открыта", progress:"В работе", review:"На проверке", backlog:"В бэклоге", closed:"Закрыта", archive:"В архиве"};
const statusBadge = (t, o) => badge(statusOf(t), STATUS_TXT[statusOf(t)], o);
const dueBadge = (t, o={}) => badge(isLate(t) ? "late" : "due", (o.long ? (isLate(t) ? "срок прошёл · " : "до ") : "") + t.due.text, o);
/* each person keeps one colour (by Telegram id, else by name) — rows of different people look different */
const WHO_N = 8;
const whoColor = a => `var(--who-${Math.abs([...String(a.id ?? a.name ?? "")].reduce((h, ch)=>(h * 31 + ch.charCodeAt(0)) | 0, 7)) % WHO_N})`;
const whoBadge = (a, o) => badge("who", o?.full ? a.name : firstName(a.name), {...o, color: whoColor(a)});
const stIcon = (k, size=18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${PICK[k].icon}</svg>`;
function maskOut(el){
  return new Promise(res=>{
    el.style.height = el.offsetHeight + "px"; el.style.clipPath = "inset(0 0 0 0)"; el.style.overflow = "hidden";
    void el.offsetWidth;
    el.style.transition = "clip-path .4s cubic-bezier(.32,.72,0,1), height .44s cubic-bezier(.32,.72,0,1) .12s, border-top-width .44s .12s";
    el.style.clipPath = "inset(0 0 100% 0)"; el.style.height = "0px"; el.style.borderTopWidth = "0px";
    setTimeout(res, 600);
  });
}
async function exitRow(id, to){
  await sleep(430);
  const w = document.querySelector(`.swipe[data-w="${id}"]`); if(!w) return;
  await maskOut(w); w.remove();
  if(!document.querySelector("#taskList .swipe")) renderTasks();
  bumpTab(to);
}
function bumpTab(to){
  const n = document.querySelector(`#filter [data-f="${to}"] .n`);
  if(n){ n.classList.remove("bump"); void n.offsetWidth; n.classList.add("bump"); }
}
/* the menu scrolls sideways: the pill follows the active tab, the edges fade where there is more */
function placeKnob(instant=false){
  const seg = $("#filter"), b = seg.querySelector(`[data-f="${filter}"]`), k = seg.querySelector(".knob");
  if(!b || !b.offsetWidth) return;
  k.classList.toggle("instant", instant);
  k.style.width = b.offsetWidth + "px"; k.style.transform = `translateX(${b.offsetLeft}px)`;
  const left = b.offsetLeft - 24, right = b.offsetLeft + b.offsetWidth + 24 - seg.clientWidth;
  if(left < seg.scrollLeft) seg.scrollTo({left, behavior: instant ? "auto" : "smooth"});
  else if(right > seg.scrollLeft) seg.scrollTo({left: right, behavior: instant ? "auto" : "smooth"});
  segFade();
}
function segFade(){
  const seg = $("#filter"), w = $("#segWrap");
  w.classList.toggle("fl", seg.scrollLeft > 2);
  w.classList.toggle("fr", seg.scrollLeft < seg.scrollWidth - seg.clientWidth - 2);
}
$("#filter").addEventListener("scroll", segFade, {passive:true});
window.addEventListener("resize", ()=>placeKnob(true));
document.fonts?.ready.then(()=>placeKnob(true));
function collapse(el){
  return new Promise(res=>{
    el.style.height = el.offsetHeight + "px"; el.style.overflow = "hidden";
    requestAnimationFrame(()=>{ el.classList.add("collapse"); el.style.height = "0px"; el.style.paddingTop = el.style.paddingBottom = "0px"; el.style.borderTopWidth = "0px"; });
    setTimeout(res, 330);
  });
}
/* ---------- Loading a task: the real screen drawn from what the lists already know, measured into bones ----------
   (the idea of skullet.lochie.me) A bar for every line of text, a block for every photo, chip, checkbox and icon, each
   exactly where the content will be — so nothing moves when it arrives. One diagonal shimmer runs across all of them. */
function taskHint(id){
  return tasks.find(x=>x.id == id) || ppl.data?.tasks.find(x=>x.id == id) || null;
}
const GHOST_TEXTS = ["Поменять текст на главной странице", "Проверить форму заявки", "Заменить фото в карточке товара на новое",
  "Убрать лишний блок", "Добавить ссылку в шапку сайта", "Обновить цены"];
function ghostTask(id, h){
  const n = h ? Math.min(Math.max(total(h) || 0, 1), 8) : 4;
  const shots = h ? (Array.isArray(h.shots) ? h.shots.length + (h.videos||[]).length : (h.shots || (h.thumb ? 1 : 0))) : 0;
  return {
    id: +id, pid: h?.pid ?? curProj?.id, project: h?.project || curProj?.name || "Проект",
    title: h?.title || "Название задачи", author: h?.author || "Автор", date: h?.date || "1 ноя, 12:00",
    archived: !!h?.archived, backlog: !!h?.backlog, progress: !!h?.progress, review: !!h?.review, due: h?.due || null, assignee: h?.assignee || null,
    shots: Array(Math.min(shots, 4)).fill(""), shotIdx: [], videos: [],
    items: Array.from({length: n}, (_, k)=>({id: -1 - k, t: GHOST_TEXTS[k % GHOST_TEXTS.length], d: false, m: []})),
    history: [], files: [], notes: [], transcripts: [], comments: [], descr: "", link: "", task_reminders: [], reminders: [], ghost: true,
  };
}
let ghosting = false;
const BONE_BLOCK_MAX = .6;   // wider than this share of the screen: a container — its insides are boned, not itself
function boneUp(root, scopes = [root]){
  const layer = document.createElement("div"); layer.className = "bones";
  const R = root.getBoundingClientRect(), st = root.scrollTop, W = root.clientWidth;
  const bones = [];
  const add = (r, radius, bar) => {
    const l = Math.max(r.left, R.left), rt = Math.min(r.right, R.right);   // only what is inside the screen
    if(rt - l < 2 || r.height < 2) return;
    r = {left: l, right: rt, width: rt - l, top: r.top, height: r.height};
    let top = r.top - R.top + st, h = r.height;
    if(bar){ const fs = bar; const hh = Math.min(fs * .72, h); top += (h - hh) / 2; h = hh; radius = Math.round(h / 3) + "px"; }
    bones.push(`<i style="left:${(r.left - R.left).toFixed(1)}px;top:${top.toFixed(1)}px;width:${r.width.toFixed(1)}px;height:${h.toFixed(1)}px;border-radius:${radius};--x:${(r.left - R.left).toFixed(1)}px;--y:${top.toFixed(1)}px"></i>`);
  };
  // visibility is not checked: the ghost itself hides what gets a block (see #s-task.ghost)
  const shown = el => { for(let e = el; e && e !== root; e = e.parentElement){ const cs = getComputedStyle(e); if(cs.display === "none" || +cs.opacity === 0) return false; } return true; };
  const SKIP = ".iacts,.idone,.acts,.tdone,#taskCode,.chev";   // swipe actions waiting off to the side, ids, arrows
  const isBlock = el => {
    if(/^(IMG|SVG|VIDEO|CANVAS|svg)$/.test(el.tagName) || el.id === "ring") return true;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if(r.width > W * BONE_BLOCK_MAX || r.height > 140) return false;
    const filled = cs.backgroundColor !== "rgba(0, 0, 0, 0)" && cs.backgroundColor !== "transparent";
    const bordered = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== "none" && !/rgba\(.*, 0\)$|transparent/.test(cs.borderTopColor);
    return filled || bordered || cs.backgroundImage !== "none";
  };
  const radiusOf = el => {
    const r = getComputedStyle(el).borderRadius;
    if(r && r !== "0px") return r;
    if(el.id === "ring" || el.closest("#ring")) return "50%";
    return el.tagName.toLowerCase() === "svg" ? "5px" : "6px";
  };
  const walk = el => {
    if(el === layer || el.matches?.(SKIP) || !shown(el)) return;
    if(el.tagName === "TEXTAREA"){   // the title: its lines, measured on a copy laid out the same way
      const cs = getComputedStyle(el), m = document.createElement("div");
      m.textContent = el.value || " ";
      for(const k of ["font","letterSpacing","lineHeight","padding","width","boxSizing","textWrap","wordBreak","overflowWrap"]) m.style[k] = cs[k];
      Object.assign(m.style, {position:"absolute", visibility:"hidden", left: (el.getBoundingClientRect().left - R.left) + "px", top: (el.getBoundingClientRect().top - R.top + st) + "px", whiteSpace: "pre-wrap"});
      root.appendChild(m);
      const rg = document.createRange(); rg.selectNodeContents(m);
      for(const r of rg.getClientRects()) add(r, 0, parseFloat(cs.fontSize));
      m.remove(); return;
    }
    if(!scopes.includes(el) && isBlock(el)){ add(el.getBoundingClientRect(), radiusOf(el)); el.setAttribute("data-boned", ""); return; }
    for(const c of el.childNodes){
      if(c.nodeType === 3){
        if(!c.textContent.trim()) continue;
        const rg = document.createRange(); rg.selectNodeContents(c);
        const fs = parseFloat(getComputedStyle(c.parentElement).fontSize);
        for(const r of rg.getClientRects()) add(r, 0, fs);
      } else if(c.nodeType === 1) walk(c);
    }
  };
  scopes.forEach(walk);
  layer.innerHTML = bones.join("");
  layer.style.height = root.scrollHeight + "px";
  layer.style.setProperty("--W", W + "px"); layer.style.setProperty("--H", root.scrollHeight + "px");
  root.appendChild(layer);
  return layer;
}
/* Lists while they load: sample rows drawn by the list's own renderer, then boned the same way */
let drawingGhost = false;
function ghostList(scopes, draw){
  const root = scopes[0].closest(".screen") || scopes[0];   // a list inside a popup bones itself
  scopes.forEach(el=>unghost(el, true));
  scopes.forEach(el=>el.classList.add("ghost"));
  drawingGhost = true; try{ draw(); } finally{ drawingGhost = false; }
  requestAnimationFrame(()=>{
    if(!scopes[0].classList.contains("ghost")) return;   // the real rows came first
    const layer = boneUp(root, scopes);
    scopes.forEach(el=>el._bones = layer);
  });
}
function unghost(el, now=false){
  if(drawingGhost || !el.classList.contains("ghost")) return;
  el.classList.remove("ghost");
  const l = el._bones; el._bones = null;
  if(!l || !l.isConnected) return;
  document.querySelectorAll(".ghost").forEach(x=>{ if(x._bones === l) x.classList.remove("ghost"), x._bones = null; });
  if(now){ l.remove(); return; }
  l.classList.add("out"); setTimeout(()=>l.remove(), 240);
  el.classList.add("revealing"); setTimeout(()=>el.classList.remove("revealing"), 260);
}
const ghostRows = (n, f) => Array.from({length: n}, (_, k)=>f(k));
function ghostProjects(){
  ghostList([$("#stats"), $("#projList")], ()=>{
    const keep = projects;
    projects = ghostRows(3, k=>({id: -1 - k, name: ["Сайт компании", "Интернет-магазин", "Лендинг"][k], members: 4, color: "#999", open: 2, items_done: 3, items_total: 9, today: 1}));
    try{ renderProjects(false); } finally{ projects = keep; }
  });
}
function ghostTasks(){
  const n = Math.min(Math.max((filter === "open" ? curProj?.open : 0) || 3, 1), 6);
  ghostList([$("#taskList")], ()=>{
    const keep = tasks;
    tasks = ghostRows(n, k=>({id: -1 - k, pid: curProj.id, title: GHOST_TEXTS[k % GHOST_TEXTS.length], author: "Автор", date: "1 ноя, 12:00",
      archived: filter === "archive", backlog: filter === "backlog", progress: filter === "progress",
      done: filter === "closed" ? 3 : 1, total: 3, thumb: null, due: null, rank: n - k, assignee: null}));
    try{ renderTasks(); } finally{ tasks = keep; }
    document.querySelectorAll("#filter .n").forEach(x=>x.textContent = "");   // the real counts come with the data
  });
}
function ghostPeople(){
  ghostList([$("#pplBar"), $("#pplFilter"), $("#pplList")], ()=>{
    const keep = ppl.data, who = ppl.who, pid = ppl.pid;
    const P = [{id: -2, name: "Анна"}, {id: -3, name: "Олег"}, {id: -4, name: "Мария"}];
    ppl.data = {me: -1, people: [{id: -1, name: "Вы", active: 1, total: 1}, ...P.map(p=>({...p, active: 1, total: 1}))],
      tasks: ghostRows(4, k=>({id: -10 - k, pid: k < 2 ? -1 : -2, project: k < 2 ? "Сайт компании" : "Интернет-магазин", color: "#999",
        title: GHOST_TEXTS[k], author: "Автор", date: "1 ноя", done: 1, total: 3, progress: k === 1, backlog: false, archived: false,
        assignee: ppl.mine ? {id: -1, name: "Вы"} : P[k % 3], due: null, rank: k}))};
    ppl.who = -1; ppl.pid = null;
    try{ renderPeople(); } finally{ ppl.data = keep; ppl.who = who; ppl.pid = pid; }
  });
}
function ghostOn(id){
  const sc = $("#s-task");
  sc.querySelector(".bones")?.remove();
  ghosting = true;
  curTask = ghostTask(id, taskHint(id));
  sc.classList.add("ghost");
  renderTask();
  histSeen = {task: null, top: null};
  requestAnimationFrame(()=>{ if(ghosting && curTask?.ghost) boneUp(sc).classList.add("block"); });
}
function ghostOff(){
  const sc = $("#s-task"), layer = sc.querySelector(".bones");
  ghosting = false;
  sc.classList.remove("ghost");
  if(!layer) return;
  layer.classList.add("out");
  sc.classList.add("revealing"); setTimeout(()=>sc.classList.remove("revealing"), 260);
  setTimeout(()=>layer.remove(), 240);
}

/* ---------- Screen 1: projects ---------- */
async function loadProjects(){
  const first = !loadProjects.done; loadProjects.done = true;
  if(first){
    ghostProjects();
  }
  if(LIVE){ try{ projects = await api("/api/projects"); }catch(e){ fail(e); } }
  else if(first) await sleep(DEMO_DELAY);
  renderProjects(first);
}
function renderProjects(anim=false){
  unghost($("#projList")); unghost($("#stats"));
  let open, iDone, iTotal, today;
  if(LIVE){
    open = projects.reduce((s,p)=>s+p.open,0); iDone = projects.reduce((s,p)=>s+p.items_done,0);
    iTotal = projects.reduce((s,p)=>s+p.items_total,0); today = projects.reduce((s,p)=>s+p.today,0);
  } else {
    const act = tasks.filter(t=>!t.archived), all = act.flatMap(t=>t.items);
    open = act.filter(t=>!isClosed(t) && !t.backlog).length; iDone = all.filter(i=>i.d).length; iTotal = all.length;
    today = tasks.filter(t=>t.date.startsWith("7 ноя")).length;
  }
  $("#stats").innerHTML = `
    <div><span class="mono">Открыто</span><b>${open}</b></div>
    <div><span class="mono">Пунктов</span><b>${iDone}<small>/${iTotal}</small></b></div>
    <div><span class="mono">Сегодня</span><b>+${today}</b></div>`;
  if(anim){ const b = $("#stats").querySelectorAll("b"); countUp(b[0], open); countUp(b[1], iDone); b[2].firstChild.nodeValue = "+0"; const t0=performance.now(); (function pl(now){ const k=Math.min(1,(now-t0)/600); b[2].firstChild.nodeValue = "+" + Math.round(today*(1-Math.pow(1-k,3))); if(k<1) requestAnimationFrame(pl); })(t0); }
  if(!projects.length){
    $("#projList").innerHTML = `<div class="empty">Пока нет проектов.<br>Добавьте бота в рабочий чат и сделайте его администратором — или создайте проект без чата.</div>`;
    return;
  }
  $("#projList").innerHTML = projects.map((p,idx)=>{
    const n = LIVE ? p.open : tasks.filter(t=>t.pid===p.id && !t.archived && !t.backlog && !isClosed(t)).length;
    const sub = p.standalone ? "без чата" : p.members ? `${p.members} ${p.members<5?"участника":"участников"} · групповой чат` : "групповой чат";
    return `<button class="row ${anim?"enter":""}" style="--i:${idx}" data-p="${p.id}">
      ${p.photo
        ? (SEEN.has(p.photo)
            ? `<div class="ava"><img class="lazy-img ok" src="${p.photo}" alt=""></div>`
            : `<div class="ava sk" data-color="${p.color}"><img class="lazy-img" src="${p.photo}" alt="" onload="avaLoaded(this)" onerror="avaFailed(this)"><span hidden>${esc(p.name.replace(/[«»"]/g,"")[0]||"?")}</span></div>`)
        : `<div class="ava" style="background:${p.color}"><span>${esc(p.name.replace(/[«»"]/g,"")[0]||"?")}</span></div>`}
      <div class="main"><div class="t">${esc(p.name)}</div><div class="s">${p.bot_admin === false ? `<span class="warn-s">Бот не видит переписку</span>` : sub}</div></div>
      <span class="pill ${n?"":"zero"}">${n} откр.</span>
      <svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M10 16L14 12L10 8"/></svg>
    </button>`;}).join("");
  settleImgs();
  document.querySelectorAll("#projList [data-p]").forEach(initProjRow);
}
/* long press on a project row lifts it (liftDrag); a tap still opens the project; a vertical move cancels the hold so the screen scrolls */
function initProjRow(row){
  let x0, y0, mode = null, moved = false, hold = null, drag = null;
  const stopHold = ()=>{ clearTimeout(hold); hold = null; };
  const open = ()=>{
    curProj = projects.find(p=>p.id == row.dataset.p); if(!curProj) return;
    filter = savedFilter(curProj.id); if(LIVE) tasks = []; ghostTasks(); go("s-tasks"); loadTasks(true);
  };
  row.onpointerdown = e=>{
    if(e.button && e.button !== 0) return;
    x0 = e.clientX; y0 = e.clientY; mode = null; moved = false; stopHold();
    hold = setTimeout(()=>{
      hold = null; mode = "drag"; moved = true;
      try{ row.setPointerCapture(e.pointerId); }catch(_){}
      const rows = [...$("#projList").querySelectorAll(".row[data-p]")];
      drag = liftDrag(row, y0, rows, (f, t)=>projDrop(rows, f, t));
    }, HOLD_MS);
  };
  row.onpointermove = e=>{
    if(x0 == null) return;
    if(mode === "drag"){ drag.move(e.clientY); return; }
    if(!mode && (Math.abs(e.clientX - x0) > 8 || Math.abs(e.clientY - y0) > 8)){ stopHold(); mode = "y"; }
  };
  row.onpointerup = row.onpointercancel = ()=>{
    stopHold(); x0 = null;
    if(mode === "drag"){ drag.end(); drag = null; mode = null; }
  };
  row.oncontextmenu = e=>e.preventDefault();
  row.onclick = e=>{
    if(moved){ e.preventDefault(); moved = false; return; }
    open();
  };
}
function projDrop(rows, from, to){
  const ids = rows.map(r=>+r.dataset.p);
  const [m] = ids.splice(from, 1); ids.splice(to, 0, m);
  const byId = new Map(projects.map(p=>[p.id, p]));
  projects = ids.map(id=>byId.get(id)).filter(Boolean);
  renderProjects();
  if(LIVE) api("/api/projects/order", "PUT", {project_ids: ids}).catch(e=>{ fail(e); loadProjects(); });
}

/* ---------- Screen 2: tasks ---------- */
async function loadTasks(first=false){
  if(LIVE){ try{ tasks = await api(`/api/projects/${curProj.id}/tasks`); }catch(e){ fail(e); } }
  else if(first) await sleep(DEMO_DELAY);
  renderTasks(first ? "enter" : "");
}
function renderTasks(anim=""){
  if(!curProj) return;   // a change made from «Мои/Все задачи» before any project was opened
  unghost($("#taskList"));
  const pt = tasks.filter(t=>t.pid===curProj.id);
  const groups = {open:[], progress:[], backlog:[], closed:[], archive:[]};
  pt.forEach(t=>groups[groupOf(t)].push(t));
  $("#projTitle").textContent = curProj.name;
  $("#adminWarn").hidden = curProj.bot_admin !== false;
  $("#renameProj").hidden = !curProj.standalone;
  $("#renameProjTx").textContent = curProj.owner ? "Переименовать или удалить" : "Переименовать проект";
  $("#tasksSub").textContent = `${pt.length} ${plural(pt.length,"задача","задачи","задач")}`;
  const btns = [...document.querySelectorAll("#filter button")];
  btns.forEach(b=>{ b.classList.toggle("on", b.dataset.f===filter); b.querySelector(".n").textContent = groups[b.dataset.f].length; });
  placeKnob(anim === "enter");
  let list = groups[filter];
  const lv = leaving && pt.find(t=>t.id === leaving.id);
  if(lv && !list.includes(lv)) list = [...list, lv].sort((a,b)=>rk(b) - rk(a));
  $("#taskList").innerHTML = list.length ? list.map((t,idx)=>{
    const d = done(t), n = total(t), pct = n? d/n*100 : 0, th = t.thumb || (t.shots||[])[0];
    return `<div class="swipe ${anim==="enter"?"enter":""}" style="--i:${idx}" data-w="${t.id}">${rowActs(t)}<button class="task" data-t="${t.id}">${rowDone(t)}
      <div class="thumb" style="${th?"":"background:var(--muted);display:grid;place-items:center;color:var(--hint)"}">${th? shotHTML(th) : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path fill-rule="evenodd" clip-rule="evenodd" d="M16.002 20.999H7.998C5.236 20.999 2.996 18.748 2.996 16.005V7.981C2.996 5.238 5.236 2.987 7.998 2.987H16.002C18.764 2.987 21.004 5.238 21.004 7.981V16.005C21.004 18.748 18.764 20.999 16.002 20.999Z"/><path d="M14.268 11.011L11.434 13.835L9.733 12.116"/></svg>`}</div>
      <div class="main">
        <div class="t">${esc(t.title)}</div>
        ${t.due || t.assignee || n || qOf(t) ? `<div class="meta">${t.due ? dueBadge(t) : ""}${t.assignee ? whoBadge(t.assignee) : ""}${qBadge(t)}${itemsBadge(d, n)}</div>` : ""}
      </div></button></div>`;}).join("")
    : `<div class="empty">${filter==="archive"?"Архив пуст":filter==="closed"?"Пока ничего не закрыто":filter==="backlog"?"В бэклоге пусто.<br>Сдвиньте задачу влево → «Статус» → «В бэклоге», чтобы отложить её на потом.":filter==="progress"?"Сейчас ничего не в работе.<br>Сдвиньте задачу влево → «Статус» → «В работе».":"Открытых задач нет"}</div>`;
  // «Создать задачу» — only on tabs a new task can land in (it can't be born closed or archived)
  if(STATUSES[filter]) $("#taskList").insertAdjacentHTML("beforeend", `<button class="add-row" id="newTaskBtn"><span class="plus"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8.004V15.987"/><path d="M16 12.016H8"/></svg></span><span class="add-l">Создать задачу</span></button>`);
  const nb = $("#newTaskBtn"); if(nb) nb.onclick = ()=>newTask();
  settleImgs();
  document.querySelectorAll("#taskList .swipe").forEach(initSwipe); markSel();
  if(lv){ const {id, to} = leaving; leaving = null; exitRow(id, to); }
}

/* The gestures of a task row — the same in every list: left — Статус · Сообщение · Переименовать · Удалить,
   right — «Закрыть» (ticks every item), long press — drag */
const rowActs = t => `<div class="acts r">${t.archived ? "" : `<button class="act-st" data-act="st" aria-label="Статус" style="background:${PICK[pickOf(t)].color}">${stIcon(pickOf(t),20)}</button>`}<button class="act-src" data-act="src" aria-label="Исходное сообщение"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11.5C20 15.6 16.4 19 12 19C10.9 19 9.8 18.8 8.8 18.4L4.5 19.5L5.6 15.9C4.6 14.6 4 13.1 4 11.5C4 7.4 7.6 4 12 4C16.4 4 20 7.4 20 11.5Z"/><path d="M8.5 10H15.5"/><path d="M8.5 13.5H13"/></svg></button><button class="act-ren" data-act="ren" aria-label="Переименовать"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M17.539 10.12L13.88 6.46"/><path fill-rule="evenodd" clip-rule="evenodd" d="M6.251 21.001H3V17.75C3 17.484 3.105 17.23 3.293 17.042L16.627 3.709C17.018 3.316 17.649 3.316 18.042 3.709L20.294 5.956C20.683 6.349 20.683 6.984 20.294 7.373L6.958 20.706C6.771 20.894 6.516 21.001 6.251 21.001V21.001Z"/></svg></button><button class="act-del" data-act="del" aria-label="Удалить"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 7.012H19"/><path d="M18.001 7.012V17.983C18.001 19.089 17.105 19.989 16 19.989H8C6.895 19.989 6 19.089 6 17.983V7.012"/><path d="M15 3.737H9"/><path d="M10 10.983V16.018"/><path d="M14 10.983V16.018"/></svg></button></div>`;
const canClose = t => !t.archived && !isClosed(t);
const rowDone = t => canClose(t) ? `<span class="tdone"><span class="in"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5L10 17.5L19 7.5"/></svg><span>Закрыть</span></span></span>` : "";
/* ---------- Swipe a task row → Переименовать / Удалить ---------- */
const OPEN_W = 184;
const IC_OK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5L10 17.5L19 7.5"/></svg>', IC_SORT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5V19"/><path d="M5 8L8 5L11 8"/><path d="M16 19V5"/><path d="M13 16L16 19L19 16"/></svg>';
const openW = w => w.querySelectorAll(".acts button").length * 64;   // 3 or 4 buttons
let openRow = null;
/* Past the ends of a swipe the row still follows the finger, ever more slowly (rubber band), and springs back */
const band = o => 80 * (1 - Math.exp(-o / 140));   // up to 80px past the end, giving way less and less
/* where a released row goes: an open one closes on any real pull back (20px or a flick to the right),
   a closed one opens past `openAt` or on a flick to the left */
function settleRow(x, base, v, openAt, W){
  if(base < 0) return x - base > 20 || v > .3 ? 0 : -W;
  return x < -openAt || v < -.3 ? -W : 0;
}
const trackV = (st, e)=>{ const dt = Math.max(1, e.timeStamp - (st.lt || e.timeStamp - 16)); st.v = (e.clientX - (st.lx ?? e.clientX)) / dt; st.lx = e.clientX; st.lt = e.timeStamp; };
const rubber = (x, min, max) => x > max ? max + band(x - max) : x < min ? min - band(min - x) : x;
function setRow(w, x){
  (w.matches(".item, .descr, .src-msg, .nrow") ? w : w.querySelector(".task")).style.transform = x ? `translateX(${x}px)` : "";
  w.classList.toggle("side-l", x > 0); w.classList.toggle("side-r", x < 0);
  w._x = x;
}
function closeRow(){ if(openRow){ setRow(openRow, 0); openRow = null; updBack(); } }
const CLOSE_AT = 96;
const inPeople = w => !!w.closest("#pplList");
const taskOf = (w, id) => (inPeople(w) ? ppl.data?.tasks : tasks)?.find(x=>x.id === id);
function initSwipe(w){
  const row = w.querySelector(".task"), id = +w.dataset.w;
  let x0, y0, base, mode = null, moved = false, st = {}, hold = null, drag = null;
  const stopHold = ()=>{ clearTimeout(hold); hold = null; };
  row.onpointerdown = e=>{
    x0 = e.clientX; y0 = e.clientY; base = w._x || 0; mode = null; moved = false; st = {};
    stopHold();
    if(openRow) return;
    hold = setTimeout(()=>{   // held still: the card lifts and follows the finger
      hold = null; mode = "drag"; moved = true;
      try{ row.setPointerCapture(e.pointerId); }catch(_){}
      if(inPeople(w) && ppl.mine){   // «Мои задачи»: anywhere in the list — your own order, across projects
        const rows = [...w.parentElement.querySelectorAll(".swipe")];
        drag = liftDrag(w, y0, rows, (f, t)=>myDrop(rows, f, t));
      }
      else if(inPeople(w)){   // it moves among the tasks of its own project
        const pid = row.dataset.pid, rows = [...w.parentElement.querySelectorAll(".swipe")].filter(r=>r.querySelector(".task").dataset.pid === pid);
        drag = liftDrag(w, y0, rows, (f, t)=>pplDrop(rows, f, t));
      }
      else drag = taskDrag(w, y0);
    }, HOLD_MS);
  };
  row.onpointermove = e=>{
    if(x0 == null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if(mode === "drag"){ drag.move(e.clientY); return; }
    if(!mode){
      if(Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)){ stopHold(); mode = "x"; try{ row.setPointerCapture(e.pointerId); }catch(_){} w.classList.add("dragging"); if(openRow && openRow!==w) closeRow(); }
      else if(Math.abs(dy) > 8){ stopHold(); mode = "y"; }
    }
    if(mode === "x"){   // left: the actions; right (from rest, if it can be closed): «Закрыть»; stretchy past the ends
      moved = true; trackV(st, e);
      const right = base === 0 && w.querySelector(".tdone") ? CLOSE_AT : 0, x = rubber(base + dx, -openW(w), right);
      setRow(w, x);
      const armed = right > 0 && x >= CLOSE_AT;
      if(armed !== w.classList.contains("armed")){ w.classList.toggle("armed", armed); if(armed) haptic(); }
    }
  };
  row.onpointerup = row.onpointercancel = ()=>{
    stopHold(); x0 = null; w.classList.remove("dragging");
    if(mode === "drag"){ drag.end(); drag = null; mode = null; return; }
    if(mode === "x"){
      if(w.classList.contains("armed")){ w.classList.remove("armed"); setRow(w, 0); const t = taskOf(w, id); if(t) setTimeout(()=>closeTask(t), 120); return; }
      if((w._x || 0) > 0){ setRow(w, 0); return; }
      const to = settleRow(w._x || 0, base, st.v || 0, 60, openW(w));
      if(to){ setRow(w, to); if(openRow !== w) haptic(); openRow = w; updBack(); } else { setRow(w, 0); if(openRow===w){ openRow = null; updBack(); } }
    }
  };
  row.oncontextmenu = e=>e.preventDefault();   // a long press is ours, not the browser's menu
  row.onclick = e=>{
    if(inPeople(w)) e.stopPropagation();   // the list's own click handler would open it a second time
    if(moved){ e.preventDefault(); moved = false; return; }
    if(openRow){ closeRow(); return; }
    if(inPeople(w)){ const t = taskOf(w, id); curProj = projects.find(p=>p.id == row.dataset.pid) || {id: +row.dataset.pid, name: t?.project || PERSONAL_NAME, personal: true}; }
    openTask(id);
  };
  w.querySelectorAll("[data-act]").forEach(b=>b.onclick = e=>{
    e.stopPropagation();
    const t = taskOf(w, id); closeRow(); if(!t) return;
    if(b.dataset.act === "src") showSource(t); else if(b.dataset.act === "ren") renameTask(t); else if(b.dataset.act === "st") pickStatus(t); else deleteTask(t);
  });
}
document.addEventListener("pointerdown", e=>{ if(openRow && !openRow.contains(e.target)) closeRow(); });
/* the page must not scroll under a lifted card (iOS starts panning otherwise) */
let lifted = false;
document.addEventListener("touchmove", e=>{ if(lifted) e.preventDefault(); }, {passive: false});

/* Long press lifts a row (a task card, a checklist item) and it follows the finger; the others make room.
   Near the top or bottom edge the screen scrolls along. onDrop(from, to) gets the new place. */
const HOLD_MS = 380;
function liftDrag(w, y0, rows, onDrop){
  const sc = w.closest(".screen");
  const from = rows.indexOf(w), H = w.offsetHeight, s0 = sc.scrollTop; let to = from, lastY = y0, raf = null;
  const mids = rows.map(r=>r.offsetTop + r.offsetHeight/2), mid0 = w.offsetTop + H/2;   // rows differ in height
  lifted = true; closeRow();
  w.classList.add("lift"); haptic("medium");
  w.style.transition = "transform .18s cubic-bezier(.3,1.4,.5,1)"; w.style.transform = "scale(1.03)";
  const place = ()=>{
    const dy = lastY - y0 + (sc.scrollTop - s0);
    w.style.transform = `translateY(${dy}px) scale(1.03)`;
    const c = mid0 + dy;
    let nt = from;
    while(nt < rows.length-1 && c > mids[nt+1]) nt++;
    while(nt > 0 && c < mids[nt-1]) nt--;
    if(nt !== to){ to = nt; haptic(); }
    rows.forEach((r,k)=>{ if(r===w) return; let sh=0; if(from<to && k>from && k<=to) sh=-H; if(from>to && k<from && k>=to) sh=H; r.style.transition="transform .2s"; r.style.transform=`translateY(${sh}px)`; });
  };
  const edge = ()=>{   // keep scrolling while the finger rests near an edge
    const b = sc.getBoundingClientRect(), z = 70;
    const v = lastY < b.top + z ? -(b.top + z - lastY) / 6 : lastY > b.bottom - z ? (lastY - b.bottom + z) / 6 : 0;
    if(v){ const before = sc.scrollTop; sc.scrollTop += v; if(sc.scrollTop !== before) place(); }
    raf = v ? requestAnimationFrame(edge) : null;
  };
  return {
    move(y){ lastY = y; w.style.transition = "none"; place(); if(!raf) edge(); },
    end(){
      lifted = false; if(raf) cancelAnimationFrame(raf);
      rows.forEach(r=>{ r.style.transform=""; r.style.transition=""; }); w.classList.remove("lift");
      if(to !== from) onDrop(from, to);
    },
  };
}
/* a task within the open tab: the tasks swap the places they hold */
function taskDrag(w, y0){
  const rows = [...document.querySelectorAll("#taskList .swipe")];
  return liftDrag(w, y0, rows, (from, to)=>{
    const group = rows.map(r=>tasks.find(t=>t.id === +r.dataset.w)).filter(Boolean);
    const ranks = group.map(rk).sort((a,b)=>b-a);
    const [m] = group.splice(from, 1); group.splice(to, 0, m);
    group.forEach((t,i)=>t.rank = ranks[i]);
    tasks.sort((a,b)=>rk(b) - rk(a) || b.id - a.id);
    renderTasks();
    if(LIVE) api(`/api/projects/${curProj.id}/order`, "PUT", {task_ids: group.map(t=>t.id)}).catch(e=>{ fail(e); loadTasks(); });
  });
}
/* a checklist item */
function itemDrag(row, y0){
  return liftDrag(row, y0, [...document.querySelectorAll("#items .item")], (from, to)=>{
    const t = curTask, prev = [...t.items];
    const [m] = t.items.splice(from,1); t.items.splice(to,0,m); renderItems();
    save(()=>api(`/api/tasks/${t.id}/order`,"PUT",{item_ids:t.items.map(i=>i.id)}), true, t.id, ()=>{
      if(curTask?.id !== t.id) return;
      t.items = prev.filter(x=>t.items.includes(x)); renderItems();
      save(()=>api(`/api/tasks/${t.id}/order`,"PUT",{item_ids:t.items.map(i=>i.id)}), true, t.id);
    });
  });
}

/* Comments on the task, or on one item of it: who wrote it and when; you can delete your own */
const itemComments = (t, it) => (t.comments || []).filter(c=>c.item_id != null && c.item_id === it.id);
let commItem = null;   // the item whose comments are open; null — the whole task (every comment, item ones marked)
/* a link in a comment opens in a window of its own (Telegram: the browser), never inside the app */
document.addEventListener("click", e=>{
  const a = e.target.closest?.(".src-msg.comm a.ln, #s-comments a.ln, #descr a.ln"); if(!a) return;
  e.preventDefault(); e.stopPropagation();
  const url = a.dataset.url.replace(/&amp;/g,"&");
  if(tg) tg.openLink(url); else window.open(url, "_blank", "noopener");
}, true);
/* one comment or question: avatar, who · when, «Вопрос» on questions; an open question has «Вопрос решён» under it */
const isOpenQ = c => c.question && !c.resolved;
const talkOrder = list => [...list.filter(isOpenQ), ...list.filter(c=>!isOpenQ(c))];   // open questions first, the rest in order
function commHtml(c, t, withItem, mark){   // mark: «Вопрос» / «Решён» on questions (where they mix with comments)
  const my = LIVE ? me?.id : 1;
  const it = withItem && c.item_id != null ? t.items.find(x=>x.id === c.item_id) : null;
  const qb = mark && c.question ? (c.resolved ? badge("ask", "Решён", {none: true}) : badge("ask", "Вопрос")) : "";
  const mine = c.user_id === my && !t.ghost;
  const card = `<div class="src-msg comm${isOpenQ(c) ? " q" : ""}" data-cid="${c.id}">${avSmall({name: c.author, photo: c.photo})}<div class="body"><div class="who"><b>${esc(c.author || "")}</b><span>${esc(c.date || "")}${c.edited ? " · изменено" : ""}</span>${qb}`
     + `</div>${c.text ? `<div class="tx">${linkify(c.text)}</div>` : ""}${cMedia(c)}`
    + (it ? `<div class="about">к пункту «${esc(it.t)}»</div>` : "")

    + (c.question && c.resolved && c.resolved_by ? `<div class="qby">Решён · ${esc(c.resolved_by)}</div>` : "") + `</div>`
    + (c.question && !t.ghost ? `<div class="qdone" aria-hidden="true">${c.resolved ? IC_UNDO : IC_DO}<span>${c.resolved ? "Открыть" : "Решён"}</span></div>` : "")
    + (mine ? `<div class="iacts"><button class="act-ren" data-e="${c.id}" aria-label="Изменить">${IC_EDIT}</button><button class="act-del" data-c="${c.id}" aria-label="Удалить">${IC_DEL}</button></div>` : "") + `</div>`;
  return mine || (c.question && !t.ghost) ? `<div class="shut">${card}</div>` : card;
}
/* a message's screenshots (open full-screen, swipe between them) and videos (play right there) */
const cMedia = c => (c.files || []).length ? `<div class="c-media">${c.files.map(f=>f.kind === "video"
  ? `<button class="c-vid" type="button" aria-label="Смотреть видео"><video src="${esc(f.url)}#t=0.1" muted playsinline preload="metadata"></video><span class="vi">${IC_PLAY}</span></button>`
  : `<a data-lightbox="c${c.id}" href="${esc(f.url)}"><img src="${esc(f.url)}" alt="" loading="lazy" decoding="async"></a>`).join("")}</div>` : "";
/* a video tile opens full screen with sound (iOS: its own player) */
document.addEventListener("click", e=>{
  const b = e.target.closest?.(".c-media .c-vid"); if(!b) return;
  const v = b.querySelector("video"); v.muted = false; v.controls = true;
  const off = ()=>{ v.pause(); v.controls = false; v.muted = true; };
  v.addEventListener("webkitendfullscreen", off, {once: true});
  document.addEventListener("fullscreenchange", function f(){ if(!document.fullscreenElement){ off(); document.removeEventListener("fullscreenchange", f); } });
  if(v.webkitEnterFullscreen) v.webkitEnterFullscreen(); else v.requestFullscreen?.().catch(()=>{});
  v.play().catch(()=>{});
});
function bindComm(box){
  box.querySelectorAll("[data-c]").forEach(b=>b.onclick = ()=>{ closeRow(); delComment(+b.dataset.c); });
  box.querySelectorAll("[data-e]").forEach(b=>b.onclick = ()=>{ closeRow(); editComment(+b.dataset.e); });
  box.querySelectorAll(".shut > .src-msg").forEach(m=>{
    const c = (curTask.comments || []).find(x=>x.id === +m.dataset.cid);
    initShutter(m, m.querySelector(".iacts") ? SHUT_W : 0, null,
      c?.question ? {at: 64, fire: ()=>resolveQ(c.id, !c.resolved)} : null);   // swipe right: answered (or open again)
  });
}
/* the pop-up keeps only one item's comments (from the item's swipe or its counter) */
function renderComments(){
  const t = curTask, box = $("#commList"), it = commItem;
  box.innerHTML = talkOrder(it ? itemComments(t, it) : (t.comments || [])).map(c=>commHtml(c, t, !it, true)).join("");
  bindComm(box);
  box.scrollTop = box.scrollHeight;
}
/* under the task: tabs «Вопросы» · «Комментарии» · «История»; the field writes a question or a comment by the tab */
let talkTab = "c", talkFor = null;   // the open tab; the task the field was set up for (a redraw while typing keeps the text)
const talkDefault = t => openQs(t) ? "q" : "c";
function renderTalk(){
  const t = curTask, list = t.comments || [];
  if(t.ghost) talkFor = null;   // the loading silhouette: the real task picks the tab when it comes
  else if(talkFor !== t.id){ talkTab = talkDefault(t); talkSetup(); }
  const qs = list.filter(c=>c.question), cs = list.filter(c=>!c.question), oq = openQs(t);
  const seg = $("#talkTabs");
  seg.querySelector('[data-k="q"] .n').textContent = oq || "";
  seg.querySelector('[data-k="c"] .n').textContent = cs.length || "";
  seg.querySelectorAll("button").forEach(b=>{ b.classList.toggle("on", b.dataset.k === talkTab); b.onclick = ()=>{ if(talkTab === b.dataset.k) return; haptic(); talkTab = b.dataset.k; seg.querySelector(".knob").classList.remove("instant"); renderTalk(); }; });
  requestAnimationFrame(()=>{ const b = seg.querySelector(".on"), k = seg.querySelector(".knob"); if(b && b.offsetWidth){ k.style.width = b.offsetWidth + "px"; k.style.transform = `translateX(${b.offsetLeft}px)`; } });
  $("#hist").hidden = talkTab !== "h"; $("#talk").hidden = talkTab === "h";
  if(talkTab === "h") return;
  const q = talkTab === "q", shown = q ? talkOrder(qs) : cs;
  $("#talkList").innerHTML = shown.length ? shown.map(c=>commHtml(c, t, true)).join("")
      + (q && openQs(t) && !t.ghost ? `<div class="talk-hint">Свайп вправо — вопрос решён · свой вопрос влево — изменить или удалить</div>` : "")
    : "";   // nothing yet: just the field, no «пока нет»
  bindComm($("#talkList"));
  $("#talkBox").hidden = !!t.ghost;
  const ti = $("#talkInp"); if(!ti.value) ti.style.height = "";
  $("#talkBox").classList.toggle("ask", q);
  $("#talkInp").placeholder = q ? "Вопрос" : "Комментарий";
  $("#talkSend").setAttribute("aria-label", q ? "Спросить" : "Отправить");
}
/* a field for a comment. In the pop-up of an item the «Вопрос» switch makes it a question; under the task the tab does */
/* one screenshot or video to the server before the comment: in parts of 8 MB (the server takes up to ~16 MB a request) */
const PART = 8 * 1024 * 1024, VIDEO_MAX = 100 * 1024 * 1024;
async function uploadCFile(taskId, blob, onProg){
  const up = [...crypto.getRandomValues(new Uint8Array(12))].map(b=>b.toString(16).padStart(2, "0")).join("");
  const n = Math.max(1, Math.ceil(blob.size / PART));
  let res;
  for(let i = 0; i < n; i++){
    const r = await fetch(`/api/tasks/${taskId}/comment_files?up=${up}&part=${i}${i === n - 1 ? "&last=1" : ""}`,
      {method: "POST", headers: {"Authorization": authHeader(), "Content-Type": "application/octet-stream"}, body: blob.slice(i * PART, (i + 1) * PART)});
    if(!r.ok) throw new Error(r.status === 415 ? "Такой файл не подходит: нужны картинка или видео MP4, MOV, WebM" : r.status + " " + await r.text());
    res = await r.json(); onProg((i + 1) / n);
  }
  return res;
}
function composer({inp, ask, btn, box, key, item, isQ, clip, att}){
  let q = false, files = [];   // {id, kind, local, name, p (0…1), err}
  const busy = ()=>files.some(f=>!f.name && !f.err);
  const drawAtt = ()=>{
    att.innerHTML = files.map(f=>`<div class="att${f.err ? " err" : ""}" data-f="${f.id}">${f.kind === "video" ? `<video src="${f.local}#t=0.1" muted playsinline preload="metadata"></video><span class="vi">${IC_PLAY}</span>` : `<img src="${f.local}" alt="">`}`
      + (f.err ? `<span class="p">Ошибка</span>` : !f.name ? `<span class="p">${Math.round(f.p * 100)}%</span>` : "")
      + `<button class="x" type="button" aria-label="Убрать"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M7 7L17 17"/><path d="M17 7L7 17"/></svg></button></div>`).join("");
    att.querySelectorAll(".x").forEach(b=>b.onclick = ()=>{ const id = b.parentElement.dataset.f; files = files.filter(f=>f.id !== id); drawAtt(); });
    btn.disabled = busy();
  };
  const add = async picked=>{
    const t = curTask;
    for(const file of picked){
      const video = file.type.startsWith("video/");
      if(!video && !file.type.startsWith("image/")) continue;
      if(video && file.size > VIDEO_MAX){ toast("Видео больше 100 МБ — обрежьте его или пришлите ссылкой", "warn"); continue; }
      const f = {id: String(Math.random()), kind: video ? "video" : "photo", local: URL.createObjectURL(file), p: 0};
      files.push(f); drawAtt();
      try{
        const blob = video ? file : await shrink(file);
        if(LIVE){ const r = await uploadCFile(t.id, blob, p=>{ f.p = p; const el = att.querySelector(`[data-f="${f.id}"] .p`); if(el) el.textContent = Math.round(p * 100) + "%"; }); f.name = r.name; f.url = r.url; }
        else { f.name = "demo"; f.url = f.local; }
      }catch(e){ f.err = true; fail(e); }
      drawAtt();
    }
  };
  att.innerHTML = "";
  clip.onclick = ()=>{ haptic(); const fi = $("#attInp"); fi.value = ""; fi.onchange = ()=>add([...fi.files]); fi.click(); };
  // measured only while it is on screen: hidden (loading, another tab) it would measure 0 and stay invisible
  const fit = ()=>{ if(!inp.offsetParent) return; inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight + 2, 160) + "px"; };
  inp.onfocus = fit;
  const setQ = v=>{ q = v; ask.classList.toggle("on", q); ask.setAttribute("aria-pressed", String(q)); box?.classList.toggle("ask", q);
    inp.placeholder = q ? "Задать вопрос" : "Написать комментарий"; btn.textContent = q ? "Спросить" : "Отправить"; };
  if(ask){ ask.onclick = ()=>{ haptic(); setQ(!q); inp.focus(); }; setQ(false); }
  inp.value = cloud.get(key) || "";
  const draft = keepDraft(key, ()=>inp.value.trim() ? inp.value : null);
  inp.oninput = ()=>{ fit(); draft(); }; requestAnimationFrame(fit);
  btn.onclick = async ()=>{
    const t = curTask, text = inp.value.trim(), ready = files.filter(f=>f.name);
    if(busy()) return;
    if(!text && !ready.length){ inp.focus(); return; }
    const asks = isQ ? isQ() : q;
    const body = {text, ...(asks ? {question: true} : {}), ...(item ? {item_id: item.id} : {}), ...(ready.length ? {files: ready.map(f=>f.name)} : {})};
    btn.disabled = true;
    try{
      if(LIVE){ const full = fromApi(await api(`/api/tasks/${t.id}/comments`, "POST", body)); t.comments = full.comments; t.history = full.history; }
      else (t.comments = t.comments || []).push({id: Date.now(), author: "Вы", user_id: 1, item_id: item ? item.id : null, question: asks, resolved: false, text, ts: Date.now()/1000, date: "сейчас", files: ready.map(f=>({url: f.url, kind: f.kind}))});
    }catch(e){ fail(e); return; }
    finally{ btn.disabled = false; }
    inp.value = ""; cloud.set(key, null); fit(); haptic("medium");
    files = []; drawAtt();
    if(ask) setQ(false);
    afterComm(t);
  };
}
function talkSetup(){
  const t = curTask; talkFor = t.id;
  composer({inp: $("#talkInp"), btn: $("#talkSend"), key: "dc_" + t.id, item: null, isQ: ()=>talkTab === "q", clip: $("#talkClip"), att: $("#talkAtt")});
}
/* everything that shows comments: the section, the tag, the item counters, the rows' question badge */
function afterComm(t){
  const n = openQs(t);
  everyCopy(t.id, x=>{ x.questions = n; });
  if(curTask?.id === t.id){ renderTalk(); renderMeta(); renderHistory(); renderItems(); }
  if($("#commSheet").classList.contains("open")) renderComments();
}
/* the tag, the comments feed and search lead down to the section (and light up one comment) */
function toTalk(cid){
  const c = cid && (curTask.comments || []).find(x=>x.id === cid);
  talkTab = c ? (c.question ? "q" : "c") : openQs(curTask) ? "q" : "c"; renderTalk();
  const el = (cid && $(`#talkList [data-cid="${cid}"]`)) || $("#talkTabs");
  el.scrollIntoView({block: cid ? "center" : "start", behavior: "smooth"});
  if(cid && el.classList.contains("src-msg")){ el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
}
function openComments(item = null, cid){
  if(!item){ toTalk(cid); return; }
  const sh = $("#commSheet"), inp = $("#commInp");
  commItem = item;
  $("#commTitle").textContent = "Комментарии к пункту";
  $("#commAbout").hidden = false; $("#commAbout").textContent = `«${item.t}»`;
  renderComments();
  composer({inp, ask: $("#commAsk"), btn: $("#commSend"), box: null, key: "dc_" + curTask.id + "_" + item.id, item, clip: $("#commClip"), att: $("#commAtt")});
  sh.classList.add("open"); haptic();
  const close = ()=>{ inp.blur(); sh.classList.remove("open"); };
  $("#commClose").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
}
/* «Вопрос решён» — anyone who sees the task; «Отменить» opens it again */
async function resolveQ(id, on, quiet){
  const t = curTask, c = (t.comments || []).find(x=>x.id === id); if(!c) return;
  try{
    if(LIVE){ const full = fromApi(await api(`/api/comments/${id}/resolve`, "POST", {resolved: on})); t.comments = full.comments; t.history = full.history; }
    else { c.resolved = on; c.resolved_by = on ? "Вы" : null; }
  }catch(e){ fail(e); return; }
  haptic(on ? "medium" : undefined); afterComm(t);
  if(!quiet) toast(on ? "Вопрос решён" : "Вопрос снова открыт", null, on ? {undo: ()=>resolveQ(id, false, true)} : undefined);
}
/* «Изменить» from the shutter: the text only (pictures and videos stay); «изменено» next to the date */
function editComment(id){
  const t = curTask, c = (t.comments || []).find(x=>x.id === id); if(!c) return;
  const sh = $("#commEditSheet"), inp = $("#commEditInp");
  $("#commEditTitle").textContent = c.question ? "Вопрос" : "Комментарий";
  inp.value = c.text || "";
  const fit = ()=>{ inp.style.height = "auto"; inp.style.height = inp.scrollHeight + 2 + "px"; };
  inp.oninput = fit;
  const close = ()=>{ inp.blur(); sh.classList.remove("open"); };
  $("#commEditCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#commEditSave").onclick = async ()=>{
    const v = inp.value.trim();
    if(v === (c.text || "")){ close(); return; }
    if(!v && !(c.files || []).length){ inp.focus(); return; }
    try{
      if(LIVE){ const full = fromApi(await api(`/api/comments/${id}`, "PATCH", {text: v})); t.comments = full.comments; }
      else { c.text = v; c.edited = true; }
    }catch(e){ fail(e); return; }
    close(); haptic("medium"); afterComm(t); toast(c.question ? "Вопрос изменён" : "Комментарий изменён");
  };
  sh.classList.add("open"); haptic(); requestAnimationFrame(fit);
  focusField(inp);
}
async function delComment(id){
  const t = curTask, c = (t.comments || []).find(x=>x.id === id); if(!c) return;
  try{ if(LIVE){ const full = fromApi(await api(`/api/comments/${id}`, "DELETE")); t.comments = full.comments; } else t.comments = t.comments.filter(x=>x.id !== id); }
  catch(e){ fail(e); return; }
  haptic(); afterComm(t);
  toast(c.question ? "Вопрос удалён" : "Комментарий удалён", null, {undo: async ()=>{   // back as a new one with the same text
    const names = (c.files || []).map(f=>(f.url.match(/\/api\/cfile\/\d+\/([^?]+)/) || [])[1]).filter(Boolean);
    const body = {text: c.text, ...(c.question ? {question: true} : {}), ...(c.item_id != null ? {item_id: c.item_id} : {}), ...(names.length ? {files: names} : {})};
    try{
      if(LIVE){ const full = fromApi(await api(`/api/tasks/${t.id}/comments`, "POST", body)); t.comments = full.comments; }
      else t.comments.push(c);
    }catch(e){ fail(e); return; }
    afterComm(t);
  }});
}
/* The description: free text over the items; links in it open in a window of their own; a tap on the text — edit */
function renderDescr(){
  const t = curTask, d = (t.descr || "").trim(), box = $("#descr");
  $("#descrLbl").hidden = $("#descrBox").hidden = !d;
  $("#addDescr").hidden = !!d || !!t.archived;
  if(openRow === box) closeRow();
  if(!d){ box.innerHTML = ""; return; }
  box.classList.add("clamp");
  box.innerHTML = `<div class="tx">${linkify(d)}</div><button class="more" type="button" hidden>Показать полностью</button>`
    + `<div class="iacts"><button class="act-ren" data-a="edit" aria-label="Изменить">${IC_EDIT}</button><button class="act-del" data-a="del" aria-label="Удалить">${IC_DEL}</button></div>`;
  const tx = box.querySelector(".tx"), more = box.querySelector(".more");
  requestAnimationFrame(()=>{ more.hidden = tx.scrollHeight <= tx.clientHeight + 1; });
  more.onclick = e=>{ e.stopPropagation(); box.classList.remove("clamp"); more.hidden = true; };
  box.querySelector('[data-a="edit"]').onclick = ()=>{ closeRow(); openDescr(); };
  box.querySelector('[data-a="del"]').onclick = ()=>{ closeRow(); saveDescr(curTask, ""); };
  if(!t.ghost) initDescrSwipe(box);
}
const IC_EDIT = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M17.539 10.12L13.88 6.46"/><path fill-rule="evenodd" clip-rule="evenodd" d="M6.251 21.001H3V17.75C3 17.484 3.105 17.23 3.293 17.042L16.627 3.709C17.018 3.316 17.649 3.316 18.042 3.709L20.294 5.956C20.683 6.349 20.683 6.984 20.294 7.373L6.958 20.706C6.771 20.894 6.516 21.001 6.251 21.001V21.001Z"/></svg>', IC_DEL = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 7.012H19"/><path d="M18.001 7.012V17.983C18.001 19.089 17.105 19.989 16 19.989H8C6.895 19.989 6 19.089 6 17.983V7.012"/><path d="M15 3.737H9"/><path d="M10 10.983V16.018"/><path d="M14 10.983V16.018"/></svg>';
/* The shutter — the one way to change or remove a thing: swipe it left, «Изменить» · «Удалить» come out (like an item).
   onTap: what a plain tap does (the description edits); links and buttons inside keep working. */
const DESCR_W = 112, SHUT_W = 112;
const IC_DO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5L10 17.5L19 7.5"/></svg>',
      IC_UNDO = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14L4 9L9 4"/><path d="M4 9H14.5C17.5 9 20 11.5 20 14.5C20 17.5 17.5 20 14.5 20H8"/></svg>';
function initShutter(row, W, onTap, right){   // right: {at, fire} — pulled right past `at` and let go: fire()
  let x0 = null, y0, base, mode = null, moved = false, st = {};
  row.onpointerdown = e=>{
    if(e.target.closest(".iacts")) return;
    x0 = e.clientX; y0 = e.clientY; base = row._x || 0; mode = null; moved = false; st = {};
  };
  row.onpointermove = e=>{
    if(x0 == null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if(!mode){
      if(Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)){ mode = "x"; try{ row.setPointerCapture(e.pointerId); }catch(_){} row.classList.add("swiping"); if(openRow && openRow !== row) closeRow(); }
      else if(Math.abs(dy) > 8) mode = "y";
    }
    if(mode === "x"){
      moved = true; trackV(st, e);
      const x = rubber(base + dx, -W, right && base === 0 ? right.at : 0);
      setRow(row, x);
      const armed = !!right && base === 0 && x >= right.at;
      if(armed !== row.classList.contains("armed")){ row.classList.toggle("armed", armed); if(armed) haptic(); }
    }
  };
  row.onpointerup = row.onpointercancel = ()=>{
    if(x0 == null) return;
    x0 = null; row.classList.remove("swiping");
    if(mode !== "x") return;
    if(row.classList.contains("armed")){ row.classList.remove("armed"); setRow(row, 0); setTimeout(right.fire, 120); return; }
    if((row._x || 0) > 0 || !W){ setRow(row, 0); return; }
    const to = settleRow(row._x || 0, base, st.v || 0, 40, W);
    if(to){ setRow(row, to); if(openRow !== row) haptic(); openRow = row; updBack(); } else { setRow(row, 0); if(openRow === row){ openRow = null; updBack(); } }
  };
  row.addEventListener("click", e=>{
    if(e.target.closest(".iacts")) return;
    if(moved){ moved = false; e.stopPropagation(); e.preventDefault(); return; }
    if(openRow === row){ closeRow(); e.stopPropagation(); e.preventDefault(); return; }
    if(onTap && !e.target.closest("a, button, video") && !getSelection()?.toString()) onTap();
  }, true);
}
const initDescrSwipe = row => initShutter(row, DESCR_W, ()=>openDescr());
/* saves the description (empty — removes it); «Отменить» brings the old one back */
async function saveDescr(t, v){
  const was = t.descr || ""; v = v.trim();
  if(v === was.trim()) return true;
  const put = async x=>{
    if(LIVE){ const full = fromApi(await api(`/api/tasks/${t.id}`, "PATCH", {descr: x})); t.descr = full.descr; t.history = full.history; }
    else t.descr = x;
    if(curTask?.id === t.id){ renderDescr(); renderHistory(); }
  };
  try{ await put(v); }catch(e){ fail(e); return false; }
  haptic("medium");
  toast(v ? (was ? "Описание изменено" : "Описание добавлено") : "Описание удалено", null, {undo: async ()=>{
    try{ await put(was); }catch(e){ fail(e); }
  }});
  return true;
}
function openDescr(){
  const t = curTask, sh = $("#descrSheet"), inp = $("#descrInp"), was = t.descr || "", dk = "dd_" + t.id;
  inp.value = cloud.get(dk) || was;
  const draft = keepDraft(dk, ()=>inp.value.trim() && inp.value !== was ? inp.value : null);
  const fit = ()=>{ inp.style.height = "auto"; inp.style.height = inp.scrollHeight + 2 + "px"; };
  inp.oninput = ()=>{ fit(); draft(); };
  $("#descrDel").hidden = !was;
  const close = ()=>{ inp.blur(); sh.classList.remove("open"); };
  const save = async v=>{ if(await saveDescr(t, v)){ cloud.set(dk, null); close(); } };
  $("#descrSave").onclick = ()=>save(inp.value);
  $("#descrDel").onclick = ()=>save("");
  $("#descrCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  sh.classList.add("open"); haptic(); requestAnimationFrame(fit);
  focusField(inp);
}
$("#addDescr").onclick = ()=>openDescr();
/* «Комментарии» on the projects screen: every task's newest comments; a tap opens the task and its comments */
let cfeed = null;
async function loadCommentsFeed(){
  let rows;
  if(LIVE){ try{ rows = await api("/api/comments"); }catch(e){ if(!cfeed) $("#cfeed").innerHTML = `<div class="empty">Не удалось загрузить</div>`; return; } }
  else {
    await sleep(250);
    rows = tasks.flatMap(t=>(t.comments || []).map(c=>({...c, task_id: t.id, pid: t.pid, task: t.title,
      project: projects.find(p=>p.id === t.pid)?.name || PERSONAL_NAME, item: c.item_id != null ? t.items.find(x=>x.id === c.item_id)?.t : null}))).reverse();
  }
  cfeed = rows; renderCommentsFeed();
}
function renderCommentsFeed(){
  const box = $("#cfeed"); unghost(box);
  if(!cfeed?.length){ box.innerHTML = `<div class="empty">Комментариев пока нет.<br>Их пишут под задачей — во вкладках «Вопросы» и «Комментарии» — или к пункту свайпом влево.</div>`; return; }
  box.innerHTML = cfeed.map((c, k)=>`<button class="src-msg comm" data-k="${k}" type="button">${avSmall({name: c.author, photo: c.photo})}<div class="body"><div class="who"><b>${esc(c.author || "")}</b><span>${esc(c.date || "")}</span></div>`
    + `<div class="tx">${linkify(c.text)}</div><div class="about">${esc(c.task || "")}${c.item ? ` · пункт «${esc(c.item)}»` : ""}</div></div></button>`).join("");
  box.querySelectorAll("[data-k]").forEach(b=>b.onclick = async ()=>{
    const c = cfeed[+b.dataset.k]; haptic();
    curProj = projects.find(p=>p.id === c.pid) || {id: c.pid, name: c.project || PERSONAL_NAME, personal: true};
    await openTask(c.task_id);
    if(curTask?.id === c.task_id && stack.at(-1) === "s-task") openComments(c.item_id != null ? curTask.items.find(x=>x.id === c.item_id) || null : null, c.id);
  });
}
$("#openComments").onclick = ()=>{ haptic(); openCommentsFeed(); };
/* ---------- «Время»: who put how much time in, where; the server gives the log rows of a period, the rest is here ---------- */
const TIME_PER = {week:"Неделя", month:"Месяц", quarter:"Квартал", all:"Всё время"};
const tm = {period: "month", range: null /* {from, to}: "YYYY-MM-DD", both days included */, pid: null, who: null, rows: null, me: null, seq: 0};
const dayTs = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d).getTime() / 1000; };   // local midnight
function rangeTxt(r){
  const a = new Date(dayTs(r.from) * 1000), b = new Date(dayTs(r.to) * 1000), now = new Date().getFullYear();
  const M = ["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"];
  const f = (d, y) => `${d.getDate()} ${M[d.getMonth()]}${y ? " " + d.getFullYear() : ""}`;
  if(r.from === r.to) return f(a, a.getFullYear() !== now);
  const y = a.getFullYear() !== b.getFullYear() || b.getFullYear() !== now;
  return a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear() && !y
    ? `${a.getDate()}–${f(b)}` : `${f(a, y)} – ${f(b, y)}`;
}
/* «Диапазон»: two dates, or a quick one (this/last week, this/last month) */
function openRange(){
  const sh = $("#rangeSheet"), fi = $("#rangeFrom"), ti = $("#rangeTo");
  const now = new Date(), d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const mon = new Date(d0); mon.setDate(d0.getDate() - (d0.getDay() + 6) % 7);
  const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const Q = [["Эта неделя", mon, d0], ["Прошлая неделя", add(mon, -7), add(mon, -1)],
             ["Этот месяц", new Date(d0.getFullYear(), d0.getMonth(), 1), d0],
             ["Прошлый месяц", new Date(d0.getFullYear(), d0.getMonth() - 1, 1), new Date(d0.getFullYear(), d0.getMonth(), 0)]];
  const sync = ()=>{ $("#rangeFromW").classList.toggle("blank", !fi.value); $("#rangeToW").classList.toggle("blank", !ti.value);
    $("#rangeQuick").querySelectorAll("button").forEach((b, i)=>b.classList.toggle("on", fi.value === ymd(Q[i][1]) && ti.value === ymd(Q[i][2]))); };
  $("#rangeQuick").innerHTML = Q.map(([l], i)=>`<button type="button" data-i="${i}">${l}</button>`).join("");
  $("#rangeQuick").querySelectorAll("button").forEach(b=>b.onclick = ()=>{ const q = Q[+b.dataset.i]; fi.value = ymd(q[1]); ti.value = ymd(q[2]); sync(); haptic(); });
  fi.value = tm.range?.from || ""; ti.value = tm.range?.to || ymd(d0);
  fi.oninput = ti.oninput = sync; sync();
  const close = ()=>{ document.activeElement?.blur(); sh.classList.remove("open"); };
  $("#rangeCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#rangeSave").onclick = ()=>{
    let a = fi.value, b = ti.value || ymd(d0);
    if(!a){ toast("Выберите, с какого дня", "warn"); fi.focus(); return; }
    if(a > b) [a, b] = [b, a];
    close();
    tm.period = "range"; tm.range = {from: a, to: b}; cloud.set("tp", "range"); cloud.set("tr", JSON.stringify(tm.range));
    haptic(); renderTime(); loadTime();
  };
  sh.classList.add("open"); haptic();
}
function demoTimeRows(){
  const P = Object.fromEntries(projects.map(p=>[p.id, p])), now = Date.now()/1000, d = 86400;
  const ppl = [{id:1,name:"Олег Петров"},{id:2,name:"Мария"},{id:3,name:"Кирилл"}];
  const raw = [[14,0,2.5,1],[14,1,1,3],[13,0,4,2],[12,1,2.5,9],[9,2,1.5,4],[8,1,3,12],[10,0,.5,40],[13,1,1.25,20],[9,2,2,70]];
  return raw.map(([tid, u, h, ago], i)=>{ const t = tasks.find(x=>x.id === tid); const p = P[t.pid];
    return {id: i + 1, task_id: tid, task: t.title, pid: t.pid, project: p.name, color: p.color, user: ppl[u], hours: h, ts: now - ago*d, closed: isClosed(t), archived: t.archived}; });
}
async function loadTime(){
  const my = ++tm.seq;
  if(!tm.rows) ghostTime();
  let data;
  const rg = tm.period === "range" && tm.range ? [dayTs(tm.range.from), dayTs(tm.range.to) + 86400] : null;   // the last day included
  if(LIVE){ try{ data = await api(rg ? `/api/time?from=${rg[0]}&to=${rg[1]}` : `/api/time?period=${tm.period}`); }catch(e){ fail(e); return; } }
  else { await sleep(DEMO_DELAY); const days = {week:7, month:30, quarter:91}[tm.period];
    data = {me: 1, rows: demoTimeRows().filter(r=>rg ? r.ts >= rg[0] && r.ts < rg[1] : !days || r.ts >= Date.now()/1000 - days*86400)}; }
  if(my !== tm.seq) return;
  tm.rows = data.rows; tm.me = data.me; renderTime();
}
function ghostTime(){
  ghostList([$("#timeStats"), $("#timeBody")], ()=>{
    tm.rows = ghostRows(4, k=>({id: -1 - k, task_id: -1 - k, task: GHOST_TEXTS[k], pid: -1 - (k % 2), project: ["Сайт компании","Интернет-магазин"][k % 2],
      color: "#999", user: {id: -1 - (k % 3), name: "Исполнитель"}, hours: 2, ts: 0}));
    try{ renderTime(); } finally{ tm.rows = null; }
  });
}
const sumH = rows => rows.reduce((s, r)=>s + r.hours, 0);
function groupTime(rows, key){
  const m = new Map();
  for(const r of rows){ const k = key(r); const g = m.get(k.id) || {...k, hours: 0, rows: []}; g.hours += r.hours; g.rows.push(r); m.set(k.id, g); }
  return [...m.values()].filter(g=>Math.abs(g.hours) >= .005).sort((a, b)=>b.hours - a.hours);
}
function renderTime(){
  unghost($("#timeStats")); unghost($("#timeBody"));
  const isRange = tm.period === "range" && tm.range;
  $("#timeBar").innerHTML = Object.entries(TIME_PER).map(([k, l])=>`<button data-k="${k}" class="${k === tm.period ? "on" : ""}">${l}</button>`).join("")
    + `<button id="timeRange" class="${isRange ? "on" : ""}">${isRange ? esc(rangeTxt(tm.range)) : "Диапазон"}</button>`;
  $("#timeRange").onclick = openRange;
  if(isRange) requestAnimationFrame(()=>{ const b = $("#timeBar"); b.scrollLeft = b.scrollWidth; });   // the picked dates in sight
  $("#timeBar").querySelectorAll("button[data-k]").forEach(b=>b.onclick = ()=>{ if(tm.period === b.dataset.k) return; tm.period = b.dataset.k; haptic(); cloud.set("tp", tm.period === "month" ? null : tm.period); loadTime(); renderTime(); });
  const all = tm.rows || [];
  const rows = all.filter(r=>(tm.pid === null || r.pid === tm.pid) && (tm.who === null || r.user.id === tm.who));
  const pj = tm.pid === null ? null : all.find(r=>r.pid === tm.pid), wh = tm.who === null ? null : all.find(r=>r.user.id === tm.who);
  const pp = $("#timeProj"), wp = $("#timeWho");
  pp.classList.toggle("set", !!pj); pp.querySelector("span").textContent = pj ? pj.project : "Все проекты"; pp.querySelector("i").style.background = pj ? pj.color : "";
  wp.classList.toggle("set", !!wh); wp.querySelector("span").textContent = wh ? (wh.user.id === tm.me ? "Я" : wh.user.name) : "Все люди"; wp.querySelector("i").style.background = wh ? avColor(wh.user.name) : "";
  const total = sumH(rows), byTask = groupTime(rows, r=>({id: r.task_id, name: r.task, pid: r.pid, project: r.project, color: r.color})),
        byProj = groupTime(rows, r=>({id: r.pid, name: r.project, color: r.color})),
        byWho = groupTime(rows, r=>({id: r.user.id, name: r.user.id === tm.me ? `${r.user.name} (вы)` : r.user.name, color: avColor(r.user.name)}));
  $("#timeStats").innerHTML = `
    <div><span class="mono">Всего</span><b>${hoursTxt(total)}</b></div>
    <div><span class="mono">Задач</span><b>${byTask.length}</b></div>
    <div><span class="mono">Людей</span><b>${byWho.length}</b></div>`;
  if(!rows.length){
    $("#timeBody").innerHTML = `<div class="empty">${all.length ? "Под эти фильтры времени нет" : `${tm.period === "range" ? "В эти дни" : `За ${tm.period === "all" ? "всё время" : TIME_PER[tm.period].toLowerCase()}`} время не отмечали.<br>Откройте задачу и нажмите «Время» — или укажите часы при закрытии.`}</div>`;
    return;
  }
  const bar = (h, c, max) => `<div class="tbar" style="--bc:${c}"><i style="width:${Math.max(2, h / max * 100)}%"></i></div>`;
  const grp = (title, list, attr, sub, max = Math.max(...list.map(g=>g.hours))) => list.length < 2 && attr !== "data-tt" ? "" : `<div class="sec-label"><span class="mono">${title}</span></div><div class="grid">${list.map(g=>`
    <button class="row" ${attr}="${g.id}"><span class="pp-dot" style="background:${g.color}"></span><div class="main"><div class="t">${esc(g.name)}</div>${sub ? `<div class="s">${esc(sub(g))}</div>` : ""}${bar(g.hours, g.color, max)}</div><span class="hrs">${hoursTxt(g.hours)}</span></button>`).join("")}</div>`;
  const people = g => { const u = new Set(g.rows.map(r=>r.user.name)); return (tm.pid === null ? g.project + " · " : "") + [...u].join(", "); };
  $("#timeBody").innerHTML = (tm.pid === null ? grp("По проектам", byProj, "data-tp") : "")
    + (tm.who === null ? grp("По людям", byWho, "data-tw") : "")
    + grp("По задачам", byTask, "data-tt", people);
  $("#timeBody").querySelectorAll("[data-tp]").forEach(b=>b.onclick = ()=>{ tm.pid = +b.dataset.tp; haptic(); renderTime(); $("#s-time").scrollTo({top:0, behavior:"smooth"}); });
  $("#timeBody").querySelectorAll("[data-tw]").forEach(b=>b.onclick = ()=>{ tm.who = +b.dataset.tw; haptic(); renderTime(); $("#s-time").scrollTo({top:0, behavior:"smooth"}); });
  $("#timeBody").querySelectorAll("[data-tt]").forEach(b=>b.onclick = ()=>openTask(+b.dataset.tt));
}
function pickTime(title, list, cur, set){
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  $("#projSheetTitle").textContent = title;
  box.innerHTML = list.map(o=>`<button data-id="${o.id ?? ""}" class="${o.id === cur ? "on" : ""}"><span class="pp-dot" style="background:${o.color}"></span><span class="nm">${esc(o.name)}</span><span class="cnt">${o.n}</span>${ck}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ sh.classList.remove("open"); const id = b.dataset.id === "" ? null : +b.dataset.id; if(id !== cur){ set(id); haptic(); renderTime(); } });
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  sh.classList.add("open"); haptic();
}
$("#timeProj").onclick = ()=>{
  if(!tm.rows) return;
  const rows = tm.rows.filter(r=>tm.who === null || r.user.id === tm.who);
  pickTime("Проект", [{id: null, name: "Все проекты", color: "var(--hint)", n: hoursTxt(sumH(rows))},
    ...groupTime(rows, r=>({id: r.pid, name: r.project, color: r.color})).map(g=>({...g, n: hoursTxt(g.hours)}))], tm.pid, id=>tm.pid = id);
};
$("#timeWho").onclick = ()=>{
  if(!tm.rows) return;
  const rows = tm.rows.filter(r=>tm.pid === null || r.pid === tm.pid);
  pickTime("Кто", [{id: null, name: "Все люди", color: "var(--hint)", n: hoursTxt(sumH(rows))},
    ...groupTime(rows, r=>({id: r.user.id, name: r.user.id === tm.me ? "Я" : r.user.name, color: avColor(r.user.name)})).map(g=>({...g, n: hoursTxt(g.hours)}))], tm.who, id=>tm.who = id);
};
$("#openTime").onclick = ()=>{ haptic(); const p = cloud.get("tp");
  if(p === "range"){ try{ const r = JSON.parse(cloud.get("tr") || "null"); if(r?.from && r?.to){ tm.period = "range"; tm.range = r; } }catch(_){} }
  else if(p && TIME_PER[p]) tm.period = p; go("s-time"); renderTime(); loadTime(); updTabs(); };
function openCommentsFeed(){
  if(!cfeed) ghostList([$("#cfeed")], ()=>{   // the silhouette of what comes: three comments
    cfeed = ghostRows(3, k=>({author: "Автор комментария", date: "1 ноя, 10:00", text: GHOST_TEXTS[k], task: "Название задачи"}));
    try{ renderCommentsFeed(); } finally{ cfeed = null; }
  });
  go("s-comments"); loadCommentsFeed(); updTabs();
}
/* ---------- Search: everything you can see — tasks, items, comments, results, projects ----------
   The server finds (SQLite FTS5, word forms: «сайта» finds «сайт»); here — sections with counts, filters,
   the found words marked, and a tap leads straight to the place: the task, the item, the comment, the result. */
const SRCH = {task:"Задачи", item:"Пункты", comment:"Комментарии", result:"Результаты", project:"Проекты"};
const SRCH_IC = {
  task: '<rect x="4.5" y="3.5" width="15" height="17" rx="3"/><path d="M8.5 8.5H15.5"/><path d="M8.5 12H15.5"/><path d="M8.5 15.5H12.5"/>',
  item: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/>',
  itemDone: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.6 12.3L10.9 14.6L15.4 9.9"/>',
  comment: BDG.comm.i, result: BDG.res.i,
};
const SRCH_DOT = {all:"", active:"var(--st-open)", closed:"var(--st-closed)", archive:"var(--st-archive)"};
const SRCH_ST = {all:"Все статусы", active:"Незакрытые", closed:"Закрытые", archive:"В архиве"};
const srch = {q: "", res: null, kind: "all", st: "all", pid: null, mine: false, seq: 0, timer: 0};
const srchIc = (k, done) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"${done ? ' class="done"' : ""}>${SRCH_IC[k === "item" && done ? "itemDone" : k]}</svg>`;
const marked = parts => parts.map(([x, h])=>h ? `<mark>${esc(x)}</mark>` : esc(x)).join("");
function srchPass(r){
  if(srch.pid !== null && r.pid !== srch.pid) return false;
  if(r.kind === "project") return srch.st === "all" && !srch.mine;
  if(srch.mine && !r.mine) return false;
  if(srch.st === "active") return !r.archived && r.status !== "closed";
  if(srch.st === "closed") return !r.archived && r.status === "closed";
  if(srch.st === "archive") return r.archived;
  return true;
}
/* demo: the same rules as the server (word starts, Russian endings cut), over the demo tasks */
const SRCH_END = "иями ями ами ого его ому ему ыми ими ость ости ах ях ам ям ом ем ой ей ий ый ая яя ое ее ую юю ых их ов ев ию ия ие ье ья а я о е ы и у ю ь й".split(" ").sort((a,b)=>b.length-a.length);
function srchTerms(q){
  const out = [];
  for(let w of (q.toLowerCase().replace(/ё/g,"е").match(/[\p{L}\p{N}_]+/gu) || [])){
    if(/^[а-я]+$/.test(w) && w.length >= 5){ const e = SRCH_END.find(e=>w.endsWith(e) && w.length - e.length >= 4); if(e) w = w.slice(0, -e.length); }
    if(!out.includes(w)) out.push(w);
  }
  return out.slice(0, 6);
}
function srchSnip(text, ts, width=90){
  text = String(text || "").replace(/\s+/g, " ").trim();
  const re = new RegExp(`(?<![\\p{L}\\p{N}_])(${ts.map(t=>t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/е/g, "[её]")).join("|")})[\\p{L}\\p{N}_]*`, "giu");
  const hits = [...text.matchAll(re)];
  if(!hits.length) return [[text.slice(0, width), 0]];
  let start = 0;
  if(text.length > width){ start = Math.max(0, Math.min(hits[0].index - (width/3|0), text.length - width)); if(start){ const sp = text.indexOf(" ", start); if(sp >= 0 && sp < hits[0].index) start = sp + 1; } }
  let end = Math.min(text.length, start + width);
  if(end < text.length){ const sp = text.lastIndexOf(" ", end); if(sp > hits[0].index + hits[0][0].length) end = sp; }
  const parts = []; let pos = start;
  for(const m of hits){ if(m.index < start || m.index + m[0].length > end) continue; if(m.index > pos) parts.push([text.slice(pos, m.index), 0]); parts.push([m[0], 1]); pos = m.index + m[0].length; }
  if(pos < end) parts.push([text.slice(pos, end), 0]);
  if(start) parts.unshift(["…", 0]); if(end < text.length) parts.push(["…", 0]);
  return parts;
}
function demoSearch(q){
  const ts = srchTerms(q), groups = {task:[], item:[], comment:[], result:[], project:[]};
  const has = x => { const n = String(x || "").toLowerCase().replace(/ё/g,"е"); return ts.length && ts.every(t=>new RegExp(`(^|[^\\p{L}\\p{N}_])${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u").test(n)); };
  for(const p of projects) if(has(p.name)) groups.project.push({kind:"project", pid:p.id, project:p.name, color:p.color, parts:srchSnip(p.name, ts)});
  for(const t of tasks){
    const p = projects.find(x=>x.id === t.pid);
    const base = {task_id:t.id, pid:t.pid, task:t.title, project:p?.name || PERSONAL_NAME, color:p?.color, archived:!!t.archived,
                  status: isClosed(t) ? "closed" : stOf(t), mine: t.assignee?.id === 1};
    if(has(t.title)) groups.task.push({...base, kind:"task", date:t.date, parts:srchSnip(t.title, ts)});
    if(has(t.result_note)) groups.result.push({...base, kind:"result", parts:srchSnip(t.result_note, ts)});
    (t.items || []).forEach((it, k)=>{ if(has(it.t)) groups.item.push({...base, kind:"item", ref:it.id ?? k, idx:k, done:!!it.d, parts:srchSnip(it.t, ts)}); });
    for(const c of t.comments || []) if(has(c.text)) groups.comment.push({...base, kind:"comment", ref:c.id, item_id:c.item_id, author:c.author, date:c.date, parts:srchSnip(c.text, ts)});
  }
  return {terms: ts, groups, total: Object.fromEntries(Object.entries(groups).map(([k, v])=>[k, v.length]))};
}
function srchRow(r, i){
  const st = r.kind === "project" ? "" : statusBadge({archived: r.archived, closed: r.status === "closed", backlog: r.status === "backlog", progress: r.status === "progress" || r.status === "review", review: r.status === "review"});
  const proj = r.project ? `<span class="pp-dot" style="background:${r.color || "var(--hint)"}"></span><span class="tn">${esc(r.project)}</span>` : "";
  const inTask = `<span class="tn">«${esc(r.task || "")}»</span>`;
  const meta = r.kind === "project" ? `<span>Проект</span>`
    : r.kind === "task" ? `${st}${proj}`
    : r.kind === "comment" ? `<span>${esc(r.author || "")}${r.date ? " · " + esc(r.date) : ""}</span>${inTask}`
    : `${inTask}${proj}`;
  const ic = r.kind === "project" ? `<span class="pdot" style="background:${r.color || "var(--hint)"}"></span>` : srchIc(r.kind, r.done);
  return `<button class="row hit" type="button" data-h="${i}">${ic}<div class="main"><div class="hx">${marked(r.parts)}</div><div class="s">${meta}</div></div></button>`;
}
const SRCH_PEEK = 4;   // under «Все» each section shows a few, «Ещё N» opens the section
let srchRows = [];
function renderSearch(){
  const box = $("#searchRes"), bar = $("#searchBar"), picks = $("#searchPicks"), r = srch.res;
  unghost(box); unghost(bar);
  $("#searchClear").hidden = !srch.q;
  if(!srch.q.trim()){
    bar.hidden = picks.hidden = true;
    const recent = cloud.get("sq") ? JSON.parse(cloud.get("sq")) : [];
    box.innerHTML = (recent.length ? `<div class="sec-label"><span class="mono">Недавние</span></div><div class="feed-bar recent">${recent.map(q=>`<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>` : "")
      + `<div class="empty">Ищет по названиям задач, пунктам чеклистов, комментариям, результатам и проектам.<br>Слово можно писать в любой форме</div>`;
    box.querySelectorAll("[data-q]").forEach(b=>b.onclick = ()=>{ haptic(); setSearch(b.dataset.q); });
    return;
  }
  if(!r) return;
  const kinds = Object.keys(SRCH), shown = {};
  for(const k of kinds) shown[k] = (r.groups[k] || []).filter(srchPass);
  const n = k => shown[k].length + Math.max(0, (r.total[k] || 0) - (r.groups[k] || []).length);   // the server sends 40 a section
  const all = kinds.reduce((a, k)=>a + n(k), 0);
  if(srch.kind !== "all" && !n(srch.kind)) srch.kind = "all";
  bar.hidden = false;
  bar.innerHTML = `<button data-k="all" class="${srch.kind === "all" ? "on" : ""}">Все<span class="n">${all}</span></button>`
    + kinds.filter(k=>n(k)).map(k=>`<button data-k="${k}" class="${srch.kind === k ? "on" : ""}">${SRCH[k]}<span class="n">${n(k)}</span></button>`).join("");
  picks.hidden = false;
  $("#searchSt span").textContent = srch.st === "all" ? "Статус" : SRCH_ST[srch.st]; $("#searchSt").classList.toggle("set", srch.st !== "all"); $("#searchSt i").style.background = SRCH_DOT[srch.st];
  const pn = srch.pid === null ? null : [...kinds.flatMap(k=>r.groups[k])].find(x=>x.pid === srch.pid);
  $("#searchProj span").textContent = pn ? pn.project : "Проект"; $("#searchProj").classList.toggle("set", srch.pid !== null);
  $("#searchProj i").style.background = pn?.color || "";
  $("#searchMine").classList.toggle("set", srch.mine); $("#searchMine").setAttribute("aria-pressed", srch.mine);
  srchRows = [];
  if(!all){ box.innerHTML = `<div class="empty">Ничего не нашлось по «${esc(srch.q.trim())}»${srch.st !== "all" || srch.pid !== null || srch.mine ? ".<br>Попробуйте убрать фильтры" : ""}</div>`; return; }
  box.innerHTML = kinds.filter(k=>(srch.kind === "all" || srch.kind === k) && shown[k].length).map(k=>{
    const rows = srch.kind === "all" ? shown[k].slice(0, SRCH_PEEK) : shown[k];
    const more = n(k) - rows.length;
    const html = rows.map(x=>{ srchRows.push(x); return srchRow(x, srchRows.length - 1); }).join("");
    return `<div class="sec-label"><span class="mono">${SRCH[k]}</span><span class="mono">${n(k)}</span></div><div class="grid">${html}</div>`
      + (more > 0 && srch.kind === "all" ? `<button class="link-row" type="button" data-more="${k}">Ещё ${more}<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6L15.5 12L9.5 18"/></svg></button>` : "");
  }).join("");
}
function srchKind(k){
  if(srch.kind === k) return;
  srch.kind = k; haptic(); renderSearch();
  const l = $("#searchRes"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap");
  $("#s-search").scrollTo({top: 0, behavior: "smooth"});
}
$("#searchBar").onclick = e=>{ const b = e.target.closest("[data-k]"); if(b) srchKind(b.dataset.k); };
$("#searchRes").addEventListener("click", e=>{
  const m = e.target.closest("[data-more]"); if(m){ srchKind(m.dataset.more); return; }
  const b = e.target.closest("[data-h]"); if(b) openHit(srchRows[+b.dataset.h]);
});
function remember(q){
  q = q.trim(); if(!q) return;
  let list = []; try{ list = JSON.parse(cloud.get("sq") || "[]"); }catch(_){}
  cloud.set("sq", JSON.stringify([q, ...list.filter(x=>x.toLowerCase() !== q.toLowerCase())].slice(0, 6)));
}
async function runSearch(){
  const q = srch.q.trim(), my = ++srch.seq;
  if(!q){ srch.res = null; renderSearch(); return; }
  if(!srch.res) ghostList([$("#searchRes"), $("#searchBar")], ()=>{   // the silhouette of what comes: a section of three
    const keep = srch.res; srch.res = {groups: {task: ghostRows(3, k=>({kind:"task", parts:[[GHOST_TEXTS[k], 0]], status:"open", project:"Проект"}))}, total: {task: 3}};
    try{ renderSearch(); } finally{ srch.res = keep; }
  });
  let r;
  try{ r = LIVE ? await api(`/api/search?q=${encodeURIComponent(q)}`) : (await sleep(200), demoSearch(q)); }
  catch(e){ if(my === srch.seq){ unghost($("#searchRes")); unghost($("#searchBar")); $("#searchBar").hidden = true; $("#searchRes").innerHTML = `<div class="empty">Не удалось найти, попробуйте ещё раз</div>`; } return; }
  if(my !== srch.seq) return;   // a newer query is on its way
  srch.res = r; renderSearch();
}
function setSearch(q){ const inp = $("#searchInp"); inp.value = q; srch.q = q; runSearch(); remember(q); }
$("#searchInp").addEventListener("input", e=>{
  srch.q = e.target.value; $("#searchClear").hidden = !srch.q;
  clearTimeout(srch.timer); srch.timer = setTimeout(runSearch, srch.q.trim() ? 220 : 0);
});
$("#searchInp").addEventListener("keydown", e=>{ if(e.key === "Enter"){ e.target.blur(); remember(srch.q); } });
$("#searchClear").onclick = ()=>{ haptic(); setSearch(""); $("#searchInp").focus(); };
/* status and project — the same pop-up as in «Мои/Все задачи»; «Мои» — only tasks you do */
$("#searchSt").onclick = ()=>{
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  box.innerHTML = Object.entries(SRCH_ST).map(([k, v])=>`<button data-c="${k}" class="${k === srch.st ? "on" : ""}"><span class="pp-dot" style="background:${SRCH_DOT[k] || "var(--hint)"}"></span><span class="nm">${v}</span>${ck}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ sh.classList.remove("open"); if(b.dataset.c !== srch.st){ srch.st = b.dataset.c; haptic(); renderSearch(); } });
  $("#projSheetTitle").textContent = "Статус";
  sh.classList.add("open"); haptic();
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
};
$("#searchProj").onclick = ()=>{
  const r = srch.res; if(!r) return;
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  const ps = new Map(); for(const x of Object.values(r.groups).flat()) if(!ps.has(x.pid)) ps.set(x.pid, {id: x.pid, name: x.project, color: x.color, n: 0});
  for(const x of Object.values(r.groups).flat()) ps.get(x.pid).n++;
  const row = (pid, name, color, n) => `<button data-pid="${pid ?? ""}" class="${pid === srch.pid ? "on" : ""}"><span class="pp-dot" style="background:${color}"></span><span class="nm">${esc(name)}</span><span class="cnt">${n}</span>${ck}</button>`;
  box.innerHTML = row(null, "Все проекты", "var(--hint)", Object.values(r.groups).flat().length) + [...ps.values()].map(p=>row(p.id, p.name || "", p.color || "var(--hint)", p.n)).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ sh.classList.remove("open"); const pid = b.dataset.pid === "" ? null : +b.dataset.pid; if(pid !== srch.pid){ srch.pid = pid; haptic(); renderSearch(); } });
  $("#projSheetTitle").textContent = "Проект";
  sh.classList.add("open"); haptic();
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
};
$("#searchMine").onclick = ()=>{ srch.mine = !srch.mine; haptic(); renderSearch(); };
/* a tap goes to the very place: the project, the task, its item (lit up), the comment, the result */
async function openHit(r){
  if(!r) return;
  haptic(); remember(srch.q); $("#searchInp").blur();
  if(r.kind === "project"){
    curProj = projects.find(p=>p.id == r.pid) || {id: r.pid, name: r.project, color: r.color};
    filter = savedFilter(curProj.id); if(LIVE) tasks = []; ghostTasks();
    go("s-tasks"); loadTasks(true); return;
  }
  curProj = projects.find(p=>p.id == r.pid) || {id: r.pid, name: r.project || PERSONAL_NAME, personal: r.project === PERSONAL_NAME};
  await openTask(r.task_id);
  const t = curTask; if(t?.id !== r.task_id || stack.at(-1) !== "s-task") return;
  if(r.kind === "result"){ showResult(t); return; }
  if(r.kind === "comment"){ openComments(r.item_id != null ? t.items.find(x=>x.id === r.item_id) || null : null, r.ref); return; }
  if(r.kind === "item"){
    const k = r.idx ?? t.items.findIndex(x=>x.id === r.ref);
    const row = document.querySelector(`#items .item[data-i="${k}"]`); if(!row) return;
    row.scrollIntoView({block: "center", behavior: "smooth"});
    row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
  }
}
function openSearch(){
  srch.kind = "all";
  renderSearch();
  go("s-search"); updTabs();
  // the keyboard comes up at once (focus still inside the tap), but the caret stays hidden while the screen slides in:
  // iOS draws it where the field was when focused — at the right edge — and it jumps across with the screen
  const inp = $("#searchInp"), scr = $("#s-search");
  inp.style.caretColor = "transparent";
  inp.focus({preventScroll: true}); inp.select();
  const show = e => {
    if(e && e.target !== scr) return;
    scr.removeEventListener("transitionend", show); scr.removeEventListener("animationend", show); clearTimeout(tm);
    if(!inp.style.caretColor) return;
    inp.style.caretColor = "";
    if(document.activeElement === inp) inp.select();   // placed again where the field is now
  };
  const tm = setTimeout(show, 450);
  scr.addEventListener("transitionend", show); scr.addEventListener("animationend", show);   // a slide, or a tab's fade-in
  if(srch.q.trim()) runSearch();   // back to it later: the same query, fresh results
}
/* on the site: «/» or Ctrl/⌘+K opens search */
document.addEventListener("keydown", e=>{
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if(((e.key === "k" || e.key === "л") && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)){
    e.preventDefault();
    if(stack.at(-1) === "s-search"){ $("#searchInp").focus(); return; }
    tabSwitch(()=>{ toRoot(); openSearch(); });
  }
});
/* «Исходное сообщение» from a row swipe: the chat messages the task was made from, in a popup — not in the chat */
const SRC_KIND = {photo:"Фото", image_doc:"Картинка", voice:"Голосовое", audio:"Аудио", video:"Видео", video_note:"Кружок", animation:"GIF", document:"Файл"};
let srcSeq = 0;
async function showSource(t){
  const sh = $("#srcSheet"), box = $("#srcList"), seq = ++srcSeq;
  const draw = ms => {
    box.innerHTML = ms.length ? ms.map(m=>`<div class="src-msg"><div class="who"><b>${esc(m.author || "")}</b><span>${esc(m.date || "")}</span></div>`
      + (SRC_KIND[m.kind] ? `<div class="kind">${SRC_KIND[m.kind]}${m.file ? " · " + esc(m.file) : ""}${m.transcript ? " — расшифровка:" : ""}</div>` : "")
      + (m.text || m.transcript ? `<div class="tx">${esc(m.text || m.transcript)}${m.text && m.transcript ? "\n\n" + esc(m.transcript) : ""}</div>` : "")
      + (m.shots?.length ? `<div class="shots">${m.shots.map(u=>`<img src="${esc(u)}" alt="" loading="lazy">`).join("")}</div>` : "")
      + `</div>`).join("")
      : `<div class="empty">Исходного сообщения нет: задачу создали в приложении или оно не сохранилось.</div>`;
  };
  ghostList([box], ()=>draw([{author:"Автор сообщения", date:"1 ноя, 10:00", kind:"text", text:"Текст исходного сообщения из чата, по которому сделана задача"}]));
  sh.classList.add("open"); haptic();
  const close = ()=>sh.classList.remove("open");
  $("#srcClose").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  let ms;
  try{
    ms = LIVE ? (await api(`/api/tasks/${t.id}/source`)).messages
      : (await sleep(300), [{author: t.author || "Анна", date: t.date || "", kind:"text", text: t.title + (t.items?.length ? ":\n" + t.items.map(i=>"— " + i.t).join("\n") : "")}]);
  }catch(e){ if(seq === srcSeq){ unghost(box, true); box.innerHTML = `<div class="empty">Не удалось загрузить</div>`; } return; }
  if(seq !== srcSeq) return;
  draw(ms); unghost(box);
}
async function applyTitle(t, v, send){
  if(send && LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", {title:v}); }catch(e){ fail(e); return; } }
  everyCopy(t.id, x=>x.title = v); t.title = v; renderTasks(); if(onPeople()) renderPeople();
  if(stack.at(-1) === "s-task" && curTask?.id === t.id) renderTask();
  if(send) toast("Название возвращено");
}
function renameTask(t){
  const sh = $("#renameSheet"), inp = $("#renameInp");
  inp.value = inp.dataset.orig = t.title; sh.classList.add("open");
  focusField(inp, true);
  $("#renameCancel").onclick = ()=>sh.classList.remove("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  $("#renameSave").onclick = async ()=>{
    const v = inp.value.trim(); if(!v || v === t.title){ sh.classList.remove("open"); return; }
    if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", {title:v}); }catch(e){ fail(e); return; } }
    sh.classList.remove("open");
    const old = t.title; await applyTitle(t, v, false);
    toast(LIVE ? "Название изменено" : "Демо: название изменено", null, {undo: ()=>applyTitle(t, old, true)});
  };
  inp.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); $("#renameSave").click(); } };
}
/* A project without a chat: made here, named here (a chat's project is named by /project in the chat) */
function projName(proj){
  const sh = $("#projNameSheet"), inp = $("#projNameInp");
  $("#projNameTitle").textContent = proj ? "Название проекта" : "Новый проект";
  $("#projNameSave").textContent = proj ? "Сохранить" : "Создать";
  $("#projNameNote").hidden = !!proj;
  $("#projNameDel").style.display = proj?.owner ? "" : "none";   // only its maker deletes a project
  $("#projNameDel").onclick = ()=>{ close(); deleteProject(proj); };
  inp.value = proj ? proj.name : ""; sh.classList.add("open");
  focusField(inp, true);
  const close = ()=>{ sh.classList.remove("open"); inp.blur(); };
  $("#projNameCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#projNameSave").onclick = async ()=>{
    const v = inp.value.replace(/\s+/g, " ").trim();
    if(!v){ inp.focus(); return; }
    if(proj){
      if(v !== proj.name){
        if(LIVE){ try{ await api(`/api/projects/${proj.id}`, "PATCH", {name:v}); }catch(e){ fail(e); return; } }
        proj.name = v; renderTasks(); renderProjects();
        toast(LIVE ? "Проект переименован" : "Демо: проект переименован");
      }
      close(); return;
    }
    let p;
    if(LIVE){ try{ p = await api("/api/projects", "POST", {name:v}); }catch(e){ fail(e); return; } }
    else p = {id: Math.max(0, ...projects.map(x=>x.id)) + 1, name:v, color:"var(--hint)", standalone:true, owner:true, open:0};
    close();
    projects.unshift(p); renderProjects();   // a new project lands on top (same as an unordered one after a personal order)
    curProj = p; filter = savedFilter(p.id); if(LIVE) tasks = [];
    go("s-tasks"); renderTasks("enter");
    toast(LIVE ? "Проект создан" : "Демо: проект создан");
  };
  inp.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); $("#projNameSave").click(); } };
}
function deleteProject(proj){
  const n = tasks.filter(t=>t.pid === proj.id).length;
  const go = async ok=>{
    if(!ok) return;
    if(LIVE){ try{ await api(`/api/projects/${proj.id}`, "DELETE"); }catch(e){ fail(e); return; } }
    haptic("heavy");
    projects = projects.filter(p=>p.id !== proj.id); tasks = tasks.filter(t=>t.pid !== proj.id);
    if(ppl.data) ppl.data.tasks = ppl.data.tasks.filter(t=>t.pid !== proj.id);
    if(cloud.get("np") === String(proj.id)) cloud.set("np", null);
    if(stack.at(-1) === "s-tasks") back(); else renderProjects();
    toast(LIVE ? "Проект удалён" : "Демо: проект удалён");
  };
  const msg = `Удалить проект «${proj.name}»${n ? ` и ${n} ${plural(n,"задачу","задачи","задач")} в нём` : ""}? Восстановить не получится.`;
  if(tg?.showConfirm) tg.showConfirm(msg, go); else go(confirm(msg));
}
/* The project's own picture: over the chat's photo, only in Todogram (the chat itself keeps its own).
   The phone crops it to a square and shrinks it before sending. */
async function squareJpeg(file, size=512){
  const bmp = await createImageBitmap(file);
  const side = Math.min(bmp.width, bmp.height), c = document.createElement("canvas");
  c.width = c.height = Math.min(size, side);
  c.getContext("2d").drawImage(bmp, (bmp.width - side) / 2, (bmp.height - side) / 2, side, side, 0, 0, c.width, c.height);
  return await new Promise((res, rej)=>c.toBlob(b=>b ? res(b) : rej(new Error("no blob")), "image/jpeg", .88));
}
function openPic(proj){
  /* Draft in the sheet: the preview updates at once, the server (or the list) only on «Готово». */
  const sh = $("#picSheet"), saveBtn = $("#picSave");
  const orig = {photo: proj.photo || null, pic: !!proj.pic};
  /* own picture URL has &v=…; without it the same path is the chat's photo again */
  const chatPhoto = orig.pic && orig.photo ? orig.photo.replace(/&v=[^&]+/, "") : (!orig.pic ? orig.photo : null);
  let draft = {photo: orig.photo, pic: orig.pic, blob: null, cleared: false};
  let previewUrl = null;   // object URL of the staged blob — revoke on redraw / close
  const dirty = () => !!draft.blob || (draft.cleared && orig.pic);
  const hasPic = () => !!draft.blob || (draft.pic && !draft.cleared);
  const draw = (busy=false)=>{
    if(previewUrl){ URL.revokeObjectURL(previewUrl); previewUrl = null; }
    const letter = esc(proj.name.replace(/[«»"]/g,"")[0] || "?");
    const src = draft.blob ? (previewUrl = URL.createObjectURL(draft.blob)) : draft.photo;
    $("#picPrev").innerHTML = src
      ? `<div class="ava${busy ? " busy" : ""}" style="background:${proj.color}"><span>${letter}</span><img src="${esc(src)}" alt="" onerror="this.remove()"></div>`
      : `<div class="ava${busy ? " busy" : ""}" style="background:${proj.color}"><span>${letter}</span></div>`;
    $("#picDel").style.display = hasPic() ? "" : "none";
    saveBtn.disabled = !dirty() || busy;
    $("#picNote").textContent = proj.standalone ? "Квадратная картинка в списке проектов. Подойдёт любое фото — обрежем по центру."
      : "Видна только в Todogram, фото самого чата не меняется. Подойдёт любое фото — обрежем по центру.";
  };
  const apply = (r, msg)=>{
    proj.photo = r.photo; proj.pic = r.pic;
    projects.forEach(p=>{ if(p.id === proj.id){ p.photo = r.photo; p.pic = r.pic; } });
    renderProjects(); haptic("medium"); toast(msg);
  };
  const close = ()=>{
    if(previewUrl){ URL.revokeObjectURL(previewUrl); previewUrl = null; }
    document.onpaste = null; sh.classList.remove("open");
  };
  $("#picCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  const stageFile = async f=>{
    let blob; try{ blob = await squareJpeg(f); }catch(e){ toast("Не получилось открыть картинку", "warn"); return; }
    draft = {photo: null, pic: true, blob, cleared: false}; draw();
  };
  const pickFile = ()=>{
    const inp = $("#fileInp"); inp.multiple = false; inp.value = "";
    inp.onchange = ()=>{ const f = [...inp.files].find(x=>x.type.startsWith("image/")); if(f) stageFile(f); };
    inp.click();
  };
  const byLink = async url=>{
    url = (url || "").trim();
    if(!/^https?:\/\/\S+$/i.test(url)){ toast("Это не похоже на ссылку", "warn"); return; }
    const go = $("#picUrlGo"); go.disabled = true; draw(true);
    try{
      const r = LIVE ? await apiFetch(`/api/projects/${proj.id}/photo_fetch`, {method:"POST", headers:{"Authorization":authHeader(), "Content-Type":"application/json"}, body: JSON.stringify({url})}, true)
                     : await fetch(url);
      if(!r.ok) throw new Error(r.status);
      const b = await r.blob();
      if(!b.type.startsWith("image/")) throw new Error("not an image");
      $("#picUrl").value = "";
      await stageFile(b);
    }catch(e){ draw(); toast("По этой ссылке не нашлось картинки", "warn"); }
    finally{ go.disabled = false; }
  };
  const zone = $("#picDrop");
  zone.onclick = pickFile;
  zone.onkeydown = e=>{ if(e.key === "Enter" || e.key === " "){ e.preventDefault(); pickFile(); } };
  zone.ondragover = e=>{ e.preventDefault(); e.dataTransfer.dropEffect = "copy"; zone.classList.add("over"); };
  zone.ondragleave = e=>{ if(!zone.contains(e.relatedTarget)) zone.classList.remove("over"); };
  zone.ondrop = e=>{
    e.preventDefault(); zone.classList.remove("over");
    const f = [...(e.dataTransfer.files||[])].find(x=>x.type.startsWith("image/"));
    if(f){ stageFile(f); return; }
    const html = e.dataTransfer.getData("text/html"), src = html && /<img[^>]+src=["']([^"']+)/i.exec(html)?.[1];
    const url = src || e.dataTransfer.getData("text/uri-list").split("\n").find(x=>x && !x.startsWith("#")) || e.dataTransfer.getData("text/plain");
    if(url) byLink(url); else toast("Перетащите картинку", "warn");
  };
  document.onpaste = e=>{
    if(!sh.classList.contains("open")) return;
    const f = [...(e.clipboardData?.files||[])].find(x=>x.type.startsWith("image/"));
    if(f){ e.preventDefault(); stageFile(f); return; }
    if(e.target === $("#picUrl")) return;
    const t = e.clipboardData?.getData("text/plain");
    if(t && /^https?:\/\//i.test(t.trim())){ e.preventDefault(); byLink(t); }
  };
  $("#picUrlGo").onclick = ()=>byLink($("#picUrl").value);
  $("#picUrl").onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); byLink($("#picUrl").value); } };
  $("#picDel").onclick = ()=>{
    draft = {photo: chatPhoto, pic: false, blob: null, cleared: !!orig.pic};
    draw();
  };
  saveBtn.onclick = async ()=>{
    if(!dirty() || saveBtn.disabled) return;
    saveBtn.disabled = true; draw(true);
    try{
      if(draft.cleared && !draft.blob){
        let r = {photo: chatPhoto, pic: false};
        if(LIVE) r = await api(`/api/projects/${proj.id}/photo`, "DELETE");
        apply(r, proj.standalone || !r.photo ? "Картинка убрана" : "Картинка убрана — снова фото чата");
        close(); return;
      }
      if(draft.blob){
        if(!LIVE){
          const url = URL.createObjectURL(draft.blob);
          apply({photo: url, pic: true}, "Демо: картинка поставлена"); close(); return;
        }
        const fd = new FormData(); fd.append("file", draft.blob, "project.jpg");
        const r = await apiFetch(`/api/projects/${proj.id}/photo`, {method:"POST", headers:{"Authorization":authHeader()}, body:fd}, true);
        if(!r.ok) throw new Error(r.status + " " + await r.text());
        apply(await r.json(), "Картинка поставлена"); close(); return;
      }
    }catch(e){ draw(); fail(e); }
  };
  $("#picUrl").value = ""; draw(); sh.classList.add("open"); haptic();
}
$("#projPic").onclick = ()=>openPic(curProj);
$("#newProjBtn").onclick = ()=>projName(null);
$("#renameProj").onclick = ()=>projName(curProj);
/* New task typed by hand: lands in the tab that is open now, the checklist is posted to the chat */
const PERSONAL_NAME = "Без проекта";
const personalProj = () => ({id: LIVE ? me?.id : -1, name: PERSONAL_NAME, color: "var(--hint)", personal: true});
const isPersonal = p => !!p && (p.personal || (LIVE ? p.id === me?.id : p.id === -1));
function newTask(fromMenu=false){
  const sh = $("#newSheet"), ti = $("#newTitle"), de = $("#newDescr"), list = $("#newIList"), status = !fromMenu && STATUSES[filter] ? filter : "open";
  // the project: the open one; from the menu — the one picked last time («Без проекта» if none)
  const last = cloud.get("np");
  let proj = !fromMenu && curProj ? curProj
    : stack.at(-1) === "s-tasks" && curProj ? curProj
    : last && last !== "me" && projects.find(p=>String(p.id) === last) || personalProj();
  const dk = "dt_" + (fromMenu ? "new" : curProj.id); let dr = {}; try{ dr = JSON.parse(cloud.get(dk) || "{}"); }catch(_){}
  // old drafts kept items as one string «i»; new ones — as an array «items»
  const draftItems = Array.isArray(dr.items) ? dr.items : (dr.i || "").split("\n").map(s=>s.trim()).filter(Boolean);
  ti.value = dr.t || ""; de.value = dr.d || ""; ti.dataset.orig = de.dataset.orig = "";
  const readItems = ()=>[...list.querySelectorAll("textarea")].map(el=>el.value.trim().replace(/^(?:[-–—•*]|\d+[.)])\s+/, "")).filter(Boolean);
  const draft = keepDraft(dk, ()=>{
    const items = readItems(), d = de.value.trim();
    return ti.value.trim() || d || items.length ? JSON.stringify({t: ti.value, d: de.value, items}) : null;
  });
  const fit = el=>{ if(!el.offsetParent) return; el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px"; };
  const wireRow = (row, focus)=>{
    const inp = row.querySelector("textarea"), del = row.querySelector(".new-idel");
    const syncDel = ()=>{ const n = list.children.length; del.hidden = n <= 1 && !inp.value.trim(); };
    inp.oninput = ()=>{ fit(inp); syncDel(); draft(); };
    inp.onkeydown = e=>{
      if(e.key === "Enter" && !e.shiftKey){
        e.preventDefault();
        const next = row.nextElementSibling?.querySelector("textarea");
        if(next){ focusField(next); return; }
        addRow("", true);
      }
    };
    del.onclick = ()=>{
      if(list.children.length <= 1){ inp.value = ""; fit(inp); syncDel(); draft(); focusField(inp); return; }
      row.remove(); draft();
      [...list.querySelectorAll(".new-irow")].forEach(r=>{
        const d = r.querySelector(".new-idel"), t = r.querySelector("textarea");
        d.hidden = list.children.length <= 1 && !t.value.trim();
      });
    };
    fit(inp); syncDel();
    if(focus) focusField(inp);
  };
  const addRow = (text="", focus=false)=>{
    const row = document.createElement("div");
    row.className = "new-irow";
    row.innerHTML = `<textarea class="fld" rows="1" maxlength="500" placeholder="Что сделать"></textarea>`
      + `<button class="new-idel" type="button" aria-label="Убрать"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7L17 17"/><path d="M17 7L7 17"/></svg></button>`;
    row.querySelector("textarea").value = text;
    list.appendChild(row);
    wireRow(row, focus);
    return row;
  };
  list.innerHTML = "";
  (draftItems.length ? draftItems : [""]).forEach(t=>addRow(t, false));
  $("#newIAdd").onclick = ()=>{ haptic(); addRow("", true); };
  let who = null;
  const drawProj = ()=>{
    const pers = isPersonal(proj);
    $("#newProj").innerHTML = `<span class="pav-s" style="background:${proj.color || "var(--hint)"}">${pers ? "" : esc((proj.name || "?").replace(/[«»"]/g, "")[0] || "?")}</span>`
      + `<span class="wl">Проект<b>${esc(proj.name || "")}</b></span>${'<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 8L14 12L10 16"/></svg>'}`;
    $("#newWho").hidden = pers;   // without a project the task is simply yours
    $("#newNote").textContent = (status === "open" ? "" : `Задача сразу будет «${STATUSES[status].name}». `)
      + (pers ? "Задача будет только у вас, в «Моих задачах», — без чата." : "Чеклист появится в чате проекта.");
  };
  const drawWho = ()=>{
    const self = !who && status === "progress";   // straight into work → the server makes you the assignee
    $("#newWho").innerHTML = (who ? avSmall(who) : `<span class="pav-s nobody"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M4 21c0-3.9 3.6-7 8-7s8 3.1 8 7"/></svg></span>`)
      + `<span class="wl">Исполнитель<b>${who ? esc(who.name) + (who.id === me?.id ? " (вы)" : "") : self ? "Вы — задача сразу в работе" : "Не назначен"}</b></span>`
      + `<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 8L14 12L10 16"/></svg>`;
  };
  drawWho(); drawProj();
  $("#newWho").onclick = ()=>{ document.activeElement?.blur(); openAssign({pid: proj.id, assignee: who}, p=>{ who = p; drawWho(); }); };
  $("#newProj").onclick = ()=>{
    document.activeElement?.blur();
    pickProject(proj, p=>{ if(p.id === proj.id) return; proj = p; who = null; drawWho(); drawProj(); cloud.set("np", isPersonal(p) ? "me" : String(p.id)); });
  };
  sh.classList.add("open");
  ti.oninput = ()=>{ fit(ti); draft(); }; de.oninput = ()=>{ fit(de); draft(); }; fit(ti); fit(de);
  focusField(ti);
  const close = ()=>{ document.activeElement?.blur(); sh.classList.remove("open"); };
  $("#newCancel").onclick = ()=>{ cloud.set(dk, null); close(); };
  sh.onclick = e=>{ if(e.target === sh) close(); };
  ti.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); focusField(de); } };
  de.onkeydown = e=>{ if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); const first = list.querySelector("textarea"); if(first) focusField(first); } };
  const btn = $("#newSave");
  btn.onclick = async ()=>{
    const title = ti.value.trim().replace(/\s*\n\s*/g, " ");
    if(!title){ ti.focus(); return; }
    cloud.set(dk, null);
    const items = readItems(), descr = de.value.trim();
    btn.disabled = true;
    let t;
    try{
      if(LIVE){
        const full = await api(`/api/projects/${proj.id}/tasks`, "POST", {title, items, descr: descr || undefined, status, assignee: who?.id ?? null});
        t = fromApi(full); t.assignee_dm = full.assignee_dm;
      }
      else {
        const d = new Date(), M = ["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"];
        t = {id: Math.max(0, ...tasks.map(x=>x.id)) + 1, pid: proj.id, title, descr, author: "Вы", shots: [], videos: [], archived: false,
             date: `${d.getDate()} ${M[d.getMonth()]}, ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`,
             backlog: status === "backlog", progress: status === "progress", items: items.map(x=>({t:x, d:false, s:null})),
             assignee: who ? {id: who.id, name: who.name, photo: who.photo || null} : isPersonal(proj) ? {id: 1, name: "Вы"} : null,
             history: [{ts: Date.now()/1000, actor: "Вы", kind: "created", text: title}], comments: []};
      }
    }catch(e){ fail(e); return; }
    finally{ btn.disabled = false; }
    close(); haptic("medium");
    const changed = curProj?.id !== proj.id; curProj = proj;
    if(ppl.data) ppl.key = "";
    tasks = [{...t, done: 0, total: t.items.length, thumb: null}, ...tasks.filter(x=>x.id !== t.id)];
    renderTasks();
    if(t.assignee_dm === false) toast(`Задача создана. ${firstName(t.assignee.name)} ещё не запускал(а) бота — личные сообщения и напоминания не дойдут.`, "warn");
    else toast(!LIVE ? "Демо: задача создана" : t.assignee && t.assignee.id !== me?.id ? `Задача создана, ${firstName(t.assignee.name)} получит её в личку` : "Задача создана, чеклист отправлен в чат");
    curTask = t; opened = {id: t.id, group: groupOf(t)};
    if(changed && stack.includes("s-tasks")) toRoot();   // another project's list is open underneath
    go("s-task"); renderTask(true);
  };
}
/* Status: Открыта / В работе / В бэклоге — from a row swipe or the chip on the task screen */
function pickStatus(t){
  const sh = $("#statusSheet"), cur = pickOf(t);
  $("#statusTitle").textContent = t.title.length > 60 ? t.title.slice(0, 58) + "…" : t.title;
  $("#statusList").innerHTML = Object.entries(PICK).map(([k, v])=>`<button data-s="${k}" class="${k===cur?"on":""}">
      <span class="ic" style="background:${v.color}">${stIcon(k)}</span>
      <span>${v.name}<span class="sub">${v.sub}</span></span>
      <svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg></button>`).join("");
  sh.classList.add("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  $("#statusList").querySelectorAll("button").forEach(b=>b.onclick = ()=>{ sh.classList.remove("open"); setStatus(t, b.dataset.s); });
}
/* a change made from a row reaches every copy of the task: the project list, «Мои/Все задачи», the open task */
function everyCopy(id, fn){
  for(const x of new Set([tasks.find(t=>t.id === id), ppl.data?.tasks.find(t=>t.id === id), curTask?.id === id ? curTask : null])) if(x) fn(x);
  if(ppl.data) ppl.key = "";   // the next load redraws «Мои/Все задачи» even if the server says the same
}
const onPeople = () => stack.at(-1) === "s-people";
async function setStatus(t, to, undoable=true){
  if(pickOf(t) === to) return;
  const was = pickOf(t);
  let rep = {};
  if(to === "closed" && undoable){ rep = await askClose(t); if(!rep) return; }   // by hand: the link, the hours, the result in words
  if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", {status:to, ...rep}); }catch(e){ fail(e); return; } }
  haptic();
  const took = to === "progress" && !t.progress && !t.assignee && me?.id ? {id: me.id, name: me.name || "Вы", photo: null} : null;   // the server makes them the assignee
  const apply = x=>{ x.backlog = to === "backlog"; x.progress = to === "progress" || to === "review"; x.review = to === "review"; x.closed = to === "closed"; if(took && !x.assignee) x.assignee = took;
    if("hours" in rep) setRes(x, rep); };
  const row = tasks.find(x=>x.id === t.id);
  if(onPeople()){
    const w = document.querySelector(`#pplList .swipe[data-w="${t.id}"]`), before = pplMatch(t, ppl.f);
    everyCopy(t.id, apply); apply(t);
    if(w && before && !pplMatch(t, ppl.f)) await maskOut(w);
    renderPeople(ppl.mine && ppl.focus);
  } else if(stack.at(-1) === "s-task" && curTask?.id === t.id){        // on the task screen: Back will animate it out of the list
    apply(curTask); if(row && row !== curTask) apply(row); renderMeta();
  } else {
    const before = groupOf(t); apply(t);
    const w = document.querySelector(`.swipe[data-w="${t.id}"]`);
    if(w && groupOf(t) !== before && before === filter){ await maskOut(w); bumpTab(groupOf(t)); }
    renderTasks();
    const pc = ppl.data?.tasks.find(x=>x.id === t.id); if(pc && pc !== t) apply(pc);
  }
  toast({open:"Задача снова открыта", progress:"Задача в работе", review:"На проверке — автору придёт напоминание", backlog:"Отложено в бэклог", closed:"Задача закрыта"}[to], null,
    undoable ? {undo: ()=>setStatus(t, was, false)} : {});
}
/* Closing asks for a link to the result and the hours spent; «Без отчёта» closes without them.
   Resolves to the fields to send ({} — without), or null when the pop-up was dismissed (nothing is closed). */
const hoursTxt = h => `${String(Math.round(h * 100) / 100).replace(".", ",")} ч`;
function askClose(t, edit=false){
  return new Promise(res=>{
    const sh = $("#closeSheet"), url = $("#closeUrl"), hrs = $("#closeHours"), note = $("#closeNote");
    $("#closeTitle").textContent = edit ? "Результат" : "Закрыть задачу";
    $("#closeHint").hidden = edit; $("#closeBare").textContent = edit ? "Отмена" : "Без отчёта";
    url.value = t.result_url || ""; hrs.value = t.hours != null ? String(t.hours).replace(".", ",") : ""; note.value = t.result_note || "";
    let done = false;
    const finish = v=>{ if(done) return; done = true; document.activeElement?.blur(); sh.classList.remove("open"); res(v); };
    sh.onclick = e=>{ if(e.target === sh) finish(null); };
    $("#closeBare").onclick = ()=>finish(edit ? null : {});
    $("#closeOk").onclick = ()=>{
      const u = url.value.trim(), h = hrs.value.trim().replace(",", ".");
      if(h && !(+h >= 0 && +h <= 10000)){ toast("Часы — числом, например 1,5", "warn"); hrs.focus(); return; }
      if(u && !/^https?:\/\/|^[\w-]+(\.[\w-]+)+/.test(u)){ toast("Похоже, это не ссылка", "warn"); url.focus(); return; }
      finish({result_url: u ? (/^https?:\/\//.test(u) ? u : "https://" + u) : null, hours: h ? +h : null, result_note: note.value.trim() || null});
    };
    hrs.onkeydown = url.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); $("#closeOk").click(); } };
    sh.classList.add("open"); haptic();
  });
}
/* «Время» on the task screen: the hours spent so far, typed or added by the quick buttons; every change is logged */
const TIME_QUICK = [[.25,"+15 мин"],[.5,"+30 мин"],[1,"+1 ч"],[2,"+2 ч"]];
function openTime(t){
  const sh = $("#timeSheet"), inp = $("#timeInp");
  const num = ()=>{ const v = inp.value.trim().replace(",", "."); return v === "" ? 0 : +v; };
  const show = h => String(Math.round(h * 100) / 100).replace(".", ",");
  inp.value = t.hours ? show(t.hours) : "";
  $("#timeDel").style.display = t.hours ? "" : "none";
  $("#timeQuick").innerHTML = TIME_QUICK.map(([h, l])=>`<button type="button" data-h="${h}">${l}</button>`).join("");
  $("#timeQuick").querySelectorAll("button").forEach(b=>b.onclick = ()=>{
    const cur = num(); inp.value = show((isNaN(cur) ? 0 : cur) + +b.dataset.h); haptic();
  });
  const close = ()=>{ document.activeElement?.blur(); sh.classList.remove("open"); };
  const save = async h=>{
    if(h != null && !(h >= 0 && h <= 10000)){ toast("Часы — числом, например 1,5", "warn"); inp.focus(); return; }
    if(h === 0) h = null;
    const was = t.hours ?? null;
    close();
    if(h === was) return;
    if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", {hours: h}); }catch(e){ fail(e); return; } }
    everyCopy(t.id, x=>x.hours = h); t.hours = h;
    (t.history ||= []).unshift({ts: Date.now()/1000, actor: me?.name || "Вы", kind: "time", extra: {hours: h, from: was}});
    if(curTask?.id === t.id){ if(curTask !== t) curTask.hours = h; renderMeta(); renderHistory(); }
    haptic("medium"); toast(h == null ? "Время убрано" : `Потрачено: ${hoursTxt(h)}`);
  };
  $("#timeCancel").onclick = close;
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#timeDel").onclick = ()=>save(null);
  $("#timeSave").onclick = ()=>save(num());
  inp.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); $("#timeSave").click(); } };
  sh.classList.add("open"); haptic();
}
const setRes = (x, rep)=>{ x.result_url = rep.result_url; x.hours = rep.hours; x.result_note = rep.result_note; };
/* the «Результат» tag: what was done, the link (opens in a window of its own), the hours; «Изменить» — the same fields as at closing */
function showResult(t){
  const sh = $("#resSheet");
  $("#resBody").innerHTML = (t.result_note ? `<div class="tx">${linkify(t.result_note)}</div>` : "")
    + (t.result_url ? `<a class="res-ln" href="${esc(t.result_url)}" id="resLink"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L12 5.6"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/></svg><span>${esc(t.result_url.replace(/^https?:\/\/(www\.)?/, ""))}</span></a>` : "")
    + (t.hours != null ? `<div class="res-h">Потрачено: ${hoursTxt(t.hours)}</div>` : "");
  const open = u=>{ if(tg) tg.openLink(u); else window.open(u, "_blank", "noopener"); };
  const rl = $("#resLink"); if(rl) rl.onclick = e=>{ e.preventDefault(); open(t.result_url); };
  $("#resBody").querySelectorAll("a.ln").forEach(a=>a.onclick = e=>{ e.preventDefault(); open(a.dataset.url.replace(/&amp;/g,"&")); });
  const close = ()=>sh.classList.remove("open");
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#resClose").onclick = close;
  $("#resEdit").onclick = ()=>{ close(); setTimeout(()=>editResult(t), 200); };
  sh.classList.add("open"); haptic();
}
async function editResult(t){
  const rep = await askClose(t, true); if(!rep) return;
  if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", rep); }catch(e){ fail(e); return; } }
  everyCopy(t.id, x=>setRes(x, rep)); setRes(t, rep);
  if(curTask?.id === t.id){ if(curTask !== t) setRes(curTask, rep); renderMeta(); }
  haptic("medium"); toast("Результат сохранён");
}
/* The task closed itself (its last item ticked): the same questions; dismissed or «Без отчёта» — it simply stays closed */
async function reportAfterClose(t){
  if(!isClosed(t) || curTask?.id !== t.id || layerOpen()) return;
  const rep = await askClose(t);
  if(!rep || !("hours" in rep)) return;
  if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", rep); }catch(e){ fail(e); return; } }
  everyCopy(t.id, x=>setRes(x, rep)); setRes(t, rep);
  if(curTask?.id === t.id) renderMeta();
  toast("Отчёт сохранён");
}
/* «Закрыть» from a swipe to the right: the task gets the status «Закрыта», its items stay as they are */
async function closeTask(t){
  if(!canClose(t)) return;
  const was = pickOf(t), rep = await askClose(t); if(!rep) return;
  if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "PATCH", {status: "closed", ...rep}); }catch(e){ fail(e); return; } }
  haptic("medium");
  const w = document.querySelector(`${onPeople() ? "#pplList" : "#taskList"} .swipe[data-w="${t.id}"]`);
  if(w && (onPeople() ? ppl.f !== "closed" : filter !== "closed")) await maskOut(w);
  const shut = x=>{ x.closed = true; x.backlog = false; x.progress = false; if("hours" in rep) setRes(x, rep); };
  everyCopy(t.id, shut); shut(t);
  renderTasks(); if(ppl.data) renderPeople(onPeople() && ppl.mine && ppl.focus);
  if(!onPeople()) bumpTab("closed");
  toast("Задача закрыта", null, {undo: ()=>setStatus(t, was, false)});
}
function deleteTask(t){
  const go = async ok=>{
    if(!ok) return;
    if(LIVE){ try{ await api(`/api/tasks/${t.id}`, "DELETE"); }catch(e){ fail(e); return; } }
    haptic("heavy");
    const w = document.querySelector(`${onPeople() ? "#pplList" : "#taskList"} .swipe[data-w="${t.id}"]`); if(w) await collapse(w);
    tasks = tasks.filter(x=>x.id !== t.id); renderTasks();
    if(ppl.data){ ppl.data.tasks = ppl.data.tasks.filter(x=>x.id !== t.id); renderPeople(onPeople() && ppl.mine && ppl.focus); }
    toast(LIVE ? "Задача удалена, чеклист убран из чата" : "Демо: задача удалена");
  };
  const msg = `Удалить задачу «${t.title}»? Её нельзя будет восстановить.`;
  if(tg?.showConfirm) tg.showConfirm(msg, go); else go(confirm(msg));
}
document.querySelectorAll("#filter button").forEach(b=>b.onclick=()=>{
  if(filter===b.dataset.f) return;
  filter=b.dataset.f; haptic(); renderTasks(); cloud.set("f_"+curProj.id, filter === "open" ? null : filter);
  const l = $("#taskList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap");
});

/* ---------- History screen: one project or all of them, newest first, by day, with a filter ---------- */
const FEED_PAGE = 30, FEED_MAX = 200;   // FEED_MAX = the server's limit per request
const FEED_CHIPS = {all:"Все", closed:"Закрыты", open:"Снова открыты", progress:"В работе", review:"На проверке", backlog:"Бэклог", created:"Созданы", archive:"Архив", items:"Пункты"};
const FEED_EMPTY = {all:"Пока ничего не менялось", closed:"Пока ни одна задача не закрыта", open:"Пока ни одну задачу не открывали заново",
  progress:"Пока ничего не брали в работу", review:"Пока ничего не отправляли на проверку", backlog:"Пока ничего не откладывали в бэклог", created:"Пока задач не создавали", archive:"В архив пока ничего не убирали", items:"Пока ни один пункт не отмечали"};
const toIs = (e, to) => e.kind === "status" && e.extra?.to === to;
const FEED_MATCH = {   // demo mode only: live data is filtered by the server
  all: e=>["created","closed","reopened","status","archived","unarchived"].includes(e.kind), closed: e=>e.kind === "closed",
  open: e=>e.kind === "reopened" || toIs(e,"open"), progress: e=>toIs(e,"progress"), review: e=>toIs(e,"review"), backlog: e=>toIs(e,"backlog"),
  created: e=>e.kind === "created", archive: e=>["archived","unarchived"].includes(e.kind), items: e=>["done","undone","done_all"].includes(e.kind),
};
let feed = {pid:null /* null = all projects */, f:"all", rows:[], next:null, loading:false, failed:false, seq:0};
$("#feedBar").innerHTML = Object.entries(FEED_CHIPS).map(([k,v])=>`<button data-c="${k}"><span class="l">${v}</span></button>`).join("");
const feedChips = ()=>document.querySelectorAll("#feedBar button").forEach(b=>b.classList.toggle("on", b.dataset.c === feed.f));
$("#feedBar").onclick = e=>{
  const c = e.target.closest("[data-c]")?.dataset.c;
  if(!c || c === feed.f) return;
  feed.f = c; haptic(); feedChips(); cloud.set("hf", c === "all" ? null : c);
  // scroll only the chip bar: scrollIntoView would also shift .screens sideways and show the hidden task screen
  const bar = $("#feedBar"), cr = e.target.closest("[data-c]").getBoundingClientRect(), br = bar.getBoundingClientRect();
  bar.scrollBy({left: cr.left + cr.width/2 - (br.left + br.width/2), behavior:"smooth"});
  loadFeed();
};
$("#feed").onclick = e=>{
  if(e.target.closest("#feedRetry")) return loadFeed();
  if(e.target.closest("#feedMore")) return loadFeed(true);
  const row = e.target.closest("[data-t]"); if(!row) return;
  if(feed.pid === null) curProj = projects.find(p=>p.id == row.dataset.pid) || curProj;   // the task screen names its project
  openTask(+row.dataset.t);
};
function openFeed(pid, name){
  const f = cloud.get("hf");
  feed = {...feed, pid, f: FEED_CHIPS[f] ? f : "all", rows:[], next:null};
  $("#histSub").textContent = name;
  feedChips();
  const chip = $("#feedBar").querySelector(".on"); $("#feedBar").scrollLeft = chip ? Math.max(0, chip.offsetLeft - 16) : 0;
  go("s-history"); loadFeed();
}
$("#histProj").onclick = ()=>openFeed(curProj.id, curProj.name);

/* ---------- «Уведомления»: what other people did in my tasks (made by me, assigned to me, touched by me), by section ----------
   Read is per notification: only a tap reads it, a swipe right makes it unread again, «Прочитать все» — the open section. */
const NOTIF = {all:"Все", q:"Вопросы", r:"На проверку", c:"Комментарии", s:"Статусы", a:"Исполнитель и срок", e:"Изменения"};
const NOTIF_KINDS = {q:["asked","answered","reasked"], r:["status"], c:["commented"], s:["status","closed","reopened","report","archived","unarchived"],
  a:["assigned","due"], e:["created","renamed","descr","item_added","items_added","done_all","shot_added","linked"]};
const NOTIF_EMPTY = {all:"Здесь появится то, что другие делают в ваших задачах: вопросы, комментарии, статусы",
  q:"Вопросов по вашим задачам пока нет", r:"На проверку ваши задачи пока не отправляли", c:"Комментариев к вашим задачам пока нет", s:"Статусы ваших задач пока не меняли",
  a:"Исполнителя и срок в ваших задачах пока не меняли", e:"В ваших задачах пока ничего не меняли"};
const NOTIF_TALK = {asked:"q", answered:"q", reasked:"q", commented:"c"};   // a tap on these opens the task at its tab
const NOTIF_AT = 72;   // px: pulled right past it — read ↔ unread
const onReview = e => e.kind === "status" && (e.extra?.to === "review" || e.extra?.from === "review");   // sent for a check, or back from it
const secOf = e => onReview(e) ? "r" : Object.keys(NOTIF_KINDS).find(k=>k !== "r" && NOTIF_KINDS[k].includes(e.kind));
let notif = {f:"all", rows:[], next:null, loading:false, failed:false, seq:0, unread:{}, demoRead:null};
const notifTotal = () => Object.keys(NOTIF_KINDS).reduce((n, k)=>n + (notif.unread[k] || 0), 0);
const cnt = n => n > 99 ? "99+" : String(n);
function renderNotifBadges(){
  const tot = notifTotal(), tb = $("#tabN");
  tb.hidden = !tot; tb.textContent = cnt(tot);
  $("#tabbar [data-tab=notif]").setAttribute("aria-label", tot ? `Уведомления, новых: ${tot}` : "Уведомления");
  $("#notifBar").innerHTML = Object.entries(NOTIF).map(([k, v])=>{ const n = k === "all" ? tot : notif.unread[k] || 0;
    return `<button data-c="${k}" class="${k === notif.f ? "on" : ""}"><span class="l">${v}</span>${n ? `<span class="n u">${cnt(n)}</span>` : ""}</button>`; }).join("");
  $("#readAll").hidden = !(notif.f === "all" ? tot : notif.unread[notif.f]);
}
/* demo: the tasks' own history, without «Вы»; the newest two of each section are unread */
function demoNotif(){
  const all = tasks.flatMap(t=>[...(t.history||[]).map(e=>({...e})),
      ...(t.comments||[]).map(c=>({ts:c.ts, actor:c.author, kind:c.question ? "asked" : "commented", text:c.text}))]
    .map(e=>({...e, task_id:t.id, task:t.title, pid:t.pid, project:projects.find(p=>p.id===t.pid)?.name})))
    .filter(e=>e.actor !== "Вы" && secOf(e)).sort((a,b)=>b.ts - a.ts).map((e,i,a)=>({...e, id:a.length - i}));
  if(!notif.demoRead){ notif.demoRead = {}; const n = {}; for(const e of all){ const k = secOf(e); n[k] = (n[k] || 0) + 1; notif.demoRead[e.id] = n[k] > 2; } }
  all.forEach(e=>e.unread = !notif.demoRead[e.id]);
  const unread = {}; for(const k in NOTIF_KINDS) unread[k] = all.filter(e=>secOf(e) === k && e.unread).length;
  return {events: notif.f === "all" ? all : all.filter(e=>secOf(e) === notif.f), next:null, unread};
}
async function loadNotifCount(){
  try{
    const r = LIVE ? await api("/api/notifications/unread", "GET", undefined, true) : demoNotif();
    notif.unread = r.unread; renderNotifBadges();
  }catch(_){ /* offline: the next try */ }
}
function openNotif(){
  const first = ["q","r","c","s","a","e"].find(k=>notif.unread[k]);   // the most important unread section first: questions, then checks
  notif = {...notif, f: first || "all", rows:[], next:null};
  renderNotifBadges(); $("#notifBar").scrollLeft = 0;
  go("s-notif"); loadNotif();
}
const notifUrl = (limit, before) => `/api/notifications?section=${notif.f}&limit=${limit}${before ? "&before="+before : ""}`;
async function loadNotif(more=false, quiet=false){
  if(quiet && document.querySelector("#notifList .nrow.swiping")) return;   // never redraw under a finger
  const my = ++notif.seq, f = notif.f, had = more ? notif.rows.length : 0;
  if(!quiet){ notif.loading = true; notif.failed = false; if(!more) notif.rows = []; renderNotif(); }
  try{
    const r = LIVE ? await api(notifUrl(quiet ? Math.max(FEED_PAGE, notif.rows.length) : FEED_PAGE, more ? notif.next : null), "GET", undefined, quiet)
                   : (await sleep(quiet || more ? 0 : DEMO_DELAY * .6), demoNotif());
    if(my !== notif.seq || f !== notif.f) return;
    const key = a => JSON.stringify(a.map(e=>[e.id, e.unread]));
    notif.unread = r.unread; renderNotifBadges();
    if(quiet && key(r.events) === key(notif.rows)) return;
    notif.rows = more ? [...notif.rows, ...r.events] : r.events; notif.next = r.next;
  }catch(e){
    if(my !== notif.seq || quiet) return;
    console.error(e); notif.failed = true; if(more) toast("Не загрузилось, попробуйте ещё раз");
  }
  notif.loading = false;
  renderNotif(more ? had : quiet ? undefined : 0);
}
/* one notification read (tapped) or unread again (swiped); the numbers change at once, the server's answer settles them */
async function setNotifRead(e, read){
  if(!!e.unread === !read) return;
  const k = secOf(e);
  e.unread = !read; notif.unread[k] = Math.max(0, (notif.unread[k] || 0) + (read ? -1 : 1));
  renderNotifBadges(); paintNotifRow(e);
  try{
    if(LIVE) notif.unread = (await api(`/api/notifications/${e.id}/read`, "POST", {read}, true)).unread;
    else { notif.demoRead[e.id] = read; notif.unread = demoNotif().unread; }
    renderNotifBadges();
  }catch(err){ e.unread = read; notif.unread[k] += read ? 1 : -1; renderNotifBadges(); paintNotifRow(e); fail(err); }
}
/* «Прочитать все» at the title: the section that is open («Все» — every one); the server stops at its newest event */
async function readAllNotif(){
  const f = notif.f, top = 2 ** 40;
  const was = notif.rows.filter(e=>e.unread);
  was.forEach(e=>{ e.unread = false; paintNotifRow(e); });
  for(const k of f === "all" ? Object.keys(NOTIF_KINDS) : [f]) notif.unread[k] = 0;
  renderNotifBadges(); haptic("medium");
  try{
    if(LIVE) notif.unread = (await api("/api/notifications/seen", "POST", {section: f, upto: top}, true)).unread;
    else { for(const e of demoNotif().events) notif.demoRead[e.id] = true; notif.unread = demoNotif().unread; }
    renderNotifBadges();
  }catch(err){ fail(err); loadNotif(false, true); }
}
const NM_READ = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>`;
const NM_UNREAD = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.5" fill="currentColor"/></svg>`;
const nmark = e => `<span class="nmark${e.unread ? "" : " un"}">${e.unread ? NM_READ + "Прочитано" : NM_UNREAD + "Не прочитано"}</span>`;
function paintNotifRow(e){
  const row = $(`#notifList .nrow[data-id="${e.id}"]`); if(!row) return;
  row.classList.toggle("unread", !!e.unread);
  row.querySelector(".ud")?.remove();
  if(e.unread) row.insertAdjacentHTML("beforeend", `<i class="ud" aria-label="Новое"></i>`);
  row.querySelector(".nmark").outerHTML = nmark(e);
}
function renderNotif(enterFrom){
  const box = $("#notifList");
  if(notif.loading && !notif.rows.length){
    box.innerHTML = `<div class="sec-label">${skLine("64px",11)}</div><div class="hist">${[0,1,2,3].map(i=>`<div class="ev" style="opacity:${1-i*.2}"><div class="av sk"></div><div class="body">${skLine("72%",13)}${skLine("46%",11,"margin-top:7px")}</div></div>`).join("")}</div>`;
    return;
  }
  if(!notif.rows.length){
    box.innerHTML = notif.failed
      ? `<div class="empty">Не удалось загрузить уведомления<br><button id="notifRetry" style="color:var(--link);margin-top:8px">Повторить</button></div>`
      : `<div class="empty">${NOTIF_EMPTY[notif.f]}</div>`;
    return;
  }
  const days = [];
  notif.rows.forEach(e=>{ const l = dayLabel(e.ts); if(days.at(-1)?.l !== l) days.push({l, rows:[]}); days.at(-1).rows.push(e); });
  const hm = ts => new Date(ts*1000).toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});
  let n = 0;
  const enter = () => { const k = n++ - enterFrom; return enterFrom !== undefined && k >= 0 && k < 12 ? ` enter" style="--i:${k}` : ""; };
  box.innerHTML = days.map(d=>`<div class="sec-label"><span class="mono">${esc(d.l)}</span></div><div class="hist nlist">${d.rows.map(e=>
    `<div class="nw"><div class="ev nrow${e.unread ? " unread" : ""}${enter()}" role="button" tabindex="0" data-id="${e.id}" data-t="${e.task_id}" data-pid="${e.pid}" data-k="${e.kind}">${nmark(e)}${evAvatar(e)}<div class="body"><div class="tx">${evText(e)}</div><div class="ft">${esc(e.task)}</div><div class="tm">${e.project ? esc(e.project) + " · " : ""}${esc(e.actor)} · ${hm(e.ts)}</div></div>${e.unread ? `<i class="ud" aria-label="Новое"></i>` : ""}</div></div>`
  ).join("")}</div>`).join("")
    + (notif.next ? `<button class="link-row" id="notifMore">${notif.loading ? "Загрузка…" : "Показать ещё"}</button>` : "");
  box.querySelectorAll(".nrow").forEach(row=>{
    const e = notif.rows.find(x=>x.id === +row.dataset.id);
    initShutter(row, 0, ()=>openNotifRow(e), {at: NOTIF_AT, fire: ()=>{ haptic("medium"); setNotifRead(e, !!e.unread); }});
  });
}
async function openNotifRow(e){
  if(!e) return;
  setNotifRead(e, true);   // only a tap reads it
  curProj = projects.find(p=>p.id == e.pid) || curProj;
  const id = e.task_id, tab = NOTIF_TALK[e.kind];
  await openTask(id);
  if(tab && curTask?.id === id && stack.at(-1) === "s-task"){ talkTab = tab; renderTalk(); $("#talkTabs").scrollIntoView({block:"start", behavior:"smooth"}); }
}
$("#notifBar").onclick = e=>{
  const b = e.target.closest("[data-c]"), c = b?.dataset.c;
  if(!c || c === notif.f) return;
  notif.f = c; haptic(); renderNotifBadges();
  const bar = $("#notifBar"), cr = b.getBoundingClientRect(), br = bar.getBoundingClientRect();
  bar.scrollBy({left: cr.left + cr.width/2 - (br.left + br.width/2), behavior:"smooth"});
  loadNotif();
};
$("#notifList").onclick = e=>{
  if(e.target.closest("#notifRetry")) return loadNotif();
  if(e.target.closest("#notifMore")) return loadNotif(true);
};
$("#notifList").onkeydown = e=>{ const r = e.target.closest?.(".nrow"); if(r && (e.key === "Enter" || e.key === " ")){ e.preventDefault(); openNotifRow(notif.rows.find(x=>x.id === +r.dataset.id)); } };
$("#readAll").onclick = ()=>readAllNotif();
$("#allHist").onclick = ()=>openFeed(null, "Все проекты");
$("#accessBtn").onclick = ()=>openAccess(curProj);

/* ---------- «Задачи людей»: mine and everyone else's, across all projects ---------- */
const PPL_FILTERS = {active:"Все незакрытые", progress:"В работе", review:"На проверке", open:"Открытые", backlog:"Бэклог", closed:"Закрытые"};
let ppl = {data:null, who:null /* user id, 0 = nobody assigned, -1 = everyone */, f:"active", key:"", mine:false, pid:null /* null = all projects */, focus:false /* «Мои задачи» folded into a stack */};
const PPL_DOT = {active:"var(--link)", progress:"var(--st-progress)", review:"var(--st-review)", open:"var(--st-open)", backlog:"var(--st-backlog)", closed:"var(--st-closed)"};
const pplMatch = (t, f) => { const g = groupOf(t); return f === "active" ? g === "open" || g === "progress" : f === "review" ? g === "progress" && !!t.review : g === f; };
function demoPeople(){
  const ts = tasks.filter(t=>!t.archived).map(t=>{ const p = projects.find(x=>x.id === t.pid) || (t.pid === -1 ? personalProj() : {});
    return {...t, project:p.name, color:p.color, done:done(t), total:total(t), assignee: t.assignee || (t.id % 3 === 0 ? {id:1, name:"Вы"} : t.id % 3 === 1 ? {id:3, name:"Анна Смирнова"} : null)}; });
  const people = {1:{id:1, name:"Вы", active:0, total:0}};
  for(const t of ts) if(t.assignee){ const w = people[t.assignee.id] ||= {...t.assignee, active:0, total:0}; w.total++; if(pplMatch(t, "active")) w.active++; }
  return {me:1, people:Object.values(people).sort((a,b)=>(a.id!==1)-(b.id!==1) || b.active-a.active), tasks:ts};
}
async function loadPeople(quiet=false){
  let d;
  try{ d = LIVE ? await api("/api/people/tasks", "GET", undefined, quiet) : (quiet ? ppl.data : (await sleep(250), demoPeople())); }
  catch(e){ if(!quiet){ $("#pplList").innerHTML = `<div class="empty">Не удалось загрузить. <button class="eb-link" id="pplRetry">Ещё раз</button></div>`; $("#pplRetry").onclick = ()=>loadPeople(); } return; }
  if(stack.at(-1) !== "s-people" && quiet) return;
  const key = JSON.stringify([d.people.map(p=>[p.id, p.name, p.active, unsigned(p.photo)]), d.tasks.map(t=>[t.id, t.title, groupOf(t), t.assignee?.id, done(t), total(t), t.due?.at, t.rank])]);
  ppl.data = d;
  if(ppl.mine) ppl.who = d.me;
  else if(ppl.who === null || ppl.who === d.me || (ppl.who > 0 && !d.people.some(p=>p.id === ppl.who))) ppl.who = -1;
  if(key !== ppl.key){ ppl.key = key; renderPeople(); }
}
function renderPeople(stackIn=false){
  const d = ppl.data; if(!d) return;
  unghost($("#pplList")); unghost($("#pplBar")); unghost($("#pplFilter"));
  if(ppl.pid !== null && !d.tasks.some(t=>t.pid === ppl.pid)) ppl.pid = null;   // the project has nothing visible any more
  const base = ppl.pid === null ? d.tasks : d.tasks.filter(t=>t.pid === ppl.pid);
  const pp = $("#pplProj"), pr = ppl.pid === null ? null : base[0];
  pp.classList.toggle("set", !!pr);
  pp.querySelector("i").style.background = pr ? (pr.color || "var(--hint)") : "";
  pp.querySelector("span").textContent = pr ? pr.project : "Все проекты";
  const actOf = {}; for(const t of base) if(t.assignee && pplMatch(t, "active")) actOf[t.assignee.id] = (actOf[t.assignee.id] || 0) + 1;
  const nobody = base.filter(t=>!t.assignee), nobodyActive = nobody.filter(t=>pplMatch(t, "active")).length;
  const av = (p, cls="") => `<span class="pav ${cls}" style="${cls?"":`background:${avColor(p.name||"?")}`}"><span class="ph">${cls.includes("all") ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M2 21c0-3.3 3.1-6 7-6s7 2.7 7 6"/><path d="M16 3.3a4 4 0 0 1 0 7.4"/><path d="M22 21c0-2.6-1.9-4.8-4.5-5.6"/></svg>` : cls ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M4 21c0-3.9 3.6-7 8-7s8 3.1 8 7"/></svg>` : esc((p.name||"?").trim()[0]||"?")}${p.photo?`<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">`:""}</span>${p.active ? `<span class="cnt"><b>${p.active}</b></span>` : ""}</span>`;
  const allActive = base.filter(t=>pplMatch(t, "active")).length;
  $("#pplTitle").textContent = ppl.mine ? "Мои задачи" : "Все задачи";
  $("#pplAll").hidden = !ppl.mine;
  $("#pplBar").hidden = ppl.mine;
  $("#pplBar").innerHTML = `<button data-u="-1" class="${ppl.who===-1?"on":""}">${av({active:allActive}, "nobody all")}<span class="nm">Все</span></button>`
    + d.people.filter(p=>p.id !== d.me).map(p=>`<button data-u="${p.id}" class="${p.id===ppl.who?"on":""}">${av({...p, active: actOf[p.id] || 0})}<span class="nm">${esc(firstName(p.name))}</span></button>`).join("")
    + (nobody.length ? `<button data-u="0" class="${ppl.who===0?"on":""}">${av({active:nobodyActive}, "nobody")}<span class="nm">Ничьи</span></button>` : "");
  const mine = ppl.who === -1 ? base : base.filter(t=>(t.assignee?.id ?? 0) === ppl.who);
  const ps = $("#pplSt"); ps.classList.toggle("set", ppl.f !== "active");
  ps.querySelector("i").style.background = PPL_DOT[ppl.f]; ps.querySelector("span").textContent = PPL_FILTERS[ppl.f];
  ps.querySelector(".n").textContent = mine.filter(t=>pplMatch(t, ppl.f)).length;
  const list = mine.filter(t=>pplMatch(t, ppl.f));
  const fl = $("#pplFocus"); fl.hidden = !ppl.mine || !list.length;
  fl.textContent = ppl.focus ? "Развернуть" : "Фокус"; fl.setAttribute("aria-pressed", ppl.focus);
  const who = ppl.who === d.me ? "вас" : ppl.who === 0 ? "" : esc(firstName(d.people.find(p=>p.id===ppl.who)?.name));
  if(!list.length){
    $("#pplList").innerHTML = ppl.who === -1 ? `<div class="empty">Здесь пусто.</div>`
      : `<div class="empty">${ppl.who === 0 ? "Здесь задачи, у которых нет исполнителя." : `У ${who} здесь пусто.`}<br>Исполнителя назначают на экране задачи — кнопка «Исполнитель».</div>`;
    return;
  }
  // grouped by project, projects in the order of the main list; inside — the project's own order (drag to change it)
  const byP = new Map();
  for(const t of list){ if(!byP.has(t.pid)) byP.set(t.pid, []); byP.get(t.pid).push(t); }
  const order = [...byP.keys()].sort((a,b)=>{ const i = id => { const k = projects.findIndex(p=>p.id === id); return k < 0 ? 1e9 : k; }; return i(a) - i(b); });
  for(const pid of order) byP.get(pid).sort((a,b)=>rk(b) - rk(a) || b.id - a.id);
  const row = t=>{
      const d = done(t), n = total(t), pct = n ? d/n*100 : 0, st = stOf(t), th = t.thumb;
      const project = ppl.mine && !th && t.project !== PERSONAL_NAME && projects.find(p=>p.id === t.pid && !isPersonal(p));
      return `<div class="swipe" data-w="${t.id}">${rowActs(t)}<button class="task" data-t="${t.id}" data-pid="${t.pid}">${rowDone(t)}
        <div class="thumb" style="${th?"":"background:var(--muted);display:grid;place-items:center;color:var(--hint)"}">${th ? shotHTML(th) : project ? avSmall(project, project.color) : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path fill-rule="evenodd" clip-rule="evenodd" d="M16.002 20.999H7.998C5.236 20.999 2.996 18.748 2.996 16.005V7.981C2.996 5.238 5.236 2.987 7.998 2.987H16.002C18.764 2.987 21.004 5.238 21.004 7.981V16.005C21.004 18.748 18.764 20.999 16.002 20.999Z"/><path d="M14.268 11.011L11.434 13.835L9.733 12.116"/></svg>`}</div>
        <div class="main"><div class="t">${esc(t.title)}</div>
          <div class="meta">${statusBadge(t)}${t.due ? dueBadge(t) : ""}${ppl.who === -1 && t.assignee ? whoBadge(t.assignee) : ""}${qBadge(t)}${itemsBadge(d, n)}${ppl.mine && ppl.pid === null && t.project ? `<span class="t-proj">${esc(t.project)}</span>` : ""}</div>
        </div></button></div>`;
  };
  const head = (p0, n) => `<div class="ppl-grp"><i style="background:${p0.color||"var(--hint)"}"></i>${esc(p0.project||"")}<span>${n}</span></div>`;
  // «Мои задачи»: one list, no project headers, in your own order (dragged ones where you put them, new ones on top)
  const flat = myOrder(order.flatMap(pid=>byP.get(pid)));
  if(ppl.mine && ppl.focus){   // one task at a time: the first of the list on top, the rest stacked under it
    const rest = flat.length - 1;
    $("#pplList").innerHTML = `<div class="stack${stackIn ? " in" : ""}"><div class="grid">${row(flat[0])}</div>${"<i></i>".repeat(Math.min(rest, 2))}</div>`
      + (rest ? `<button class="stack-more" type="button" data-unstack>Ещё ${rest} ${plural(rest, "задача", "задачи", "задач")} в стопке</button>` : "");
  }
  else if(ppl.mine) $("#pplList").innerHTML = `<div class="grid">${flat.map(row).join("")}</div>`;
  else $("#pplList").innerHTML = order.map(pid=>{ const ts = byP.get(pid); return head(ts[0], ts.length) + `<div class="grid">${ts.map(row).join("")}</div>`; }).join("");
  $("#pplList").querySelectorAll(".grid .swipe").forEach(initSwipe);
  markSel();
}
/* long press on a row of «Мои/Все задачи» lifts it; it moves within its project, and the project keeps the new order */
/* «Мои задачи» keeps a personal order (Telegram cloud storage: yours on every device), apart from the projects' order */
const myIds = () => { try{ return JSON.parse(cloud.get("mo") || "[]"); }catch(_){ return []; } };
function myOrder(list){
  const pos = new Map(myIds().map((id, k)=>[id, k]));
  if(!pos.size) return list;
  return list.map((t, k)=>[t, k]).sort((a, b)=>{
    const pa = pos.get(a[0].id), pb = pos.get(b[0].id);
    if(pa === undefined || pb === undefined) return (pa === undefined ? -1 : 1) - (pb === undefined ? -1 : 1) || a[1] - b[1];   // not placed yet: on top
    return pa - pb;
  }).map(x=>x[0]);
}
function myDrop(rows, from, to){
  const ids = rows.map(r=>+r.dataset.w), [m] = ids.splice(from, 1); ids.splice(to, 0, m);
  const rest = myIds().filter(id=>!ids.includes(id));   // tasks not shown now (other filter) keep their places after these
  cloud.set("mo", JSON.stringify([...ids, ...rest].slice(0, 400)));
  renderPeople();
}
function pplDrop(rows, from, to){
  const group = rows.map(r=>ppl.data.tasks.find(t=>t.id === +r.querySelector(".task").dataset.t)).filter(Boolean);
  if(group.length !== rows.length) return;
  const pid = group[0].pid, ranks = group.map(rk).sort((a,b)=>b-a);
  const [m] = group.splice(from, 1); group.splice(to, 0, m);
  group.forEach((t,i)=>t.rank = ranks[i]);
  for(const t of tasks) if(t.pid === pid){ const g = group.find(x=>x.id === t.id); if(g) t.rank = g.rank; }   // the project's list, if it is loaded
  renderPeople();
  if(LIVE) api(`/api/projects/${pid}/order`, "PUT", {task_ids: group.map(t=>t.id)}).catch(e=>{ fail(e); loadPeople(); });
}
$("#pplFocus").onclick = ()=>toggleFocus();
$("#pplAll").onclick = ()=>{ haptic(); ppl.fromMine = true; openPeople(false, "slide-fwd"); };   // a step forward: Back returns to «Мои задачи»
function toggleFocus(){
  ppl.focus = !ppl.focus; haptic(); cloud.set("pfocus", ppl.focus ? "1" : null); renderPeople(true);
  if(!ppl.focus){ const l = $("#pplList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap"); }
};
$("#pplBar").onclick = e=>{
  const b = e.target.closest("[data-u]"); if(!b) return;
  const u = +b.dataset.u; if(u === ppl.who) return;
  ppl.who = u; haptic(); renderPeople(); updTabs();
  const bar = $("#pplBar"), cr = b.getBoundingClientRect(), br = bar.getBoundingClientRect();
  bar.scrollBy({left: cr.left + cr.width/2 - (br.left + br.width/2), behavior:"smooth"});
  const l = $("#pplList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap");
};
/* status drop-down: the same sheet as the project one, with counts for the person and project chosen */
$("#pplSt").onclick = ()=>{
  const d = ppl.data; if(!d) return;
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  const mine = d.tasks.filter(t=>(ppl.pid === null || t.pid === ppl.pid) && (ppl.who === -1 || (t.assignee?.id ?? 0) === ppl.who));
  box.innerHTML = Object.entries(PPL_FILTERS).map(([k, v])=>`<button data-c="${k}" class="${k === ppl.f ? "on" : ""}"><span class="pp-dot" style="background:${PPL_DOT[k]}"></span><span class="nm">${v}</span><span class="cnt">${mine.filter(t=>pplMatch(t, k)).length}</span>${ck}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{
    sh.classList.remove("open");
    const c = b.dataset.c; if(c === ppl.f) return;
    ppl.f = c; haptic(); cloud.set("pf", c === "active" ? null : c); renderPeople();
    const l = $("#pplList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap");
  });
  $("#projSheetTitle").textContent = "Статус";
  sh.classList.add("open"); haptic();
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
};
$("#pplProj").onclick = ()=>{
  const d = ppl.data; if(!d) return;
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  const of = pid => d.tasks.filter(t=>(pid === null || t.pid === pid) && (ppl.who === -1 || (t.assignee?.id ?? 0) === ppl.who) && pplMatch(t, ppl.f)).length;
  const ps = new Map(); for(const t of d.tasks) if(!ps.has(t.pid)) ps.set(t.pid, {id:t.pid, name:t.project, color:t.color});
  const order = [...ps.values()].sort((a,b)=>{ const i = p => { const k = projects.findIndex(x=>x.id === p.id); return k < 0 ? 1e9 : k; }; return i(a) - i(b); });
  const row = (pid, name, color) => `<button data-pid="${pid ?? ""}" class="${pid === ppl.pid ? "on" : ""}"><span class="pp-dot" style="background:${color}"></span><span class="nm">${esc(name)}</span><span class="cnt">${of(pid)}</span>${ck}</button>`;
  box.innerHTML = row(null, "Все проекты", "var(--hint)") + order.map(p=>row(p.id, p.name || "", p.color || "var(--hint)")).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{
    sh.classList.remove("open");
    const pid = b.dataset.pid === "" ? null : +b.dataset.pid; if(pid === ppl.pid) return;
    ppl.pid = pid; haptic(); cloud.set("pp", pid === null ? null : String(pid)); renderPeople();
    const l = $("#pplList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap");
  });
  $("#projSheetTitle").textContent = "Проект";
  sh.classList.add("open"); haptic();
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
};
/* «Новая задача» in the bottom menu: inside a project — straight away, elsewhere — pick the project first */
function newTaskFromMenu(){
  if(stack.at(-1) === "s-tasks" && curProj){ newTask(); return; }
  newTask(true);
}
/* the project picker of the new task: the projects and «Без проекта» */
function pickProject(cur, onPick){
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  const all = [...projects, personalProj()];
  $("#projSheetTitle").textContent = "Проект";
  box.innerHTML = all.map((p, k)=>`<button data-k="${k}" class="${(isPersonal(p) ? isPersonal(cur) : p.id === cur.id) ? "on" : ""}"><span class="pp-dot" style="background:${p.color || "var(--hint)"}"></span><span class="nm">${esc(p.name || "")}</span>${ck}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ sh.classList.remove("open"); haptic(); onPick(all[+b.dataset.k]); });
  sh.classList.add("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
}
$("#pplList").onclick = e=>{
  if(e.target.closest("[data-unstack], .stack>i")){ toggleFocus(); return; }   // the rest of the stack → the whole list
  const row = e.target.closest("[data-t]"); if(!row) return;
  curProj = projects.find(p=>p.id == row.dataset.pid) || curProj;   // the task screen names its project
  openTask(+row.dataset.t);
};
function openPeople(mine, anim){
  const f = cloud.get("pf"); ppl.f = PPL_FILTERS[f] ? f : "active";
  const pp = cloud.get("pp"); ppl.pid = pp ? +pp : null;
  ppl.focus = cloud.get("pfocus") === "1";
  ppl.mine = mine; if(ppl.data) ppl.who = mine ? ppl.data.me : -1;
  if(ppl.data) renderPeople(); else ghostPeople();
  if(stack.at(-1) === "s-people"){
    if(anim){ $("#s-people").scrollTop = 0; screenAnim($("#s-people"), anim); }
    else { const l = $("#pplList"); l.classList.remove("fade-swap"); void l.offsetWidth; l.classList.add("fade-swap"); $("#s-people").scrollTo({top:0, behavior:"smooth"}); }
  }
  else go("s-people");
  loadPeople(); updTabs();
}
/* The menu at the bottom: each tab starts from the main screen, so «Назад» leads home */
function toRoot(){
  while(stack.length > 1){ const el = document.getElementById(stack.pop()); el.classList.add("off-right"); el.classList.remove("off-left"); }
  if(curTask) openSeq++;
  if(ghosting) ghostOff();
  stopVoice();
}
/* A tab of the menu is switched, not navigated to: the old screen does not slide back while the new one slides in
   (that read as a reversed animation) — screens change in place and the new one fades up */
function tabSwitch(open){
  const root = document.documentElement;
  root.classList.add("tab-swap");
  open();
  const top = document.getElementById(stack.at(-1));
  void top.offsetWidth;                  // the screens take their places without a transition
  root.classList.remove("tab-swap");
  screenAnim(top, "tab-in");
}
/* one-off entrance of a screen that is already in place: "tab-in" (a tab), "slide-fwd" / "slide-back" (a step inside it) */
function screenAnim(el, cls){
  el.classList.remove("tab-in", "slide-fwd", "slide-back"); void el.offsetWidth; el.classList.add(cls);
  el.addEventListener("animationend", e=>{ if(e.target === el) el.classList.remove(cls); }, {once: true});
}
$("#tabbar").onclick = e=>{
  const tab = e.target.closest("[data-tab]")?.dataset.tab; if(!tab) return;
  haptic();
  if(tab === "new"){ newTaskFromMenu(); return; }
  if(tab === "proj"){ if(stack.length === 1){ $("#s-projects").scrollTo({top:0, behavior:"smooth"}); return; } tabSwitch(()=>{ toRoot(); document.getElementById("s-projects").classList.remove("off-left"); updBack(); updTabs(); }); return; }
  if(tab === "notif"){
    if(stack.at(-1) === "s-notif"){ $("#s-notif").scrollTo({top:0, behavior:"smooth"}); return; }
    tabSwitch(()=>{ toRoot(); openNotif(); }); return;
  }
  if(tab === "search"){
    if(stack.at(-1) === "s-search"){ $("#s-search").scrollTo({top:0, behavior:"smooth"}); $("#searchInp").focus(); return; }
    tabSwitch(()=>{ toRoot(); openSearch(); }); return;
  }
  const mine = tab === "mine";
  if(stack.at(-1) === "s-people"){ if(ppl.mine === mine) { $("#s-people").scrollTo({top:0, behavior:"smooth"}); return; } openPeople(mine); return; }
  ppl.fromMine = false;
  tabSwitch(()=>{ toRoot(); openPeople(mine); });
};
function updTabs(){
  try{ ppl; feed; }catch(_){ return; }   // called by updBack before the screens below are set up
  const top = twoPanes() && stack.at(-1) === "s-task" && stack.length > 1 ? stack.at(-2) : stack.at(-1);   // two panes: the list's tab stays lit
  const on = top === "s-projects" || top === "s-comments" || top === "s-time" ? "proj" : top === "s-search" ? "search" : top === "s-people" ? (ppl.mine ? "mine" : "") : top === "s-notif" || (top === "s-history" && feed.pid === null) ? "notif" : "";
  document.querySelectorAll("#tabbar [data-tab]").forEach(b=>b.classList.toggle("on", b.dataset.tab === on));
}

/* ---------- Access: people let into a project by its invite link, without being in the chat ---------- */
let access = null;
function renderAccess(){
  const box = $("#accList");
  if(!access){ box.innerHTML = [0,1].map(i=>`<div style="height:50px;border-radius:var(--r-m);background:var(--muted);opacity:${1-i*.3}"></div>`).join(""); return; }
  box.innerHTML = access.people.length ? access.people.map(p=>`<div class="acc-row"><span class="pav" style="background:${avColor(p.name)}">${esc((p.name||"?").trim()[0]||"?")}${p.photo?`<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">`:""}</span><div class="nm">${esc(p.name)}<span>с ${esc(p.date)}</span></div><button class="rm" data-u="${p.id}" aria-label="Убрать доступ"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7L17 17"/><path d="M17 7L7 17"/></svg></button></div>`).join("")
    : `<div class="acc-empty">Пока никто. Кто откроет ссылку, появится здесь — его можно будет убрать.</div>`;
  box.querySelectorAll(".rm").forEach(b=>b.onclick = ()=>{
    const p = access.people.find(x=>x.id == b.dataset.u);
    const run = async ok=>{
      if(!ok) return;
      if(LIVE){ try{ access = await api(`/api/projects/${curProj.id}/access/${p.id}`, "DELETE"); }catch(e){ fail(e); return; } }
      else access.people = access.people.filter(x=>x !== p);
      haptic("medium"); renderAccess(); toast(`${p.name} больше не видит проект`);
    };
    const msg = `${p.name} перестанет видеть задачи проекта.`;
    tg?.showConfirm ? tg.showConfirm(msg, run) : run(confirm(msg));
  });
}
async function openAccess(proj){
  const sh = $("#accessSheet");
  access = null; renderAccess();
  sh.classList.add("open"); haptic();
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  const link = ()=>access?.link;
  $("#accCopy").onclick = async ()=>{ if(!link()) return; if(await copyText(link())){ haptic(); toast("Ссылка скопирована"); } else if(tg?.showPopup) tg.showPopup({title:"Ссылка-приглашение", message:link(), buttons:[{type:"close"}]}); };
  $("#accSend").onclick = ()=>{
    if(!link()) return;
    const url = `https://t.me/share/url?url=${encodeURIComponent(link())}&text=${encodeURIComponent(`Задачи проекта «${proj.name}»`)}`;
    tg?.openTelegramLink ? tg.openTelegramLink(url) : window.open(url, "_blank");
  };
  $("#accReset").onclick = ()=>{
    const run = async ok=>{
      if(!ok) return;
      if(LIVE){ try{ access = await api(`/api/projects/${proj.id}/access/reset`, "POST"); }catch(e){ fail(e); return; } }
      haptic("medium"); renderAccess(); toast("Ссылка новая, старая больше не работает");
    };
    const msg = "Старая ссылка перестанет работать. Те, кто уже вошёл по ней, останутся — их можно убрать в списке.";
    tg?.showConfirm ? tg.showConfirm(msg, run) : run(confirm(msg));
  };
  try{ access = LIVE ? await api(`/api/projects/${proj.id}/access`) : (await sleep(300), {link:"https://t.me/bot?startapp=join_demo", people:[{id:7,name:"Дизайнер Лена",date:"10 ноя, 12:05"}]}); }
  catch(e){ fail(e); sh.classList.remove("open"); return; }
  if(curProj === proj) renderAccess();
}
function demoFeed(){
  const all = tasks.filter(t=>feed.pid === null || t.pid === feed.pid).flatMap(t=>(t.history||[]).map(e=>({...e, task_id:t.id, task:t.title, pid:t.pid, project:projects.find(p=>p.id===t.pid)?.name})));
  return all.filter(FEED_MATCH[feed.f]).sort((a,b)=>b.ts - a.ts).map((e,i)=>({...e, id:1e6 - i}));
}
const feedUrl = (limit, before) => `${feed.pid === null ? "/api/history" : `/api/projects/${feed.pid}/history`}?filter=${feed.f}&limit=${limit}${before ? "&before="+before : ""}`;
async function loadFeed(more=false){
  const my = ++feed.seq, f = feed.f, had = more ? feed.rows.length : 0;
  feed.loading = true; feed.failed = false;
  if(!more) feed.rows = [];
  renderFeed();
  try{
    let r;
    if(LIVE) r = await api(feedUrl(FEED_PAGE, more ? feed.next : null));
    else { await sleep(more ? 0 : DEMO_DELAY * .6); r = {events: demoFeed(), next: null}; }
    if(my !== feed.seq || f !== feed.f) return;           // another chip was tapped meanwhile
    feed.rows = more ? [...feed.rows, ...r.events] : r.events; feed.next = r.next;
  }catch(e){
    if(my !== feed.seq) return;
    console.error(e); feed.failed = true; if(more) toast("Не загрузилось, попробуйте ещё раз");
  }
  feed.loading = false;
  renderFeed(Infinity, more ? had : 0);
}
/* Quiet update of what is on screen: from the poll and after coming back from a task */
const feedKey = a => JSON.stringify(a.map(e=>[e.id, e.task]));
async function refreshFeed(){
  if(!LIVE){ if(!feed.loading){ feed.rows = demoFeed(); renderFeed(); } return; }
  if(feed.loading || feed.failed || feed.rows.length > FEED_MAX) return;   // a longer list than one request can bring back stays as is
  const my = feed.seq, f = feed.f, gen = apiGen, had = feed.rows;
  try{
    const r = await api(feedUrl(Math.max(FEED_PAGE, had.length)), "GET", undefined, true);
    if(my !== feed.seq || f !== feed.f || gen !== apiGen || stack.at(-1) !== "s-history") return;
    if(feedKey(r.events) === feedKey(had)){ feed.next = r.next; return; }
    const top = had[0]?.id ?? Infinity;
    feed.rows = r.events; feed.next = r.next;
    renderFeed(top);
  }catch(_){ /* offline: the next tick tries again */ }
}
function dayLabel(ts){
  const d = new Date(ts*1000), now = new Date(), day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 864e5);
  if(diff === 0) return "Сегодня";
  if(diff === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", d.getFullYear() === now.getFullYear() ? {day:"numeric", month:"long"} : {day:"numeric", month:"long", year:"numeric"});
}
// enterFrom: rows from this index on slide in one after another (after a load); undefined = no entrance
function renderFeed(newerThan=Infinity, enterFrom){
  const box = $("#feed");
  if(feed.loading && !feed.rows.length){
    box.innerHTML = `<div class="sec-label">${skLine("64px",11)}</div><div class="hist">${[0,1,2,3,4].map(i=>`<div class="ev" style="opacity:${1-i*.16}"><div class="av sk"></div><div class="body">${skLine("72%",13)}${skLine("46%",11,"margin-top:7px")}</div></div>`).join("")}</div>`;
    return;
  }
  if(!feed.rows.length){
    box.innerHTML = feed.failed
      ? `<div class="empty">Не удалось загрузить историю<br><button id="feedRetry" style="color:var(--link);margin-top:8px">Повторить</button></div>`
      : `<div class="empty">${FEED_EMPTY[feed.f]}</div>`;
    return;
  }
  const days = [];
  feed.rows.forEach(e=>{ const l = dayLabel(e.ts); if(days.at(-1)?.l !== l) days.push({l, rows:[]}); days.at(-1).rows.push(e); });
  const hm = ts => new Date(ts*1000).toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});
  let n = 0;
  const enter = () => { const k = n++ - enterFrom; return enterFrom !== undefined && k >= 0 && k < 12 ? ` enter" style="--i:${k}` : ""; };
  box.innerHTML = days.map(d=>`<div class="sec-label"><span class="mono">${esc(d.l)}</span></div><div class="hist">${d.rows.map(e=>
    `<button class="ev ${e.id > newerThan ? "new" : ""}${enter()}" data-t="${e.task_id}" data-pid="${e.pid}">${evAvatar(e)}<div class="body"><div class="tx">${evText(e)}</div><div class="ft">${esc(e.task)}</div><div class="tm">${feed.pid === null ? esc(e.project||"") + " · " : ""}${esc(e.actor)} · ${hm(e.ts)}</div></div></button>`
  ).join("")}</div>`).join("")
    + (feed.next ? `<button class="link-row" id="feedMore">${feed.loading ? "Загрузка…" : "Показать ещё"}</button>` : "");
}

let openSeq = 0;
async function openTask(id, keepTrail=false){
  if(!keepTrail) taskTrail = [];          // opened from a list: Back goes to the list, not through linked tasks
  const my = ++openSeq;                    // only the newest open wins (double taps, tapping another row meanwhile)
  const shown = Date.now();
  ghostOn(id); go("s-task");
  let t;
  if(LIVE){
    try{ t = fromApi(await api(`/api/tasks/${id}`)); }catch(e){ if(my === openSeq){ fail(e); ghostOff(); back(); } return; }
  } else { await sleep(DEMO_DELAY * .7); t = tasks.find(x=>x.id==id) || demoPool().find(x=>x.id==id); }
  // a fast answer must not swap the skeleton for the task mid-slide: that reads as a blink
  const left = 400 - (Date.now() - shown); if(left > 0) await sleep(left);
  if(my !== openSeq || stack.at(-1) !== "s-task") return;   // superseded, or the user already went back
  curTask = t;
  opened = {id: curTask.id, group: groupOf(curTask)};
  renderTask(); ghostOff(); markSel();   // the bones fade out over the content that has taken their place
}

/* ---------- «Связанные задачи»: the same thing in another chat or project; each task stays where it is ---------- */
let taskTrail = [];   // opened a linked task: Back returns to the task it was opened from
const IC_UNLINK = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 7.5L16.5 16.5"/><path d="M16.5 7.5L7.5 16.5"/></svg>`;
const linkMeta = x => statusBadge({archived: x.archived, closed: x.status === "closed", backlog: x.status === "backlog", progress: x.status === "progress" || x.status === "review", review: x.status === "review"})
  + (x.project ? `<span class="tn">${esc(x.project)}</span>` : "");
const linkBody = x => `<span class="pdot" style="background:${x.color || "var(--hint)"}"></span><div class="main"><div class="hx">${esc(x.title)}</div><div class="s">${linkMeta(x)}</div></div>`;
function renderLinks(){
  const t = curTask, ls = t.links || [], hid = t.links_hidden || 0, box = $("#links");
  box.hidden = $("#linksLbl").hidden = !ls.length && !hid;
  box.innerHTML = ls.map((x,i)=>`<div class="row hit lnk" role="button" tabindex="0" data-i="${i}">${linkBody(x)}<button class="unl" type="button" aria-label="Убрать связь" title="Убрать связь">${IC_UNLINK}</button></div>`).join("")
    + (hid ? `<div class="row lnk-hidden">${hid === 1 ? "Ещё одна — в проекте" : `Ещё ${hid} — в проектах`}, где вас нет</div>` : "");
  box.querySelectorAll(".lnk").forEach(r=>{
    const x = ls[+r.dataset.i];
    r.onclick = e=>{ if(e.target.closest(".unl")) return; haptic(); openLinked(x.id); };
    r.querySelector(".unl").onclick = ()=>{ haptic(); unlinkTask(x); };
  });
}
function openLinked(id){ if(curTask) taskTrail.push(curTask.id); openTask(id, true); }
function applyLinks(fresh){
  if(curTask?.id !== fresh.id) return;
  curTask.links = fresh.links; curTask.links_hidden = fresh.links_hidden; curTask.history = fresh.history;
  renderLinks(); renderHistory();
}
/* demo: the tasks the app has in hand stand for «every project» */
function demoPool(){ const m = new Map(); for(const x of [...tasks, ...(ppl.data?.tasks || [])]) if(x?.title && !m.has(x.id)) m.set(x.id, x); return [...m.values()]; }
function demoRow(o){
  const pid = o.pid ?? curProj?.id, p = projects.find(x=>x.id == pid);
  return {id: o.id, title: o.title, pid, project: o.project || p?.name || "", color: o.color || p?.color,
          status: o.closed ? "closed" : o.backlog ? "backlog" : o.review ? "review" : o.progress ? "progress" : "open", archived: !!o.archived};
}
function demoRelated(t, q){
  const stems = x => String(x || "").toLowerCase().replace(/ё/g, "е").split(/[^0-9a-zа-я]+/).filter(w=>w.length >= 4).map(w=>w.slice(0, 5));
  const want = stems(q || t.title), skip = new Set([t.id, ...(t.links || []).map(x=>x.id)]);
  return demoPool().filter(o=>!skip.has(o.id)).map(o=>{
    const have = new Set(stems(o.title + " " + (o.items || []).map(i=>i.t).join(" ")));
    const n = want.filter(w=>[...have].some(h=>h.startsWith(w) || w.startsWith(h))).length;
    return {o, n};
  }).filter(r=>q ? r.n === want.length && r.n : r.n).sort((a,b)=>b.n - a.n).slice(0, 8).map(r=>demoRow(r.o));
}
function demoLink(t, x, on){
  const o = demoPool().find(y=>y.id === x.id), me = demoRow(t);
  t.links = on ? [...(t.links || []), x] : (t.links || []).filter(y=>y.id !== x.id);
  if(o && o !== t) o.links = on ? [...(o.links || []), me] : (o.links || []).filter(y=>y.id !== t.id);
  (t.history ||= []).unshift({ts: Math.floor(Date.now()/1000), actor: "Вы", kind: on ? "linked" : "unlinked", text: x.title, extra: {id: x.id, project: x.project}});
}
async function linkTask(x, undoable=true){
  const t = curTask; if(!t) return;
  if(LIVE){ try{ applyLinks(fromApi(await api(`/api/tasks/${t.id}/links`, "POST", {id: x.id}))); }catch(e){ fail(e); return; } }
  else { demoLink(t, x, true); renderLinks(); renderHistory(); }
  toast(undoable ? "Задачи связаны" : "Связь возвращена", "ok", undoable ? {undo: ()=>unlinkTask(x, false)} : {});
}
async function unlinkTask(x, undoable=true){
  const t = curTask; if(!t) return;
  if(LIVE){ try{ applyLinks(fromApi(await api(`/api/tasks/${t.id}/links/${x.id}`, "DELETE"))); }catch(e){ fail(e); return; } }
  else { demoLink(t, x, false); renderLinks(); renderHistory(); }
  toast(undoable ? "Связь убрана" : "Связь отменена", "ok", undoable ? {undo: ()=>linkTask(x, false)} : {});
}
let linkSeq = 0, linkT = null;
function openLinkPicker(){
  const sh = $("#linkSheet"), inp = $("#linkInp");
  inp.value = ""; sh.classList.add("open"); loadLinkRes("");
  if(!matchMedia("(pointer:coarse)").matches) setTimeout(()=>inp.focus(), 50);   // on a phone the keyboard would hide the suggestions
  inp.oninput = ()=>{ clearTimeout(linkT); linkT = setTimeout(()=>loadLinkRes(inp.value.trim()), 250); };
  inp.onkeydown = e=>{ if(e.key === "Enter"){ e.preventDefault(); clearTimeout(linkT); loadLinkRes(inp.value.trim()); } };
  $("#linkCancel").onclick = ()=>sh.classList.remove("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
}
async function loadLinkRes(q){
  const my = ++linkSeq, box = $("#linkRes"), t = curTask;
  $("#linkResLbl").querySelector(".mono").textContent = q ? "Найдено" : "Похожие";
  const draw = rows => {
    box.innerHTML = rows.length ? `<div class="grid">${rows.map((x,i)=>`<button class="row hit" type="button" data-i="${i}">${linkBody(x)}</button>`).join("")}</div>`
      : `<div class="empty">${q ? "Ничего не нашлось" : "Похожих задач нет — найдите нужную по словам"}</div>`;
    box.querySelectorAll("[data-i]").forEach(b=>b.onclick = ()=>{ haptic(); $("#linkSheet").classList.remove("open"); linkTask(rows[+b.dataset.i]); });
  };
  ghostList([box], ()=>draw([{title: "Название похожей задачи", status: "open", project: "Проект"}, {title: "Ещё одна задача", status: "progress", project: "Другой проект"}, {title: "И ещё одна", status: "open", project: "Проект"}]));
  let rows;
  if(LIVE){
    try{ rows = (await api(`/api/tasks/${t.id}/related` + (q ? `?q=${encodeURIComponent(q)}` : ""))).rows; }
    catch(e){ if(my === linkSeq){ unghost(box, true); box.innerHTML = `<div class="empty">Не удалось загрузить</div>`; } return; }
  } else { await sleep(300); rows = demoRelated(t, q); }
  if(my !== linkSeq) return;
  draw(rows); unghost(box);
}
$("#linkTask").onclick = ()=>{ haptic(); openLinkPicker(); };


/* ---------- Screenshots strip + upload from the photo library ---------- */
function renderStrip(uploading=0){
  const t = curTask, shotsN = (t.shots||[]).length, vids = t.videos||[];
  const all = [...(t.shots||[]).map((src,i)=>({src,i,video:false})), ...vids.map((v,k)=>({src:v.src,i:shotsN+k,video:true}))];
  const empty = !all.length && !uploading;   // nothing yet: no empty strip on top, «Добавить фото» below instead
  $("#strip").style.display = empty ? "none" : "";
  $("#addPhoto").hidden = !empty;
  $("#strip").innerHTML = all.map(s=> !s.video && s.src.includes("/")
      ? `<a class="shot" data-lightbox="shots" href="${s.src}" data-n="${s.i}" draggable="false">${shotHTML(s.src)}</a>`
      : `<button class="shot ${s.video?"video":""}" data-s="${s.i}">${shotHTML(s.src)}${s.video?`<span class="num mono">видео</span>`:""}</button>`).join("")
    + Array.from({length:uploading}, ()=>`<div class="shot sk"></div>`).join("")
    + `<button class="shot add-shot" id="addShot" aria-label="Добавить скриншот"><span class="plus-big"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21.003 12.981V16.993C21.003 19.203 19.214 21.004 17.003 21.004H6.998C4.788 21.004 2.995 19.203 2.995 16.993V7.004C2.995 4.793 4.788 2.992 6.998 2.992H10.998"/><circle cx="18.002" cy="5.98" r="4.002"/><path d="M19.255 5.98H16.754"/><path d="M18.002 4.752V7.249"/><path d="M2.995 13.022L4.292 11.712C4.743 11.261 5.357 11.016 5.999 11.016C6.638 11.016 7.252 11.261 7.702 11.712L12.001 16.01"/><path d="M7.002 21.004L13.299 14.7C14.24 13.758 15.767 13.758 16.709 14.7L20.651 18.63"/></svg></span><span>Скрин</span></button>`;
  document.querySelectorAll("[data-s]").forEach(b=>b.onclick=()=>{
    const i=+b.dataset.s;
    if(i>=shotsN){ const v = vids[i-shotsN]; if(LIVE && v.link) tg.openTelegramLink(v.link); else toast("Видео откроется в чате"); return; }
    openLB(i);
  });
  settleImgs();
  $("#addShot").onclick = $("#addPhoto").onclick = ()=>pickImages(true).then(n=>{ if(n) toast(n>1 ? `Добавлено фото: ${n}` : "Фото добавлено"); });
}

/* Shrink big photos on the phone before sending: fast on mobile internet, same look */
async function shrink(file){
  if(file.type === "image/gif") return file;
  try{
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas"); c.width = Math.round(bmp.width*k); c.height = Math.round(bmp.height*k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise(r=>c.toBlob(b=>r(b || file), "image/jpeg", .86));
  }catch(e){ return file; }
}

/* Opens the photo library; uploads the chosen pictures. Returns how many were added (last number in curTask.lastShot). */
function pickImages(multiple){
  return new Promise(resolve=>{
    const inp = $("#fileInp"); inp.multiple = multiple; inp.value = "";
    inp.onchange = async ()=>{
      const files = [...inp.files].filter(f=>f.type.startsWith("image/"));
      if(!files.length){ resolve(0); return; }
      let added = 0;
      renderStrip(files.length);
      for(const f of files){
        const blob = await shrink(f);
        if(LIVE){
          try{
            const fd = new FormData(); fd.append("file", blob, (f.name || "photo").replace(/\.\w+$/, "") + ".jpg");
            const r = await fetch(`/api/tasks/${curTask.id}/media`, {method:"POST", headers:{"Authorization":authHeader()}, body:fd});
            if(!r.ok) throw new Error(r.status + " " + await r.text());
            const fresh = await r.json();
            curTask.shots = fromApi(fresh).shots; curTask.lastShot = fresh.new_shot; added++;
          }catch(e){ fail(e); }
        } else {
          curTask.shots = [...(curTask.shots||[]), URL.createObjectURL(blob) + "#/demo"]; curTask.lastShot = curTask.shots.length; added++;
        }
        renderStrip(files.length - added);
      }
      renderStrip(); haptic("light");
      resolve(added);
    };
    inp.click();
  });
}

/* ---------- Screen 3: task ---------- */
/* History: newest first, the last few by default, «Показать всю» for the rest */
const HIST_SHORT = 4;
let histAll = false, histSeen = {task: null, top: null};
const ST_RU = {open:"открытой", progress:"«В работе»", review:"«На проверке»", backlog:"бэклог"};
const ICK = {
  done:   ["var(--green)", '<path d="M20 6.501L9 17.501L4 12.498"/>'],
  closed: ["var(--green)", '<path d="M20 6.501L9 17.501L4 12.498"/>'],
  report: ["var(--green)", '<path d="M20 6.501L9 17.501L4 12.498"/>'],
  done_all:["var(--green)", '<path d="M20 6.501L9 17.501L4 12.498"/>'],
  undone: ["#94a3b8", '<path d="M7.999 7.998L16.002 16.001"/><path d="M16.002 7.998L7.999 16.001"/>'],
  reopened:["#f59e0b", '<path d="M19.333 6.788C17.699 4.496 15.026 2.981 12.001 2.981C7.031 2.981 2.999 7.034 2.999 11.987C2.999 12.52 3.056 13.052 3.146 13.584"/><path d="M4.423 19.602V17.228H6.814"/><path d="M4.669 17.187C6.298 19.52 8.971 20.994 12.001 20.994C16.971 20.994 20.999 16.982 20.999 11.987C20.999 11.455 20.946 10.923 20.851 10.432"/><path d="M19.574 4.414V6.788H17.188"/>'],
  created:["#0ea5e9", '<g transform="rotate(45 12 12)"><path d="M7.999 7.998L16.002 16.001"/><path d="M16.002 7.998L7.999 16.001"/></g>'],
  archived:["#94a3b8", '<path d="M12 5V19"/><path d="M6 13L12 19L18 13"/>'],
  unarchived:["#94a3b8", '<path d="M12 19V5"/><path d="M6 11L12 5L18 11"/>'],
  assigned:["#0d9488", '<path d="M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M4 21c0-3.9 3.6-7 8-7s8 3.1 8 7"/>'],
  linked:  ["var(--link)", '<path d="M10 14C11.5 15.8 14.4 16 16.1 14.3L19.3 11.1C21 9.4 21 6.6 19.3 4.9C17.6 3.2 14.8 3.2 13.1 4.9L12 6"/><path d="M14 10C12.5 8.2 9.6 8 7.9 9.7L4.7 12.9C3 14.6 3 17.4 4.7 19.1C6.4 20.8 9.2 20.8 10.9 19.1L12 18"/>'],
  unlinked:["var(--hint)", '<path d="M10 14C11.5 15.8 14.4 16 16.1 14.3L19.3 11.1C21 9.4 21 6.6 19.3 4.9C17.6 3.2 14.8 3.2 13.1 4.9L12 6"/><path d="M14 10C12.5 8.2 9.6 8 7.9 9.7L4.7 12.9C3 14.6 3 17.4 4.7 19.1C6.4 20.8 9.2 20.8 10.9 19.1L12 18"/>'],
  commented:["var(--k-comm)", '<path d="M19.5 11.5C19.5 15.4 16.1 18.5 12 18.5C11 18.5 10 18.3 9.1 18L4.5 19.5L5.7 15.8C4.9 14.6 4.5 13.1 4.5 11.5C4.5 7.6 7.9 4.5 12 4.5C16.1 4.5 19.5 7.6 19.5 11.5Z"/>'],
  asked:["var(--k-ask)", '<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6C9.8 8.3 10.8 7.4 12 7.4C13.2 7.4 14.2 8.3 14.2 9.5C14.2 11.2 12 11.4 12 13.2"/><path d="M12 16.4H12.01"/>'],
  answered:["var(--green)", '<path d="M20 6.501L9 17.501L4 12.498"/>'],
  reasked:["var(--k-ask)", '<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6C9.8 8.3 10.8 7.4 12 7.4C13.2 7.4 14.2 8.3 14.2 9.5C14.2 11.2 12 11.4 12 13.2"/><path d="M12 16.4H12.01"/>'],
  descr:["var(--link)", '<path d="M4.5 6.5H19.5"/><path d="M4.5 10.5H19.5"/><path d="M4.5 14.5H14"/><path d="M4.5 18.5H11"/>'],
  moved:["#0ea5e9", '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M13.5 17H20.5"/><path d="M17 13.5V20.5"/>'],
  time:["var(--k-time)", '<circle cx="12" cy="13.2" r="7.3"/><path d="M12 9.6V13.2L14.2 14.6"/><path d="M10 3.5H14"/>'],
  due:["#8b5cf6", '<path d="M16.641 16L11.719 13.064V6.736"/><path d="M18.364 5.636C21.879 9.151 21.879 14.849 18.364 18.364C14.849 21.879 9.151 21.879 5.636 18.364C2.122 14.849 2.122 9.151 5.636 5.636C9.151 2.121 14.849 2.121 18.364 5.636"/>'],
};
function evText(e){
  const q = t => `<q>${esc(String(t||"").length > 90 ? String(t).slice(0, 88) + "…" : t)}</q>`, x = e.extra || {};
  switch(e.kind){
    case "created":   return x.auto ? `Задача создана по сообщениям: ${esc(x.author||"")}` : "Задача создана";
    case "done":      return `Выполнено: ${q(e.text)}`;
    case "undone":    return `Снята отметка: ${q(e.text)}`;
    case "done_all":  return `Отмечены все оставшиеся пункты (${x.count})`;
    case "report":    return "Отчёт" + (x.hours != null ? `: ${hoursTxt(x.hours)}` : "") + (x.result_url ? " · есть ссылка на результат" : "") + (x.result_note ? ` · «${x.result_note.length > 80 ? x.result_note.slice(0, 79) + "…" : x.result_note}»` : "");
    case "closed":    return "Задача закрыта" + (x.hours != null ? `, ${hoursTxt(x.hours)}` : "") + (x.result_url ? " · есть ссылка на результат" : "") + (x.result_note ? ` · «${x.result_note.length > 80 ? x.result_note.slice(0, 79) + "…" : x.result_note}»` : "");
    case "time":      return x.hours != null ? `Потрачено: ${hoursTxt(x.hours)}` + (x.from ? ` (было ${hoursTxt(x.from)})` : "") : "Время убрано";
    case "reopened":  return "Задача снова открыта";
    case "status":    return x.to === "progress" ? (x.from === "review" ? "Возвращена в работу после проверки" : "Взята в работу") : x.to === "review" ? "Отправлена на проверку" : x.to === "backlog" ? "Отложена в бэклог" : "Возвращена в открытые";
    case "archived":  return "Перенесена в архив";
    case "unarchived":return "Возвращена из архива";
    case "renamed":   return `Новое название: ${q(e.text)}`;
    case "item_added":return `Добавлен пункт: ${q(e.text)}`;
    case "item_edited":return `Изменён пункт: ${q(e.text)}`;
    case "item_deleted":return `Удалён пункт: ${q(e.text)}`;
    case "items_added":return `Добавлены пункты из чата (${x.count})`;
    case "items_removed":return `Отменено добавление пунктов из чата (${x.count})`;
    case "shot_added":return "Добавлен скриншот";
    case "shot_deleted":return "Удалён скриншот";
    case "due":       return x.text ? `Срок: ${esc(x.text)}` : "Срок убран";
    case "assigned":  return x.name ? `Исполнитель: ${esc(x.name)}` : "Исполнитель снят";
    case "linked":    return x.hidden ? "Связана с задачей в другом проекте" : `Связана с задачей ${q(e.text)}` + (x.project ? ` · ${esc(x.project)}` : "");
    case "unlinked":  return x.hidden ? "Убрана связь с задачей в другом проекте" : `Убрана связь с задачей ${q(e.text)}`;
    case "asked":     return x.item ? `Вопрос к пункту ${q(x.item)}: ${q(e.text)}` : `Вопрос: ${q(e.text)}`;
    case "answered":  return `Вопрос решён: ${q(e.text)}`;
    case "reasked":   return `Вопрос снова открыт: ${q(e.text)}`;
    case "descr":     return e.text ? `Описание: ${q(e.text)}` : "Описание убрано";
    case "moved":     return x.from_project
      ? `Перенесена из «${esc(x.from_project)}» в «${esc(e.text || x.project || "")}»`
      : `Перенесена в «${esc(e.text || x.project || "")}»`;
    case "commented": return x.item ? `Комментарий к пункту ${q(x.item)}: ${q(e.text)}` : `Комментарий: ${q(e.text)}`;
    default: return esc(e.kind);
  }
}
function evTime(ts){
  const d = new Date(ts*1000), now = new Date(), hm = d.toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = (day(now) - day(d)) / 864e5;
  if(diff === 0) return `сегодня, ${hm}`;
  if(diff === 1) return `вчера, ${hm}`;
  return `${d.toLocaleDateString("ru-RU", {day:"numeric", month:"short"}).replace(".", "")}${d.getFullYear() !== now.getFullYear() ? " " + d.getFullYear() : ""}, ${hm}`;
}
const AV_COLORS = ["#6366f1","#0ea5e9","#14b8a6","#f59e0b","#ec4899","#8b5cf6","#ef4444","#22c55e"];
const avColor = n => AV_COLORS[[...String(n)].reduce((a,c)=>a + c.charCodeAt(0), 0) % AV_COLORS.length];
function evAvatar(e){
  const k = e.kind === "status" && PICK[e.extra?.to] ? [PICK[e.extra.to].color, ""] : ICK[e.kind];
  const av = e.agent
    ? `<div class="av" style="background:#334155"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 15.5L5 15.5"/><path d="M3 12L5 12"/><path d="M3 8.5L5 8.5"/><path d="M21 8.5L19 8.5"/><path d="M21 12L19 12"/><path d="M21 15.5L19 15.5"/><path d="M8.5 3L8.5 5"/><path d="M12 3L12 5"/><path d="M15.5 3L15.5 5"/><path d="M15.5 21L15.5 19"/><path d="M12 21L12 19"/><path d="M8.5 21L8.5 19"/><path fill-rule="evenodd" clip-rule="evenodd" d="M15 19H9C6.791 19 5 17.209 5 15V9C5 6.791 6.791 5 9 5H15C17.209 5 19 6.791 19 9V15C19 17.209 17.209 19 15 19Z"/><path d="M8.732 13.38H12.276"/><path d="M12.58 14.3L11.09 9.7H9.93L8.44 14.3"/><path d="M15.11 14.3V9.7"/></svg>`
    : `<div class="av" style="background:${avColor(e.actor)}">${esc((e.actor||"?").trim()[0] || "?")}`
      // the photo lies over the letter: if it fails to load it removes itself and the letter stays
      + (e.photo ? `<img src="${esc(e.photo)}" alt="" loading="lazy" onerror="this.remove()">` : "");
  const badge = k ? `<span class="k" style="background:${k[0]}"><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">${k[1]}</svg></span>` : "";
  return av + badge + "</div>";
}
function renderHistory(){
  const t = curTask, h = t.history || [], box = $("#hist");
  if(!h.length){ box.innerHTML = `<div class="empty">Пока ничего не происходило</div>`; return; }
  if(histSeen.task !== t.id){ histAll = false; }
  const fresh = histSeen.task === t.id && histSeen.top !== null ? h.findIndex(e=>e.ts === histSeen.top.ts && e.kind === histSeen.top.kind) : 0;
  const list = histAll ? h : h.slice(0, HIST_SHORT);
  box.innerHTML = list.map((e, i)=>{
    return `<div class="ev ${i < fresh ? "new" : ""}">${evAvatar(e)}<div><div class="tx">${evText(e)}</div><div class="tm">${esc(e.actor)} · ${evTime(e.ts)}</div></div></div>`;
  }).join("") + (h.length > HIST_SHORT ? `<button class="more" id="histMore">${histAll ? "Свернуть" : `Показать всю историю (${h.length})`}</button>` : "");
  histSeen = {task: t.id, top: h[0]};
  const m = $("#histMore"); if(m) m.onclick = ()=>{ histAll = !histAll; renderHistory(); };
}
const IC_CLOCK = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.641 16L11.719 13.064V6.736"/><path d="M18.364 5.636C21.879 9.151 21.879 14.849 18.364 18.364C14.849 21.879 9.151 21.879 5.636 18.364C2.122 14.849 2.122 9.151 5.636 5.636C9.151 2.121 14.849 2.121 18.364 5.636"/></svg>`;
const IC_BELL = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13.6 20.001H10.4"/><path fill-rule="evenodd" clip-rule="evenodd" d="M17 10.033V10V10C17 7.241 14.761 5.001 12 5.001V5.001C9.239 5.001 7 7.241 7 10V10V10.033V12.505C7 12.829 6.817 13.123 6.528 13.267L6.025 13.521C5.397 13.836 5 14.474 5 15.178V15.178C5 16.202 5.83 17.033 6.854 17.033H17.146C18.17 17.033 19 16.202 19 15.178V15.178C19 14.474 18.603 13.836 17.975 13.521L17.472 13.271C17.183 13.123 17 12.829 17 12.505V10.033Z"/></svg>`;
/* deadline passed: all-day — after that day ends */
const dueEnd = du => du.allday ? du.at + 86400 : du.at;
const isLate = t => !!t.due && !t.archived && !isClosed(t) && Date.now()/1000 > dueEnd(t.due);
const ymd = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
function quickDays(){
  const now = new Date(), add = n => { const d = new Date(now); d.setDate(d.getDate()+n); return d; };
  const fri = (5 - now.getDay() + 7) % 7 || 7;
  return [["Сегодня", add(0)], ["Завтра", add(1)], ["Пятница", add(fri)], ["Через неделю", add(7)]].map(([l,d])=>[l, ymd(d)]);
}
const REM = {day:["За день","накануне"], hour:["За час",""], due:["В момент срока",""]};
function openDue(){
  const t = curTask, sh = $("#dueSheet");
  let date = t.due?.date || "", tm = t.due?.time || "", rem = new Set(t.reminders||[]);
  const draw = ()=>{
    $("#dueQuick").innerHTML = quickDays().map(([l,d])=>`<button data-d="${d}" class="${d===date?"on":""}">${l}</button>`).join("");
    $("#dueQuick").querySelectorAll("button").forEach(b=>b.onclick = ()=>{ date = b.dataset.d; $("#dueDate").value = date; haptic(); draw(); });
    const allday = !tm;
    const rows = allday ? [["day","Накануне","в 10:00"],["due","В день срока","в 10:00"]] : [["day","За день",""],["hour","За час",""],["due","В момент срока",""]];
    if(allday) rem.delete("hour");
    $("#remList").innerHTML = rows.map(([k,l,sub])=>`<button data-k="${k}" class="${rem.has(k)?"on":""}" ${date?"":"disabled style=\"opacity:.5\""}><span>${l}${sub?`<span class="sub">${sub}</span>`:""}</span><span class="box"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5L10 17.5L19 7.5"/></svg></span></button>`).join("");
    $("#remList").querySelectorAll("button").forEach(b=>b.onclick = ()=>{ if(!date) return; const k = b.dataset.k; rem.has(k) ? rem.delete(k) : rem.add(k); haptic(); draw(); });
    const other = t.assignee && t.assignee.id !== me?.id;
    $("#remNote").textContent = !date ? "Сначала выберите дату"
      : other ? `Бот пришлёт их в личку вам и исполнителю — ${t.assignee.name}.`
      : t.assignee ? "Бот пришлёт их вам в личку. Вы исполнитель — придут и те, что поставили другие."
      : "Напоминания видите только вы. Бот пришлёт их в личку. Если назначить исполнителя, они придут и ему.";
    $("#dueDel").style.display = t.due ? "" : "none";
    $("#dueNoTime").hidden = !tm;
    $("#dueDateW").classList.toggle("blank", !date); $("#dueTimeW").classList.toggle("blank", !tm);
  };
  $("#dueNoTime").onclick = ()=>{ tm = ""; $("#dueTime").value = ""; haptic(); draw(); };
  $("#dueDate").value = date; $("#dueTime").value = tm;
  $("#dueDate").onchange = ()=>{ date = $("#dueDate").value; draw(); };
  $("#dueTime").onchange = ()=>{ tm = $("#dueTime").value; draw(); };
  draw();
  sh.classList.add("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  $("#dueCancel").onclick = ()=>sh.classList.remove("open");
  $("#dueDel").onclick = ()=>{ sh.classList.remove("open"); saveDue(t, null, []); };
  $("#dueSave").onclick = ()=>{ sh.classList.remove("open"); saveDue(t, date ? {date, time: tm || null} : null, date ? [...rem] : []); };
}
async function saveDue(t, due, rem){
  const same = (t.due?.date||null) === (due?.date||null) && (t.due?.time||null) === (due?.time||null);
  const remSame = [...(t.reminders||[])].sort().join() === [...rem].sort().join();
  if(same && remSame) return;
  if(!LIVE){
    const d = due && new Date(due.date + "T" + (due.time || "00:00"));
    t.due = due ? {...due, allday: !due.time, at: d.getTime()/1000, text: d.toLocaleDateString("ru-RU", {day:"numeric", month:"long"}) + (due.time ? ", " + due.time : "")} : null;
    t.reminders = rem; renderMeta(); toast(due ? "Срок сохранён" : "Срок убран"); return;
  }
  if(rem.length && !remSame && !me?.notify && tg.requestWriteAccess && tg.isVersionAtLeast("6.9")){
    const ok = await new Promise(r=>tg.requestWriteAccess(r));
    if(!ok){ toast("Без разрешения бот не сможет прислать напоминание", "warn"); rem = []; }
  }
  try{
    let full = null;
    if(!same) full = await api(`/api/tasks/${t.id}`, "PATCH", {due});
    if(!remSame || (!same && !due)) full = await api(`/api/tasks/${t.id}/reminders`, "PUT", {kinds: rem});
    if(full){ const f = fromApi(full); t.due = f.due; t.reminders = f.reminders; t.history = f.history; const row = tasks.find(x=>x.id===t.id); if(row && row !== t){ row.due = t.due; } }
  }catch(e){ fail(e); return; }
  haptic("medium"); renderMeta(); renderHistory();
  const to = t.assignee && t.assignee.id !== me?.id ? ` вам и ${firstName(t.assignee.name)}` : "";
  toast(!due ? "Срок убран" : rem.length ? `Срок ${t.due.text} · напомню в личку${to}` : `Срок: ${t.due.text}`);
}
/* ---------- Assignee ---------- */
const firstName = n => String(n||"").trim().split(/\s+/)[0] || "";
const avSmall = (p, color = avColor(p.name||"?")) => `<span class="pav-s" style="background:${color}">${esc((p.name||"?").replace(/[«»"]/g,"").trim()[0]||"?")}${p.photo?`<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">`:""}</span>`;
const peopleCache = {};
async function openAssign(t, onPick = p=>setAssignee(t, p)){
  const sh = $("#assignSheet"), box = $("#assignList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  const draw = people => {
    const cur = t.assignee?.id ?? null;
    const list = [...people];
    if(t.assignee && !list.some(p=>p.id === t.assignee.id)) list.unshift(t.assignee);   // left the list, still assigned
    const mi = list.findIndex(p=>p.id === me?.id); if(mi > 0) list.unshift(...list.splice(mi, 1));
    const row = p=>`<button data-u="${p.id}" class="${p.id===cur?"on":""}"><span class="pav" style="background:${avColor(p.name||"?")}">${esc((p.name||"?").trim()[0]||"?")}${p.photo?`<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">`:""}</span><span class="nm">${esc(p.name)}${p.id===me?.id?"<span>это вы</span>":p.outside&&p.via?`<span>${esc(p.via)}</span>`:""}</span>${ck}</button>`;
    const ins = list.filter(p=>!p.outside), outs = list.filter(p=>p.outside);
    box.innerHTML = `<button data-u="" class="${cur===null?"on":""}"><span class="pav nobody"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 7L17 17"/><path d="M17 7L7 17"/></svg></span><span class="nm">Без исполнителя</span>${ck}</button>`
      + ins.map(row).join("")
      + (outs.length ? `<div class="as-sec">Из других проектов<span>увидят только эту задачу, не весь проект</span></div>` + outs.map(row).join("") : "");
    box.querySelectorAll("button").forEach(b=>b.onclick = ()=>{
      sh.classList.remove("open");
      const id = b.dataset.u ? Number(b.dataset.u) : null;
      haptic(); onPick(id === null ? null : list.find(p=>p.id === id));
    });
  };
  const cached = peopleCache[t.pid];
  if(cached) draw(cached); else box.innerHTML = [0,1,2].map(i=>`<div style="height:50px;border-radius:var(--r-m);background:var(--muted);opacity:${1-i*.3}"></div>`).join("");
  sh.classList.add("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
  try{
    const people = LIVE ? await api(`/api/projects/${t.pid}/people`, "GET", null, true)
      : [{id:1,name:"Вы"},{id:2,name:"Олег Петров"},{id:3,name:"Анна Смирнова"},{id:4,name:"Дизайнер Лена"},
         {id:5,name:"Мария Козлова",outside:true,via:"Магазин сантехники"},{id:6,name:"Павел 1С",outside:true,via:"Лендинг «Кофейня»"}];
    peopleCache[t.pid] = people;
    if(sh.classList.contains("open")) draw(people);
  }catch(e){ if(!cached){ sh.classList.remove("open"); fail(e); } }
}
async function setAssignee(t, p){
  if((t.assignee?.id ?? null) === (p?.id ?? null)) return;
  let warn = false; const wasWork = !!t.progress;
  if(LIVE){
    try{
      const full = await api(`/api/tasks/${t.id}`, "PATCH", {assignee: p ? p.id : null});
      const f = fromApi(full); t.assignee = f.assignee; t.history = f.history; t.task_reminders = f.task_reminders; t.progress = f.progress;
      warn = full.assignee_dm === false;
    }catch(e){ fail(e); return; }
  } else { t.assignee = p ? {id:p.id, name:p.name, photo:p.photo||null} : null; if(!p) t.progress = false; }
  const reopened = !p && wasWork && !t.progress;   // nobody does it → open again (the server did the same)
  everyCopy(t.id, x=>{ x.assignee = t.assignee; x.progress = t.progress; });
  haptic("medium"); renderMeta(); renderHistory();
  if(!p) toast(reopened ? "Исполнитель снят · задача снова открыта" : "Исполнитель снят");
  else if(warn) toast(`${firstName(p.name)} ещё не запускал(а) бота — личные сообщения и напоминания не дойдут. Попросите открыть бота и нажать «Старт».`, "warn");
  else if(p.id === me?.id) toast("Вы исполнитель");
  else if(p.outside) toast(`Исполнитель: ${p.name} · откроется только эта задача, написал в личку`);
  else toast(`Исполнитель: ${p.name}` + (t.due && (t.task_reminders||[]).length ? " · напоминания придут и исполнителю" : " · исполнителю написал в личку"));
}
function renderMeta(){
  const t = curTask, m = $("#taskMeta");
  const chip = statusBadge(t, t.archived ? {} : {tag:"button", id:"stChip"});   // a closed task can be reopened here too
  const du = t.due, bell = (t.reminders||[]).length ? `<i class="bell">${bIc("bell")}</i>` : "";
  const dueChip = du ? dueBadge(t, {tag:"button", id:"dueChip", long:true, extra:bell})
    : !t.archived ? badge("due", "Срок", {tag:"button", id:"dueChip", none:true}) : "";
  const a = t.assignee;
  const whoChip = a ? whoBadge(a, {tag:"button", id:"whoChip", full:true})
    : !t.archived ? badge("who", "Исполнитель", {tag:"button", id:"whoChip", none:true}) : "";
  // questions matter more: while one is open the tag says so; either way a tap leads down to them
  const nc = (t.comments || []).filter(c=>!c.question).length, oq = openQs(t);
  const commChip = oq ? badge("ask", `${oq} ${plural(oq, "вопрос", "вопроса", "вопросов")}`, {tag:"button", id:"commChip"})
    : nc ? badge("comm", `${nc} ${plural(nc, "комментарий", "комментария", "комментариев")}`, {tag:"button", id:"commChip"}) : "";
  const hasRes = t.result_url || t.hours != null || t.result_note;
  const resChip = isClosed(t) ? badge("res", hasRes ? "Результат" + (t.hours != null ? ` · ${hoursTxt(t.hours)}` : "") : "Результат", {tag:"button", id:"resChip", none: !hasRes}) : "";
  // time spent: any time before closing (after it, it is part of «Результат»)
  const timeChip = isClosed(t) ? "" : t.archived ? (t.hours ? badge("time", hoursTxt(t.hours)) : "")
    : badge("time", t.hours ? hoursTxt(t.hours) : "Время", {tag:"button", id:"timeChip", none: !t.hours});
  m.innerHTML = `<div class="bdgs">${chip}${resChip}${whoChip}${dueChip}${timeChip}${commChip}</div>`;
  const tc = $("#timeChip"); if(tc) tc.onclick = ()=>openTime(curTask);   // who made it and when — in «История»
  const rc = $("#resChip"); if(rc) rc.onclick = ()=> hasRes ? showResult(curTask) : editResult(curTask);
  const c = $("#stChip"); if(c) c.onclick = ()=>pickStatus(curTask);
  const w = $("#whoChip");
  if(w) w.onclick = t.guest ? ()=>toast("Исполнителя меняют участники проекта") : ()=>openAssign(curTask);
  const dc = $("#dueChip"); if(dc) dc.onclick = ()=>openDue();
  const cc = $("#commChip"); if(cc) cc.onclick = ()=>toTalk();
}
/* Voice playback: the original Telegram recording, one at a time */
const IC_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.52.85l10.4-6.5a1 1 0 0 0 0-1.7L9.52 4.65A1 1 0 0 0 8 5.5Z"/></svg>',
      IC_PAUSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4.2" height="14" rx="1.2"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.2"/></svg>';
let voiceAu = null, voiceKey = null;
let voiceView = {id:null, requests:new Map()};
let voiceCardSeq = 0;

function voiceSummaryHtml(s){
  const list = (title, items)=>items.length ? `<div><b>${title}</b><ul>${items.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
  return `<div class="voice-summary">${list("Что сделать", s.actions) || "<div>В сообщении нет прямой просьбы.</div>"}
    ${s.purpose ? `<div><b>Для чего</b><p>${esc(s.purpose)}</p></div>` : ""}
    ${list("Что учесть", s.details)}</div>`;
}

// One pending request per transcript, including identical recordings opened together.
function requestVoiceSummary(view, key){
  if(!view.requests.has(key)){
    const request = api(`/api/tasks/${view.id}/voice/${key}/summary`, "POST", undefined, true);
    view.requests.set(key, request);
    request.catch(()=>{ if(view.requests.get(key) === request) view.requests.delete(key); });
  }
  return view.requests.get(key);
}

function createVoiceCard(view){
  const card = $("#voiceCard").content.firstElementChild.cloneNode(true);
  const play = card.querySelector(".vplay"), toggle = card.querySelector(".voice-toggle");
  const body = card.querySelector(".voice-body"), content = card.querySelector(".voice-content");
  const tabs = body.querySelectorAll("[data-view]"), time = toggle.querySelector(".vtime");
  body.id = "voice-body-" + ++voiceCardSeq;
  toggle.setAttribute("aria-controls", body.id);
  content.id = body.id + "-text";
  let x, mode = "summary", result, pending = false, failed = false;

  function showContent(){
    if(body.hidden) return;
    if(mode === "text"){ content.textContent = x.text || "Расшифровка недоступна."; return; }
    if(x.summary || result){ content.innerHTML = voiceSummaryHtml(x.summary || result); return; }
    if(!x.key || !x.text?.trim()){ content.textContent = "Расшифровка недоступна."; return; }
    if(failed){
      content.innerHTML = 'Не удалось получить разбор.<button class="link-row" type="button">Повторить</button>';
      content.querySelector("button").onclick = ()=>{ haptic(); failed = false; showContent(); };
      return;
    }
    content.innerHTML = '<div class="voice-loading" role="status" aria-label="Готовим разбор"><i class="sk"></i><i class="sk"></i></div>';
    if(pending) return;
    pending = true;
    requestVoiceSummary(view, x.key).then(s=>{ result = s; }, ()=>{ failed = true; }).finally(()=>{
      pending = false;
      if(card.isConnected && mode === "summary") showContent();
    });
  }
  toggle.onclick = ()=>{
    haptic(); body.hidden = !body.hidden;
    toggle.setAttribute("aria-expanded", String(!body.hidden)); showContent();
  };
  for(const tab of tabs){
    tab.setAttribute("aria-controls", content.id);
    tab.onclick = ()=>{
      haptic(); mode = tab.dataset.view;
      for(const b of tabs){ b.classList.toggle("on", b === tab); b.setAttribute("aria-pressed", String(b === tab)); }
      showContent();
    };
  }
  play.innerHTML = IC_PLAY + IC_PAUSE;
  play.onclick = ()=>toggleVoice(play.dataset.au);
  card.updateRecording = recording=>{
    x = recording;
    card.querySelector(".voice-author").textContent = x.author || "Голосовое сообщение";
    play.hidden = !x.audio;
    play.dataset.au = x.audio || "";
    if(x.duration > 0) time.dataset.duration = x.duration;
    time.textContent = time.dataset.duration ? mmss(+time.dataset.duration) : "";
    showContent();
  };
  return card;
}

function renderVoice(t){
  const recordings = t.transcripts || [], box = $("#voice");
  if(voiceView.id !== t.id){ voiceView = {id:t.id, requests:new Map()}; box.replaceChildren(); }
  box.hidden = $("#voiceLbl").hidden = !recordings.length;
  recordings.forEach((x,i)=>{
    const key = x.key || x.text;
    let card = box.children[i];
    if(!card || card.recordingKey !== key){
      const next = createVoiceCard(voiceView);
      next.recordingKey = key;
      if(card) card.replaceWith(next); else box.append(next);
      card = next;
    }
    card.updateRecording(x);
  });
  while(box.children.length > recordings.length) box.lastElementChild.remove();
  syncVoice();
}
const mmss = s => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,"0")}`;
function syncVoice(){
  document.querySelectorAll("#voice .vplay:not([hidden])").forEach(b=>{
    const mine = voiceAu && unsigned(b.dataset.au) === voiceKey, on = mine && !voiceAu.paused;
    b.classList.toggle("on", !!on);
    b.setAttribute("aria-label", on ? "Пауза" : "Прослушать");
    const tm = b.parentNode.querySelector(".vtime");
    if(mine && Number.isFinite(voiceAu.duration) && voiceAu.duration > 0) tm.dataset.duration = voiceAu.duration;
    const totalTime = tm.dataset.duration ? mmss(+tm.dataset.duration) : "";
    tm.textContent = mine && (voiceAu.currentTime > 0 || on)
      ? mmss(voiceAu.currentTime) + (totalTime ? " / " + totalTime : "") : totalTime;
  });
}
function stopVoice(){ if(voiceAu){ voiceAu.pause(); voiceAu = voiceKey = null; syncVoice(); } }
function toggleVoice(url){
  haptic();
  if(voiceAu && voiceKey === unsigned(url)){ voiceAu.paused ? voiceAu.play().catch(()=>{}) : voiceAu.pause(); syncVoice(); return; }
  stopVoice();
  const a = voiceAu = new Audio(url); voiceKey = unsigned(url);
  a.onplay = a.onpause = a.ontimeupdate = a.ondurationchange = ()=>{ if(a === voiceAu) syncVoice(); };
  a.onended = ()=>{ if(a === voiceAu) stopVoice(); };
  a.onerror = ()=>{ if(a === voiceAu){ stopVoice(); toast("Не удалось воспроизвести запись"); } };
  a.play().catch(()=>{ if(a === voiceAu && a.error){ stopVoice(); toast("Не удалось воспроизвести запись"); } });
  syncVoice();
}
function fmtSize(b){
  if(!b) return "";
  if(b < 1024) return `${b} Б`;
  if(b < 1024*1024) return `${Math.round(b/1024)} КБ`;
  return `${(b/1024/1024).toFixed(b < 10*1024*1024 ? 1 : 0).replace(".", ",")} МБ`;
}
const IC_FILE = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9V3.498C5 2.671 5.672 2 6.5 2V2C7.328 2 8 2.671 8 3.498V9.999C8 11.657 6.657 13 5 13V13C3.343 13 2 11.657 2 9.999V5.001"/><path d="M11 8.001H16"/><path d="M11 12.001H16"/><path d="M9 16.001H16"/><path d="M5 13V19.001C5 20.107 5.895 20.999 7 20.999H18C19.105 20.999 20 20.107 20 19.001V5.001C20 3.895 19.105 2.999 18 2.999H8"/></svg>`;
const IC_DL = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M11.998 17V2.999"/><path d="M21 17C21 19.21 19.207 20.999 17.001 20.999H7C4.789 20.999 3 19.21 3 17"/><path d="M17.001 12.001L11.998 17L7 12.001"/></svg>`;
const IC_TOCHAT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.95 7.05L7.05 16.95"/><path d="M10 7L16.95 7.049L17 14"/></svg>`;
function renderFiles(){
  const fs = curTask.files||[];
  $("#files").style.display = $("#filesLbl").style.display = fs.length ? "" : "none";
  $("#files").innerHTML = fs.map((f,i)=>`<div class="frow" data-i="${i}" role="button" tabindex="0">
      <span class="fic">${IC_FILE}</span>
      <span class="fn"><b>${esc(f.name)}</b><span>${[fmtSize(f.size), f.author, !f.url && f.link ? "больше 20 МБ — откроется в чате" : ""].filter(Boolean).map(esc).join(" · ")}</span></span>
      ${f.url ? `<span class="fdl">${IC_DL}</span>` : ""}
      ${f.link ? `<button class="fchat" aria-label="К сообщению в чате">${IC_TOCHAT}</button>` : ""}
    </div>`).join("");
  $("#files").querySelectorAll(".frow").forEach(row=>{
    const f = fs[+row.dataset.i];
    row.onclick = ()=>downloadFile(f);
    const c = row.querySelector(".fchat");
    if(c) c.onclick = ev=>{ ev.stopPropagation(); openMsg(f.link); };
  });
}
function openMsg(url){
  if(LIVE && url && url.startsWith("https://t.me/")) tg.openTelegramLink(url); else toast("Откроется сообщение в чате");
}
function downloadFile(f){
  haptic();
  if(!f.url){ if(f.link) openMsg(f.link); else toast("Файл не скачать: больше 20 МБ"); return; }
  if(!LIVE){ toast(`Скачается «${f.name}»`); return; }
  const url = new URL(f.url, location.href).href;
  // Telegram 8.0+: its own download dialog; older apps open the link in the browser
  if(tg.isVersionAtLeast?.("8.0") && tg.downloadFile){
    try{ tg.downloadFile({url, file_name:f.name}); return; }catch(e){}
  }
  tg.openLink(url);
}
function renderTask(anim=false){
  const t = curTask;
  $("#taskCode").textContent = `TK-${String(t.id).padStart(3,"0")}`;
  // no project name above the task; someone assigned from outside the project is told they see this one task only
  $("#taskEyebrow").hidden = !t.guest;
  $("#taskProj").textContent = t.guest ? "Вам видна только эта задача" : "";
  const pc = $("#taskProjCap"), pname = !t.guest && !isPersonal({id: t.pid}) && (projects.find(p=>p.id == t.pid)?.name || t.project);
  pc.hidden = !pname; pc.textContent = pname || "";
  $("#archTask").hidden = !!t.guest;
  $("#archLbl").textContent = t.archived ? "Вернуть из архива" : "В архив";
  // project picker: any non-guest task; hide only when there is nowhere else to put it
  $("#moveTask").hidden = !!t.guest || (isPersonal({id: t.pid}) && !projects.length);
  $("#titleInp").value = t.title; autosize($("#titleInp")); setTimeout(()=>autosize($("#titleInp")), 420);
  renderMeta();
  renderStrip();
  renderVoice(t);
  renderFiles();
  renderLinks();
  renderHistory();
  $("#toChat").style.display = (!LIVE || t.link) ? "" : "none";
  $("#showSrc").style.display = isPersonal({id: t.pid}) ? "none" : "";   // no chat behind «Без проекта»
  clearTimeout(copyT); resetCopy();
  renderDescr(); renderTalk();
  renderItems(anim); updRing(true);
}
/* the caption above the title: to the task's project — back, if the task was opened from that very project */
$("#taskProjCap").onclick = ()=>{
  const t = curTask; if(!t) return;
  if(stack.at(-2) === "s-tasks" && curProj?.id == t.pid){ back(); return; }
  curProj = projects.find(p=>p.id == t.pid) || {id: t.pid, name: t.project};
  filter = savedFilter(curProj.id); if(LIVE) tasks = []; ghostTasks();
  if(twoPanes()) back();   // two panes: the project fills the list on the left, nothing stays open on the right
  go("s-tasks"); loadTasks(true);
};
function updRing(silent=false){
  const t = curTask, d = done(t), n = total(t);
  renderMeta();
  const c = 169.6; const off = n ? c*(1-d/n) : c;
  $("#ring .val").style.strokeDashoffset = off;
  const ring = $("#ring"), was = ring.classList.contains("full"), full = n>0 && d===n;
  ring.classList.toggle("full", full);
  if(full && !was && !silent){
    ring.classList.remove("celebrate"); void ring.offsetWidth; ring.classList.add("celebrate");
    tg?.HapticFeedback?.notificationOccurred?.("success");
    setTimeout(()=>toast("Все пункты выполнены"), 250);
  }
  $("#ringLbl").textContent = `${d}/${n}`;
}
let demoIid = 0;
function renderItems(anim=false, popLast=false){
  const t = curTask;
  t.items.forEach(it=>{ if(it.id == null) it.id = -(++demoIid); });   // demo items: an id to hang comments on
  if(openRow?.matches(".item")){ openRow = null; updBack(); }   // the rows are rebuilt below
  $("#items").innerHTML = t.items.map((it,i)=>`
    <div class="item ${it.d?"done":""} ${anim?"enter":""} ${popLast && i===t.items.length-1?"pop-in":""}" style="--i:${i}" data-i="${i}">
      <button class="check" aria-label="Отметить"><svg viewBox="0 0 24 24"><path d="M20 6.501L9 17.501L4 12.498"/></svg></button>
      <div class="txt"><span class="body">${linkify(it.t)}</span>${(()=>{ const n = itemComments(t, it).length; return n ? `<button class="icomm" data-ic="${i}">${bIc("comm")}${n}</button>` : ""; })()}</div>
      ${it.s && t.shots[it.s-1] ? `<button class="mini" data-m="${it.s-1}">${shotHTML(t.shots[it.s-1])}</button>` : ""}
      ${(it.m && it.m.length) ? `<button class="src" aria-label="К сообщению в чате"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.95 7.05L7.05 16.95"/><path d="M10 7L16.95 7.049L17 14"/></svg></button>` : ""}
      <div class="idone" aria-hidden="true"><svg class="i-undo" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14L4 9L9 4"/><path d="M4 9H14.5C17.5 9 20 11.5 20 14.5C20 17.5 17.5 20 14.5 20H8"/></svg><svg class="i-do" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5L10 17.5L19 7.5"/></svg></div>
      <div class="iacts"><button class="act-comm" data-a="comm" aria-label="Комментарий"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M19.5 11.5C19.5 15.4 16.1 18.5 12 18.5C11 18.5 10 18.3 9.1 18L4.5 19.5L5.7 15.8C4.9 14.6 4.5 13.1 4.5 11.5C4.5 7.6 7.9 4.5 12 4.5C16.1 4.5 19.5 7.6 19.5 11.5Z"/></svg></button><button class="act-ren" data-a="edit" aria-label="Изменить"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M17.539 10.12L13.88 6.46"/><path fill-rule="evenodd" clip-rule="evenodd" d="M6.251 21.001H3V17.75C3 17.484 3.105 17.23 3.293 17.042L16.627 3.709C17.018 3.316 17.649 3.316 18.042 3.709L20.294 5.956C20.683 6.349 20.683 6.984 20.294 7.373L6.958 20.706C6.771 20.894 6.516 21.001 6.251 21.001V21.001Z"/></svg></button><button class="act-del" data-a="del" aria-label="Удалить"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 7.012H19"/><path d="M18.001 7.012V17.983C18.001 19.089 17.105 19.989 16 19.989H8C6.895 19.989 6 19.089 6 17.983V7.012"/><path d="M15 3.737H9"/><path d="M10 10.983V16.018"/><path d="M14 10.983V16.018"/></svg></button></div>
    </div>`).join("") + `
    <button class="add-row" id="addBtn"><span class="plus"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path fill-rule="evenodd" clip-rule="evenodd" d="M18 19.999H6C4.895 19.999 4 19.098 4 17.993V5.998C4 4.893 4.895 3.992 6 3.992H18C19.105 3.992 20 4.893 20 5.998V17.993C20 19.098 19.105 19.999 18 19.999Z"/><path d="M12 8.004V15.987"/><path d="M16 12.016H8"/></svg></span><span class="add-l">Добавить пункт</span></button>`;

  document.querySelectorAll("#items [data-ic]").forEach(b=>b.onclick = e=>{ e.stopPropagation(); openComments(t.items[+b.dataset.ic]); });
  document.querySelectorAll(".item").forEach(row=>{
    const i = +row.dataset.i, it = t.items[i];
    row.querySelector(".check").onclick = ()=>{
      const openBefore = t.items.some(x=>!x.d), closedBefore = isClosed(t);
      it.d = !it.d; flipClosed(t, openBefore); row.classList.toggle("done", it.d); haptic(it.d?"medium":"light"); updRing();
      if(!closedBefore && isClosed(t)) setTimeout(()=>reportAfterClose(t), 900);   // the last item closed it: ask for the report too
      if(it.d){ replay(row.querySelector(".check"), "tick"); replay(row, "flash"); }
      // fast taps: send only the final state, 0.3 s after the last tap
      clearTimeout(it._t);
      it._t = setTimeout(()=>{ it._t = null; save(()=>api(`/api/items/${it.id}`,"PATCH",{done:it.d}), false, t.id, ()=>{   // «Отменить»: the tick goes back
        const k = curTask?.id === t.id ? t.items.indexOf(it) : -1;
        document.querySelector(`.item[data-i="${k}"] .check`)?.click();
      }); }, 300);
    };
    initItemSwipe(row, i);
    const m = row.querySelector(".mini"); if(m) m.onclick = ()=>openShot(+m.dataset.m);
    row.querySelectorAll("a.ln").forEach(a=>a.onclick = ev=>{
      ev.preventDefault(); ev.stopPropagation();
      const url = a.dataset.url.replace(/&amp;/g,"&");
      if(tg) tg.openLink(url); else window.open(url, "_blank", "noopener");
    });
    const src = row.querySelector(".src");
    if(src) src.onclick = ()=>{
      const url = it.m[0];
      if(LIVE && url.startsWith("https://t.me/")) tg.openTelegramLink(url);
      else toast(it.mtxt ? `Сообщение: ${it.mtxt}` : "Откроется сообщение в чате");
    };
  });
  $("#addBtn").onclick = ()=>openItemEditor(null);
}

/* ---------- Item editor: a card from the bottom, lifted above the keyboard (--kb) ---------- */
function openItemEditor(i){
  const t = curTask, isNew = i === null, it = isNew ? {t:"", s:null} : t.items[i];
  const sh = $("#itemSheet"), inp = $("#itemInp");
  let shot = it.s || null;
  $("#itemSheetTitle").textContent = isNew ? "Новый пункт" : "Пункт";
  $("#itemDel").style.display = isNew ? "none" : "";
  inp.value = isNew ? (cloud.get("di_"+t.id) || "") : it.t;
  inp.dataset.orig = it.t;
  const draft = isNew ? keepDraft("di_"+t.id, ()=>inp.value) : null;
  const pick = ()=>{
    $("#shotPick").style.display = "";
    $("#shotPick").innerHTML = `<button class="chip ${!shot?"on":""}" data-k="0" aria-label="Без скрина"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="M4.5 20.5L19.5 3.5"/></svg></button>` +
      t.shots.map((src,k)=>`<button class="chip shot-chip ${shot===k+1?"on":""}" data-k="${k+1}">${shotHTML(src)}</button>`).join("") +
      `<button class="chip add-chip" id="chipAdd" aria-label="Добавить скриншот"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6V18"/><path d="M6 12H18"/></svg></button>`;
    $("#shotPick").querySelectorAll(".chip[data-k]").forEach(c=>c.onclick = ()=>{ shot = +c.dataset.k || null; haptic(); pick(); });
    $("#chipAdd").onclick = async ()=>{ const n = await pickImages(false); if(n){ shot = curTask.lastShot; pick(); } };
  };
  pick();
  sh.classList.add("open");
  const fit = ()=>{ inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 240) + "px"; };
  inp.oninput = ()=>{ fit(); draft?.(); }; requestAnimationFrame(fit); focusField(inp);
  const close = ()=>{ inp.blur(); sh.classList.remove("open"); };
  $("#itemCancel").onclick = ()=>{ if(isNew) cloud.set("di_"+t.id, null); close(); };
  sh.onclick = e=>{ if(e.target === sh) close(); };
  $("#itemSave").onclick = async ()=>{
    const v = inp.value.trim().replace(/\s*\n\s*/g, " ");
    if(!v){ inp.focus(); return; }
    close();
    if(isNew){
      cloud.set("di_"+t.id, null);
      t.items.push({t:v, d:false, s:shot}); renderItems(false, true); updRing();
      await save(async ()=>{
        let r = await api(`/api/tasks/${t.id}/items`,"POST",{text:v});
        const added = r.items[r.items.length-1];
        if(shot && added) r = await api(`/api/items/${added.id}`,"PATCH",{shot});
        return r;
      }, false, t.id, ()=>{   // «Отменить»: the new item goes away again
        const k = curTask?.id === t.id ? t.items.map(x=>x.t).lastIndexOf(v) : -1;
        if(k >= 0) deleteItem(k);
      });
      if(LIVE) renderItems();
      return;
    }
    if(v === it.t && shot === (it.s||null)) return;
    const old = {t: it.t, s: it.s || null};
    it.t = v; it.s = shot; renderItems();
    save(()=>api(`/api/items/${it.id}`,"PATCH",{text:v, shot:shot||0}), false, t.id, ()=>{   // «Отменить»: the old text and screenshot
      it.t = old.t; it.s = old.s; if(curTask?.id === t.id) renderItems();
      save(()=>api(`/api/items/${it.id}`,"PATCH",{text:old.t, shot:old.s||0}), false, t.id);
    });
  };
  $("#itemDel").onclick = ()=>{ close(); deleteItem(i); };
}
async function deleteItem(i){
  const t = curTask, it = t.items[i], row = document.querySelector(`.item[data-i="${i}"]`), pos = t.items.indexOf(it);
  haptic("medium"); if(row) await collapse(row);
  t.items.splice(pos, 1); renderItems(); updRing();
  const undo = ()=>restoreItem(t, it, pos);
  if(!LIVE){ toast("Демо: пункт удалён", null, {undo}); return; }
  try{ const fresh = fromApi(await api(`/api/items/${it.id}`,"DELETE")); if(curTask?.id === t.id) applyFresh(fresh, true); toast("Пункт удалён", null, {undo}); }
  catch(e){ fail(e); }
}
/* «Отменить» after deleting an item: it comes back with its text, tick and place */
async function restoreItem(t, it, pos){
  if(!LIVE){ t.items.splice(pos, 0, it); if(curTask?.id === t.id){ renderItems(); updRing(); } toast("Пункт возвращён"); return; }
  try{
    let full = await api(`/api/tasks/${t.id}/items`, "POST", {text: it.t});
    const ids = full.items.map(x=>x.id), nid = ids.pop();
    if(it.d) full = await api(`/api/items/${nid}`, "PATCH", {done: true});
    ids.splice(Math.min(pos, ids.length), 0, nid);
    full = await api(`/api/tasks/${t.id}/order`, "PUT", {item_ids: ids});
    if(curTask?.id === t.id) applyFresh(fromApi(full), true);
    toast("Пункт возвращён");
  }catch(e){ fail(e); }
}
/* Swipe an item right-to-left → «Изменить» / «Удалить» (a tap on the text does nothing: no accidental edits) */
const ITEM_W = 168, DONE_AT = 64;   // three actions: comment · edit · delete
function initItemSwipe(row, i){
  let x0 = null, y0, base, mode = null, moved = false, st = {}, hold = null, drag = null;
  const stopHold = ()=>{ clearTimeout(hold); hold = null; };
  row.onpointerdown = e=>{
    if(e.target.closest(".iacts")) return;
    x0 = e.clientX; y0 = e.clientY; base = row._x || 0; mode = null; moved = false; st = {};
    stopHold();
    if(openRow || curTask.items.length < 2) return;
    hold = setTimeout(()=>{   // held still: the item lifts and follows the finger
      hold = null; mode = "drag"; moved = true;
      try{ row.setPointerCapture(e.pointerId); }catch(_){}
      drag = itemDrag(row, y0);
    }, HOLD_MS);
  };
  row.oncontextmenu = e=>e.preventDefault();
  row.onpointermove = e=>{
    if(x0 == null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if(mode === "drag"){ drag.move(e.clientY); return; }
    if(!mode){
      if(Math.abs(dx) > 8 || Math.abs(dy) > 8) stopHold();
      if(Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)){ mode = "x"; try{ row.setPointerCapture(e.pointerId); }catch(_){} row.classList.add("swiping"); if(openRow && openRow!==row) closeRow(); }
      else if(Math.abs(dy) > 8){ mode = "y"; }
    }
    if(mode === "x"){
      moved = true; trackV(st, e);
      const x = rubber(base + dx, -ITEM_W, base === 0 ? DONE_AT : 0);   // from rest it may go right: that is «done»
      setRow(row, x);
      const armed = base === 0 && x >= DONE_AT;
      if(armed !== row.classList.contains("armed")){ row.classList.toggle("armed", armed); if(armed) haptic(); }
    }
  };
  row.onpointerup = row.onpointercancel = ()=>{
    stopHold();
    if(x0 == null) return;
    x0 = null; row.classList.remove("swiping");
    if(mode === "drag"){ drag.end(); drag = null; mode = null; return; }
    if(mode === "x"){
      if(row.classList.contains("armed")){   // pulled right far enough: tick it (or untick), the row springs back
        row.classList.remove("armed"); setRow(row, 0);
        setTimeout(()=>row.querySelector(".check")?.onclick?.(), 120);
        return;
      }
      const to = settleRow(row._x || 0, base, st.v || 0, 40, ITEM_W);
      if(to){ setRow(row, to); if(openRow !== row) haptic(); openRow = row; updBack(); } else { setRow(row, 0); if(openRow===row){ openRow = null; updBack(); } }
    }
  };
  // after a swipe, or while the row is open, a tap must not tick the item or follow a link
  row.addEventListener("click", e=>{
    if(e.target.closest(".iacts")) return;
    if(moved){ moved = false; e.stopPropagation(); e.preventDefault(); }
    else if(openRow === row){ closeRow(); e.stopPropagation(); e.preventDefault(); }
  }, true);
  row.querySelectorAll("[data-a]").forEach(b=>b.onclick = ()=>{
    closeRow();
    if(b.dataset.a === "comm") openComments(curTask.items[i]); else if(b.dataset.a === "edit") openItemEditor(i); else deleteItem(i);
  });
}
function autosize(el){ el.style.height="auto"; el.style.height=el.scrollHeight+"px"; }
window.addEventListener("resize", ()=>autosize($("#titleInp")));
document.fonts?.ready.then(()=>autosize($("#titleInp")));
$("#titleInp").oninput = e=>autosize(e.target);
$("#titleInp").onchange = e=>{
  const v = e.target.value.trim(); if(!v) return;
  const t = curTask, old = t.title; if(v === old) return;
  const put = x=>{ everyCopy(t.id, c=>c.title = x); t.title = x; renderTasks(); if(onPeople() || ppl.data) renderPeople(); };   // the lists beside the task (two panes) show the new name at once
  put(v); save(()=>api(`/api/tasks/${t.id}`,"PATCH",{title:v}), false, t.id, ()=>{
    put(old); if(curTask?.id === t.id){ $("#titleInp").value = old; autosize($("#titleInp")); }
    save(()=>api(`/api/tasks/${t.id}`,"PATCH",{title:old}), false, t.id);
  });
};
/* «Скопировать ссылку»: t.me/<bot>?startapp=task_<id> — opens this task in the app for anyone in the chat */
let linkTpl = null;
async function taskLink(t){
  if(!LIVE) return `https://t.me/your_bot?startapp=task_${t.id}`;
  if(!linkTpl){ try{ linkTpl = (await api("/api/me")).link; }catch(e){ fail(e); return null; } }
  return linkTpl.replace("{param}", `task_${t.id}`);
}
async function copyText(text){
  try{ await navigator.clipboard.writeText(text); return true; }catch(_){}
  const ta = document.createElement("textarea");            // older WebViews: the classic way
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;font-size:16px";
  document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
  let ok = false; try{ ok = document.execCommand("copy"); }catch(_){}
  ta.remove(); return ok;
}
let copyT = null;
$("#copyLink").onclick = async ()=>{
  const t = curTask, url = await taskLink(t); if(!url) return;
  if(await copyText(url)){
    haptic("light");   // said right on the tile: green, a tick, «Скопировано» — no message on top
    $("#copyLink").classList.add("ok");
    $("#copyLbl").textContent = "Скопировано";
    $("#copyIc").innerHTML = '<path d="M5 12.5L10 17.5L19 7.5"/>';
    clearTimeout(copyT); copyT = setTimeout(resetCopy, 1800);
  } else if(tg?.showPopup){
    tg.showPopup({title:"Ссылка на задачу", message:url, buttons:[{type:"close"}]});
  } else prompt("Ссылка на задачу", url);
};
function resetCopy(){
  $("#copyLink").classList.remove("ok");
  $("#copyLbl").textContent = "Копировать ссылку";
  $("#copyIc").innerHTML = '<rect x="6.998" y="7.008" width="14.006" height="14.006" rx="2"/><path d="M6.998 16.997H4.996C3.891 16.997 2.995 16.096 2.995 14.991V5.002C2.995 3.897 3.891 2.996 4.996 2.996H15.002C16.107 2.996 17.003 3.897 17.003 5.002V7.008"/>';
}
$("#showSrc").onclick = ()=>{ haptic(); showSource(curTask); };
$("#toChat").onclick = ()=>{ if(LIVE && curTask.link) tg.openTelegramLink(curTask.link); else toast("Переход к сообщению в чате"); };

/* «В архив» / «Вернуть из архива»: a tile under the task; «Отменить» in the message brings it back */
$("#archTask").onclick = async ()=>{
  const arch = !curTask.archived; haptic("medium");
  if(LIVE){ try{ curTask = fromApi(await api(`/api/tasks/${curTask.id}`,"PATCH",{archived:arch})); }catch(err){ fail(err); return; } }
  else curTask.archived = arch;
  const at = curTask;
  everyCopy(at.id, x=>x.archived = arch);
  toast(arch?"Задача в архиве":"Задача возвращена", null, {undo: async ()=>{   // back where it was, from any screen
    if(LIVE){ try{ await api(`/api/tasks/${at.id}`, "PATCH", {archived: !arch}); }catch(err){ fail(err); return; } }
    everyCopy(at.id, x=>x.archived = !arch); at.archived = !arch; renderTasks(); if(ppl.data) renderPeople();
    if(curTask === at && stack.at(-1) === "s-task") renderTask();
    toast(arch ? "Задача возвращена из архива" : "Задача снова в архиве");
  }});
  setTimeout(back, 350);
};

/* «Проект»: move into another project or «Без проекта»; current project is hidden from the list */
$("#moveTask").onclick = ()=>{
  const t = curTask; if(!t || t.guest) return;
  const pers = personalProj();
  const choices = [...projects.filter(p=>p.id != t.pid), ...(isPersonal({id: t.pid}) ? [] : [pers])];
  if(!choices.length) return;
  haptic();
  const sh = $("#projSheet"), box = $("#projPickList");
  const ck = '<svg class="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6.501L9 17.501L4 12.498"/></svg>';
  $("#projSheetTitle").textContent = "Проект";
  box.innerHTML = choices.map((p, k)=>`<button data-k="${k}"><span class="pp-dot" style="background:${p.color || "var(--hint)"}"></span><span class="nm">${esc(p.name || "")}</span>${ck}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick = async ()=>{
    sh.classList.remove("open"); haptic("medium");
    const p = choices[+b.dataset.k]; if(!p) return;
    const body = {project_id: isPersonal(p) ? null : p.id};
    if(LIVE){
      try{ curTask = fromApi(await api(`/api/tasks/${t.id}/move`, "POST", body)); }
      catch(err){ fail(err); return; }
    } else {
      const from = isPersonal({id: t.pid}) ? PERSONAL_NAME : (projects.find(x=>x.id == t.pid)?.name || t.project || "");
      t.pid = isPersonal(p) ? pers.id : p.id; t.project = p.name;
      (t.history ||= []).unshift({ts: Math.floor(Date.now()/1000), actor: me?.name || "Вы", kind: "moved", text: p.name,
        extra: {id: t.pid, to: t.pid, project: p.name, from_project: from}});
      curTask = t;
    }
    everyCopy(t.id, x=>{ x.pid = curTask.pid; x.project = curTask.project; });
    if(ppl.data) renderPeople();
    renderTasks();
    renderTask();
    toast(isPersonal(p) ? "Без проекта" : `В проекте «${p.name}»`);
  });
  sh.classList.add("open");
  sh.onclick = e=>{ if(e.target === sh) sh.classList.remove("open"); };
};

/* Lightbox */
function openLB(start){
  const s = curTask.shots, lb = $("#lb"), tr = $("#lbTrack");
  // build the slides once per task and reuse them, so already loaded pictures are not reloaded (no blink)
  const key = s.join("|");
  if(tr.dataset.key !== key){
    tr.innerHTML = s.map(k=>{
      const isUrl = k.includes("/");
      const pic = isUrl
        ? `<i class="sk img-sk"></i><img src="${k}" alt="" decoding="async" onload="this.previousElementSibling?.remove()" onerror="imgFailed(this)" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">`
        : shotHTML(k);
      return `<div class="slide"><div class="img" style="position:relative;overflow:hidden">${pic}</div></div>`;
    }).join("");
    tr.dataset.key = key;
  }
  $("#lbDots").innerHTML = s.length > 1 ? s.map((_,i)=>`<span class="${i===start?"on":""}"></span>`).join("") : "";
  lb.classList.add("open"); tg?.BackButton.show();
  tr.scrollLeft = start * tr.offsetWidth;            // jump to the right picture while still invisible
  const upd = ()=>{ const i = Math.round(tr.scrollLeft/tr.offsetWidth); $("#lbCount").textContent = `${i+1} из ${s.length}`; [...$("#lbDots").children].forEach((d,j)=>d.classList.toggle("on",j===i)); };
  tr.onscroll = upd; upd();
  void lb.offsetWidth; lb.classList.add("show");      // force layout, then fade in (works even in a background tab)
}
$("#lbClose").onclick = closeLB;
const inViewer = e => !!e.target?.closest?.(".vibe-lightbox");
["gesturestart","gesturechange","gestureend"].forEach(ev=>document.addEventListener(ev, e=>{ if(!inViewer(e)) e.preventDefault(); }, {passive:false}));
document.addEventListener("touchmove", e=>{ if(e.touches.length > 1 && !inViewer(e)) e.preventDefault(); }, {passive:false});

/* Full-screen screenshots: vibe-lightbox (pinch / double-tap zoom anywhere, pan with inertia, grows out of the thumbnail) */
if(window.VibeLightbox){
  VibeLightbox.init({
    hash: false,   // Telegram keeps its own data in the URL hash; Back is handled by back()
    paging: "slide",   // usual side-by-side paging instead of the curtain
    labels: { dialog: "Скриншот", close: "Закрыть", prev: "Предыдущий скриншот", next: "Следующий скриншот", thumb: "Скриншот {n}" },
    onOpen: i=>{ haptic(); updBack(); if(i?.group === "shots") addDelBtn(); else document.querySelector(".vibe-lightbox .lb-del")?.remove(); },
    onClose: ()=>setTimeout(updBack, 0),
  });
}
/* Delete the screenshot on screen: the viewer has no API for its current photo, its counter «2 / 3» tells it */
function addDelBtn(){
  const box = document.querySelector(".vibe-lightbox");
  if(!box || box.querySelector(".lb-del")) return;
  const b = document.createElement("button");
  b.className = "vibe-lightbox__btn lb-del"; b.type = "button"; b.setAttribute("aria-label", "Удалить скриншот");
  b.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7.012H19"/><path d="M18.001 7.012V17.983C18.001 19.089 17.105 19.989 16 19.989H8C6.895 19.989 6 19.089 6 17.983V7.012"/><path d="M15 3.737H9"/><path d="M10 10.983V16.018"/><path d="M14 10.983V16.018"/></svg>';
  b.onclick = e=>{ e.stopPropagation(); const n = parseInt(box.querySelector(".vibe-lightbox__counter")?.textContent) || 1; deleteShot(n - 1); };
  box.appendChild(b);
}
function deleteShot(i){
  const t = curTask; if(!t?.shots?.[i]) return;
  const used = t.items.filter(it=>it.s === i + 1).length;
  const msg = `Удалить скриншот ${i + 1}?` + (used ? ` ${used === 1 ? "Пункт, к которому он привязан, останется" : "Пункты, к которым он привязан, останутся"} без скриншота.` : "");
  const go = async ok=>{
    if(!ok) return;
    haptic("medium");
    if(window.VibeLightbox?.isOpen()) VibeLightbox.close();
    if(!LIVE){
      t.shots.splice(i, 1);
      t.items.forEach(it=>{ if(it.s === i + 1) it.s = null; else if(it.s > i + 1) it.s--; });
      setTimeout(()=>{ renderTask(); toast("Демо: скриншот удалён"); }, 340);
      return;
    }
    try{
      const fresh = fromApi(await api(`/api/tasks/${t.id}/media/${t.shotIdx?.[i] ?? i + 1}`, "DELETE"));
      setTimeout(()=>{ if(curTask?.id === fresh.id){ curTask = fresh; renderTask(); } toast("Скриншот удалён"); }, 340);   // after the viewer has folded away
    }catch(e){ toast("Не удалось удалить скриншот"); }
  };
  if(tg?.showConfirm) tg.showConfirm(msg, go); else go(confirm(msg));
}
function openShot(i){
  const src = curTask.shots[i];
  if(window.VibeLightbox && src && src.includes("/") && document.querySelector('[data-lightbox="shots"]')) VibeLightbox.open("shots", i);
  else openLB(i);   // demo pictures drawn with CSS
}
let lastTap = 0;
$("#lbTrack").addEventListener("pointerup", e=>{
  const img = e.target.closest(".img"); if(!img) return;
  const now = Date.now();
  if(now - lastTap < 300){ img.style.transform = img.style.transform ? "" : "scale(1.9)"; lastTap = 0; } else lastTap = now;
});
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && layerOpen()){ e.preventDefault(); back(); } });

/* Theme switcher of the browser demo: the same setting as in «Настройки» */
document.querySelectorAll("#themeSw button").forEach(b=>b.onclick = ()=>setTheme(b.dataset.t));

/* ---------- Background refresh: changes made elsewhere (MCP agent, buttons in the chat, other people) ---------- */
const POLL_MS = 5000, NOTIF_POLL_MS = 30000;
const unsigned = s => String(s||"").replace(/\?e=\d+&s=\w+/, "");   // picture links are re-signed hourly: that is not a change
const projKey = a => JSON.stringify(a.map(p=>[p.id, p.name, p.open, p.items_done, p.items_total, p.today, unsigned(p.photo), p.bot_admin]));
const listKey = a => JSON.stringify(a.map(t=>[t.id, t.title, !!t.archived, !!t.backlog, !!t.progress, !!t.review, done(t), total(t), unsigned(t.thumb || t.shots?.[0]), t.assignee?.id]));
const taskKey = t => JSON.stringify([t.title, !!t.archived, !!t.backlog, !!t.progress, !!t.review, t.link, (t.shots||[]).map(unsigned), (t.videos||[]).length,
  (t.transcripts||[]).map(x=>[x.key, !!x.summary]), (t.files||[]).map(f=>f.name), t.due?.at, (t.reminders||[]).join(), t.assignee?.id, t.items.map(i=>[i.id, i.t, !!i.d, i.s]), (t.history||[]).length]);
/* never redraw under the user's fingers: open sheets, a swipe, typing, a tick not sent yet, a screenshot uploading */
function pollBlocked(){
  return document.hidden || apiBusy || leaving || ghosting || lifted || layerOpen() || document.querySelector(".swipe.dragging, .item.swiping, #strip .shot.sk")
      || /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName) || curTask?.items?.some(i=>i._t);
}
let polling = false;
async function poll(){
  if(polling || pollBlocked()) return;
  lastPoll = Date.now(); liveDirty = false;
  const screen = stack.at(-1), gen = apiGen, pid = curProj?.id, tid = curTask?.id;
  if(screen === "s-task" && !tid) return;
  if(screen === "s-history"){ polling = true; try{ await refreshFeed(); }finally{ polling = false; } return; }
  if(screen === "s-notif"){ polling = true; try{ await loadNotif(false, true); }finally{ polling = false; } return; }
  if(screen === "s-people"){ polling = true; try{ await loadPeople(true); }finally{ polling = false; } return; }
  const path = screen === "s-projects" ? "/api/projects" : screen === "s-tasks" ? `/api/projects/${pid}/tasks` : `/api/tasks/${tid}`;
  polling = true;
  try{
    const data = await api(path, "GET", undefined, true);
    if(gen !== apiGen || pollBlocked() || stack.at(-1) !== screen || curProj?.id !== pid || curTask?.id !== tid) return;   // stale answer
    if(screen === "s-projects"){
      const changed = projKey(data) !== projKey(projects);
      projects = data; if(changed) renderProjects();
    } else if(screen === "s-tasks"){
      const changed = listKey(data) !== listKey(tasks);
      tasks = data; if(changed) renderTasks();
    } else {
      const fresh = fromApi(data);
      if(taskKey(fresh) !== taskKey(curTask)){ curTask = fresh; renderTask(); }
    }
  }catch(_){ /* offline or the session expired: stay quiet, the next tick tries again */ }
  finally{ polling = false; }
}
/* Pushes from the server (/api/live, see app/live.py): «something changed» → refresh the screen now. While the
   stream is up the timer is only a backstop (LIVE_BACKUP_MS); without it (old proxy, no network) — the 5 s poll.
   A push that lands while the user is busy (typing, a sheet open) waits in liveDirty for the first free tick. */
const LIVE_BACKUP_MS = 30000;
let liveOn = false, liveDirty = false, lastPoll = 0;
async function liveStream(){
  for(let wait = 1000; ; wait = Math.min(wait * 2, 30000)){
    try{
      const r = await fetch("/api/live", {cache: "no-store", headers: {"Authorization": authHeader()}});
      if(r.status === 401 || r.status === 404) return;   // signed out, or a server without pushes: the poll does it all
      if(!r.ok || !r.body) throw new Error(r.status);
      const rd = r.body.getReader(), dec = new TextDecoder();
      liveOn = true; wait = 500;
      for(;;){
        const {done, value} = await rd.read();
        if(done) break;
        if(dec.decode(value, {stream: true}).includes("data:")){
          liveDirty = true; poll();
          if(!document.hidden && stack.at(-1) !== "s-notif") loadNotifCount();
        }
      }
    }catch(_){ /* the network dropped or the server restarts: try again a little later */ }
    if(liveOn){ liveOn = false; liveDirty = true; }   // something may have changed while it was down
    await sleep(wait);
  }
}
if(LIVE){
  setInterval(()=>{ if(liveDirty || Date.now() - lastPoll >= (liveOn ? LIVE_BACKUP_MS : POLL_MS)) poll(); }, 1000);
  if(!(tg?.web && !siteSession)) liveStream();
  setInterval(()=>{ if(!document.hidden && stack.at(-1) !== "s-notif") loadNotifCount(); }, NOTIF_POLL_MS);   // the red number on the menu
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadNotifCount(); });
  document.addEventListener("visibilitychange", poll);   // back to the app: refresh at once, without waiting for the tick
  tg.onEvent("activated", poll);
}

/* ---------- Telegram's cloud storage: per-person settings and drafts, the same on all their devices ----------
   Read once at start into memory, so reads are instant; writes go through. Outside Telegram — localStorage. */
const cloud = (()=>{
  const cs = tg?.CloudStorage && tg.isVersionAtLeast?.("6.9") ? tg.CloudStorage : null;
  const mem = {};
  const ls = (k, v)=>{ try{ if(v === undefined) return localStorage.getItem("c_"+k); v === null ? localStorage.removeItem("c_"+k) : localStorage.setItem("c_"+k, v); }catch(_){ return null; } };
  const ready = new Promise(res=>{
    if(!cs) return res();
    setTimeout(res, 1500);   // never hold the app up for long
    cs.getKeys((err, keys)=>{
      if(err || !keys?.length) return res();
      cs.getItems(keys, (e2, vals)=>{ if(!e2 && vals) Object.assign(mem, vals); res(); });
    });
  });
  return {
    ready,
    get: k => k in mem ? mem[k] : (cs ? null : ls(k)),
    set(k, v){
      v = v == null || v === "" ? null : String(v).slice(0, 4000);
      if(v === null){ delete mem[k]; cs?.removeItem(k); } else { mem[k] = v; cs?.setItem(k, v); }
      if(!cs) ls(k, v);
    },
  };
})();
/* a draft saved half a second after the last keystroke */
/* Focus a field of a pop-up — ALWAYS this way, never focus() in a setTimeout: called right inside the tap, so iOS raises
   the keyboard; and once the card has slid in, the caret is put back (WebKit leaves it undrawn if the field got focus
   while its card was still moving — the field looks focused, but no blinking caret). all: select the text. */
function focusField(inp, all = false){
  const place = ()=>{
    if(document.activeElement !== inp) return;
    if(all) inp.select(); else { const n = inp.value.length; try{ inp.setSelectionRange(n, n); }catch(_){} }
    inp.style.caretColor = "transparent"; requestAnimationFrame(()=>{ inp.style.caretColor = ""; });   // redraw the caret
  };
  try{ inp.focus({preventScroll: true}); }catch(_){ inp.focus(); }
  place();
  inp.closest(".card")?.addEventListener("transitionend", place, {once: true});
  setTimeout(place, 380);
}
function keepDraft(key, value){
  let tm;
  return ()=>{ clearTimeout(tm); tm = setTimeout(()=>cloud.set(key, value()), 500); };
}
const TABS = ["open","progress","backlog","closed","archive"];
function savedFilter(pid){ const f = cloud.get("f_"+pid); return TABS.includes(f) ? f : "open"; }

/* ---------- On-screen keyboard: how much of the bottom it covers (iOS overlays it on the page) ---------- */
(()=>{
  const vv = window.visualViewport, phone = document.querySelector(".phone");
  // Different webviews report the keyboard differently, so take the largest of:
  //  · the page below the visible area (iOS Safari-like: the keyboard covers the page, visualViewport shrinks);
  //  · the app's own box below the visible area (the box keeps Telegram's stable height while the view shrinks);
  //  · Telegram's own numbers: the current height dropping under the stable one.
  const upd = ()=>{
    let kb = 0;
    if(vv){
      const seen = vv.offsetTop + vv.height;
      kb = Math.max(kb, document.documentElement.clientHeight - seen, phone.getBoundingClientRect().bottom - seen);
    }
    if(tg?.viewportStableHeight && tg.viewportHeight) kb = Math.max(kb, tg.viewportStableHeight - tg.viewportHeight);
    const a = document.activeElement, typing = !!a && (/^(INPUT|TEXTAREA)$/.test(a.tagName) || a.isContentEditable);
    const up = typing && kb > 60;
    document.documentElement.style.setProperty("--kb", (up ? Math.round(kb) : 0) + "px");
    document.documentElement.classList.toggle("kb", up);
    if(up){ clearTimeout(seeT); seeT = setTimeout(()=>keepSeen(a, kb), 280); }   // after the pop-up has risen (.25s)
  };
  // the field being typed in — with its send button — stays above the keyboard: a pop-up's card or the screen scrolls it up
  let seeT;
  const keepSeen = (a, kb)=>{
    if(document.activeElement !== a) return;
    const box = a.closest(".composer") || a, card = a.closest(".sheet .card"), sc = !card && a.closest(".screen");
    const r = box.getBoundingClientRect(), gap = 12;
    if(card){
      const c = card.getBoundingClientRect();
      if(r.bottom > c.bottom - gap) card.scrollTop += r.bottom - c.bottom + gap;
      else if(r.top < c.top + gap) card.scrollTop -= c.top + gap - r.top;
    } else if(sc){
      const limit = phone.getBoundingClientRect().bottom - kb - gap;
      if(r.bottom > limit) sc.scrollTop += r.bottom - limit;   // at once: a smooth scroll gets dropped while the keyboard animates
    }
  };
  vv?.addEventListener("resize", upd); vv?.addEventListener("scroll", upd);
  tg?.onEvent("viewportChanged", upd);
  document.addEventListener("focusin", ()=>{ upd(); setTimeout(upd, 300); setTimeout(upd, 700); });
  document.addEventListener("focusout", ()=>setTimeout(upd, 50));
})();

/* ---------- Closing the app while typing: Telegram asks first ---------- */
function guardClose(){
  if(!tg?.enableClosingConfirmation) return;
  const dirty = [...document.querySelectorAll(".sheet.open textarea")].some(el=>el.value.trim() !== (el.dataset.orig || "").trim());
  dirty ? tg.enableClosingConfirmation() : tg.disableClosingConfirmation();
}
document.addEventListener("input", guardClose);
{ // every pop-up: a light tap of the vibration as it opens, and the closing guard re-checked
  const mo = new MutationObserver(ms=>{
    if(ms.some(m=>m.target.classList.contains("open") && !String(m.oldValue).split(" ").includes("open"))) haptic("light");
    guardClose();
  });
  document.querySelectorAll(".sheet").forEach(sh=>mo.observe(sh, {attributes:true, attributeFilter:["class"], attributeOldValue:true}));
}


/* ---------- Settings: private notifications, home screen icon ---------- */
let me = LIVE ? null : {notify:false};
let homeSt = LIVE ? "unsupported" : "missed";   // Telegram: unsupported / unknown / added / missed
async function loadMe(){
  if(LIVE){ try{ me = await api("/api/me", "GET", null, true); }catch(_){ return; } }
  renderSettings();
}
const NOTIFY_KINDS = {   // what the bot sends to the private chat; the order of the sections in «Уведомления»
  questions: ["Вопросы", "Задали вопрос, вопрос решён или снова открыт"], comments: ["Комментарии", "Кто-то написал комментарий к задаче"],
  status: ["Смена статуса", "Взяли в работу, отправили на проверку, закрыли, отложили, открыли снова"],
  assignee: ["Исполнитель", "Назначили или сняли исполнителя"], due: ["Срок", "Поставили, изменили или убрали срок"],
  items: ["Новые пункты", "Кто-то добавил пункт в задачу"], shots: ["Скриншоты", "К задаче добавили скриншот"],
  changes: ["Название и описание", "Задачу переименовали или изменили описание"]};
function renderSettings(){
  const on = !!me?.notify;
  $("#notifySw").classList.toggle("on", on); $("#setNotify").setAttribute("aria-checked", on);
  const kinds = me?.notify_kinds || Object.keys(NOTIFY_KINDS), box = $("#notifyKinds");
  box.hidden = !on;
  box.innerHTML = Object.entries(NOTIFY_KINDS).map(([k, [t, sub]])=>`<button class="set-row" data-k="${k}" role="switch" aria-checked="${kinds.includes(k)}"><span class="tx"><b>${t}</b><span>${sub}</span></span><span class="sw${kinds.includes(k) ? " on" : ""}"></span></button>`).join("");
  box.querySelectorAll("[data-k]").forEach(b=>b.onclick = ()=>setNotifyKind(b.dataset.k, !kinds.includes(b.dataset.k), b));
  $("#notifyAsk").hidden = !me || on || !!cloud.get("nAsk");
  const h = $("#setHome");
  h.hidden = homeSt === "unsupported";
  h.classList.toggle("done", homeSt === "added");
  $("#homeSub").textContent = homeSt === "added" ? "Уже добавлена" : "Открывает приложение сразу, минуя список чатов";
  $("#homeIc").innerHTML = homeSt === "added" ? IC_OK : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6V18"/><path d="M6 12H18"/></svg>`;
}
async function setNotify(on){
  if(!me) return false;
  if(!LIVE){ me.notify = on; renderSettings(); toast(on ? "Демо: уведомления включены" : "Демо: уведомления выключены"); return true; }
  if(on && tg.requestWriteAccess && tg.isVersionAtLeast("6.9")){
    const ok = await new Promise(r=>tg.requestWriteAccess(r));
    if(!ok){ toast("Без разрешения бот не сможет вам писать"); return false; }
  }
  const row = $("#setNotify"); row.classList.add("busy");
  try{ me.notify = (await api("/api/me/notify", "POST", {on})).notify; }
  catch(e){ toast(on ? "Не получилось: откройте чат с ботом и нажмите «Старт»" : "Не удалось сохранить"); return false; }
  finally{ row.classList.remove("busy"); renderSettings(); }
  haptic("medium"); toast(on ? "Готово: бот прислал сообщение в личку" : "Уведомления выключены");
  return true;
}
async function setNotifyKind(k, on, row){
  const cur = me.notify_kinds || Object.keys(NOTIFY_KINDS);
  const next = Object.keys(NOTIFY_KINDS).filter(x=>x === k ? on : cur.includes(x));
  haptic();
  if(!LIVE){ me.notify_kinds = next; renderSettings(); return; }
  row.classList.add("busy");
  try{ me.notify_kinds = (await api("/api/me/notify_kinds", "POST", {kinds: next})).notify_kinds; }
  catch(e){ toast("Не удалось сохранить"); }
  finally{ renderSettings(); }
}
$("#setNotify").onclick = ()=>setNotify(!me?.notify);
$("#setLogout").hidden = !tg?.web;
$("#setLogout").onclick = ()=>{ if(confirm("Выйти с этого сайта?")) signOut(); };
$("#notifyAskOn").onclick = ()=>setNotify(true);
$("#notifyAskX").onclick = ()=>{ cloud.set("nAsk", "1"); renderSettings(); };
$("#openSettings").onclick = ()=>{ renderSettings(); haptic(); go("s-settings"); };
$("#setHome").onclick = ()=>{
  if(homeSt === "added") return;
  if(LIVE) tg.addToHomeScreen(); else toast("Демо: здесь Telegram предложит добавить иконку");
};
if(tg){
  if(tg.isVersionAtLeast("8.0") && tg.checkHomeScreenStatus) tg.checkHomeScreenStatus(st=>{ homeSt = st || "unknown"; renderSettings(); });
  tg.onEvent("homeScreenAdded", ()=>{ homeSt = "added"; renderSettings(); toast("Иконка добавлена на экран «Домой»"); });
  if(tg.SettingsButton && tg.isVersionAtLeast("6.10")){ tg.SettingsButton.show(); tg.SettingsButton.onClick(()=>$("#openSettings").click()); }
}

/* ---------- «Поделиться задачей»: Telegram's chat picker with a card of the task ---------- */
$("#shareTask").hidden = LIVE && !(tg.shareMessage && tg.isVersionAtLeast("8.0"));
$("#shareTask").onclick = async ()=>{
  haptic();
  if(!LIVE){ toast("Демо: откроется выбор чата с карточкой задачи"); return; }
  const b = $("#shareTask"); b.disabled = true;
  try{
    const {id} = await api(`/api/tasks/${curTask.id}/share`, "POST");
    tg.shareMessage(id, ok=>{ if(ok) toast("Карточка задачи отправлена"); });
  }catch(e){ toast("Не удалось подготовить карточку"); }
  finally{ b.disabled = false; }
};

/* ---------- Design system: a live page of the tokens and components (rules: webapp/DESIGN.md) ---------- */
function renderDs(){
  const sec = (t, html, note="") => `<div class="sec-label"><span class="mono">${t}</span></div>${html}${note ? `<div class="ds-note">${note}</div>` : ""}`;
  const sw = names => `<div class="ds-sw">${names.map(n=>`<div><i style="background:var(--${n})"></i><span>--${n}</span></div>`).join("")}</div>`;
  const svg = (sz, sw2, paths) => `<svg width="${sz}" height="${sz}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw2}" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  const IC = '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.8V12L14.8 13.8"/>';
  const now = Date.now()/1000;
  const sample = {id:0, title:"Поменять цвет кнопки «Купить»", due:{text:"16 ноября", at:now + 86400*2}, assignee:{name:"Олег Петров"}, archived:false, progress:true};
  $("#dsBody").innerHTML =
    sec("Цвета: поверхности и текст", sw(["bg","page","surface","muted","fill","card","border","text","hint","link","accent"]),
      "Берутся из темы Telegram, у каждого есть светлое и тёмное значение. Никаких #hex в стилях экранов — только эти токены.")
    + sec("Цвета: виды и статусы", sw(["st-open","st-progress","st-review","st-backlog","st-closed","st-archive","k-due","k-late","k-who","k-comm","k-time","k-ask","who-0","who-1","who-2","who-3","who-4","who-5","who-6","who-7","green","red","amber"]),
      "Один вид — один цвет везде: в бейдже, в свайпе, в истории. Новый вид = новый токен в :root.")
    + sec("Типографика", `<div class="ds-type">${[["--fs-2xl","Заголовок экрана",600],["--fs-xl","Числа",600],["--fs-l","Заголовок карточки",600],["--fs-base","Основной текст",400],["--fs-m","Кнопки, второстепенный",500],["--fs-s","Подписи",400],["--fs-xs","МЕТКИ · СЧЁТЧИКИ",500]].map(([v,t,w])=>`<div><code>${v}</code><span style="font-size:var(${v});font-weight:${w}">${t}</span></div>`).join("")}</div>`,
      "TikTok Sans — один шрифт на всё, без моноширинного. Вес 400 текст, 500 кнопки и бейджи, 600 заголовки. Ширина: заголовки 112 % (--wd-head), названия в списках 92 % (--wd-list), остальное 100 %.")
    + sec("Ритм: три зазора", `<div class="ds-rhythm">${[["--gap-in","8 — внутри группы"],["--gap-block","16 — между блоками"],["--gap-sec","24 — перед секцией"]].map(([t,l])=>`<div><i style="height:var(${t})"></i><span class="mono">${t}</span> ${l}</div>`).join("")}</div>`,
      "Между соседними блоками — только эти три зазора. Их ставят компоненты: действия (.link-row, .foot-grid, .ask-card) отходят от содержимого на --gap-block, друг от друга — на --gap-in; .sec-label и вкладки под задачей — --gap-sec. Своих margin в разметке нет.")
    + sec("Скругления", `<div class="ds-rad">${["r-s","r-m","r-l","r-xl","r-full"].map(r=>`<div style="border-radius:var(--${r})">--${r}</div>`).join("")}</div>`,
      "--r-l у блоков и списков, --r-m у кнопок и полей, --r-xl у попапов, --r-full у бейджей и чипов.")
    + sec("Заставка", `<button class="ds-splash" type="button" onclick="this.innerHTML=this.innerHTML">${SPLASH_SVG}</button>`,
      "#splash — пока грузятся первые данные, серым (--hint) рисуются рамка и галочка логотипа (--ic-xl, линия 1.5), затем экран гаснет: не раньше 850 мс, не позже 5 с. Нажмите — нарисуется ещё раз.")
    + sec("Иконки", `<div class="ds-ic"><div>${svg(13,2.2,IC)}--ic-s</div><div>${svg(18,1.7,IC)}--ic-m</div><div>${svg(22,1.8,IC)}--ic-l</div></div>`,
      "Сетка 24, круглые концы. 13 px в бейджах (линия 2.2), 18 px в строках и кнопках (1.7), 22 px в нижнем меню (1.8).")
    + sec("Счётчик над иконкой", `<div class="ppl-bar" style="margin:0;overflow:visible"><button class="on" type="button"><span class="pav nobody"><span class="ph"></span><span class="cnt"><b>16</b></span></span><span class="nm">Все</span></button><button type="button"><span class="pav" style="background:${avColor("Анна")}"><span class="ph">А</span><span class="cnt"><b>7</b></span></span><span class="nm">Анна</span></button><span style="position:relative;display:inline-grid;place-items:center;width:var(--btn-h);height:var(--btn-h);margin-left:var(--sp-4);align-self:center;color:var(--hint)"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 10C6 6.7 8.7 4 12 4C15.3 4 18 6.7 18 10V13.6L19.6 16.4C19.9 16.9 19.5 17.5 19 17.5H5C4.5 17.5 4.1 16.9 4.4 16.4L6 13.6V10Z"/><path d="M9.8 20.2C10.3 20.7 11.1 21 12 21C12.9 21 13.7 20.7 14.2 20.2"/></svg><span class="tb-n">2</span></span></div>`,
      "Один компонент для кружка над иконкой или аватаркой: 16 px (--dot-h), центр — на краю под 45° вверху справа (--r — радиус иконки: 21 у аватарки 42, 11 у иконки меню 22), вырез 2 px цветом фона (--ring; у выбранного человека — --surface), цифры 11 px, вес 500, обрезаны до заглавных — по центру и в Safari. Красный (--red) — новое, непрочитанное (колокольчик); тёмный (--accent) — сколько (задачи человека). Новый счётчик — класс .cbdg с --r, без своих размеров и сдвигов.")
    + sec("Бейджи", `<div class="ds-row">${["open","progress","review","backlog","closed","archive"].map(k=>badge(k, STATUS_TXT[k])).join("")}</div><div class="ds-row" style="margin-top:6px">${badge("due","16 ноября")}${badge("late","срок прошёл")}${["Олег","Алексей","Ira","Мария"].map((n, i)=>whoBadge({id: i + 1, name: n})).join("")}${badge("ask","1 вопрос")}${badge("comm","2 комментария")}${badge("time","1,5 ч")}${itemsBadge(1,4)}${itemsBadge(4,4)}${badge("due","Срок",{none:true})}${badge("comm","Комментарии",{none:true})}${badge("time","Время",{none:true})}</div>`,
      "badge(kind, text) · statusBadge(t) · dueBadge(t) · whoBadge(a) · itemsBadge(d, n) · qBadge(t). Исполнитель — свой цвет у каждого человека (--who-0…7 по id, whoColor). Открытые вопросы — фиолетовый «N вопрос» (--k-ask) в строке задачи и тег на экране задачи. Порядок в строке: статус → срок → исполнитель → вопросы → пункты.")
    + sec("Проект над названием", `<div class="title-edit"><button class="proj-cap" type="button">Проверка сайтов</button><div style="font-size:var(--fs-xl);font-weight:600;letter-spacing:-.025em;line-height:1.15">${esc(sample.title)}</div></div>`,
      ".proj-cap — на экране задачи, серым --fs-s; тап открывает проект. Нет у задач без проекта и у гостя.")
    + sec("Результат", `<div class="ds-row">${badge("closed", "Закрыта")}${badge("res", "Результат · 2,5 ч")}${badge("res", "Результат", {none:true})}</div><div class="res-body"><div class="tx">Поменял цены на капучино и латте</div><a class="res-ln" href="#"><span>example.com/result</span></a><div class="res-h">Потрачено: 2,5 ч</div></div>`,
      "Тег «Результат» у закрытой задачи открывает попап #resSheet: описание, ссылка, часы; «Изменить» — поля закрытия. Пустой — серый, открывает поля сразу.")
    + sec("Поиск", `<label class="search-box"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20L15.8 15.8"/></svg><input type="search" value="кнопка" readonly></label>`
      + `<div class="feed-bar"><button class="on">Все<span class="n">3</span></button><button>Задачи<span class="n">1</span></button><button>Пункты<span class="n">2</span></button></div>`
      + `<div class="grid">${srchRow({kind:"task", parts:[["Кнопка", 1], [" «Записаться» на главной", 0]], status:"progress", project:"Сайт клиники", color:"var(--st-progress)"}, -1)}${srchRow({kind:"item", done:true, parts:[["Поменять цвет ", 0], ["кнопки", 1], [" «Купить»", 0]], task:"Исправления на главной", project:"Сайт клиники", color:"var(--st-progress)"}, -1)}</div>`,
      "Вкладка «Поиск» (на сайте ещё «/» и Ctrl/⌘+K): поле .search-box, чипы разделов с числом (.feed-bar), фильтры статус · проект · «Мои» (.ppl-picks), разделы .sec-label + .grid со строками .row.hit — иконка вида, кусок текста с найденными словами (mark, фон --hl), ниже задача и проект. Тап ведёт на место: задача, пункт (подсвечивается), комментарий, результат, проект.")
    + sec("Строка задачи", `<div class="grid"><div class="swipe"><button class="task" type="button"><div class="thumb" style="background:var(--muted)"></div><div class="main"><div class="t">${esc(sample.title)}</div><div class="meta">${statusBadge(sample)}${dueBadge(sample)}${whoBadge(sample.assignee)}${itemsBadge(1,4)}</div></div></button></div></div>`,
      "Список — .grid со строками внутри. Долгое нажатие поднимает строку (liftDrag), свайп влево — действия.")
    + sec("Фокус: стопка", `<div class="ppl-head"><h2 class="h-big">Мои задачи</h2><button class="head-link" type="button">Фокус</button></div><div class="stack"><div class="grid"><div class="swipe"><button class="task" type="button"><div class="thumb" style="background:var(--muted)"></div><div class="main"><div class="t">${esc(sample.title)}</div><div class="meta">${statusBadge(sample)}${dueBadge(sample)}${itemsBadge(1,4)}</div></div></button></div></div><i></i><i></i></div><button class="stack-more" type="button">Ещё 3 задачи в стопке</button>`,
      "«Фокус» — синяя ссылка .head-link справа от заголовка экрана, по его базовой линии (во включённом режиме — «Развернуть»). Верхнюю задачу можно свайпать — закрыли, и наверх встаёт следующая. Стопка — .stack: сверху .grid с первой задачей, под ней до двух слоёв &lt;i&gt;; .in — анимация складывания. Подпись .stack-more приглушена и отступает от слоёв; нажатие на неё или на слои раскрывает весь список.")
    + sec("Строка проекта", `<div class="grid"><button class="row" type="button"><div class="ava" style="background:#6366f1"><span>С</span></div><div class="main"><div class="t">Сайт клиники</div><div class="s">5 участников · групповой чат</div></div><span class="pill">2 откр.</span><svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M10 16L14 12L10 8"/></svg></button><button class="row" type="button"><div class="ava" style="background:#0ea5e9"><span>Л</span></div><div class="main"><div class="t">Личные дела</div><div class="s">без чата</div></div><span class="pill zero">0 откр.</span><svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M10 16L14 12L10 8"/></svg></button></div>`,
      "Список проектов (#projList): тап открывает проект, долгое нажатие — liftDrag, порядок свой у каждого (PUT /api/projects/order), новые без места — сверху.")
    + sec("Строка с долей времени", `<div class="grid">${[["Сайт клиники","var(--k-time)",100,"11,25 ч"],["Лендинг «Кофейня»","#f59e0b",14,"1,5 ч"]].map(([n,c,w,h])=>`<button class="row" type="button"><span class="pp-dot" style="background:${c}"></span><div class="main"><div class="t">${n}</div><div class="tbar" style="--bc:${c}"><i style="width:${w}%"></i></div></div><span class="hrs">${h}</span></button>`).join("")}</div>`,
      "Экран «Время»: строка .row, под названием полоска .tbar (--bc — цвет проекта или человека, ширина — доля от самой большой строки секции), справа .hrs")
    + sec("Связанные задачи", `<div class="grid">${[{title:"Электронные подарочные сертификаты к Новому году", status:"open", project:"Магазин", color:"#0ea5e9"}, {title:"Добавить подарочные сертификаты", status:"progress", project:"Разработка", color:"#7c3aed"}].map(x=>`<div class="row hit lnk">${linkBody(x)}<button class="unl" type="button" aria-label="Убрать связь">${IC_UNLINK}</button></div>`).join("")}<div class="row lnk-hidden">Ещё одна — в проекте, где вас нет</div></div>`,
      "Экран задачи под пунктами: .row.hit.lnk — квадрат цвета проекта .pdot, название, статус и проект; тап — открыть (Назад вернёт), крестик .unl — убрать связь. Задачи из чатов, где человека нет, — только числом (.lnk-hidden). Связать — строка «Связать с задачей» внизу → попап #linkSheet: похожие по словам названия или поиск")
    + sec("Кнопка действия: плитка и строка", `<div class="foot-grid" style="margin-top:0"><button class="tile" type="button">${svg(18,1.7,IC)}<span>Плитка</span></button><button class="tile" type="button">${svg(18,1.7,IC)}<span>Плитка</span></button></div><button class="link-row" type="button">${svg(18,1.7,IC)}Строка-ссылка<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6L15.5 12L9.5 18"/></svg></button>`,
      "Одна кнопка действия в двух ширинах, на всех экранах одинаковая (задача, проект, «Проекты», настройки): высота --act-h 44, обводка --act-line, иконка 18 / линия 1.7 на --sp-3 от края, до подписи --sp-2, синий текст --fs-s в одну строку. Плитка .foot-grid > .tile — пол-ширины, 2 в ряд, основной вид. Строка .link-row — во всю ширину со стрелкой .chev: одно действие над списком («Новый проект») или переход («История всех проектов»). Стоят внизу экрана, над заголовком — никогда.")
    + sec("Фильтры", `<div class="seg-wrap"><div class="seg" id="dsSeg"><span class="knob instant"></span><button class="on" type="button">Открытые</button><button type="button">В работе</button><button type="button">Бэклог</button></div></div><div class="feed-bar" style="margin-top:10px"><button class="on" type="button">Все незакрытые 4</button><button type="button">В работе 1</button><button type="button">Закрытые</button></div><div class="ppl-picks" style="margin-top:10px"><button class="proj-pick" type="button"><i style="background:var(--link)"></i><span>Все незакрытые</span><b class="n">4</b><svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5L12 15.5L18 9.5"/></svg></button><button class="proj-pick" type="button"><i></i><span>Все проекты</span><svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5L12 15.5L18 9.5"/></svg></button></div>`,
      "Сегменты (.seg) — вкладки одного списка. Чипы (.feed-bar) — фильтры, можно с числом. Выпадающие списки (.ppl-picks > .proj-pick) — статус и проект в одну строку, выбор в попапе; .set — выбрано не значение по умолчанию.")
    + sec("Картинка: нажать, перетащить, вставить", `<div class="sheet ds-static"><div class="card"><div class="h">Картинка проекта</div><div class="pic-drop"><div class="pic-prev"><div class="ava" style="background:var(--green)"><span>К</span></div></div><div class="pic-hint">Нажмите, перетащите или вставьте ⌘V</div></div><div class="pic-url"><input class="fld" type="url" placeholder="Ссылка на картинку"><button type="button" aria-label="По ссылке">${svg(18,1.8,'<path d="M5 12H19"/><path d="M13 6L19 12L13 18"/>')}</button></div><div class="btns3"><button class="danger" type="button">Убрать</button><button type="button">Отмена</button><button class="primary" type="button" disabled>Готово</button></div></div></div>`, ".pic-drop + .pic-url (попап #picSheet): тап по зоне открывает выбор файла, перетаскивание и ⌘V — в превью сразу; «Готово» сохраняет (пока нет изменений — выключена), «Отмена» отбрасывает черновик. Отступы — общие токены попапа.")
    + sec("Новая задача", `<div class="sheet ds-static"><div class="card"><div class="h">Новая задача</div><textarea class="fld" rows="1" readonly placeholder="Название задачи">Поменять цвет кнопки</textarea><div class="sheet-stack"><textarea class="fld" rows="2" readonly placeholder="Описание: подробности, ссылка на макет или документ">Макет: https://figma.com/file/abc</textarea><div class="sheet-hint">Ссылки в описании станут кликабельными</div></div><div class="new-sub"><div class="new-sub-h">Подзадачи</div><div class="new-ilist"><div class="new-irow"><textarea class="fld" rows="1" readonly>Поменять цвет</textarea><button class="new-idel" type="button" aria-label="Убрать">${svg(18,1.7,'<path d="M7 7L17 17"/><path d="M17 7L7 17"/>')}</button></div><div class="new-irow"><textarea class="fld" rows="1" readonly placeholder="Что сделать"></textarea><button class="new-idel" type="button" aria-label="Убрать">${svg(18,1.7,'<path d="M7 7L17 17"/><path d="M17 7L7 17"/>')}</button></div></div><button class="new-iadd" type="button">${svg(18,1.7,'<path d="M12 8V16"/><path d="M8 12H16"/>')}Добавить подзадачу</button></div><div class="btns"><button type="button">Отмена</button><button class="primary" type="button">Создать</button></div></div></div>`,
      "Попап #newSheet (newTask): проект · название · описание (#newDescr в .sheet-stack с подсказкой) · подзадачи (.new-ilist + «Добавить подзадачу») · исполнитель. Ритм — токены --sheet-*. Описание и пункты уходят в POST вместе с задачей.")
    + sec("Попап", `<div class="sheet ds-static"><div class="card"><div class="h">Заголовок попапа</div><div class="sheet-hint">Карточка — flex-колонка с gap --sheet-gap: одинаковый зазор между полями, --sheet-head после заголовка, --sheet-foot перед кнопками. Своих margin у полей нет.</div><div class="btns3"><button class="danger" type="button">Удалить</button><button type="button">Отмена</button><button class="primary" type="button">Сохранить</button></div></div></div>`,
      "Все попапы (.sheet > .card): padding --sheet-pad, gap --sheet-gap, заголовок --sheet-head, кнопки --sheet-foot. Поле + подсказка — .sheet-stack (--sheet-hint).")
    + sec("Исходное сообщение", `<div class="src-list"><div class="src-msg"><div class="who"><b>Анна</b><span>12 ноя, 10:24</span></div><div class="tx">Поменяйте, пожалуйста, цвет кнопки «Купить» на зелёный</div></div><div class="src-msg"><div class="who"><b>Анна</b><span>12 ноя, 10:25</span></div><div class="kind">Голосовое — расшифровка:</div><div class="tx">и на мобильной тоже</div></div></div>`,
      "Попап #srcSheet из свайпа строки задачи («Сообщение»): сообщения чата, из которых сделана задача, — .src-msg (автор · дата, тип, текст или расшифровка, скриншоты). Не уводит в чат.")
    + sec("Голосовое сообщение", '<div id="dsVoice"></div>')
    + sec("Описание задачи", `<div class="descr"><div class="tx">${linkify("Лендинг акции к Новому году. Макет: https://figma.com/file/abc\nТексты — в документе https://docs.google.com/d/xyz")}</div></div>`,
      "#descr над пунктами (renderDescr): свободный текст, ссылки (linkify) открываются в отдельном окне. Длинное — 8 строк и «Показать полностью». Тап по тексту — попап #descrSheet (openDescr): textarea.fld, «Убрать» · «Отмена» · «Сохранить», черновик хранится, после — «Отменить». Пустое — плитка «Описание» внизу экрана.")
    + sec("Уведомления", `<div class="ppl-head"><h2 class="h-big">Уведомления</h2><button class="head-link" type="button">Прочитать все</button></div><div class="feed-bar" style="margin:0 0 var(--sp-2)"><button type="button"><span class="l">Все</span><span class="n u">3</span></button><button class="on" type="button"><span class="l">Вопросы</span><span class="n u">1</span></button><button type="button"><span class="l">Комментарии</span><span class="n u">2</span></button><button type="button"><span class="l">Статусы</span></button></div><div class="hist nlist">${[{actor:"Анна", kind:"asked", text:"Форма уходит на почту или в CRM?", task:"Форма записи", unread:true, x:110}, {actor:"Олег", kind:"status", extra:{to:"progress"}, task:"Исправления на главной", x:110}, {actor:"Мария", kind:"commented", text:"Проверила на телефоне", task:"Форма записи"}].map(e=>`<div class="nw"><div class="ev nrow${e.unread ? " unread" : ""}"${e.x ? ` style="transform:translateX(${e.x}px)"` : ""}>${nmark(e)}${evAvatar(e)}<div class="body"><div class="tx">${evText(e)}</div><div class="ft">${esc(e.task)}</div><div class="tm">Сайт клиники · ${e.actor} · 12:40</div></div>${e.unread ? `<i class="ud"></i>` : ""}</div></div>`).join("")}</div><div class="ds-row" style="margin-top:var(--sp-3)"><span style="position:relative;display:inline-grid;place-items:center;width:var(--btn-h);height:var(--btn-h);color:var(--hint)"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 10C6 6.7 8.7 4 12 4C15.3 4 18 6.7 18 10V13.6L19.6 16.4C19.9 16.9 19.5 17.5 19 17.5H5C4.5 17.5 4.1 16.9 4.4 16.4L6 13.6V10Z"/><path d="M9.8 20.2C10.3 20.7 11.1 21 12 21C12.9 21 13.7 20.7 14.2 20.2"/></svg><span class="tb-n">6</span></span></div>`,
      "Вкладка-колокольчик в нижнем меню с красным счётчиком .tb-n (всё непрочитанное, 99+). Экран #s-notif: чипы разделов .feed-bar — Все · Вопросы · На проверку · Комментарии · Статусы · Исполнитель и срок · Изменения, у раздела с новым — красное число .n.u. Строки .nlist .nrow: новое — красная точка .ud и жирное название. Прочитанным делает только тап (открывает задачу, по вопросу или комментарию — на нужной вкладке). Свайп вправо — .nmark «Прочитано» (серая) у нового, «Не прочитано» (красная) у прочитанного. Справа от заголовка — ссылка .head-link «Прочитать все» (как «Фокус») (открытый раздел, на «Все» — всё; только пока в нём есть новое); внизу — «История всех проектов».")
    + sec("Вопросы · Комментарии · История", `<div class="seg-wrap talk-tabs" style="margin-top:0"><div class="seg even" id="dsTalk"><span class="knob instant"></span><button class="on" type="button">Вопросы<span class="n">1</span></button><button type="button">Комментарии<span class="n">2</span></button><button type="button">История</button></div></div><div class="talk-list">${commHtml({id:1, author:"Анна", date:"12 ноя, 10:24", text:"Цвет кнопки — как в макете?", question:true, resolved:false, files:[{kind:"photo", url:"desktop-light.webp"}]}, {items:[]}, false)}${commHtml({id:2, author:"Олег", date:"12 ноя, 11:02", text:"Шрифт заголовка какой?", question:true, resolved:true, resolved_by:"Анна"}, {items:[]}, false)}</div><div class="composer ask"><div class="att-row"><div class="att"><img src="static/loading-icon.svg" alt=""><span class="p">40%</span></div></div><div class="composer-line"><button class="att-btn" type="button"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20.3 11.7L12.6 19.4C10.6 21.4 7.4 21.4 5.4 19.4C3.4 17.4 3.4 14.2 5.4 12.2L13.1 4.5C14.4 3.2 16.6 3.2 17.9 4.5C19.2 5.8 19.2 8 17.9 9.3L10.3 16.9C9.6 17.6 8.5 17.6 7.8 16.9C7.1 16.2 7.1 15.1 7.8 14.4L14.9 7.3"/></svg></button><textarea rows="1" placeholder="Вопрос" readonly></textarea><button class="send" type="button" aria-label="Спросить"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M6 11L12 5L18 11"/></svg></button></div></div><div class="composer-bar" style="margin-top:var(--sp-3)"><button class="ask-tg on" type="button"><span class="sw"></span>Вопрос</button><button class="ask-tg" type="button"><span class="sw"></span>Вопрос</button></div>`,
      "Под задачей — сегменты #talkTabs (.seg.even): «Вопросы» (число открытых) · «Комментарии» · «История». Открывается «Вопросы», если есть открытые, иначе «Комментарии». Поле .composer (.composer-line: скрепка .att-btn · текст · кнопка) пишет в открытую вкладку; скриншоты и видео — превью .att с процентом загрузки и ×, в сообщении — сетка .c-media (картинка открывается на весь экран, видео играет на месте): на «Вопросах» — вопрос (рамка и кнопка «Спросить» --k-ask). Открытый вопрос — .src-msg.comm.q с подсветкой; свайп вправо — «Решён» (.qdone, зелёная, может любой, «Отменить»), у решённого — «Открыть» (серая); решённые — ниже, «Решён · кто». Свои сообщения — шторка .shut: свайп влево «Изменить» · «Удалить» (initShutter), у изменённых «· изменено». В попапе комментариев пункта вид выбирается переключателем .ask-tg «Вопрос».")
    + sec("Карточки", `<div class="ask-card" style="margin-top:0"><div class="ic">${svg(18,1.7,IC)}</div><div class="tx"><b>Предложение</b><span>Карточка с вопросом и кнопкой</span><button class="on-btn" type="button">Включить</button></div></div><div class="warn-card" style="margin-top:10px"><div class="ic">${svg(18,1.8,'<path d="M12 9v4"/><path d="M12 16.5h.01"/>')}</div><div class="tx"><b>Предупреждение</b><span>Что-то мешает работе</span></div></div><div class="empty">Пустое состояние: коротко, что здесь будет</div>`)
    + sec("Сообщения сверху", `<div class="ds-row"><button class="pill" type="button" data-ds-toast="ok">Готово</button><button class="pill" type="button" data-ds-toast="warn">Предупреждение</button><button class="pill" type="button" data-ds-toast="err">Ошибка</button><button class="pill" type="button" id="dsToastUndo">С «Отменить»</button></div>`,
      "toast(text, kind, {undo}): ok · warn · err. Коротко, по-русски, без точки в конце. С undo справа кнопка «Отменить» (5 с): после смены статуса, закрытия, архива, переименования, удаления пункта.")
    + sec("Загрузка", `<div class="grid" id="dsGhost"></div><div class="ds-row" style="margin-top:var(--gap-in)"><button class="pill" type="button" id="dsGhostBtn">Показать скелетон</button></div>`,
      "Пока грузится — силуэт ровно того, что появится: список рисуется своим рендером на примерах и превращается в кости (ghostList / boneUp).");
  { const ts = $("#dsTalk"), tk = ts.querySelector(".knob"), tp = b => { tk.style.width = b.offsetWidth + "px"; tk.style.transform = `translateX(${b.offsetLeft}px)`; };
    requestAnimationFrame(()=>tp(ts.querySelector(".on")));
    ts.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ ts.querySelector(".on")?.classList.remove("on"); b.classList.add("on"); tk.classList.remove("instant"); tp(b); haptic(); }); }
  const seg = $("#dsSeg"), knob = seg.querySelector(".knob");
  const place = b => { knob.style.width = b.offsetWidth + "px"; knob.style.transform = `translateX(${b.offsetLeft}px)`; };
  seg.querySelectorAll("button").forEach(b=>b.onclick = ()=>{ seg.querySelector(".on")?.classList.remove("on"); b.classList.add("on"); knob.classList.remove("instant"); place(b); haptic(); });
  requestAnimationFrame(()=>place(seg.querySelector(".on")));
  $("#dsBody").querySelectorAll("[data-ds-toast]").forEach(b=>b.onclick = ()=>toast({ok:"Сохранено", warn:"Бот не видит переписку", err:"Не удалось сохранить"}[b.dataset.dsToast], b.dataset.dsToast));
  $("#dsToastUndo").onclick = ()=>toast("Задача закрыта", null, {undo: ()=>toast("Задача снова открыта")});
  const dsVoice = createVoiceCard({requests:new Map()});
  $("#dsVoice").append(dsVoice);
  dsVoice.querySelector(".voice-toggle").setAttribute("aria-expanded", "true");
  dsVoice.querySelector(".voice-body").hidden = false;
  dsVoice.updateRecording({author:"Анна", text:"Предложите, где разместить ссылку на демоверсию, чтобы её нашли посетители из поиска. Адрес страницы сохраняем.",
    summary:{actions:["Предложить место для ссылки на демоверсию."], purpose:"Помочь посетителям из поиска найти демоверсию.", details:["Сохранить адрес страницы."]}});
  const row = `<div class="swipe"><button class="task" type="button"><div class="thumb" style="background:var(--muted)"></div><div class="main"><div class="t">${esc(sample.title)}</div><div class="meta">${statusBadge(sample)}${itemsBadge(1,4)}</div></div></button></div>`;
  $("#dsGhost").innerHTML = row;
  $("#dsGhostBtn").onclick = ()=>{
    const g = $("#dsGhost");
    if(g.classList.contains("ghost")){ unghost(g); $("#dsGhostBtn").textContent = "Показать скелетон"; return; }
    ghostList([g], ()=>{ g.innerHTML = row; }); $("#dsGhostBtn").textContent = "Убрать скелетон";
  };
}
$("#openDs").onclick = ()=>{ renderDs(); haptic(); go("s-ds"); };

/* ---------- «Что нового»: newest first; a dot until the person has seen the latest entry ---------- */
const NEWS = [
  {v:"2026-11-20", d:"20 ноября", items:[
    "<b>Создание задачи.</b> В форме сразу есть описание (можно вставить ссылку на макет или документ — ссылки станут кликабельными) и подзадачи: кнопка «Добавить подзадачу» прямо при создании.",
    "<b>Ритм попапов.</b> Во всех нижних листах одинаковые зазоры: между полями, после заголовка и перед кнопками — без случайных отступов.",
  ]},
  {v:"2026-11-17", d:"17 ноября", items:[
    "<b>Уведомления.</b> Вместо «Истории» в нижнем меню — колокольчик с красным числом новых. Там всё, что другие сделали в ваших задачах (вы автор, исполнитель или что-то в ней делали), по разделам: Вопросы · Комментарии · Статусы · Исполнитель и срок · Изменения. Уведомление становится прочитанным, только когда вы его откроете; свайп вправо снова делает его непрочитанным. История всех проектов — строкой внизу экрана.",
    "<b>Что присылает бот.</b> В «Настройках» под «Уведомления в личку» вопросы и комментарии теперь выбираются отдельно, добавились «Название и описание».",
  ]},
  {v:"2026-11-16c", d:"16 ноября", items:[
    "<b>Вопросы.</b> Под задачей — вкладки «Вопросы» · «Комментарии» · «История». Вопрос виден сверху и в строке задачи, пока кто-нибудь не нажмёт «Вопрос решён».",
    "<b>Комментарии прямо в задаче.</b> Их видно сразу под задачей, написать можно там же — без отдельного окна.",
    "<b>Вопросы свайпом.</b> Вопрос решён — свайп вправо, как отметка пункта. Свой вопрос или комментарий — свайп влево: «Изменить» и «Удалить».",
    "<b>Скриншоты и видео в вопросах и комментариях.</b> Скрепка слева от поля: картинки открываются на весь экран, видео (до 100 МБ) играет прямо в задаче.",
    "<b>Описание задачи.</b> Плитка «Описание» внизу задачи: любой текст и ссылки, ссылки нажимаются. Свайп влево по описанию — «Изменить» и «Удалить».",
    "<b>Исполнители разными цветами.</b> У каждого человека свой цвет — в списке сразу видно, чья задача.",
    "<b>Действия под задачей — плитками в два столбца.</b> Архив — плиткой «В архив» вместо ползунка.",
  ]},
  {v:"2026-11-15a", d:"15 ноября", items:[
    "<b>Фокус.</b> В «Моих задачах» справа от названия первого проекта — ссылка «Фокус»: задачи складываются в стопку, сверху самая первая.",
    "<b>Сайт на компьютере.</b> На широком экране — две панели: слева список, справа открытая задача.",
    "<b>Задачи на сайте.</b> tasks.isite.pro открывается и в обычном браузере: «Войти через Telegram» — бот спросит «Войти?» — и вы на сайте со своими задачами. Выйти — в настройках.",
    "<b>Короткие уведомления.</b> В личку — только название задачи и что изменилось; у закрытой — ссылка на результат.",
    "<b>Отчёт при закрытии.</b> Закрывая задачу, можно оставить ссылку на результат и сколько часов ушло — или закрыть без отчёта. Ссылка и часы видны в задаче, в истории и в чеклисте в чате.",
    "<b>Комментарии к пунктам и лента комментариев.</b> Пункт свайпните влево → «Комментарий»; под пунктом видно, сколько их. В меню вместо «Все задачи» — «Комментарии»: свежие комментарии из всех задач. «Все задачи» — внизу «Моих задач».",
    "<b>Комментарии к задаче.</b> На экране задачи — тег «Комментарии»: видно, кто и когда написал, можно ответить и удалить свой. Новые комментарии — в истории и в уведомлениях.",
    "<b>Отменить.</b> После смены статуса, закрытия, архива, переименования или удаления пункта в сообщении сверху есть «Отменить» — если нажали случайно.",
    "<b>Какие уведомления присылать.</b> В настройках под «Уведомления в личку» — переключатель на каждый вид: смена статуса, новые пункты, скриншоты, исполнитель, срок.",
    "<b>Без эмодзи.</b> Сообщения бота и приложение — без эмодзи; число пунктов в строке задачи — простым серым текстом; выпадающие списки — по ширине текста.",
    "<b>Потяните, чтобы обновить.</b> Наверху любого экрана потяните вниз посильнее и отпустите — данные обновятся.",
    "<b>Приложение открывается на «Моих задачах».</b> В меню внизу появились «Проекты». В «Моих задачах» — один список без заголовков проектов, «Фокус» — справа от заголовка; верхнюю задачу стопки можно закрыть свайпом, и наверх встанет следующая.",
    "<b>Задачи без проекта.</b> «Создать» в меню сразу открывает новую задачу, проект выбирается в ней — или «Без проекта»: такая задача только ваша и живёт в «Моих задачах», без чата.",
    "<b>Статус «Закрыта».</b> Задачу можно закрыть, даже если у неё нет пунктов или отмечены не все: в попапе статуса или свайпом вправо. Отметили последний пункт — закроется сама, как раньше.",
    "<b>Жесты во всех списках.</b> В «Моих» и «Всех задачах» строки свайпаются так же, как в проекте: влево — статус, исходное сообщение, переименовать, удалить; вправо — «Закрыть». Долгое нажатие — перетащить.",
    "<b>Исходное сообщение.</b> Свайп задачи влево → «Сообщение»: в попапе видно, что написали в чате, — не уходя из приложения.",
    "<b>Новая задача из меню.</b> Кнопка внизу экрана: внутри проекта создаёт задачу сразу, в остальных местах сначала спрашивает проект."]},
  {v:"2026-11-13a", d:"13 ноября", items:[
    "<b>Исполнитель задачи.</b> Назначьте человека в задаче или сразу при создании — бот напишет ему в личку, и все напоминания по задаче будут приходить и ему.",
    "<b>Задачи людей</b> — свои и чужие задачи из всех проектов на одном экране: выберите человека или «Ничьи», отфильтруйте по статусу.",
    "Исполнителем можно назначить человека из другого вашего проекта, даже если его нет в чате: он увидит только эту задачу, а не весь проект.",
    "Пока что-то грузится — задача, список задач или проектов, — на его месте силуэт ровно того, что появится, без прыжков.",
    "<b>Порядок задач и пунктов</b> — долгое нажатие поднимает карточку или пункт, дальше просто перетащите. Отдельной кнопки «Порядок» больше нет.",
    "<b>Меню внизу на всех экранах:</b> «Мои задачи», «Все задачи» (из всех проектов, можно выбрать человека) и «История». На главной остались «Настройки» и «Что нового».",
    "Всплывающие сообщения сверху стали плавнее; смахните вверх, чтобы скрыть.",
  ]},
  {v:"2026-11-11a", d:"11 ноября", items:[
    "<b>Срок задачи и напоминания.</b> Под названием задачи — «Срок»: быстрые дни, дата и время. Там же можно попросить бота напомнить вам в личку — за день, за час или в момент срока. Под напоминанием — «Через час» и «Сделано».",
    "Бот сам ставит срок, если в сообщениях есть «до пятницы» или дата акции.",
    "<b>Файлы из чата</b> (таблицы, PDF) сохраняются в задаче — раздел «Файлы», скачиваются в одно касание.",
    "<b>Пересылка:</b> выделите сообщения в чате и перешлите боту в личку — он сделает из них задачу и спросит проект.",
    "Сообщение с чеклистом в чате стало короче: только название и пункты.",
  ]},
  {v:"2026-11-10e", d:"10 ноября", items:[
    "<b>Тема в настройках:</b> авто (как в Telegram), светлая или тёмная.",
    "Настройки и «Что нового» открываются отдельными экранами.",
    "Свайп пункта вправо отмечает его выполненным (или снимает отметку).",
    "Попапы выезжают снизу на размытом фоне и поднимаются над клавиатурой.",
    "<b>Уведомления в личку.</b> Бот пишет, когда в ваших задачах появляются новые пункты, меняется статус или всё выполнено. Включаются в настройках.",
    "<b>Поделиться задачей</b> — карточка с пунктами и кнопкой «Открыть» уходит в любой чат.",
    "<b>Иконка на экране «Домой»</b> — открывает приложение сразу, минуя список чатов.",
    "Приложение помнит выбранную вкладку и фильтр истории, а недописанный пункт или задача сохраняются как черновик.",
    "Если закрыть приложение посреди набора текста, Telegram сначала переспросит.",
    "Свайп от левого края — назад.",
    "Голосовые можно прослушать прямо в задаче, рядом с расшифровкой.",
    "Скриншот плавно скругляет углы, когда сворачивается обратно в превью.",
    "Свайп по пункту влево открывает действия; в истории — аватарки людей.",
    "Кнопки с иконками вместо подписей, аккуратнее отступы и анимации.",
  ]},
  {v:"2026-11-09", d:"9 ноября", items:[
    "Задачи может создавать и отмечать ИИ-агент.",
    "У чеклиста в чате осталась одна кнопка — «Открыть».",
  ]},
  {v:"2026-11-08", d:"8 ноября", items:["История изменений по проекту и по всем проектам сразу, с фильтрами."]},
  {v:"2026-11-07", d:"7 ноября", items:["Мини-приложение: проекты, задачи, чеклисты и «Создать задачу»."]},
];
const newsSeenIdx = ()=>{ const i = NEWS.findIndex(n=>n.v === cloud.get("news")); return i < 0 ? NEWS.length : i; };
function renderNewsDot(){ $("#newsDot").hidden = newsSeenIdx() === 0; }
$("#openNews").onclick = ()=>{
  const seen = newsSeenIdx();
  $("#newsList").innerHTML = NEWS.map((n,i)=>`<div class="day"><div class="d mono">${n.d}${i < seen && seen < NEWS.length ? `<span class="new">новое</span>` : ""}</div><ul>${n.items.map(x=>`<li><span>${x}</span></li>`).join("")}</ul></div>`).join("");
  haptic(); go("s-news");
  cloud.set("news", NEWS[0].v); renderNewsDot();
};

/* ---------- One copy of the app at a time ----------
   Telegram may open a second copy next to one already open or minimised. The new copy says «I'm here»,
   older ones close themselves; the newest stays, on the task it was opened for. */
if(LIVE){
  const me = {id: Math.random().toString(36).slice(2), at: Date.now()};
  const older = m => m && m.id !== me.id && m.at > me.at;
  const leave = ()=>{ try{ tg.disableClosingConfirmation?.(); }catch(_){} tg.close(); };
  let bc = null;
  try{ bc = new BroadcastChannel("tg-checklists"); bc.onmessage = e=>{ if(older(e.data)) leave(); }; bc.postMessage(me); }catch(_){}
  // where BroadcastChannel does not reach, a localStorage write does (the «storage» event fires in the other copies)
  addEventListener("storage", e=>{ if(e.key === "appCopy"){ try{ if(older(JSON.parse(e.newValue))) leave(); }catch(_){} } });
  try{ localStorage.setItem("appCopy", JSON.stringify(me)); }catch(_){}
}

/* ---------- Signing in on the site: a one-time code → the bot asks «Войти?» → the site gets a session ---------- */
let loginPoll = null;
async function showLogin(){
  const box = $("#login"), go = $("#loginGo"), st = $("#loginSt");
  box.hidden = false; clearInterval(loginPoll);
  go.setAttribute("aria-disabled", "true"); st.textContent = "";
  let lg;
  try{ lg = await (await fetch("/api/login/start", {method: "POST"})).json(); }catch(e){ st.textContent = "Не удалось связаться с сервером. Обновите страницу."; return; }
  go.href = lg.link; go.setAttribute("aria-disabled", "false");
  go.onclick = ()=>{ st.textContent = "Ждём подтверждения в Telegram…"; };
  const until = Date.now() + (lg.ttl || 300) * 1000;
  loginPoll = setInterval(async ()=>{
    if(Date.now() > until){ clearInterval(loginPoll); st.innerHTML = `Ссылка устарела. <button class="eb-link" id="loginAgain">Получить новую</button>`; $("#loginAgain").onclick = showLogin; return; }
    let r; try{ r = await (await fetch("/api/login/poll?token=" + encodeURIComponent(lg.token), {cache: "no-store"})).json(); }catch(_){ return; }
    if(r.status === "ok"){ clearInterval(loginPoll); try{ localStorage.setItem("siteSession", r.session); }catch(_){} st.textContent = "Готово, открываем…"; location.reload(); }
    else if(r.status === "alpha"){ clearInterval(loginPoll); box.hidden = true; $("#alphaJoin").href = r.link; $("#alphaGate").hidden = false; }
    else if(r.status === "expired"){ clearInterval(loginPoll); st.innerHTML = `Ссылка устарела. <button class="eb-link" id="loginAgain">Получить новую</button>`; $("#loginAgain").onclick = showLogin; }
  }, 2000);
}
function signedOut(){
  const shown = !$("#login").hidden;
  try{ localStorage.removeItem("siteSession"); }catch(_){} siteSession = null;
  if(!shown) showLogin();   // once: a second 401 must not replace the code the person is confirming right now
}
async function signOut(){
  try{ await fetch("/api/logout", {method: "POST", headers: {"Authorization": authHeader()}}); }catch(_){}
  signedOut();
}

/* ---------- Start: ?startapp=task_14 opens that task (the «Открыть» button) ---------- */
/* The splash goes once the first screen has its data — but not before its lines are drawn (SPLASH_MIN) */
const SPLASH_T0 = performance.now(), SPLASH_MIN = 850, SPLASH_SVG = $("#splash")?.innerHTML || "";   // kept for the style guide
function hideSplash(){
  const el = $("#splash"); if(!el || el.classList.contains("out")) return;
  if(document.documentElement.classList.contains("site")){ el.remove(); return; }
  setTimeout(()=>{ el.classList.add("out"); setTimeout(()=>el.remove(), 350); }, Math.max(0, SPLASH_MIN - (performance.now() - SPLASH_T0)));
}
setTimeout(hideSplash, 5000);   // never longer than this, whatever happens

(async function start(){
  if(tg?.web && !siteSession){ hideSplash(); showLogin(); return; }   // the site, not signed in yet
  const qs = new URLSearchParams(location.search);
  if(qs.get("theme")) document.querySelector(`#themeSw [data-t="${qs.get("theme")}"]`)?.click();
  const sp = tg?.initDataUnsafe?.start_param || qs.get("tgWebAppStartParam") || "";
  const q = (sp.match(/^task_(\d+)$/)||[])[1] || qs.get("task");
  const join = (sp.match(/^join_([\w-]+)$/)||[])[1];
  let joined = null;
  const home = !q && !join && sp !== "ds" && !qs.has("ds");
  if(home && !qs.has("projects")){   // the app opens on «Мои задачи» (Back / «Проекты» lead to the projects); ?projects — on the projects (the landing's demo)
    await cloud.ready;
    const a = $("#s-projects"), b = $("#s-people"); [a, b].forEach(x=>x.style.transition = "none");
    openPeople(true);
    requestAnimationFrame(()=>requestAnimationFrame(()=>[a, b].forEach(x=>x.style.transition = "")));
  }
  if(join && LIVE){ try{ joined = await api("/api/join", "POST", {token: join}); }catch(e){ toast("Ссылка-приглашение больше не работает"); } }
  await Promise.all([loadProjects(), cloud.ready]);
  loadNotifCount();
  hideSplash();
  const ct = cloud.get("theme") || "auto";
  if(LIVE && ct !== (themePref() || "auto")) setTheme(ct, true);
  renderNewsDot(); loadMe();
  if(home && ppl.data) renderPeople();   // the projects came: their order sorts the list
  if(joined){
    curProj = projects.find(p=>p.id===joined.id) || {id:joined.id, name:joined.name};
    filter = "open"; tasks = []; ghostTasks(); go("s-tasks"); loadTasks(true);
    toast(`Вы в проекте «${joined.name}»`); return;
  }
  if(sp === "ds" || qs.has("ds")){ renderDs(); go("s-ds"); return; }
  if(!q) return;
  let t;
  if(LIVE){ try{ t = fromApi(await api(`/api/tasks/${q}`)); }catch(e){ toast("Задача не найдена или нет доступа"); return; } }
  else t = tasks.find(x=>x.id==q);
  if(!t) return;
  curProj = projects.find(p=>p.id===t.pid) || {id:t.pid, name:t.project};
  if(!t.guest){ filter = "open"; await loadTasks(); go("s-tasks"); }   // assignee from outside: just the task
  curTask = t; opened = {id: t.id, group: groupOf(t)}; renderTask(); go("s-task");
})();
