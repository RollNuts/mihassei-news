(() => {
  'use strict';

  // An article already loads its own comment thread; avoid duplicate requests.
  if(document.querySelector('[data-comments]'))return;
  const badges=[...document.querySelectorAll('[data-comment-count]')];
  const discussions=document.querySelector('[data-comment-discussions]');
  if(!badges.length && !discussions)return;

  const validSlug=/^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  let options=[];
  if(discussions){
    try{
      const parsed=JSON.parse(discussions.dataset.commentDiscussionOptions || '[]');
      if(Array.isArray(parsed))options=parsed.filter(item=>
        item && typeof item.slug==='string' && validSlug.test(item.slug) &&
        typeof item.title==='string' && typeof item.category==='string'
      ).slice(0,20);
    }catch(_){}
  }
  const slugs=[...new Set([
    ...options.map(x=>x.slug),
    ...badges.map(b=>b.dataset.commentCount)
  ])].filter(x=>typeof x==='string' && validSlug.test(x)).slice(0,20);
  if(!slugs.length)return;

  function apply(counts){
    for(const badge of badges){
      const n=counts[badge.dataset.commentCount];
      if(Number.isInteger(n) && n>0){
        badge.textContent=String(n);
        badge.hidden=false;
      } else {
        badge.hidden=true;
      }
    }
    if(!discussions)return;
    const list=discussions.querySelector('[data-discussion-list]');
    const entries=options
      .map(item=>({...item,count:counts[item.slug]}))
      .filter(item=>Number.isInteger(item.count) && item.count>0)
      .sort((a,b)=>b.count-a.count)
      .slice(0,3);
    if(!entries.length){
      list.hidden=true;
      list.replaceChildren();
      return;
    }
    const fragment=document.createDocumentFragment();
    for(const item of entries){
      const link=document.createElement('a');
      link.className='reader-discussion-link';
      link.href='/articles/'+item.slug+'/#reader-comments';
      const label=document.createElement('span');
      label.className='reader-discussion-category';
      label.textContent=item.category+' / '+item.count+'件のコメント';
      const title=document.createElement('strong');
      title.textContent=item.title;
      link.append(label,title);
      fragment.append(link);
    }
    list.replaceChildren(fragment);
    list.hidden=false;
  }

  const cacheKey='mihassei-comment-count-summary-v1';
  let cached;
  try {
    const value=JSON.parse(sessionStorage.getItem(cacheKey)||'null');
    if(value && value.expires>Date.now() && slugs.every(slug=>Number.isInteger(value.counts?.[slug])))
      cached=value.counts;
  }catch(_){}
  if(cached){apply(cached);return;}

  async function load(){
    try{
      const response=await fetch('/api/comments/summary?articles='+encodeURIComponent(slugs.join(',')),
        {cache:'no-store',credentials:'same-origin'});
      if(!response.ok)return;
      const data=await response.json();
      if(data.version!==1 || !data.counts || typeof data.counts!=='object')return;
      const counts={};
      for(const slug of slugs){
        const n=data.counts[slug];
        if(!Number.isInteger(n) || n<0)return;
        counts[slug]=n;
      }
      try{sessionStorage.setItem(cacheKey,JSON.stringify({expires:Date.now()+600000,counts}));}catch(_){}
      apply(counts);
    }catch(_){/* The newspaper remains usable without an API response. */}
  }

  // Homepage comment activity is secondary content. Do not spend a Worker/DO
  // read until the reader approaches that section; cached counts remain instant.
  if(discussions && 'IntersectionObserver' in window){
    const observer=new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.isIntersecting)){
        observer.disconnect();
        void load();
      }
    },{rootMargin:'600px'});
    observer.observe(discussions);
  }else{
    void load();
  }
})();
