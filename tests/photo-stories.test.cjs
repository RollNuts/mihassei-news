'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../assets/photo-stories.js'),'utf8');
const japanese={name:'Device Japanese',lang:'ja-JP',localService:true,default:true};
const fixture=(id='saya-one')=>({id,modelId:'nagino-saya',name:'凪野さや',
  text:'私の取材メモに、寄り道をひとつ足しました。じゃ、行ってきます。',
  publishedAt:'2020-11-15T00:40:00Z',dateLabel:'2020年11月15日',settingLabel:'舞台：2031年11月',
  image:'/photo/weekly/autumn-saya-001/01.jpg?v=7d6077a0f2ef',
  sourceUrl:'/photo/weekly/autumn-saya-001/#photo-01',alt:'秋の小道の凪野さや。',
  width:800,height:1200,voice:{rate:0.98,pitch:1.02}});

function element(){
  return {listeners:{},dataset:{},textContent:'',disabled:false,hidden:false,focused:0,
    addEventListener(name,fn){this.listeners[name]=fn;},focus(){this.focused++;}};
}
function page(options={}){
  const fields=Object.fromEntries(['name','image','text','date','setting','source','audio','next','close','status']
    .map(name=>[name,element()]));
  fields.audio.disabled=true;
  const dialog=element();dialog.open=false;
  dialog.querySelector=selector=>fields[selector.match(/^\[data-story-(.+)\]$/)?.[1]]||null;
  dialog.showModal=()=>{dialog.open=true;};
  dialog.close=()=>{dialog.open=false;dialog.listeners.close?.();};
  const data={textContent:JSON.stringify(options.stories||[fixture()])};
  const link=element();link.dataset.storyOpen='nagino-saya';
  const root={querySelector:selector=>({'[data-photo-story-data]':data,'[data-story-dialog]':dialog})[selector],
    querySelectorAll:selector=>selector==='[data-story-open]'?[link]:[]};
  const spoken=[],timers=new Map(),windowEvents={};let nextTimer=0;
  const synth={voices:options.voices??[japanese],cancellations:0,listeners:{},
    getVoices(){if(options.listError)throw new Error('unavailable');return this.voices;},
    speak(utterance){if(options.speakError)throw new Error('blocked');spoken.push(utterance);},
    cancel(){this.cancellations++;},addEventListener(event,fn){this.listeners[event]=fn;}};
  const window={speechSynthesis:options.unsupported?undefined:synth,
    SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},
    setTimeout(fn){timers.set(++nextTimer,fn);return nextTimer;},clearTimeout(id){timers.delete(id);},
    addEventListener(event,fn){windowEvents[event]=fn;}};
  vm.runInNewContext(source,{document:{querySelectorAll:()=>[root]},window,Date,Intl});
  const open=()=>{let prevented=false;link.listeners.click?.({preventDefault(){prevented=true;}});return prevented;};
  return {fields,dialog,link,synth,spoken,timers,windowEvents,open};
}

test('opening a photo story and voice-list changes never autoplay',()=>{
  const p=page();
  assert.equal(p.spoken.length,0);
  assert.equal(p.open(),true);
  assert.equal(p.dialog.open,true);
  assert.equal(p.fields.image.src,fixture().image);
  assert.equal(p.fields.source.href,fixture().sourceUrl);
  assert.equal(p.fields.text.textContent,fixture().text);
  assert.equal(p.fields.audio.disabled,false);
  p.synth.listeners.voiceschanged();
  assert.equal(p.spoken.length,0);
  assert.match(p.fields.status.textContent,/端末により異なります/);
});

test('audio action uses only an explicit local Japanese voice and reports actual playback events',()=>{
  const remote={name:'Remote Japanese',lang:'ja-JP',localService:false,default:true};
  const english={name:'Local English',lang:'en-US',localService:true,default:true};
  const p=page({voices:[remote,english,japanese]});p.open();
  p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,1);
  const utterance=p.spoken[0];
  assert.equal(utterance.voice,japanese);
  assert.equal(utterance.lang,'ja-JP');
  assert.equal(utterance.rate,0.98);assert.equal(utterance.pitch,1.02);
  assert.equal(utterance.text,fixture().text);
  assert.match(p.fields.status.textContent,/準備/);
  assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
  utterance.onstart();assert.match(p.fields.status.textContent,/再生中/);
  utterance.onend();assert.match(p.fields.status.textContent,/再生しました/);
  assert.equal(p.fields.audio.textContent,'音声を聴く');
  assert.equal(p.timers.size,0);
});

