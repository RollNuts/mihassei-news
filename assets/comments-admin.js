(() => {
  'use strict';
  const form=document.querySelector('#comments-admin-form');
  if(!form)return;
  const input=document.querySelector('#comments-admin-token');
  const list=document.querySelector('#comments-admin-list');
  const status=document.querySelector('#comments-admin-status');
  let token='';
  const el=(tag,value)=>{const e=document.createElement(tag);e.textContent=value;return e;};
  async function api(method,payload){
    const response=await fetch('/api/comments/admin',{
      method,cache:'no-store',headers:{'authorization':'Bearer '+token,'content-type':'application/json'},
      body:payload?JSON.stringify(payload):undefined
    });
    const json=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(json.error||'操作に失敗しました');
    return json;
  }
  async function refresh(){
    status.textContent='読み込み中…';
    try{
      const data=await api('GET');list.replaceChildren();
      for(const c of data.comments){
        const item=document.createElement('article');
        const head=el('strong',c.nickname+' / '+c.article+' / '+c.status+' / 報告'+c.reports+'件');
        item.append(head,el('p',c.body),el('small',new Date(c.createdAt).toLocaleString('ja-JP')));
        const link=el('a','記事を見る ↗');link.href='/articles/'+encodeURIComponent(c.article)+'/';
        link.target='_blank';link.rel='noopener noreferrer';item.append(link,el('p','ID: '+c.id));
        for(const [action,label] of [['hide','非表示'],['restore','再表示'],['delete','完全削除']]){
          const b=el('button',label);b.type='button';
          b.disabled=action==='hide'&&c.status==='hidden' || action==='restore'&&c.status==='visible';
          b.addEventListener('click',async()=>{
            if(action==='delete'&&!confirm('完全削除しますか？'))return;
            b.disabled=true;
            try{await api('POST',{action,commentId:c.id});await refresh();}
            catch(e){status.textContent=e.message;b.disabled=false;}
          });
          item.append(b);
        }
        list.append(item);
      }
      status.textContent='最新'+data.comments.length+'件を表示';
    }catch(e){status.textContent=e.message;}
  }
  form.addEventListener('submit',event=>{
    event.preventDefault();
    token=input.value.trim();input.value='';void refresh();
  });
})();
