(() => {
  'use strict';
  const root=document.querySelector('[data-comments]');
  if(!root)return;
  const article=root.dataset.article;
  const list=root.querySelector('[data-comments-list]');
  const status=root.querySelector('[data-comments-status]');
  const count=root.querySelector('[data-comments-count]');
  const form=root.querySelector('[data-comments-form]');
  const submit=form.querySelector('[type="submit"]');
  const nickname=form.querySelector('[name="nickname"]');
  const body=form.querySelector('[name="body"]');
  const idKey='mihassei-anonymous-browser-v1';
  const ownKey='mihassei-own-comments-v1';
  const reportedKey='mihassei-reported-comments-v1';
  const cacheRead=(key)=>{try{const v=JSON.parse(localStorage.getItem(key)||'[]');return Array.isArray(v)?v.filter(x=>typeof x==='string').slice(-200):[];}catch(_){return [];}};
  const cacheWrite=(key,list)=>{try{localStorage.setItem(key,JSON.stringify([...list].slice(-200)));}catch(_){}};
  const mine=new Set(cacheRead(ownKey)), reported=new Set(cacheRead(reportedKey));
  let clientId='';
  try {
    clientId=localStorage.getItem(idKey)||crypto.randomUUID();
    localStorage.setItem(idKey,clientId);
  } catch(_){clientId=crypto.randomUUID();}
  const fmt=new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
  let loading=false,ready=false;

  function message(text,isError=false){
    status.textContent=text;
    status.classList.toggle('is-error',isError);
    status.hidden=false;
  }
  async function api(action,extras={}){
    const response=await fetch('/api/comments',{
      method:'POST',headers:{'content-type':'application/json'},credentials:'same-origin',cache:'no-store',
      body:JSON.stringify({action,article,clientId,...extras})
    });
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(result.error||'現在コメント機能を利用できません');
    return result;
  }
  function text(tag,cls,value){
    const el=document.createElement(tag);
    if(cls)el.className=cls;
    el.textContent=value;
    return el;
  }
  function renderComment(c){
    const el=document.createElement('article');
    el.className='reader-comment';
    const head=document.createElement('div');head.className='reader-comment-head';
    head.append(text('strong','reader-comment-name',c.nickname||'匿名の未来人'));
    const dt=text('time','reader-comment-date',fmt.format(new Date(c.createdAt)));
    dt.dateTime=c.createdAt;head.append(dt);el.append(head);
    el.append(text('p','reader-comment-body',c.body||''));
    const actions=document.createElement('div');actions.className='reader-comment-actions';
    const own=mine.has(c.id);
    const btn=document.createElement('button');
    btn.type='button';btn.dataset.commentId=c.id;
    btn.dataset.commentAction=own?'remove':'report';
    btn.textContent=own?'自分のコメントを削除':reported.has(c.id)?'通報済み':'問題を報告';
    btn.disabled=!own && reported.has(c.id);
    actions.append(btn);el.append(actions);
    return el;
  }
  async function refresh(){
    if(loading)return;
    loading=true;
    if(!ready)message('読者の声を読み込んでいます…');
    try{
      const res=await fetch('/api/comments?article='+encodeURIComponent(article),{cache:'no-store'});
      const data=await res.json().catch(()=>({}));
      if(!res.ok || !Array.isArray(data.comments))throw new Error(data.error||'読者の声を取得できませんでした');
      list.replaceChildren(...data.comments.map(renderComment));
      count.textContent='（'+(data.total||0)+'件）';
      message(data.comments.length ? '新しいコメントから最大20件表示しています。' : 'まだコメントはありません。最初の未来人になろう。');
      ready=true;
    }catch(_){message('コメントを読み込めませんでした。投稿機能は一時的に利用できない場合があります。',true);}
    finally{loading=false;}
  }
  if('IntersectionObserver' in window){
    const observer=new IntersectionObserver((entries)=>{
      if(entries.some(e=>e.isIntersecting)){observer.disconnect();void refresh();}
    },{rootMargin:'250px'});
    observer.observe(root);
  }else{void refresh();}

  form.addEventListener('submit',async (event)=>{
    event.preventDefault();
    if(submit.disabled)return;
    const content=body.value.trim();
    if(Array.from(content).length<2 || Array.from(content).length>180){message('本文は2〜180文字で入力してください。',true);body.focus();return;}
    submit.disabled=true;submit.textContent='投稿中…';
    try{
      const data=await api('post',{nickname:nickname.value,body:content});
      if(data.comment?.id){mine.add(data.comment.id);cacheWrite(ownKey,mine);}
      body.value='';
      await refresh();
      message('コメントを公開しました。ありがとうございます。');
    }catch(e){message(e.message||'投稿できませんでした。',true);}
    finally{submit.disabled=false;submit.textContent='コメントを投稿';}
  });

  list.addEventListener('click',async event=>{
    const btn=event.target.closest('[data-comment-action]');
    if(!btn || btn.disabled)return;
    const id=btn.dataset.commentId,action=btn.dataset.commentAction;
    if(action==='remove' && !window.confirm('このコメントを削除しますか？'))return;
    btn.disabled=true;
    try{
      const data=await api(action,{commentId:id});
      if(action==='report'){
        reported.add(id);cacheWrite(reportedKey,reported);
        if(data.hidden){await refresh();message('ご報告ありがとうございます。コメントを一時非表示にしました。');}
        else{btn.textContent='通報済み';message('ご報告ありがとうございます。');}
      } else {
        mine.delete(id);cacheWrite(ownKey,mine);
        await refresh();message('コメントを削除しました。');
      }
    }catch(e){btn.disabled=false;message(e.message||'操作に失敗しました。',true);}
  });
})();
