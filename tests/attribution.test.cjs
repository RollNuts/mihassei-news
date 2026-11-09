'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');

const frontend=readFileSync(join(__dirname,'../assets/engagement.js'),'utf8');

async function visitPayload(href,referrer=''){
  const page=new URL(href);
  const posts=[];
  const context={
    document:{
      querySelectorAll:()=>[],querySelector:()=>null,
      addEventListener:()=>{},referrer
    },
    location:{pathname:page.pathname,search:page.search,origin:page.origin},
    window:{},
    localStorage:{getItem:()=>null,setItem:()=>{}},
    crypto:{randomUUID:()=> 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'},
    URL,URLSearchParams,Intl,Date,console,
    fetch:async (path,options={})=>{
      if(options.method==='POST')posts.push(JSON.parse(options.body));
      return {ok:true,json:async()=>({unit:'anonymous-browser',visitors:0,articles:{}})};
    }
  };
  vm.runInNewContext(frontend,vm.createContext(context),{timeout:1000});
  await new Promise(resolve=>setImmediate(resolve));
  const visit=posts.find(item=>item.action==='visit');
  assert.ok(visit,'every page view posts one visit');
  return visit;
}

async function classifyArrival(href,referrer=''){
  const visit=await visitPayload(href,referrer);
  return visit.channel===undefined?null:visit.channel;
}

test('X UTM campaigns are observed without sending arbitrary URL data',async()=>{
  assert.equal(await classifyArrival('https://veripsa.com/articles/test/?utm_source=x&utm_medium=social'), 'x');
  assert.equal(await classifyArrival('https://veripsa.com/?utm_source=twitter'), 'x');
  assert.equal(await classifyArrival('https://veripsa.com/?src=x-launch-20261102-v3'), 'x');
});

test('shared photo versions preserve article identity and coarse X attribution',async()=>{
  const visit=await visitPayload('https://veripsa.com/articles/photo-story/?utm_source=x&utm_medium=social&image=896c6fd6f728');
  assert.equal(visit.article,'photo-story');
  assert.equal(visit.pageKind,'article');
  assert.equal(visit.channel,'x');
  assert.equal(JSON.stringify(visit).includes('896c6fd6f728'),false);
});

test('common social and search referrers map to coarse categories',async()=>{
  assert.equal(await classifyArrival('https://veripsa.com/','https://t.co/jk3d0Q'), 'x');
  assert.equal(await classifyArrival('https://veripsa.com/','https://www.google.co.jp/search?q=private'), 'search');
  assert.equal(await classifyArrival('https://veripsa.com/','https://www.bing.com/search?q=private'), 'search');
  assert.equal(await classifyArrival('https://veripsa.com/','https://social.example.com/some-user'), 'external');
});

test('missing referrer is unattributed, not asserted to be a direct user',async()=>{
  assert.equal(await classifyArrival('https://veripsa.com/'), 'unattributed');
});

test('internal movement is not misleadingly counted as a new arrival',async()=>{
  assert.equal(await classifyArrival('https://veripsa.com/articles/test/','https://veripsa.com/archive/'), null);
});

test('unrecognised campaign markers cannot invent categories',async()=>{
  assert.equal(await classifyArrival('https://veripsa.com/?utm_source=unspecified','https://veripsa.com/'), null);
  assert.equal(await classifyArrival('https://veripsa.com/?src=unrelated','https://veripsa.com/'), null);
  assert.equal(await classifyArrival('https://veripsa.com/?src=x-launch-missing-version','https://veripsa.com/'), null);
});

test('article-to-article navigation uses only same-origin safe source slugs',async()=>{
  const v=await visitPayload(
    'https://veripsa.com/articles/target-story/',
    'https://veripsa.com/articles/original-story/?private=secret#reader-comments'
  );
  assert.equal(v.fromArticle,'original-story');
  assert.equal(v.article,'target-story');
  assert.equal(v.channel,undefined,'internal movement must not count as a new incoming channel');
  assert.ok(!JSON.stringify(v).includes('private=secret'));
  assert.ok(!JSON.stringify(v).includes('reader-comments'));
});

test('external, same-story and non-article navigation never imply continuation',async()=>{
  for(const [url,referrer] of [
    ['https://veripsa.com/articles/target-story/','https://social.example.com/articles/original-story/'],
    ['https://veripsa.com/articles/target-story/','https://veripsa.com/articles/target-story/'],
    ['https://veripsa.com/articles/target-story/','https://veripsa.com/archive/'],
    ['https://veripsa.com/','https://veripsa.com/articles/original-story/'],
    ['https://veripsa.com/articles/target-story/','https://veripsa.com/articles/malicious--slug/'],
    ['https://veripsa.com/articles/target-story/','']
  ]){
    const v=await visitPayload(url,referrer);
    assert.equal(v.fromArticle,undefined,referrer+' should not count');
  }
});
