(() => {
  'use strict';
  // Public aggregate data only. Never create site visits, votes or likes here.
  const root=document.querySelector('[data-growth-desk]');
  if(!root)return;

  const status=root.querySelector('[data-growth-status]');
  const summary=root.querySelector('[data-growth-summary]');
  const details=root.querySelector('[data-growth-details]');
  const categoryBody=root.querySelector('[data-growth-categories]');
  const articleBody=root.querySelector('[data-growth-articles]');
  const sort=root.querySelector('[data-growth-sort]');
  const advice=root.querySelector('[data-growth-advice]');
  const pvCoverage=root.querySelector('[data-growth-pv-coverage]');
  const pattern=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const categories=new Set(['社会','経済','技術','政治','暮らし','スポーツ','エンタメ']);
  let stories=[];
  try{
    const parsed=JSON.parse(root.dataset.growthStories || '[]');
    if(!Array.isArray(parsed))throw new Error('invalid stories');
    const seen=new Set();
    stories=parsed.filter(a=>{
      if(!a || typeof a.slug!=='string' || !pattern.test(a.slug) ||
         typeof a.title!=='string' || !a.title.trim() || !categories.has(a.category) ||
         typeof a.published!=='string' || !/^\d{4}-\d{2}-\d{2}$/.test(a.published) ||
         seen.has(a.slug))return false;
      seen.add(a.slug);
      return true;
    }).slice(0,20);
  }catch(_){stories=[];}

  const number=new Intl.NumberFormat('ja-JP');
  const count=n=>number.format(n);
  const valid=n=>Number.isInteger(n) && n>=0;
  const own=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
  function optionalCount(object,key){
    if(!object || !own(object,key) || object[key]===null)return null;
    if(!valid(object[key]))throw new Error('PV集計が不正です');
    return object[key];
  }
  const displayCount=n=>n===null?'—':count(n);
  function note(msg){if(status)status.textContent=msg;}
  function cell(row,value){
    const td=document.createElement('td');
    td.textContent=String(value);
    row.append(td);
    return td;
  }
  function headingCell(row,value){
    const th=document.createElement('th');
    th.scope='row';
    th.textContent=value;
    row.append(th);
  }
  function build(rows){
    const grouped=new Map();
    for(const a of rows){
      const sums=grouped.get(a.category)||{stories:0,x:0,search:0,readers:0,continued:0};
      sums.stories++;
      sums.x+=a.x;sums.search+=a.search;
      sums.readers+=a.readers;sums.continued+=a.continuedReaders;
      grouped.set(a.category,sums);
    }
    const groupRows=[...grouped].sort((a,b)=>b[1].x-a[1].x || b[1].search-a[1].search)
      .map(([category,totals])=>{
        const tr=document.createElement('tr');
        const th=document.createElement('th');th.scope='row';
        th.append(document.createTextNode(category));
        const small=document.createElement('small');small.textContent=totals.stories+'記事の合計';
        th.append(small);tr.append(th);
        cell(tr,count(totals.x));cell(tr,count(totals.search));
        cell(tr,count(totals.readers));cell(tr,count(totals.continued));
        return tr;
      });
    categoryBody.replaceChildren(...groupRows);

    function draw(order){
      const sorted=[...rows].sort((a,b)=>{
        let primary=0;
        if(order==='published'){
          primary=b.published.localeCompare(a.published);
        }else{
          const left=a[order],right=b[order];
          if(left===null && right!==null)primary=1;
          else if(left!==null && right===null)primary=-1;
          else if(left!==null && right!==null)primary=right-left;
        }
        return primary ||
          b.published.localeCompare(a.published) ||
          a.slug.localeCompare(b.slug);
      });
      const trs=sorted.map(a=>{
        const tr=document.createElement('tr');
        const td=document.createElement('td');
        const link=document.createElement('a');
        link.href='/articles/'+a.slug+'/';
        link.textContent=a.title;
        td.append(link);
        const meta=document.createElement('small');
        meta.textContent=a.category+' · 実際の掲載日 '+a.published;
        td.append(meta);tr.append(td);
        cell(tr,count(a.x));cell(tr,count(a.search));
        cell(tr,displayCount(a.weekPv));cell(tr,displayCount(a.pageViews));
        cell(tr,count(a.readers));cell(tr,count(a.continuedReaders));cell(tr,count(a.likes));
        cell(tr,count(a.shareClicks));
        return tr;
      });
      articleBody.replaceChildren(...trs);
    }
    draw('x');
    sort?.addEventListener('change',()=>draw(sort.value));
  }
  if(!stories.length){note('公開済みの記事候補がありません。');return;}

  async function load(){
    const url='/api/engagement?articles='+encodeURIComponent(stories.map(a=>a.slug).join(','));
    const response=await fetch(url,{cache:'no-store',credentials:'same-origin'});
    if(!response.ok)throw new Error('集計APIの応答がありません');
    const data=await response.json();
    if(data.unit!=='anonymous-browser' || !valid(data.visitors) ||
       !data.articles || typeof data.articles!=='object' ||
       !data.channels || !valid(data.channels.x) || !valid(data.channels.search))
      throw new Error('公開集計が不完全です');

    // Never convert missing or invalid observations into fake zero values.
    const rows=stories.map(a=>{
      const metric=data.articles[a.slug];
      if(!metric || !metric.arrivals || !valid(metric.arrivals.x) ||
         !valid(metric.arrivals.search) || !valid(metric.readers) ||
         !valid(metric.continuedReaders) || !valid(metric.likes) ||
         !valid(metric.shareClicks))
        throw new Error('新しい記事別流入計測の公開反映待ちです');
      const pageViews=optionalCount(metric,'pageViews');
      const weekPv=optionalCount(metric,'last7DaysPageViews');
      if(pageViews!==null && weekPv!==null && weekPv>pageViews)
        throw new Error('記事PVの期間集計が累計を超えています');
      return {...a,
        x:metric.arrivals.x,
        search:metric.arrivals.search,
        pageViews,
        weekPv,
        readers:metric.readers,
        continuedReaders:metric.continuedReaders,
        likes:metric.likes,
        shareClicks:metric.shareClicks
      };
    });

    const s=root.querySelector('[data-growth-visitors]');
    const x=root.querySelector('[data-growth-channel-x]');
    const search=root.querySelector('[data-growth-channel-search]');
    const pv=root.querySelector('[data-growth-page-views]');
    const weekPvNode=root.querySelector('[data-growth-week-page-views]');
    const weekPvNote=root.querySelector('[data-growth-week-page-views-note]');
    if(s)s.textContent=count(data.visitors);
    if(x)x.textContent=count(data.channels.x);
    if(search)search.textContent=count(data.channels.search);

    const sitePv=optionalCount(data,'pageViews');
    let siteWeekPv=null;
    let coverageText='直近7日PVは現在未取得';
    if(own(data,'last7Days')){
      const week=data.last7Days;
      if(!week || typeof week!=='object' ||
         typeof week.startDateJst!=='string' ||
         typeof week.endDateJst!=='string' ||
         !/^\d{4}-\d{2}-\d{2}$/.test(week.startDateJst) ||
         !/^\d{4}-\d{2}-\d{2}$/.test(week.endDateJst) ||
         !valid(week.measuredDays) || week.measuredDays>7 ||
         typeof week.complete!=='boolean')
        throw new Error('PV計測範囲が不正です');
      siteWeekPv=optionalCount(week,'pageViews');
      coverageText=siteWeekPv===null
        ? 'PV計測開始待ち'
        : week.startDateJst+'〜'+week.endDateJst+' · '+week.measuredDays+
          '/7日計測'+(week.complete?'':'（一部期間）');
    }
    if(pv)pv.textContent=displayCount(sitePv);
    if(weekPvNode)weekPvNode.textContent=displayCount(siteWeekPv);
    if(weekPvNote)weekPvNote.textContent=coverageText;
    if(pvCoverage)pvCoverage.textContent=
      '記事PV：'+coverageText+'。未計測の記事は0ではなく「—」です。';

    build(rows);
    summary.hidden=false;
    details.hidden=false;
    const xArticles=rows.filter(item=>item.x>0).length;
    const searchArticles=rows.filter(item=>item.search>0).length;
    const pvRows=rows.filter(item=>item.weekPv!==null);
    const topPv=[...pvRows].sort((a,b)=>b.weekPv-a.weekPv)[0];
    const pvAdvice=topPv && topPv.weekPv>0
      ? ' 取得できた直近7日PVでは「'+topPv.title+'」が'+count(topPv.weekPv)+
        'PV。計測範囲を揃えて次回も比較します。'
      : '';
    if(xArticles===0 && searchArticles===0)
      advice.textContent='この記事別の流入記録はまだありません。X投稿デスクから2〜3本を実際に紹介し、数日後にX・検索の実測が増えたか確認しましょう。計測開始前の訪問は復元できません。'+pvAdvice;
    else if(xArticles===0)
      advice.textContent='検索由来の記録がある一方、今回の対象記事にはX由来の記録がありません。Xで記事と画像を紹介して、数日後に記事別の到着を比べてみましょう。'+pvAdvice;
    else
      advice.textContent='Xから届いた記録のある記事が'+xArticles+'本あります。見出しと画像の題材を比べ、翌日以降の実測を確認してから投稿するテーマを絞りましょう。'+pvAdvice;
    note('実測を表示しています。最新の公開集計／直近'+rows.length+'記事（全期間の合計ではありません）。');
  }
  load().catch(error=>{
    summary.hidden=true;
    details.hidden=true;
    note('集計を表示できません：'+(error?.message||'現在利用できません')+
      '。公開記事は引き続き閲覧できます。');
    advice.textContent='計測が正常に公開された後、X投稿デスクで紹介した記事への流入を確認できます。実数が取れない間は成果を推測しません。';
  });
})();