test('unsupported, empty local Japanese voice list, remote-only and list failure remain distinct',()=>{
  for(const options of [{unsupported:true},{voices:[]},
    {voices:[{name:'Remote',lang:'ja-JP',localService:false}]},
    {voices:[{name:'Unknown service',lang:'ja-JP',localService:'true'}]},
    {voices:[{name:'English',lang:'en-US',localService:true}]},{listError:true}]){
    const p=page(options);p.open();
    assert.equal(p.fields.audio.disabled,true);
    const expected=options.unsupported?/対応していません/:options.listError?/取得できません/:/見つかりません/;
    assert.match(p.fields.status.textContent,expected);
    assert.equal(p.spoken.length,0);
    assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
  }
  const later=page({voices:[]});later.open();
  later.synth.voices=[japanese];later.synth.listeners.voiceschanged();
  assert.equal(later.fields.audio.disabled,false);
  assert.equal(later.spoken.length,0);
});

test('stop, close, Escape, next story and page exit cancel audio with no overlap or automatic next playback',()=>{
  for(const action of ['stop','close','escape','next','pagehide']){
    const p=page({stories:[fixture(),fixture('saya-two')]});p.open();
    p.fields.audio.listeners.click();const utterance=p.spoken[0];utterance.onstart();
    const before=p.synth.cancellations;
    if(action==='stop')p.fields.audio.listeners.click();
    if(action==='close')p.fields.close.listeners.click();
    if(action==='escape'){p.dialog.listeners.cancel();p.dialog.close();}
    if(action==='next')p.fields.next.listeners.click();
    if(action==='pagehide')p.windowEvents.pagehide();
    assert.equal(p.synth.cancellations,before+1,action);
    assert.equal(p.spoken.length,1,action);
    const status=p.fields.status.textContent;
    utterance.onerror({error:'canceled'});utterance.onend();
    assert.equal(p.fields.status.textContent,status,'canceled events cannot report a successful play');
    assert.equal(p.timers.size,0);
    if(action==='close' || action==='escape')assert.equal(p.link.focused,1);
  }
  const p=page();p.open();p.fields.audio.listeners.click();
  p.fields.audio.listeners.click();p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,2);
  assert.equal(p.synth.cancellations,3);
});

test('engine errors, rejected starts and silent starts never appear as a successful play',()=>{
  for(const mode of ['error','throw','timeout','end-before-start']){
    const p=page({speakError:mode==='throw'});p.open();p.fields.audio.listeners.click();
    if(mode==='error')p.spoken[0].onerror({error:'synthesis-failed'});
    if(mode==='timeout')[...p.timers.values()][0]();
    if(mode==='end-before-start')p.spoken[0].onend();
    assert.match(p.fields.status.textContent,/できません/);
    assert.doesNotMatch(p.fields.status.textContent,/再生しました/);
    assert.equal(p.fields.audio.textContent,'音声を聴く');
  }
});

test('voice loss after opening refuses playback instead of choosing a remote fallback',()=>{
  const p=page();p.open();p.synth.voices=[];p.fields.audio.listeners.click();
  assert.equal(p.spoken.length,0);
  assert.equal(p.fields.audio.disabled,true);
  assert.match(p.fields.status.textContent,/見つかりません/);
});

test('unsafe or unpublished payloads leave the original photo link intact',()=>{
  for(const mutate of [story=>{story.image='https://remote.test/photo.jpg';},
    story=>{story.sourceUrl='javascript:alert(1)';},story=>{story.text='<img src=x onerror=alert(1)>';},
    story=>{story.voice.rate=2;},story=>{story.publishedAt='2999-01-01T00:00:00Z';},
    story=>{story.width='800';}]){
    const story=fixture();mutate(story);const p=page({stories:[story]});
    assert.equal(p.open(),false);
    assert.equal(p.dialog.open,false);
    assert.equal(p.spoken.length,0);
  }
});
