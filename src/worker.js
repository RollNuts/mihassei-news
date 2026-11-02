import { DurableObject } from "cloudflare:workers";
export { ReaderComments } from "./comments.js";

// True, browser-level counters. No IP logging, cookies, tracking pixels or external APIs.
const attributionChannels = ["x","search","external","unattributed"];
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
function validArticle(slug) {
  return typeof slug === "string" && slug.length <= 100 && slugPattern.test(slug);
}
function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers:{
      "content-type":"application/json; charset=utf-8",
      "cache-control":"no-store",
      "x-content-type-options":"nosniff"
    }
  });
}

export class NewsroomStats extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql=ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS events (kind TEXT NOT NULL, article TEXT NOT NULL, visitor TEXT NOT NULL, PRIMARY KEY(kind,article,visitor)) WITHOUT ROWID;");
    this.sql.exec("CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);");
  }
  count(key) {
    return this.sql.exec("SELECT value FROM counters WHERE key=?", key).toArray()[0]?.value ?? 0;
  }
  increment(key, delta) {
    this.sql.exec("INSERT INTO counters(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=MAX(0,value+excluded.value)",key,delta);
  }
  insertOnce(kind, article, visitor) {
    this.sql.exec("INSERT OR IGNORE INTO events(kind,article,visitor) VALUES (?,?,?)",kind,article,visitor);
    return this.sql.exec("SELECT changes() AS n").one().n > 0;
  }
  async fetch(req) {
    const url=new URL(req.url);
    if(req.method==="GET") {
      const details={};
      const articles=(url.searchParams.get("articles")||"").split(",").filter(validArticle).slice(0,20);
      for (const slug of new Set(articles)) {
        details[slug]={likes:this.count("likes:"+slug),readers:this.count("readers:"+slug),shareClicks:this.count("shares:"+slug)};
      }
      const channels={};
      for(const source of attributionChannels)channels[source]=this.count("channel:"+source);
      return json({version:1,unit:"anonymous-browser",visitors:this.count("visitors"),articles:details,
        channels,channelScope:"distinct-browser-per-observed-channel-since-activation"});
    }
    if(req.method!=="POST")return json({error:"method not allowed"},405);
    if(Number(req.headers.get("content-length")||0)>512)return json({error:"payload too large"},413);
    let data;
    try{data=await req.json();}catch(_){return json({error:"invalid payload"},400);}
    if(!data || JSON.stringify(data).length>512 || !idPattern.test(data.clientId||""))return json({error:"invalid client"},400);
    if(!["visit","like","share"].includes(data.action))return json({error:"invalid action"},400);
    const article=data.article||"";
    if(article && !validArticle(article))return json({error:"invalid article"},400);
    if(data.action!=="visit" && !article)return json({error:"article required"},400);
    const channel=data.action==="visit" ? (data.channel??"") : "";
    if(channel!=="" && !attributionChannels.includes(channel))
      return json({error:"invalid channel"},400);
    const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(data.clientId));
    const visitor=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
    let liked=null;
    this.ctx.storage.transactionSync(()=>{
      if(data.action==="visit") {
        if(this.insertOnce("site","",visitor))this.increment("visitors",1);
        if(article && this.insertOnce("reader",article,visitor))this.increment("readers:"+article,1);
        // Record at most one visit per hashed browser *per coarse channel*.
        // No complete referral URLs, full UTM strings, search terms, or IPs are saved.
        if(channel && this.insertOnce("channel",channel,visitor))
          this.increment("channel:"+channel,1);
      } else if (data.action==="like") {
        const prior=this.sql.exec("SELECT 1 FROM events WHERE kind=? AND article=? AND visitor=?","like",article,visitor).toArray().length>0;
        if(prior) {
          this.sql.exec("DELETE FROM events WHERE kind=? AND article=? AND visitor=?","like",article,visitor);
          this.increment("likes:"+article,-1);
          liked=false;
        } else {
          this.insertOnce("like",article,visitor);
          this.increment("likes:"+article,1);
          liked=true;
        }
      } else if(data.action==="share") {
        // Counts intent-clicks only, not confirmed X publications.
        if(this.insertOnce("share",article,visitor))this.increment("shares:"+article,1);
      }
    });
    return json({version:1,unit:"anonymous-browser",visitors:this.count("visitors"),article,
      readers:article?this.count("readers:"+article):undefined,
      likes:article?this.count("likes:"+article):undefined,
      shareClicks:article?this.count("shares:"+article):undefined,liked});
  }
}

export default {
  async fetch(req, env) {
    const url=new URL(req.url);
    const isComments=["/api/comments","/api/comments/summary","/api/comments/admin"].includes(url.pathname);
    if(url.pathname!=="/api/engagement" && !isComments)return env.ASSETS.fetch(req);
    if(req.method==="POST") {
      if(!req.headers.get("origin") || req.headers.get("origin")!==url.origin)return json({error:"same origin only"},403);
      if(!(req.headers.get("content-type")||"").startsWith("application/json"))return json({error:"JSON required"},415);
    }
    try {
      if(isComments){
        // Ignore requests to comment on non-existent pages.
        if(req.method==="POST" && url.pathname==="/api/comments"){
          const payload=await req.clone().json().catch(()=>null);
          if(payload?.action==="post" && validArticle(payload.article)){
            const page=new URL("/articles/"+payload.article+"/",url);
            const exists=await env.ASSETS.fetch(new Request(page.toString()));
            if(!exists.ok)return json({error:"記事が見つかりません"},404);
          }
        }
        const comments=env.READER_COMMENTS.get(env.READER_COMMENTS.idFromName("mihassei-comments-main-v1"));
        return await comments.fetch(req);
      }
      const stub=env.NEWSROOM_STATS.get(env.NEWSROOM_STATS.idFromName("mihassei-main-v1"));
      return await stub.fetch(req);
    } catch(_) {
      return json({error:isComments?"comments unavailable":"engagement unavailable"},503);
    }
  }
};
