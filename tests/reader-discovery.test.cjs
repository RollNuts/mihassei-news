/* Reader recommendations use real aggregate numbers, no invented trends. */
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/engagement.js'),'utf8');

const candidate=(slug,title,category='社会')=>({slug,title,category});

async function simulate(candidates,counts,apiFails=false){
  const rankings=[],calls=[];
  const popular={dataset:{popularOptions:JSON.stringify(candidates)},hidden:true};
  const list={
    replaceChildren(...items){rankings.splice(0,rankings.length,...items);}
  };
  function element(tag){
    return {
      tag,className:'',textContent:'',href:'',children:[],attributes:{},
      append(...nodes){this.children.push(...nodes);},
      setAttribute(name,value){this.attributes[name]=value;}
    };
  }
  const doc={
    referrer:'',
    querySelector(sel){
      return sel==='[data-popular-section]'?popular:
             sel==='[data-popular-list]'?list:null;
    },
    querySelectorAll(){return [];},
    createElement:element,
    addEventListener(){}
  };
  const ctx={
    window:{},document:doc,Date,Intl,URL,URLSearchParams,
    location:{pathname:'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,options={})=>{
      calls.push({url,method:options.method||'GET'});
      if(options.method==='POST'){
        return {ok:true,json:async()=>({unit:'anonymous-browser',visitors:20,articles:{}})};
      }
      if(apiFails)return {ok:false,json:async()=>({})};
      const articles={};
      for(const [slug,readers] of Object.entries(counts)){
        articles[slug]={readers,likes:0,shareClicks:0};
      }
      return {ok:true,json:async()=>({unit:'anonymous-browser',visitors:20,articles})};
    }
  };
  vm.runInNewContext(source,vm.createContext(ctx),{timeout:1500});
  await new Promise(resolve=>setImmediate(resolve));
  return {popular,rankings,calls};
}

test('without at least two qualifying articles, no false popular area appears',async()=>{
  const items=[candidate('a','記事A'),candidate('b','記事B')];
  for(const counts of [{a:0,b:0},{a:7,b:3},{a:4,b:4},{}]){
    const result=await simulate(items,counts);
    assert.equal(result.popular.hidden,true);
    assert.equal(result.rankings.length,0);
    assert.equal(result.calls.filter(x=>x.method==='GET').length,1);
    assert.equal(result.calls.filter(x=>x.method==='POST').length,1);
  }
});

test('verified browser counts select actual top three from latest list',async()=>{
  const items=[candidate('a','見出しA'),candidate('b','見出しB','経済'),
               candidate('c','見出しC','技術'),candidate('d','見出しD','暮らし')];
  const result=await simulate(items,{a:6,b:11,c:7,d:3});
  assert.equal(result.popular.hidden,false);
  assert.equal(result.rankings.length,3);
  const urls=result.rankings.map(li=>li.children[1].children[1].href);
  assert.deepEqual(urls,['/articles/b/','/articles/c/','/articles/a/']);
  assert.deepEqual(result.rankings.map(li=>li.children[2].textContent),
                   ['11ブラウザ','7ブラウザ','6ブラウザ']);
  assert.equal(result.rankings[0].children[0].textContent,'01');
  assert.equal(result.rankings[0].children[1].children[0].textContent,'経済 / 創作ニュース');
  assert.equal(result.calls.filter(x=>x.method==='GET').length,1,
               'Ranking must reuse one existing GET request');
});

test('invalid candidates and oversized pools cannot create unsafe links',async()=>{
  const items=[
    candidate('../invalid','unsafe'),
    candidate('a',''),
    ...Array.from({length:25},(_,i)=>candidate('story-'+i,'掲載記事 '+i))
  ];
  const counts=Object.fromEntries(items.map((v,i)=>[v.slug,i+20]));
  const result=await simulate(items,counts);
  const get=result.calls.find(x=>x.method==='GET');
  const query=new URL('https://veripsa.com'+get.url).searchParams.get('articles').split(',');
  assert.equal(query.length,20);
  assert.equal(query[0],'story-0');
  assert.ok(query.every(name=>/^story-\d+$/.test(name)));
  assert.equal(result.rankings.length,3);
  assert.ok(result.rankings.every(li=>
    li.children[1].children[1].href.startsWith('/articles/story-')
  ));
});

