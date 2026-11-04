(() => {
  "use strict";

  // Broken or blocked third-party stock photographs never leave empty cards.
  document.querySelectorAll("img[data-fallback]").forEach(image=>{
    const recoverImage=()=>{
      const fallback=image.getAttribute("data-fallback");
      if(!fallback || image.getAttribute("src")===fallback)return;
      const fallbackAlt=image.getAttribute("data-fallback-alt");
      image.removeAttribute("data-fallback");
      image.removeAttribute("data-fallback-alt");
      if(fallbackAlt) image.setAttribute("alt",fallbackAlt);
      image.src=fallback;
      const caption=image.closest("figure")?.querySelector("figcaption");
      if(caption)caption.textContent="イメージ画像";
      image.removeAttribute("data-generated-scene");
      image.closest(".story-image-link")?.classList.remove("story-image-link--generated");
    };
    image.addEventListener("error",recoverImage,{once:true});
    // A cached failure can finish before this deferred script attaches.
    if(image.complete && image.naturalWidth===0)recoverImage();
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
        if (editionTitle) editionTitle.textContent = todayIssued ? "本日の架空ニュース" : "最新の架空ニュース";
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


  // A real publication is newer than the last headline this browser saw.
  // Do not show a fictional number or call an analytics endpoint.
  const returnNotice=document.querySelector("[data-return-update]");
  if(returnNotice){
    const key="mihassei-latest-published-v1";
    const current=returnNotice.dataset.latestPublished||"";
    const currentTime=Date.parse(current);
    if(Number.isFinite(currentTime)){
      try{
        const seen=localStorage.getItem(key);
        const seenTime=seen ? Date.parse(seen) : NaN;
        if(Number.isFinite(seenTime) && currentTime>seenTime){
          returnNotice.hidden=false;
        }
        // Never roll back the saved revision if an older cached page is opened.
        if(!Number.isFinite(seenTime) || currentTime>seenTime){
          localStorage.setItem(key,current);
        }
      }catch(_){/* Some browsers disable local storage. The paper still works. */}
    }
  }

  // Progressive enhancement: animate what is already on the page only once.
  // Unscripted browsers and reduced-motion users always see static content.
  const reduceMotion=window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches===true;
  if(!reduceMotion && typeof window.IntersectionObserver==="function"){
    const elements=[...document.querySelectorAll(
      ".index-main .lead, .index-main .brief-card, .index-main .story-card, "+
      ".index-main .genre-explore, .index-main .serials-feature, .index-main .starter-pick-card"
    )].slice(0,32);
    if(elements.length){
      const observer=new window.IntersectionObserver((entries,instance)=>{
        entries.forEach(entry=>{
          if(entry.isIntersecting){
            entry.target.classList.add("motion-arrived");
            instance.unobserve(entry.target);
          }
        });
      },{threshold:0.07,rootMargin:"0px 0px -24px 0px"});
      elements.forEach(node=>observer.observe(node));
    }
  }

  // Show progress through the *content*, not the footer or comment widgets.
  // This works for short newspaper articles and long serial episodes alike.
  const readingBody=document.querySelector(".article-body, .serial-article-body");
  if(readingBody?.getBoundingClientRect && !reduceMotion &&
     document.body?.appendChild &&
     typeof window.requestAnimationFrame==="function"){
    const progress=document.createElement("div");
    progress.className="reading-progress";
    progress.setAttribute("aria-hidden","true");
    document.body.appendChild(progress);
    let scheduled=false;
    function drawProgress(){
      scheduled=false;
      const rect=readingBody.getBoundingClientRect();
      const contentTop=window.scrollY+rect.top;
      const contentHeight=Math.max(1,rect.height);
      // Read position follows the middle of the viewport so it reaches 100%
      // at the text's end instead of requiring a scroll through the footer.
      const readPosition=window.scrollY+window.innerHeight*0.5;
      const fraction=Math.max(0,Math.min(1,
        (readPosition-contentTop)/contentHeight));
      progress.style.transform="scaleX("+fraction.toFixed(4)+")";
    }
    function scheduleProgress(){
      if(scheduled)return;
      scheduled=true;
      window.requestAnimationFrame(drawProgress);
    }
    window.addEventListener("scroll",scheduleProgress,{passive:true});
    window.addEventListener("resize",scheduleProgress);
    scheduleProgress();
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
  const analyticsPreferenceKey="mihassei-analytics-disabled-v1";
  let analyticsDisabled=false,analyticsPreferenceStored=true;
  try{analyticsDisabled=localStorage.getItem(analyticsPreferenceKey)==="1";}
  catch(_){analyticsPreferenceStored=false;}
  // A deliberate local opt-out also lets production checks remain read-only.
  // Never transmit the query, and honour it even when storage is unavailable.
  const analyticsQuery=new URLSearchParams(location.search).get("analytics");
  if(analyticsQuery==="off" || analyticsQuery==="on"){
    analyticsDisabled=analyticsQuery==="off";
    try{
      localStorage.setItem(analyticsPreferenceKey,analyticsDisabled?"1":"0");
      analyticsPreferenceStored=true;
    }catch(_){analyticsPreferenceStored=false;}
  }
  const automatedPreview=typeof navigator!=="undefined" && navigator.webdriver===true;
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
         selectedPoll?(total>0?"投票済み。この『もしも』に "+total+"票（ブラウザ単位）":"投票済み。集計を取得しています。"):
         total>0?"この『もしも』に "+total+"票。投票すると内訳が見られます。":"まだ投票はありません。最初の一票をどうぞ。1ブラウザにつき1票。");
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

  // The homepage's "readers' choice" only uses the existing engagement GET.
  // Titles are data from published local articles; no third-party requests.
  const popularSection=document.querySelector("[data-popular-section]");
  const popularList=document.querySelector("[data-popular-list]");
  let popularCandidates=[];
  if(popularSection && popularList) {
    try {
      const values=JSON.parse(popularSection.dataset.popularOptions||"[]");
      if(Array.isArray(values)){
        const unique=new Set();
        popularCandidates=values.filter(item=>{
          if(!item || typeof item.slug!=="string" ||
             !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.slug) ||
             typeof item.title!=="string" || !item.title.trim() ||
             typeof item.category!=="string" || unique.has(item.slug))return false;
          unique.add(item.slug);
          return true;
        }).slice(0,20);
      }
    }catch(_){popularCandidates=[];}
  }
  // Fetch visible card PVs with the same read-only statistics request. The
  // Worker accepts at most 20 slugs; large archives use bounded batches.
  function requestedArticleSlugs(){
    const visible=[...document.querySelectorAll("[data-article-pv]")]
      .filter(node=>!node.closest?.("[hidden]"))
      .map(node=>node.dataset.articlePv);
    return [...new Set([
      article,...visible,...popularCandidates.map(x=>x.slug),
      ...buttons.map(b=>b.dataset.likeSlug)
    ])].filter(slug=>typeof slug==="string" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug));
  }

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

  async function apiPost(action,articleSlug="",choice="",viewId=""){
    const payload={action,article:articleSlug,clientId};
    if(action==="vote")payload.choice=choice;
    if(action==="visit" && visitChannel)payload.channel=visitChannel;
    if(action==="visit" && fromArticle)payload.fromArticle=fromArticle;
    if(action==="visit" && viewId)payload.viewId=viewId;
    const response=await fetch("/api/engagement",{
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify(payload),
      cache:"no-store",credentials:"same-origin"
    });
    if(!response.ok)throw new Error("engagement not available");
    return response.json();
  }

  function showPopular(articles){
    if(!popularSection || !popularList || !popularCandidates.length)return;
    const ranked=popularCandidates.map((candidate,order)=>({
      ...candidate,order,readers:articles?.[candidate.slug]?.readers
    })).filter(x=>Number.isInteger(x.readers) && x.readers>=5)
      .sort((a,b)=>b.readers-a.readers || a.order-b.order)
      .slice(0,3);
    // No empty or statistically meaningless "popular" section: all ranking
    // rows are based on observed browser counts, not fake defaults.
    if(ranked.length<2){
      popularList.replaceChildren();
      popularSection.hidden=true;
      return;
    }
    const rows=ranked.map((item,index)=>{
      const li=document.createElement("li");
      li.className="readers-choice-item";
      const rank=document.createElement("span");
      rank.className="readers-choice-rank";
      rank.textContent=String(index+1).padStart(2,"0");
      rank.setAttribute("aria-label",String(index+1)+"番目");
      const content=document.createElement("div");
      const category=document.createElement("span");
      category.className="readers-choice-category";
      category.textContent=item.category+" / 創作ニュース";
      const link=document.createElement("a");
      link.href="/articles/"+item.slug+"/";
      link.textContent=item.title;
      link.className="readers-choice-link";
      content.append(category,link);
      const count=document.createElement("span");
      count.className="readers-choice-count";
      count.textContent=Intl.NumberFormat("ja-JP").format(item.readers)+"ブラウザ";
      count.setAttribute("aria-label","閲覧した匿名ブラウザの累計 "+item.readers);
      li.append(rank,content,count);
      return li;
    });
    popularList.replaceChildren(...rows);
    popularSection.hidden=false;
  }

  // Daily counters are server-measured by JST date. A browser contributes
  // once per day, and at most once per article per day; never show fake PVs.
  // Re-reading these counters is GET-only and cannot inflate readership.
  const observerShell=document.querySelector("[data-observer-widget]");
  const observerValue=document.querySelector("[data-observer-count]");
  const observerYesterdayStat=document.querySelector("[data-observer-yesterday-stat]");
  const observerYesterday=document.querySelector("[data-observer-yesterday]");
  const observerReads=document.querySelector("[data-observer-reads]");
  const observerHint=observerShell?.querySelector("small");
  const pvWidget=document.querySelector("[data-pv-widget]");
  const pvToday=document.querySelector("[data-site-pv-today]");
  const pvYesterday=document.querySelector("[data-site-pv-yesterday]");
  const pvWeek=document.querySelector("[data-site-pv-week]");
  const pvPeriodLabel=document.querySelector("[data-site-pv-period-label]");
  const pvTrackingNote=document.querySelector("[data-pv-tracking-note]");
  const knownArticlePvs=new Set();
  let pvMeasurementNote="",pvStartLabel="";
  const observedAtFormat=new Intl.DateTimeFormat("ja-JP",{
    timeZone:"Asia/Tokyo",hour:"2-digit",minute:"2-digit"
  });
  const LIVE_REFRESH_MS=180000; // 3 minutes, only while a page is visible.
  const RETURN_REFRESH_COOLDOWN_MS=45000;
  let lastCheckedAt=0,haveSnapshot=false,refreshPromise=null,observedDay="";
  function updateObserverNumber(node,number){
    if(!node)return;
    const next=Intl.NumberFormat("ja-JP").format(number);
    if(node.textContent===next)return;
    node.textContent=next;
    // Animate only when a newly confirmed count changes, not every refresh.
    if(haveSnapshot && node.classList){
      node.classList.remove("observer-updated");
      void node.offsetWidth;
      node.classList.add("observer-updated");
    }
  }
  function observerFeedback(healthy){
    if(!observerShell)return;
    if(pvWidget){
      observerShell.hidden=false;
      if(pvTrackingNote)pvTrackingNote.textContent=healthy ? pvMeasurementNote :
        haveSnapshot ? pvMeasurementNote+" · 通信回復待ち" : "PVを取得できません・自動再試行";
      return;
    }
    // A failed first fetch is not a visitor count. Keep the widget hidden
    // instead of exposing the placeholder dash as if it were a real number.
    // If we already have a verified snapshot, retain it with a stale hint.
    if(!healthy && !haveSnapshot){
      observerShell.hidden=true;
      return;
    }
    observerShell.hidden=false;
    if(!observerHint)return;
    observerHint.textContent=healthy
      ? "※"+observedDay+"（日本時間）・3分ごと更新・ブラウザ単位"
          +" / 確認 "+observedAtFormat.format(new Date())
      : haveSnapshot ? "※"+observedDay+"の確認値・通信回復待ち"
                     : "※集計を取得できません・自動再試行";
  }

  function validPageViews(value){
    return value===null || (Number.isSafeInteger(value) && value>=0);
  }
  function showPageViewNumber(node,value){
    if(!node)return;
    if(value===null){node.textContent="—";return;}
    updateObserverNumber(node,value);
  }
  function showPageViews(data){
    const tracking=data.pageViewTracking,today=data.today;
    if(!tracking || tracking.timezone!=="Asia/Tokyo" ||
       !(tracking.startedAtUtc===null ||
         (typeof tracking.startedAtUtc==="string" && Number.isFinite(Date.parse(tracking.startedAtUtc)))) ||
       !today || !/^\d{4}-\d{2}-\d{2}$/.test(today.dateJst||"") ||
       !validPageViews(today.pageViews) || !validPageViews(data.pageViews))return false;
    const todayStamp=Date.parse(today.dateJst+"T00:00:00Z");
    if(!Number.isFinite(todayStamp))return false;
    // The legacy browser counters are never used as a fallback for PV.
    if(tracking.startedAtUtc===null && (data.pageViews!==null || today.pageViews!==null))return false;
    observedDay=today.dateJst;
    pvStartLabel=tracking.startedAtUtc===null ? "" : new Intl.DateTimeFormat("ja-JP",{
      timeZone:"Asia/Tokyo",year:"numeric",month:"numeric",day:"numeric"
    }).format(new Date(tracking.startedAtUtc));
    showPageViewNumber(pvToday,today.pageViews);
    const expectedPrevious=new Date(todayStamp-86400000).toISOString().slice(0,10);
    const yesterday=data.yesterday;
    const validYesterday=Boolean(yesterday && yesterday.dateJst===expectedPrevious &&
      validPageViews(yesterday.pageViews));
    showPageViewNumber(pvYesterday,validYesterday ? yesterday.pageViews : null);
    const windowStart=new Date(todayStamp-6*86400000).toISOString().slice(0,10);
    const week=data.last7Days;
    const validWeek=Boolean(week && week.startDateJst===windowStart && week.endDateJst===today.dateJst &&
      validPageViews(week.pageViews) && typeof week.complete==="boolean" &&
      Number.isInteger(week.measuredDays) && week.measuredDays>=0 && week.measuredDays<=7);
    showPageViewNumber(pvWeek,validWeek ? week.pageViews : null);
    if(pvPeriodLabel)pvPeriodLabel.textContent=validWeek && !week.complete ? "計測開始から" : "直近7日";
    pvMeasurementNote=pvStartLabel ? pvStartLabel+"から計測・日本時間" : "PVの計測開始待ち";
    if(pvStartLabel && validYesterday){
      if(yesterday.pageViews===null)pvMeasurementNote+=" · 昨日は未計測";
      else if(yesterday.pageViewsCoverage==="partial")pvMeasurementNote+=" · 昨日は途中から";
    }
    observerFeedback(true);
    return true;
  }
  function showArticlePageViews(slug,metric){
    if(!validPageViews(metric.pageViews))return;
    knownArticlePvs.add(slug);
    document.querySelectorAll("[data-article-pv]").forEach(node=>{
      if(node.dataset.articlePv!==slug)return;
      const count=node.querySelector("[data-article-pv-count]");
      showPageViewNumber(count,metric.pageViews);
      const label=metric.pageViews===null ? "この記事のPVは未計測" :
        "この記事のページ表示回数 "+Intl.NumberFormat("ja-JP").format(metric.pageViews)+"PV";
      node.setAttribute("aria-label",label);
      node.setAttribute("title",pvStartLabel ? pvStartLabel+"からの表示回数" : "計測開始後の表示回数");
    });
  }

  function showMetrics(data){
    if(!data || data.unit!=="anonymous-browser" ||
       !Number.isSafeInteger(data.visitors) || data.visitors<0)return false;
    // A legacy API can still return cumulative 'visitors'. Never label that
    // lifetime figure as today's traffic during a staggered deployment.
    const today=data.today;
    if(!today || !/^\d{4}-\d{2}-\d{2}$/.test(today.dateJst||"") ||
       !Number.isSafeInteger(today.visitors) || today.visitors<0 ||
       !Number.isSafeInteger(today.articleReads) || today.articleReads<0)
      return false;
    showPopular(data.articles||{});
    observedDay=today.dateJst;
    const pvHealthy=pvWidget ? showPageViews(data) : true;
    if(!pvWidget){
      updateObserverNumber(observerValue,today.visitors);
      updateObserverNumber(observerReads,today.articleReads);
    }
    // Only show yesterday when the API returns the actual preceding JST date.
    // Missing fields from a staggered/older deployment must not become zero.
    const yesterday=data.yesterday;
    const todayStamp=Date.parse(today.dateJst+"T00:00:00Z");
    const expectedPrevious=Number.isFinite(todayStamp)
      ? new Date(todayStamp-86400000).toISOString().slice(0,10) : "";
    if(!pvWidget && observerYesterdayStat && observerYesterday){
      const verified=Boolean(yesterday && yesterday.dateJst===expectedPrevious &&
        Number.isSafeInteger(yesterday.visitors) && yesterday.visitors>=0 &&
        Number.isSafeInteger(yesterday.articleReads) && yesterday.articleReads>=0);
      observerYesterdayStat.hidden=!verified;
      if(verified)updateObserverNumber(observerYesterday,yesterday.visitors);
    }
    if(!pvWidget)observerFeedback(true);
    for(const [slug,metric] of Object.entries(data.articles||{})){
      if(!metric)continue;
      if(pvHealthy && data.pageViewTracking)showArticlePageViews(slug,metric);
      if(!Number.isFinite(metric.likes))continue;
      document.querySelectorAll("[data-like-count]").forEach(node=>{
        if(node.dataset.likeCount===slug){
          node.textContent=Intl.NumberFormat("ja-JP").format(metric.likes);
          node.hidden=false;
        }
      });
      if(slug===article && Number.isSafeInteger(metric.todayReaders) && metric.todayReaders>=0){
        const wrapper=document.querySelector("[data-article-readers]");
        const count=document.querySelector("[data-article-reader-count]");
        if(wrapper && count){
          wrapper.hidden=false;
          updateObserverNumber(count,metric.todayReaders);
        }
      }
      if(slug===article)renderPoll(metric.votes);
    }
    return pvHealthy;
  }
  async function refresh(){
    if(refreshPromise)return refreshPromise; // Avoid overlapping requests.
    refreshPromise=(async()=>{
      try{
        const slugs=requestedArticleSlugs(),batches=[];
        for(let offset=0;offset<slugs.length;offset+=20)batches.push(slugs.slice(offset,offset+20));
        if(!batches.length)batches.push([]);
        const snapshots=await Promise.all(batches.map(async batch=>{
          const params=new URLSearchParams();
          if(batch.length)params.set("articles",batch.join(","));
          const res=await fetch("/api/engagement?"+params.toString(),{
            cache:"no-store",credentials:"same-origin"
          });
          if(!res.ok)throw new Error("audience data unavailable");
          return res.json();
        }));
        const first=snapshots[0];
        if(!first || snapshots.some(item=>item.unit!==first.unit || !item.articles || typeof item.articles!=="object") ||
           !showMetrics({...first,articles:Object.assign({},...snapshots.map(item=>item.articles))}))
          throw new Error("audience data unavailable");
        haveSnapshot=true;
      }catch(_){
        // Keep the last confirmed count when a transient API error occurs.
        // Never turn a failed request into a plausible-looking zero.
        observerFeedback(false);
      }
    })();
    try{await refreshPromise;}
    finally{lastCheckedAt=Date.now();refreshPromise=null;}
  }

  // A page display has its own random event ID, separate from browser-level
  // readership. A retry reuses this ID; only a new document/BFCache display
  // creates a new one. Never count pre-rendered or still-hidden documents.
  let currentView={id:"",attempts:0,finished:false,pending:false};
  const visibleDocument=()=>document.visibilityState!=="hidden" && !document.prerendering;
  const trackingAllowed=()=>!analyticsDisabled && !automatedPreview;
  async function recordView(view){
    if(!visibleDocument() || !trackingAllowed() || view.pending || view.finished || view.attempts>=2)return;
    if(!view.id)view.id=makeId();
    view.pending=true;view.attempts++;
    try{
      await apiPost("visit",article,"",view.id);
      view.finished=true;
    }catch(_){/* A lost response may be retried safely with the same event ID. */}
    finally{view.pending=false;}
    await refresh();
    if(!view.finished && view===currentView && view.attempts<2 && typeof window.setTimeout==="function"){
      window.setTimeout(()=>{if(view===currentView)void recordView(view);},1500);
    }
  }
  async function showVisibleDocument(){
    if(!visibleDocument())return;
    if(trackingAllowed())await recordView(currentView);
    else await refresh();
  }
  void showVisibleDocument();

  const preferenceControls=[...document.querySelectorAll("[data-analytics-opt-out]")];
  const preferenceStatus=document.querySelector("[data-analytics-status]");
  function renderAnalyticsPreference(){
    preferenceControls.forEach(control=>{control.checked=analyticsDisabled;});
    if(preferenceStatus)preferenceStatus.textContent=automatedPreview
      ? "この自動テスト環境では閲覧を集計しません。"
      : analyticsDisabled
        ? analyticsPreferenceStored ? "このブラウザの閲覧は集計しません。" : "このページの閲覧は集計しません。設定を保存できないため、次のページには引き継がれません。"
        : analyticsPreferenceStored ? "このブラウザの閲覧を集計します。" : "閲覧を集計します。設定を保存できない状態です。";
  }
  preferenceControls.forEach(control=>{
    control.addEventListener("change",()=>{
      analyticsDisabled=control.checked;
      try{
        localStorage.setItem(analyticsPreferenceKey,analyticsDisabled?"1":"0");
        analyticsPreferenceStored=true;
      }catch(_){analyticsPreferenceStored=false;}
      renderAnalyticsPreference();
      if(!analyticsDisabled && !currentView.finished)void showVisibleDocument();
    });
  });
  renderAnalyticsPreference();
  window.addEventListener?.("storage",event=>{
    if(event.key!==analyticsPreferenceKey)return;
    analyticsDisabled=event.newValue==="1";
    analyticsPreferenceStored=true;
    renderAnalyticsPreference();
  });

  // Poll no more than once every 3 minutes on visible pages. Paused tabs
  // make no requests. Returning to a tab refreshes stale data immediately.
  if(observerShell && typeof window.setInterval==="function"){
    window.setInterval(()=>{
      if(visibleDocument() && !refreshPromise && !currentView.pending)void refresh();
    },LIVE_REFRESH_MS);
  }
  const refreshOnReturn=()=>{
    if(!visibleDocument() || refreshPromise || currentView.pending ||
       Date.now()-lastCheckedAt<RETURN_REFRESH_COOLDOWN_MS)return;
    void refresh();
  };
  const onVisible=()=>{
    if(!visibleDocument())return;
    if(trackingAllowed() && !currentView.finished && currentView.attempts<2)void showVisibleDocument();
    else refreshOnReturn();
  };
  document.addEventListener("visibilitychange",onVisible);
  document.addEventListener("prerenderingchange",onVisible);
  window.addEventListener?.("focus",refreshOnReturn);
  window.addEventListener?.("pageshow",event=>{
    if(!event.persisted)return;
    currentView={id:"",attempts:0,finished:false,pending:false};
    void showVisibleDocument();
  });

  // Archive filters reveal existing cards. Load newly visible PVs once, using
  // GET-only batches rather than turning a filter action into a page view.
  const archiveList=document.querySelector(".archive-list");
  if(archiveList && typeof window.MutationObserver==="function" && typeof window.setTimeout==="function"){
    let queued=false;
    const watchCards=new window.MutationObserver(()=>{
      if(queued)return;
      queued=true;
      window.setTimeout(()=>{
        queued=false;
        const fillVisibleCards=()=>{
          if(visibleDocument() && requestedArticleSlugs().some(slug=>!knownArticlePvs.has(slug)))void refresh();
        };
        // A filter can change while the preceding batch is in flight. Keep
        // that request's result, then read any newly visible missing cards.
        if(refreshPromise)refreshPromise.then(fillVisibleCards);
        else fillVisibleCards();
      },200);
    });
    watchCards.observe(archiveList,{subtree:true,attributes:true,attributeFilter:["hidden"]});
  }

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
        throw new Error("投票結果を確認できませんでした");
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

