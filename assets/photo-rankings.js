/* Published photo issues ranked by observed displays in the current JST week. */
(()=>{
  "use strict";
  const section=document.querySelector("[data-photo-rankings]");
  if(!section)return;
  const status=section.querySelector("[data-ranking-status]");
  const list=section.querySelector("[data-ranking-list]");
  if(!status || !list)return;
  const keyPattern=/^photo-issue:([a-z0-9]+(?:-[a-z0-9]+)*)$/;
  const candidates=new Map();
  for(const item of list.querySelectorAll("[data-ranking-key]")){
    const key=item.dataset.rankingKey,match=keyPattern.exec(key||"");
    const link=item.querySelector("a"),pv=item.querySelector("[data-ranking-pv]");
    if(!match || match[1].length>100 || !link || !pv || candidates.has(key))continue;
    try{
      const url=new URL(link.getAttribute("href"),location.origin);
      if(url.origin!==location.origin || url.username || url.password ||
         url.pathname!=="/photo/weekly/"+match[1]+"/" ||
         url.search || url.hash || !link.textContent.trim())continue;
    }catch(_){continue;}
    candidates.set(key,{item,pv});
  }
  if(!candidates.size){
    list.hidden=true;
    status.textContent="公開号の閲覧ランキングはまだありません。";
    return;
  }
  async function load(){
    try{
      const response=await fetch("/api/engagement?view=public&photoPopular=1",{
        method:"GET",headers:{accept:"application/json"},credentials:"same-origin",cache:"no-store"
      });
      if(!response.ok)throw new Error("ranking unavailable");
      const data=await response.json(),rows=data.topPhotoIssues?.last7Days;
      if(data.pageViewTracking?.unit!=="page-view" || !Array.isArray(rows))
        throw new Error("ranking unavailable");
      const selected=[],seen=new Set();
      let previous=Infinity,rank=0;
      for(const row of rows){
        const candidate=candidates.get(row?.article);
        if(!candidate || seen.has(row.article))continue;
        if(!Number.isSafeInteger(row.pageViews) || row.pageViews<=0 ||
           !Number.isSafeInteger(row.todayPageViews) || row.todayPageViews<0 ||
           row.todayPageViews>row.pageViews)continue;
        if(row.pageViews>previous)throw new Error("ranking out of order");
        if(row.pageViews!==previous)rank=selected.length+1;
        candidate.item.value=rank;
        candidate.pv.textContent=row.pageViews.toLocaleString("ja-JP")+" PV";
        selected.push(candidate.item);
        seen.add(row.article);
        previous=row.pageViews;
        if(selected.length===10)break;
      }
      list.replaceChildren(...selected);
      list.hidden=selected.length===0;
      const startedAt=Date.parse(data.pageViewTracking.startedAtUtc);
      const period=data.last7Days?.complete===false && Number.isFinite(startedAt)
        ? new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",year:"numeric",month:"numeric",day:"numeric"})
          .format(startedAt)+"の計測開始からの表示回数（PV）。"
        : "当日を含む7日間（日本時間）の表示回数（PV）。";
      status.textContent=selected.length
        ? period+"同じPVは同順位です。"
        : "この7日間に閲覧が記録された公開号はまだありません。";
    }catch(_){
      list.hidden=true;
      status.textContent="閲覧ランキングを取得できませんでした。時間をおいて再読み込みしてください。";
    }
  }
  void load();
})();
