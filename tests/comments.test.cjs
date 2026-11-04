'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {DatabaseSync}=require('node:sqlite');
const vm=require('node:vm');
const {webcrypto,randomUUID}=require('node:crypto');
const path=require('node:path');

const backend=readFileSync(path.join(__dirname,'../src/comments.js'),'utf8')
  .replace("import { DurableObject } from 'cloudflare:workers';",'class DurableObject { constructor(ctx) { this.ctx=ctx; } }')
  .replace('export class ReaderComments','class ReaderComments');
const sandbox=vm.createContext({crypto:{...webcrypto,subtle:webcrypto.subtle,randomUUID},
  Response,Request,URL,TextEncoder,console,Date});
vm.runInContext(backend,sandbox);
const ReaderComments=vm.runInContext('ReaderComments',sandbox);

function fixture(adminToken){
  const db=new DatabaseSync(':memory:');
  const sql={exec(query,...args){
    const statement=db.prepare(query);
    if(/^\s*SELECT\b/i.test(query)){
      const rows=statement.all(...args);
      return {toArray:()=>rows,one:()=>rows[0]||null};
    }
    statement.run(...args);
    return {toArray:()=>[],one:()=>null};
  }};
  const ctx={storage:{sql,transactionSync(fn){db.exec('BEGIN IMMEDIATE');try{const result=fn();db.exec('COMMIT');return result;}catch(e){db.exec('ROLLBACK');throw e;}}}};
  const instance=new ReaderComments(ctx,{COMMENTS_ADMIN_TOKEN:adminToken});
  const api=async(method,uri,payload,headers={})=>{
    const request=new Request('https://veripsa.com'+uri,{method,headers:{'content-type':'application/json',...headers},body:payload===undefined?undefined:JSON.stringify(payload)});
    const result=await instance.fetch(request);
    return {status:result.status,data:await result.json()};
  };
  return {db,api};
}
const makePost=(clientId,body='こんなニュースが本当に起きそうで笑った！')=>({action:'post',article:'test-story',clientId,body,nickname:'未来の読者'});

test('post, list, duplicate and abuse rules',async()=>{
  const {db,api}=fixture();
  const id=randomUUID();
  const first=await api('POST','/api/comments',makePost(id));
  assert.equal(first.status,201);assert.ok(first.data.comment.id);
  const listed=await api('GET','/api/comments?article=test-story');
  assert.equal(listed.status,200);assert.equal(listed.data.total,1);
  assert.equal(listed.data.comments[0].body,'こんなニュースが本当に起きそうで笑った！');
  assert.equal((await api('POST','/api/comments',makePost(id,'二件目'))).status,429);
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'<script>alert(1)</script>'))).status,400);
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'https://spam.example.com'))).status,400);
  assert.equal((await api('GET','/api/comments?article=../../etc')).status,400);
  db.close();
});

test('reports hide after three independent browsers; owner can remove',async()=>{
  const {db,api}=fixture();
  const author=randomUUID();
  const posted=await api('POST','/api/comments',makePost(author));
  const commentId=posted.data.comment.id;
  const report=(visitor)=>api('POST','/api/comments',{action:'report',article:'test-story',clientId:visitor,commentId});
  assert.equal((await report(author)).status,403);
  for(let i=0;i<2;i++){const ret=await report(randomUUID());assert.equal(ret.status,200);assert.equal(ret.data.hidden,false);}
  const third=await report(randomUUID());assert.equal(third.data.hidden,true);
  assert.equal((await api('GET','/api/comments?article=test-story')).data.total,0);
  assert.equal((await api('POST','/api/comments',{action:'remove',article:'test-story',commentId,clientId:randomUUID()})).status,403);
  assert.equal((await api('POST','/api/comments',{action:'remove',article:'test-story',commentId,clientId:author})).status,200);
  db.close();
});

test('admin requires secret and hides/restores/deletes',async()=>{
  const token='a'.repeat(36);
  const {db,api}=fixture(token);
  const posted=await api('POST','/api/comments',makePost(randomUUID()));
  const commentId=posted.data.comment.id;
  assert.equal((await api('GET','/api/comments/admin')).status,401);
  const headers={authorization:'Bearer '+token};
  const all=await api('GET','/api/comments/admin',undefined,headers);
  assert.equal(all.data.comments.length,1);
  const moderate=(action)=>api('POST','/api/comments/admin',{action,commentId},headers);
  assert.equal((await moderate('hide')).status,200);
  assert.equal((await api('GET','/api/comments?article=test-story')).data.total,0);
  assert.equal((await moderate('restore')).status,200);
  assert.equal((await api('GET','/api/comments?article=test-story')).data.total,1);
  assert.equal((await moderate('delete')).status,200);
  assert.equal((await api('GET','/api/comments?article=test-story')).data.total,0);
  db.close();
});

