(() => {
  "use strict";
  const zone = "Asia/Tokyo";
  const clocks = [...document.querySelectorAll("[data-future-clock]")];
  const hmsNodes = [...document.querySelectorAll("[data-future-hms]")];
  const format = new Intl.DateTimeFormat("ja-JP", {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  });
  function clock() {
    const fields = Object.fromEntries(format.formatToParts(new Date()).map(p => [p.type,p.value]));
    const hms = fields.hour+":"+fields.minute+":"+fields.second;
    const virtual = (Number(fields.year)+5)+"."+fields.month+"."+fields.day+" "+hms;
    clocks.forEach(n => {n.textContent=virtual;n.setAttribute("datetime",virtual);});
    hmsNodes.forEach(n => n.textContent=hms);
  }
  if (clocks.length || hmsNodes.length) {
    clock();
    window.setInterval(clock, 1000);
  }

  // Hearts are local preferences, not a fabricated global "likes" count.
  const storageKey="mihassei-likes-v1";
  let saved=[];
  try {
    const fromStorage=JSON.parse(localStorage.getItem(storageKey)||"[]");
    if(Array.isArray(fromStorage)) saved=fromStorage.filter(x=>typeof x==="string");
  }catch(_){}
  const liked=new Set(saved);
  function renderHearts(){
    document.querySelectorAll("[data-like-slug]").forEach(b=>{
      const on=liked.has(b.dataset.likeSlug);
      b.classList.toggle("liked",on);
      b.setAttribute("aria-pressed",String(on));
      b.setAttribute("aria-label",on?"いいねを取り消す":"この記事にいいね");
      const heart=b.querySelector("span[aria-hidden]");
      if(heart)heart.textContent=on?"♥":"♡";
    });
  }
  document.addEventListener("click", e=>{
    const b=e.target.closest("[data-like-slug]");
    if(!b) return;
    if(liked.has(b.dataset.likeSlug))liked.delete(b.dataset.likeSlug);
    else liked.add(b.dataset.likeSlug);
    try{localStorage.setItem(storageKey,JSON.stringify([...liked]));}catch(_){}
    renderHearts();
  });
  renderHearts();

  document.querySelectorAll("[data-copy-url]").forEach(b=>{
    b.addEventListener("click",async()=>{
      const url=b.dataset.copyUrl;
      const label=b.querySelector("span:last-child");
      try{
        if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(url);}
        else{
          const ta=document.createElement("textarea");ta.value=url;ta.style.position="fixed";
          ta.style.opacity="0";document.body.appendChild(ta);ta.select();
          if(!document.execCommand("copy"))throw new Error("copy not supported");
          ta.remove();
        }
        if(label)label.textContent="コピーしました ✓";
        window.setTimeout(()=>{if(label)label.textContent="リンクをコピー";},2200);
      }catch(_){window.prompt("記事のURL",url);}
    });
  });
})();