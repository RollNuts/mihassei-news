'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','assets','engagement.js'),'utf8');
const original='/generated/story.jpg?v=123456789abc', fallback='/images/story.svg';

function imageFixture({complete=false,naturalWidth=0,dynamic=false}={}){
  const attrs={src:original,alt:'創作ニュースの再現イメージ','data-fallback':fallback,
    'data-fallback-alt':'創作記事のイラスト','data-generated-scene':'true'};
  const caption={textContent:'AI生成の写真'};
  const classes=new Set(['story-image-link--generated']);
  const link={classList:{contains:c=>classes.has(c),remove:c=>classes.delete(c),add:c=>classes.add(c)}};
  let visible=true;
  const image={complete,naturalWidth,isConnected:true,onerror:null,
    getAttribute:n=>attrs[n]??null,
    removeAttribute:n=>{delete attrs[n];},
    setAttribute:(n,v)=>{attrs[n]=v;},
    getClientRects:()=>visible?[{}]:[],
    closest:n=>n==='figure'?{querySelector:()=>caption}:link,
    get src(){return attrs.src;},set src(v){attrs.src=v;},
  };
  const events={},probes=[],timers=new Map();
  let timerId=0;
  const listen=(type,fn)=>(events[type]??=[]).push(fn);
  const document={visibilityState:'visible',querySelectorAll:()=>dynamic?[]:[image],addEventListener:listen};
  const navigator={onLine:true};
  const window={addEventListener:listen,
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
    Image:class{
      constructor(){this.naturalWidth=0;probes.push(this);}
      removeAttribute(name){if(name==='src')this.src=undefined;}
    }};
  const api=vm.runInNewContext(source.split('// Keep the genuine edition label current')[0]+
    'return {watchImage};})();',{document,window,navigator});
  if(dynamic)api.watchImage(image);
  return {image,attrs,caption,classes,probes,timers,document,navigator,
    event:(name,event={})=>(events[name]||[]).forEach(fn=>fn(event)),
    visible:value=>{visible=value;},
    fail:()=>image.onerror?.(),
    loaded:()=>{const probe=probes.at(-1);probe.naturalWidth=1200;probe.onload();},
    rejected:()=>probes.at(-1).onerror(),
    timeout:()=>[...timers.values()].forEach(fn=>fn()),
  };
}

test('already-failed cached photo switches immediately without a fallback error loop',()=>{
  const ui=imageFixture({complete:true});
  assert.equal(ui.attrs.src,fallback);
  assert.equal(ui.attrs['data-fallback'],undefined);
  assert.equal(ui.attrs['data-generated-scene'],undefined);
  assert.equal(ui.attrs.alt,'創作記事のイラスト');
  assert.equal(ui.caption.textContent,'イメージ画像');
  assert.equal(ui.classes.has('story-image-link--generated'),false);
  assert.equal(ui.image.onerror,null);
  ui.fail();
  assert.equal(ui.probes.length,0);
});

for(const dynamic of [false,true])test(`${dynamic?'dynamic card':'initial photo'} recovers after a transient error without hiding its fallback`,()=>{
  const ui=imageFixture({dynamic});
  ui.fail();
  ui.event('online');
  assert.equal(ui.probes.length,1);
  assert.equal(ui.probes[0].src,original);
  assert.equal(ui.attrs.src,fallback,'keep the displayed fallback until the photograph loads');
  ui.event('visibilitychange');ui.event('pageshow',{persisted:true});
  assert.equal(ui.probes.length,1,'return events cannot start overlapping probes');
  ui.loaded();
  assert.equal(ui.attrs.src,original);
  assert.equal(ui.attrs.alt,'創作ニュースの再現イメージ');
  assert.equal(ui.attrs['data-generated-scene'],'true');
  assert.equal(ui.attrs['data-fallback'],fallback);
  assert.equal(ui.caption.textContent,'AI生成の写真');
  assert.equal(ui.classes.has('story-image-link--generated'),true);
  assert.equal(ui.timers.size,0);
  ui.event('online');ui.event('visibilitychange');
  assert.equal(ui.probes.length,1,'a recovered image is no longer a retry target');
});

test('permanent failures stop after two return-triggered requests',()=>{
  const ui=imageFixture();ui.fail();
  ui.event('online');ui.rejected();
  assert.equal(ui.attrs.src,fallback);
  ui.event('pageshow',{persisted:true});ui.rejected();
  for(let i=0;i<6;i++){ui.event('online');ui.event('visibilitychange');}
  assert.equal(ui.probes.length,2);
  assert.equal(ui.attrs.src,fallback);
  assert.equal(ui.image.onerror,null);
  assert.equal(ui.timers.size,0);
});

test('a stalled probe times out and still consumes the bounded retry allowance',()=>{
  const ui=imageFixture();ui.fail();
  for(let i=0;i<2;i++){
    ui.event('online');
    const lateLoad=ui.probes.at(-1).onload;
    ui.timeout();
    ui.probes.at(-1).naturalWidth=1200;lateLoad();
    assert.equal(ui.attrs.src,fallback,'late completion of an expired probe cannot replace the fallback');
  }
  ui.event('online');
  assert.equal(ui.probes.length,2);
  assert.equal(ui.timers.size,0);
});

test('hidden, offline and initial pageshow events do not consume retries',()=>{
  const ui=imageFixture();ui.fail();
  ui.event('pageshow',{persisted:false});
  ui.document.visibilityState='hidden';ui.event('online');
  ui.document.visibilityState='visible';ui.navigator.onLine=false;ui.event('visibilitychange');
  ui.navigator.onLine=true;ui.visible(false);ui.event('online');
  assert.equal(ui.probes.length,0);
  ui.visible(true);ui.event('pageshow',{persisted:true});
  assert.equal(ui.probes.length,1);
  ui.loaded();assert.equal(ui.attrs.src,original);
});

test('detached and replaced cards cannot be overwritten by an old probe',()=>{
  for(const change of [ui=>{ui.image.isConnected=false;},ui=>{ui.image.src='/generated/replacement.jpg';}]){
    const ui=imageFixture();ui.fail();ui.event('online');change(ui);
    const current=ui.attrs.src;
    ui.loaded();ui.event('online');
    assert.equal(ui.attrs.src,current);
    assert.equal(ui.probes.length,1);
  }
});

test('loaded and not-yet-loaded lazy images make no recovery requests',()=>{
  for(const options of [{complete:true,naturalWidth:1200},{complete:false,naturalWidth:0}]){
    const ui=imageFixture(options);
    ui.event('online');ui.event('visibilitychange');ui.event('pageshow',{persisted:true});
    assert.equal(ui.attrs.src,original);
    assert.equal(ui.attrs.alt,'創作ニュースの再現イメージ');
    assert.equal(ui.caption.textContent,'AI生成の写真');
    assert.equal(ui.attrs['data-generated-scene'],'true');
    assert.equal(ui.probes.length,0);
  }
});
