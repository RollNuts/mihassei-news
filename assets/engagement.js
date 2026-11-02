(() => {
  "use strict";

  // Broken or blocked third-party stock photographs never leave empty cards.
  document.querySelectorAll("img[data-fallback]").forEach(image=>{
    image.addEventListener("error",()=>{
      const fallback=image.getAttribute("data-fallback");
      if(!fallback || image.getAttribute("src")===fallback)return;
      image.removeAttribute("data-fallback");
      image.src=fallback;
      const caption=image.closest("figure")?.querySelector("figcaption");
      if(caption)caption.textContent="イメージ画像";
    },{once:true});
  });

  // An animated, explicitly fictional clock, shifted five calendar years.
  const clockFormat = new Intl.DateTimeFormat("ja-JP", {
    timeZone:"Asia/Tokyo",year:"numeric",month:"2-digit",day:"2-digit",
    hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"
  });
  const futureClocks = [...document.querySelectorAll("[data-future-clock]")];
  const hmsClocks = [...document.querySelectorAll("[data-future-hms]")];
  const editionStatus = document.querySelector("[data-edition-status]");
  const editionTitle = document.querySelector("[data-edition-title]");
  const editionEyebrow = document.querySelector("[data-edition-eyebrow]");
  const editionUpdated = document.querySelector("[data-edition-updated]");
  let lastDisplayedDay = "";
  function updateClock() {
    const f = Object.fromEntries(clockFormat.formatToParts(new Date()).map(x => [x.type,x.value]));
    const hms = f.hour+":"+f.minute+":"+f.second;
    const virtual = String(Number(f.year)+5)+"."+f.month+"."+f.day+" "+hms;
    futureClocks.forEach(x => {x.textContent=virtual; x.setAttribute("datetime",virtual);});
    hmsClocks.forEach(x => x.textContent=hms);
    // The fictional clock keeps running even between publications. Never call
    // an old article "today's news" after the Japan calendar day changes.
    if (editionStatus) {
      const today = f.year+"-"+f.month+"-"+f.day;
      if (today !== lastDisplayedDay) {
        lastDisplayedDay = today;
        const todayIssued = editionStatus.dataset.lastEdition === today;
        const latestCount = Math.max(0,Number(editionStatus.dataset.editionCount)||0);
        const lastLabel = editionStatus.dataset.lastLabel || editionStatus.dataset.lastEdition || "不明";
        if (editionTitle) editionTitle.textContent = todayIssued ? "本日の未発生ニュース" : "最新の未発生ニュース";
        if (editionEyebrow) editionEyebrow.textContent = todayIssued ? "TODAY'S HEADLINES" : "LATEST HEADLINES";
        if (editionUpdated) editionUpdated.textContent = todayIssued ?
          "本日発行 "+latestCount+"本 ・ 最終発行："+lastLabel+"（現実時間）" :
          "最終発行："+lastLabel+"（現実時間）";
      }
    }
  }
  if(futureClocks.length || hmsClocks.length || editionStatus){
    updateClock();
    window.setInterval(updateClock,1000);
  }


  // Reveal one real archived fictional story; never fabricate a "breaking" event.
  const picker=document.querySelector("[data-issue-picker]");
  if(picker){
    let choices=[];
    try{choices=JSON.parse(picker.dataset.issueOptions || "[]");}
    catch(_){choices=[];}
    const button=picker.querySelector("[data-pick-issue]");
    const label=picker.querySelector("[data-issue-headline]");
    const link=picker.querySelector("[data-issue-link]");
    const result=picker.querySelector("[data-issue-result]");
    const banner=picker.querySelector(".issue-picker-result .small-label");
    let previous=-1,working=false;
    const reduced=window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches || false;
    function choose(){
      if(choices.length===1)return 0;
      if(previous<0)return Math.floor(Math.random()*choices.length);
      return (previous+1+Math.floor(Math.random()*(choices.length-1)))%choices.length;
    }
    function reveal(index){
      const story=choices[index];
      if(!story)return;
      previous=index;
      label.textContent=story.headline;
      banner.textContent=story.category+" / 号外";
      link.href=story.url;
      link.hidden=false;
      result.classList.remove("shuffling");
      button.disabled=false;working=false;
      button.firstChild.textContent="もう一枚めくる ";
    }
    button?.addEventListener("click",()=>{
      if(working || !choices.length)return;
      working=true;button.disabled=true;link.hidden=true;
      result.classList.add("shuffling");
      const chosen=choose();
      if(reduced){reveal(chosen);return;}
      let ticks=0;
      const interval=window.setInterval(()=>{
        ticks++;
        label.textContent=choices[(chosen+ticks)%choices.length].headline;
        if(ticks>=9){window.clearInterval(interval);reveal(chosen);}
      },105);
    });
    if(!choices.length)button.disabled=true;
  }

  // A random browser ID is used only for one-per-browser deduplication.
  // No fingerprinting, user accounts, cookies, or IP-derived identity.
  const idKey = "mihassei-anonymous-browser-v1";
  const likesKey = "mihassei-likes-v1";
  function makeId(){
    if(crypto.randomUUID)return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6]=(b[6]&15)|64; b[8]=(b[8]&63)|128;
    const x=Array.from(b,v=>v.toString(16).padStart(2,"0")).join("");
    return [x.slice(0,8),x.slice(8,12),x.slice(12,16),x.slice(16,20),x.slice(20)].join("-");
  }
  let clientId="";
  try {
    clientId=localStorage.getItem(idKey)||makeId();
    localStorage.setItem(idKey,clientId);
  } catch(_) {clientId=makeId();}

  let liked=[];
  try {
    const prior=JSON.parse(localStorage.getItem(likesKey)||"[]");
    if(Array.isArray(prior))liked=prior.filter(v=>typeof v==="string");
  }catch(_){}
  const likedSet=new Set(liked);
  const pending = new Set();
  const buttons = [...document.querySelectorAll("[data-like-slug]")];
  const article = location.pathname.match(/^\/articles\/([a-z0-9-]+)\/?$/)?.[1]||"";

  // Which story led this browser to read another? Only same-origin article
  // path slugs are retained; never transmit complete referrer URLs or queries.
  function precedingArticle(){
    if(!article)return "";
    let prior;
    try{prior=new URL(document.referrer);}catch(_){return "";}
    if(prior.origin!==location.origin)return "";
    const match=prior.pathname.match(/^\/articles\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/);
    return match && match[1]!==article ? match[1] : "";
  }
  const fromArticle=precedingArticle();
  // Reader predictions are confirmed by the existing free engagement API.
  // Never display invented votes and never count failed requests.
  const poll=document.querySelector("[data-future-poll]");
  const pollKey="mihassei-future-verdicts-v1";
  let savedPolls={};
  try {
    const value=JSON.parse(localStorage.getItem(pollKey)||"{}");
    if(value && typeof value==="object" && !Array.isArray(value)){
      savedPolls=Object.fromEntries(Object.entries(value).filter(([slug,choice])=>
        /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) && ["plausible","unlikely"].includes(choice)
      ).slice(-250));
    }
  }catch(_){}
  let selectedPoll=poll && savedPolls[article] || "";
  let pollPending=false,pollError="",pollCounts=null;
  function savePolls(){
    if(!article || !selectedPoll)return;
    savedPolls[article]=selectedPoll;
    try{localStorage.setItem(pollKey,JSON.stringify(Object.fromEntries(Object.entries(savedPolls).slice(-250))));}
    catch(_){}
  }
  function renderPoll(votes){
    if(!poll || poll.dataset.pollArticle!==article)return;
    if(votes && Number.isInteger(votes.plausible) && votes.plausible>=0 &&
       Number.isInteger(votes.unlikely) && votes.unlikely>=0)pollCounts=votes;
    const plausible=pollCounts?.plausible||0,unlikely=pollCounts?.unlikely||0;
    const total=plausible+unlikely;
    poll.querySelectorAll("[data-poll-choice]").forEach(button=>{
      const selected=button.dataset.pollChoice===selectedPoll;
      button.classList.toggle("is-selected",selected);
      button.setAttribute("aria-pressed",String(selected));
      button.disabled=Boolean(selectedPoll)||pollPending;
    });
    const message=poll.querySelector("[data-poll-status]");
    if(message){
      message.classList.toggle("is-error",Boolean(pollError));
      message.textContent=pollError ||
        (pollPending?"投票を送信しています…" :
         selectedPoll?(total>0?"投票済み・実際の予想 "+total+"票（ブラウザ単位）":"投票済み。集計を取得しています。"):
         total>0?"いままでの予想 "+total+"票。投票すると結果が見られます。":"最初の予想をどうぞ。1ブラウザにつき1票。");
    }
    const results=poll.querySelector("[data-poll-results]");
    if(results){
      results.hidden=!selectedPoll || total===0;
      if(total>0 && selectedPoll){
        const yes=Math.round(plausible/total*100),no=100-yes;
        const y=poll.querySelector("[data-poll-plausible-percent]");
        const n=poll.querySelector("[data-poll-unlikely-percent]");
        const yBar=poll.querySelector("[data-poll-plausible-bar]");
        const nBar=poll.querySelector("[data-poll-unlikely-bar]");
        if(y)y.textContent=yes+"%";
        if(n)n.textContent=no+"%";
        if(yBar)yBar.style.width=yes+"%";
        if(nBar)nBar.style.width=no+"%";
      }
    }
  }
  renderPoll();

  const allSlugs=[...new Set(buttons.map(b=>b.dataset.likeSlug).filter(Boolean))].slice(0,20);
  if(article && !allSlugs.includes(article))allSlugs.unshift(article);

  // Approximate, privacy-minimal traffic sources: never transmit a referrer URL.
  // Only externally arriving page views get a category; internal navigation is ignored.
  function arrivalChannel(){
    const params=new URLSearchParams(location.search);
    const tagged=(params.get("utm_source")||"").toLowerCase();
    if(tagged==="x" || tagged==="twitter")return "x";
    // The site's X launch share URL uses ?src=x-launch-YYYYMMDD-vN.
    // Classify the short known campaign marker, never store the entire URL.
    const launch=(params.get("src")||"").toLowerCase();
    if(/^x-launch-[0-9]{8}-v[0-9]+$/.test(launch))return "x";
    if(["google","bing","yahoo","duckduckgo","search"].includes(tagged))return "search";
    let referrer;
    try{referrer=new URL(document.referrer);}catch(_){return "unattributed";}
    if(referrer.origin===location.origin)return "";
    const host=referrer.hostname.toLowerCase();
    const matches=(domain)=>host===domain || host.endsWith("."+domain);
    if(["x.com","twitter.com","t.co"].some(matches))return "x";
    if(["google.com","google.co.jp","bing.com","search.yahoo.co.jp",
        "duckduckgo.com","search.brave.com","ecosia.org"].some(matches))return "search";
    return "external";
  }
  const visitChannel=arrivalChannel();

  function heartView() {
    for(const b of buttons){
      const on=likedSet.has(b.dataset.likeSlug);
      b.classList.toggle("liked",on);
      b.setAttribute("aria-pressed",String(on));
      b.setAttribute("aria-label",on?"いいねを取り消す":"この記事にいいね");
      b.disabled=pending.has(b.dataset.likeSlug);
      const heart=b.querySelector("span[aria-hidden]");
      if(heart)heart.textContent=on?"♥":"♡";
    }
    try{localStorage.setItem(likesKey,JSON.stringify([...likedSet]));}catch(_){}
  }
  heartView();

  async function apiPost(action,articleSlug="",choice=""){
    const payload={action,article:articleSlug,clientId};
    if(action==="vote")payload.choice=choice;
    if(action==="visit" && visitChannel)payload.channel=visitChannel;
    if(action==="visit" && fromArticle)payload.fromArticle=fromArticle;
    const response=await fetch("/api/engagement",{
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify(payload),
      cache:"no-store",credentials:"same-origin"
    });
    if(!response.ok)throw new Error("engagement not available");
    return response.json();
  }

  function showMetrics(data){
    if(!data || data.unit!=="anonymous-browser" || !Number.isFinite(data.visitors))return;
    const shell=document.querySelector("[data-observer-widget]");
    const value=document.querySelector("[data-observer-count]");
    if(shell && value){
      value.textContent=Intl.NumberFormat("ja-JP").format(data.visitors);
      shell.hidden=false;
    }
    for(const [slug,metric] of Object.entries(data.articles||{})){
      if(!metric || !Number.isFinite(metric.likes))continue;
      document.querySelectorAll("[data-like-count]").forEach(node=>{
        if(node.dataset.likeCount===slug){
          node.textContent=Intl.NumberFormat("ja-JP").format(metric.likes);
          node.hidden=false;
        }
      });
      if(slug===article && Number.isFinite(metric.readers)){
        const wrapper=document.querySelector("[data-article-readers]");
        const count=document.querySelector("[data-article-reader-count]");
        if(wrapper && count){wrapper.hidden=false;count.textContent=Intl.NumberFormat("ja-JP").format(metric.readers);}
      }
      if(slug===article)renderPoll(metric.votes);
    }
  }
  async function refresh(){
    try{
      const params=new URLSearchParams();
      if(allSlugs.length)params.set("articles",allSlugs.join(","));
      const res=await fetch("/api/engagement?"+params.toString(),{cache:"no-store"});
      if(res.ok)showMetrics(await res.json());
    }catch(_){}
  }

  // A page visit counts each browser once globally, and once per article.
  (async()=>{
    try{await apiPost("visit",article);}
    catch(_){/* The news pages and local likes work independently. */}
    await refresh();
  })();

  document.addEventListener("click",async e=>{
    const b=e.target.closest("[data-like-slug]");
    if(!b || pending.has(b.dataset.likeSlug))return;
    const slug=b.dataset.likeSlug;
    if(likedSet.has(slug))likedSet.delete(slug);
    else likedSet.add(slug);
    pending.add(slug);
    heartView();
    try {
      const result=await apiPost("like",slug);
      if(result.liked===true)likedSet.add(slug);
      else if(result.liked===false)likedSet.delete(slug);
      await refresh();
    } catch(_){
      // Local personal preference remains available during Free-tier limits.
    } finally {
      pending.delete(slug);
      heartView();
    }
  });

  poll?.addEventListener("click",async event=>{
    const button=event.target.closest("button[data-poll-choice]");
    if(!button || pollPending || selectedPoll || !article)return;
    const choice=button.dataset.pollChoice;
    if(!["plausible","unlikely"].includes(choice))return;
    pollPending=true;pollError="";renderPoll();
    try {
      const result=await apiPost("vote",article,choice);
      if(!["plausible","unlikely"].includes(result.vote))
        throw new Error("予想を確認できませんでした");
      // Server confirms the choice, including idempotent retries.
      selectedPoll=result.vote;
      savePolls();
      renderPoll(result.votes);
    }catch(_){
      pollError="投票を保存できませんでした。通信状況を確認して再度お試しください。";
    }finally{
      pollPending=false;renderPoll();
    }
  });

  document.querySelectorAll("[data-share-slug]").forEach(link=>{
    link.addEventListener("click",()=>{
      const body=JSON.stringify({action:"share",article:link.dataset.shareSlug,clientId});
      try {
        if(navigator.sendBeacon){
          navigator.sendBeacon("/api/engagement",new Blob([body],{type:"application/json"}));
        } else {
          fetch("/api/engagement",{method:"POST",headers:{"content-type":"application/json"},
                body,keepalive:true}).catch(()=>{});
        }
      }catch(_){}
    });
  });

  document.querySelectorAll("[data-copy-url]").forEach(b=>{
    b.addEventListener("click",async()=>{
      const label=b.querySelector("span:last-child");
      try{
        await navigator.clipboard.writeText(b.dataset.copyUrl);
        if(label)label.textContent="コピーしました ✓";
        window.setTimeout(()=>{if(label)label.textContent="リンクをコピー";},2200);
      }catch(_){window.prompt("記事のURL",b.dataset.copyUrl);}
    });
  });
})();
