import { DurableObject } from "cloudflare:workers";
export { ReaderComments } from "./comments.js";

// True, browser-level counters. No IP logging, cookies, tracking pixels or external APIs.
const attributionChannels = ["x","search","external","unattributed"];
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
function currentJstDay(now = Date.now()) {
  const serial = Math.floor((now + JST_OFFSET_MS) / DAY_MS);
  return {serial, date: new Date(serial * DAY_MS).toISOString().slice(0,10)};
}
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
  // Retain only the most recent 14 JST days of daily deduplication records.
  // Cumulative counters, likes, votes and the existing attribution history
  // are untouched. Cleanup runs at most once per day on a genuine visit.
  pruneDailyCounts(serial) {
    if(this.count("maintenance:daily-pruned")>=serial)return;
    const oldest=new Date((serial-13)*DAY_MS).toISOString().slice(0,10);
    const threshold="day:"+oldest;
    this.sql.exec("DELETE FROM events WHERE kind LIKE 'day:%' AND kind < ?",threshold);
    this.sql.exec("DELETE FROM counters WHERE key LIKE 'day:%' AND key < ?",threshold);
    this.sql.exec("INSERT INTO counters(key,value) VALUES ('maintenance:daily-pruned',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",serial);
  }
  dailySnapshot(day) {
    const key="day:"+day.date+":";
    return {
      dateJst:day.date,
      visitors:this.count(key+"visitors"),
      articleReads:this.count(key+"articleReads")
    };
  }
  async fetch(req) {
    const url=new URL(req.url);
    const day=currentJstDay();
    if(req.method==="GET") {
      // Optional historical read for the completed-day archive. Never accept
      // arbitrary dates: daily deduplication is retained for 14 JST days.
      const requestedDay=url.searchParams.get("day");
      if(requestedDay!==null){
        const stamp=/^\d{4}-\d{2}-\d{2}$/.test(requestedDay)
          ? Date.parse(requestedDay+"T00:00:00Z") : NaN;
        const serial=stamp/DAY_MS;
        if(!Number.isInteger(serial) ||
           new Date(stamp).toISOString().slice(0,10)!==requestedDay ||
           serial<day.serial-13 || serial>day.serial)
          return json({error:"day outside available JST history"},400);
      }
      const details={};
      const articles=(url.searchParams.get("articles")||"").split(",").filter(validArticle).slice(0,20);
      for (const slug of new Set(articles)) {
        details[slug]={
          likes:this.count("likes:"+slug),
          readers:this.count("readers:"+slug),
          todayReaders:this.count("day:"+day.date+":reader:"+slug),
          shareClicks:this.count("shares:"+slug),
          // An approximate number of browser IDs that opened another story
          // after visiting this article. Not people, clicks, or a conversion rate.
          continuedReaders:this.count("continued:"+slug),
          // Distinct browser IDs arriving at this specific story from a
          // coarse X/search category since feature activation, not PV.
          arrivals:{
            x:this.count("arrival:x:"+slug),
            search:this.count("arrival:search:"+slug)
          },
          votes:{
            plausible:this.count("predictions:plausible:"+slug),
            unlikely:this.count("predictions:unlikely:"+slug)
          }
        };
      }
      const channels={};
      for(const source of attributionChannels)channels[source]=this.count("channel:"+source);
      const previousDate=new Date((day.serial-1)*DAY_MS).toISOString().slice(0,10);
      return json({version:1,unit:"anonymous-browser",visitors:this.count("visitors"),articles:details,
        today:this.dailySnapshot(day),
        yesterday:this.dailySnapshot({date:previousDate}),
        ...(requestedDay===null ? {} : {day:this.dailySnapshot({date:requestedDay})}),
        channels,channelScope:"distinct-browser-per-observed-channel-since-activation"});
    }
    if(req.method!=="POST")return json({error:"method not allowed"},405);
    if(Number(req.headers.get("content-length")||0)>512)return json({error:"payload too large"},413);
    let data;
    try{data=await req.json();}catch(_){return json({error:"invalid payload"},400);}
    if(!data || JSON.stringify(data).length>512 || !idPattern.test(data.clientId||""))return json({error:"invalid client"},400);
    if(!["visit","like","share","vote"].includes(data.action))return json({error:"invalid action"},400);
    if(data.action==="vote" && !["plausible","unlikely"].includes(data.choice))
      return json({error:"invalid prediction choice"},400);
    const article=data.article||"";
    if(article && !validArticle(article))return json({error:"invalid article"},400);
    if(data.action!=="visit" && !article)return json({error:"article required"},400);
    // Accept only a same-site article slug sent on normal article visits.
    // The client derives this from document.referrer without transmitting URLs.
    // Never accept arbitrary text or count a reload as article continuation.
    if(data.fromArticle!==undefined &&
       (data.action!=="visit" || !article || !validArticle(data.fromArticle) ||
        data.fromArticle===article))
      return json({error:"invalid previous article"},400);
    const previousArticle=data.action==="visit" ? (data.fromArticle||"") : "";
    const channel=data.action==="visit" ? (data.channel??"") : "";
    if(channel!=="" && !attributionChannels.includes(channel))
      return json({error:"invalid channel"},400);
    const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(data.clientId));
    const visitor=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
    let liked=null,selectedVote=null,alreadyVoted=false;
    this.ctx.storage.transactionSync(()=>{
      if(data.action==="visit") {
        this.pruneDailyCounts(day.serial);
        const dayPrefix="day:"+day.date+":";
        // Real daily browser activity: a reload does not inflate the numbers.
        // Article reads count distinct (browser, article) pairs per JST date,
        // not total page views or unique people.
        if(this.insertOnce(dayPrefix+"site","",visitor))
          this.increment(dayPrefix+"visitors",1);
        if(article && this.insertOnce(dayPrefix+"reader",article,visitor)){
          this.increment(dayPrefix+"articleReads",1);
          this.increment(dayPrefix+"reader:"+article,1);
        }
        if(this.insertOnce("site","",visitor))this.increment("visitors",1);
        if(article && this.insertOnce("reader",article,visitor))this.increment("readers:"+article,1);
        // Count one browser once per source article when it arrives at a
        // DIFFERENT article. No destination pair or full referrer is stored.
        if(previousArticle && this.insertOnce("continued",previousArticle,visitor))
          this.increment("continued:"+previousArticle,1);
        // Record at most one visit per hashed browser *per coarse channel*.
        // No complete referral URLs, full UTM strings, search terms, or IPs are saved.
        if(channel && this.insertOnce("channel",channel,visitor))
          this.increment("channel:"+channel,1);
        // Different arrival sources may overlap for a single browser/article.
        // Never persist a URL, UTM value, referrer hostname or search query.
        if(article && (channel==="x" || channel==="search") &&
           this.insertOnce("arrival:"+channel,article,visitor))
          this.increment("arrival:"+channel+":"+article,1);
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
      } else if(data.action==="vote") {
        // Immutable one-vote-per-browser/article: both options are checked
        // atomically; repeated POSTs never add phantom votes.
        const previous=this.sql.exec(
          "SELECT kind FROM events WHERE kind IN (?,?) AND article=? AND visitor=? LIMIT 1",
          "prediction:plausible","prediction:unlikely",article,visitor
        ).toArray()[0];
        if(previous) {
          selectedVote=previous.kind.slice("prediction:".length);
          alreadyVoted=true;
        } else {
          this.insertOnce("prediction:"+data.choice,article,visitor);
          this.increment("predictions:"+data.choice+":"+article,1);
          selectedVote=data.choice;
        }
      }
    });
    return json({version:1,unit:"anonymous-browser",visitors:this.count("visitors"),article,
      today:this.dailySnapshot(day),
      readers:article?this.count("readers:"+article):undefined,
      likes:article?this.count("likes:"+article):undefined,
      shareClicks:article?this.count("shares:"+article):undefined,
      continuedReaders:article?this.count("continued:"+article):undefined,
      arrivals:article?{
        x:this.count("arrival:x:"+article),
        search:this.count("arrival:search:"+article)
      }:undefined,
      liked,vote:selectedVote,alreadyVoted,
      votes:article?{
        plausible:this.count("predictions:plausible:"+article),
        unlikely:this.count("predictions:unlikely:"+article)
      }:undefined});
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
