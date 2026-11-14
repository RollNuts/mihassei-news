'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../assets/comments.js'),'utf8');
const adminSource=fs.readFileSync(path.join(__dirname,'../assets/comments-admin.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const key='photo-issue:winter-coat-swimsuit-saya-001';

function element(){
  return {
    textContent:'',hidden:false,disabled:false,value:'',dataset:{},children:[],listeners:{},
    classList:{toggle(){}},focus(){},
    append(...nodes){this.children.push(...nodes);},
    replaceChildren(...nodes){this.children=nodes;},
    addEventListener(name,callback){this.listeners[name]=callback;}
  };
}
const descendants=node=>[node,...node.children.flatMap(descendants)];

async function commentsPage({comments=[],failRead=false}={}){
  const storage=new Map(),requests=[];
  const list=element(),status=element(),count=element(),form=element(),submit=element(),nickname=element(),body=element();
  const root=element();
  root.dataset={commentsKey:key,article:'source-story'};
  form.querySelector=selector=>({'[type="submit"]':submit,'[name="nickname"]':nickname,'[name="body"]':body})[selector]||null;
  root.querySelector=selector=>({'[data-comments-list]':list,'[data-comments-status]':status,
    '[data-comments-count]':count,'[data-comments-form]':form})[selector]||null;
  const document={querySelector:selector=>selector==='[data-comments]'?root:null,
    querySelectorAll:()=>[],createElement:element};
  vm.runInNewContext(source,{
    document,window:{confirm:()=>true},Intl,Date,Array,
    crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    sessionStorage:{removeItem(){}},
    fetch:async(url,options={})=>{
      const payload=options.body?JSON.parse(options.body):null;
      requests.push({url,method:options.method||'GET',payload});
      if(payload){
        if(payload.action==='post')comments=[{id:'own-1',body:payload.body,nickname:payload.nickname,createdAt:'2026-11-15T00:00:00Z'},...comments];
        if(payload.action==='remove' || payload.action==='report')comments=comments.filter(c=>c.id!==payload.commentId);
        return {ok:true,json:async()=>({comment:{id:'own-1'},hidden:payload.action==='report'})};
      }
      return {ok:!failRead,json:async()=>({comments,total:comments.length})};
    }
  });
  await settle();
  return {storage,requests,list,status,count,form,body,nickname};
}

test('weekly comment reads and writes keep their own key and reuse owner deletion',async()=>{
  const page=await commentsPage();
  assert.equal(page.requests[0].url,'/api/comments?article='+encodeURIComponent(key));
  assert.equal(page.storage.has('mihassei-anonymous-browser-v1'),false);
  assert.match(page.status.textContent,/まだコメントはありません/);
  page.body.value='冬の海辺の写真とコラムが好きです';page.nickname.value='読者';
  await page.form.listeners.submit({preventDefault(){}});
  const post=page.requests.find(request=>request.payload?.action==='post');
  assert.equal(post.payload.article,key);
  assert.equal(post.payload.body,'冬の海辺の写真とコラムが好きです');
  assert.equal(page.count.textContent,'（1件）');
  const remove=descendants(page.list).find(node=>node.dataset.commentAction==='remove');
  assert.ok(remove,'the same browser can delete its issue comment');
  await page.list.listeners.click({target:{closest:()=>remove}});
  assert.equal(page.requests.find(request=>request.payload?.action==='remove').payload.article,key);
  assert.equal(page.count.textContent,'（0件）');
});

test('weekly comments retain reporting and a useful API error',async()=>{
  const page=await commentsPage({comments:[{id:'other-1',nickname:'読者',body:'写真の感想です',createdAt:'2026-11-15T00:00:00Z'}]});
  const report=descendants(page.list).find(node=>node.dataset.commentAction==='report');
  await page.list.listeners.click({target:{closest:()=>report}});
  const request=page.requests.find(request=>request.payload?.action==='report');
  assert.equal(request.payload.article,key);
  assert.equal(request.payload.commentId,'other-1');
  assert.equal(page.count.textContent,'（0件）');
  const failed=await commentsPage({failRead:true});
  assert.match(failed.status.textContent,/読み込めませんでした/);
  assert.equal(failed.storage.has('mihassei-anonymous-browser-v1'),false);
});

test('comment moderation links open the correct photo issue or news story',async()=>{
  const form=element(),input=element(),list=element(),status=element();
  input.value='test-admin-token';
  const document={createElement:element,querySelector:selector=>({
    '#comments-admin-form':form,'#comments-admin-token':input,
    '#comments-admin-list':list,'#comments-admin-status':status
  })[selector]||null};
  vm.runInNewContext(adminSource,{document,Date,encodeURIComponent,fetch:async()=>({ok:true,json:async()=>({
    comments:[key,'news-story'].map(article=>({id:article,article,nickname:'読者',status:'visible',reports:0,body:'感想です',createdAt:'2026-11-15T00:00:00Z'}))
  })})});
  form.listeners.submit({preventDefault(){}});await settle();
  const links=descendants(list).filter(node=>node.href);
  assert.deepEqual(links.map(node=>node.href),['/photo/weekly/winter-coat-swimsuit-saya-001/','/articles/news-story/']);
  assert.equal(links[0].textContent,'この号を見る ↗');
});