test('batch summaries include only visible verified counts',async()=>{
  const token='q'.repeat(40);
  const {db,api}=fixture(token);
  const first=await api('POST','/api/comments',makePost(randomUUID(),'この未来予想はいい線いっていると思う'));
  assert.equal(first.status,201);
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'ニュースの続きが楽しみです'))).status,201);
  const query='/api/comments/summary?articles=test-story,other-story,bad_slug';
  const initial=await api('GET',query);
  assert.equal(initial.status,200);
  assert.equal(initial.data.counts['test-story'],2);
  assert.equal(initial.data.counts['other-story'],0);
  assert.equal(Object.hasOwn(initial.data.counts,'bad_slug'),false);
  assert.equal('visitor' in initial.data,false);
  const headers={authorization:'Bearer '+token};
  const hidden=await api('POST','/api/comments/admin',{action:'hide',commentId:first.data.comment.id},headers);
  assert.equal(hidden.status,200);
  const summary=await api('GET',query);
  assert.equal(summary.data.counts['test-story'],1);
  assert.equal((await api('POST','/api/comments/summary',{})).status,405);
  db.close();
});

test('comment form rejects obvious phone numbers in either character width',async()=>{
  const {db,api}=fixture();
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'連絡先 090-1234-5678'))).status,400);
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'０８０ー１２３４ー５６７８に連絡'))).status,400);
  assert.equal((await api('POST','/api/comments',makePost(randomUUID(),'数字だけの未来予報は2040年です'))).status,201);
  db.close();
});

test('browser renders user submissions without innerHTML',()=>{
  const js=readFileSync(path.join(__dirname,'../assets/comments.js'),'utf8');
  assert.match(js,/textContent=value/);
  assert.doesNotMatch(js,/\.innerHTML\s*=/);
});


test('reading comments does not create a browser id until the reader writes',async()=>{
  const browserSource=readFileSync(path.join(__dirname,'../assets/comments.js'),'utf8');
  const storage=new Map(),requests=[];
  const listeners={};
  const basic=()=>({
    textContent:'',hidden:false,disabled:false,value:'',dataset:{},
    classList:{toggle(){}},
    addEventListener(name,callback){this.listeners??={};this.listeners[name]=callback;},
    replaceChildren(){},focus(){}
  });
  const status=basic(),count=basic(),list=basic(),submit=basic(),nickname=basic(),body=basic();
  body.value='読んでから初めて投稿します';
  const form=basic();
  form.querySelector=selector=>({
    '[type="submit"]':submit,'[name="nickname"]':nickname,'[name="body"]':body
  })[selector]||null;
  form.addEventListener=(name,callback)=>{listeners[name]=callback;};
  const root=basic();root.dataset.article='test-story';
  root.querySelector=selector=>({
    '[data-comments-list]':list,'[data-comments-status]':status,
    '[data-comments-count]':count,'[data-comments-form]':form
  })[selector]||null;
  const document={
    querySelector:selector=>selector==='[data-comments]'?root:null,
    querySelectorAll:()=>[],
    createElement:basic
  };
  const window={confirm:()=>true};
  const localStorage={
    getItem:key=>storage.get(key)||null,
    setItem:(key,value)=>storage.set(key,value)
  };
  const fetch=async(url,options={})=>{
    const method=options.method||'GET';
    const payload=options.body?JSON.parse(options.body):null;
    requests.push({url,method,payload});
    if(method==='POST')return {ok:true,json:async()=>({comment:{id:'comment-1'}})};
    return {ok:true,json:async()=>({comments:[],total:0})};
  };
  vm.runInNewContext(browserSource,{
    document,window,localStorage,sessionStorage:{removeItem(){}},fetch,
    crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},Intl,Date,Array,
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.filter(x=>x.method==='GET').length,1);
  assert.equal(requests.filter(x=>x.method==='POST').length,0);
  assert.equal(storage.has('mihassei-anonymous-browser-v1'),false,
    'opening and reading comments stays identifier-free');
  await listeners.submit({preventDefault(){}});
  assert.equal(requests.filter(x=>x.method==='POST').length,1);
  const post=requests.find(x=>x.method==='POST');
  assert.equal(post.payload.clientId,'11111111-1111-4111-8111-111111111111');
  assert.equal(storage.get('mihassei-anonymous-browser-v1'),post.payload.clientId);
});