test('API failure preserves hidden layout without fabricated readership',async()=>{
  const result=await simulate([candidate('a','A'),candidate('b','B')],{},true);
  assert.equal(result.popular.hidden,true);
  assert.equal(result.rankings.length,0);
  assert.equal(result.calls.filter(x=>x.method==='GET').length,1);
});


test('real browser counters refresh while visible, resume on focus and preserve last good value',async()=>{
  const timers=[],listeners={},requests=[];
  let clock=Date.UTC(2026,10,3,2,30);
  class FakeDate extends Date {
    constructor(...args){super(...(args.length?args:[clock]));}
    static now(){return clock;}
  }
  const note={textContent:'※ブラウザ単位の参考値'};
  const widget={hidden:true,querySelector:selector=>selector==='small'?note:null};
  const number={textContent:'—'};
  const responses=[25,26,27,'offline',28];
  const document={
    referrer:'',visibilityState:'visible',
    querySelector:selector=>selector==='[data-observer-widget]'?widget:
                            selector==='[data-observer-count]'?number:null,
    querySelectorAll:()=>[],
    addEventListener(name,callback){listeners[name]=callback;}
  };
  const window={
    setInterval(callback,delay){timers.push({callback,delay});return timers.length;},
    addEventListener(name,callback){listeners['window:'+name]=callback;}
  };
  const ctx={
    window,document,Date:FakeDate,Intl,URL,URLSearchParams,
    location:{pathname:'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,options={})=>{
      if(options.method==='POST'){
        requests.push('POST');
        return {ok:true,json:async()=>({})};
      }
      requests.push('GET');
      const current=responses.shift();
      if(current==='offline')return {ok:false,json:async()=>({})};
      return {ok:true,json:async()=>({
        unit:'anonymous-browser',visitors:current,articles:{}
      })};
    }
  };
  vm.runInNewContext(source,vm.createContext(ctx),{timeout:1500});
  const settle=()=>new Promise(resolve=>setImmediate(resolve));

  await settle();
  assert.deepEqual(requests,['POST','GET']);
  assert.equal(number.textContent,'25');
  assert.equal(widget.hidden,false);
  assert.match(note.textContent,/最終確認/);
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,180000,'bounded free-tier polling');

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.deepEqual(requests,['POST','GET','GET']);
  assert.equal(number.textContent,'26');

  document.visibilityState='hidden';
  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(requests.filter(x=>x==='GET').length,2,'hidden tabs make no API requests');

  document.visibilityState='visible';
  listeners.visibilitychange();
  await settle();
  assert.equal(number.textContent,'27');
  listeners['window:focus']();
  await settle();
  assert.equal(requests.filter(x=>x==='GET').length,3,'focus is throttled');

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(number.textContent,'27','do not show a fake zero on API failure');
  assert.match(note.textContent,/再接続待ち/);

  clock+=180000;
  timers[0].callback();
  await settle();
  assert.equal(number.textContent,'28');
  assert.match(note.textContent,/最終確認/);
  assert.equal(requests.filter(x=>x==='POST').length,1,
    'read-only refresh never registers additional visits');
});

test('a failed first metrics request never displays the placeholder as a visitor total',async()=>{
  const hint={textContent:''};
  const widget={hidden:true,querySelector:selector=>selector==='small'?hint:null};
  const number={textContent:'—'};
  const document={
    referrer:'',visibilityState:'visible',
    querySelector:selector=>selector==='[data-observer-widget]'?widget:
                            selector==='[data-observer-count]'?number:null,
    querySelectorAll:()=>[],
    addEventListener(){}
  };
  const context={
    window:{setInterval(){},addEventListener(){}},document,Date,Intl,URL,URLSearchParams,
    location:{pathname:'/',origin:'https://veripsa.com',search:''},
    localStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'aabbccdd-eeff-4abb-8ccc-123456789012'},
    fetch:async(url,options={})=>options.method==='POST'
      ? {ok:true,json:async()=>({})}
      : {ok:false,json:async()=>({})}
  };
  vm.runInNewContext(source,vm.createContext(context),{timeout:1500});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(widget.hidden,true,'no verified count means no counter on screen');
  assert.equal(number.textContent,'—','error must not invent a count');
});
