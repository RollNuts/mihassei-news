import { DurableObject } from "cloudflare:workers";
export { ReaderComments } from "./comments.js";

// Browser-level engagement and page displays, without IP logging or external APIs.
const attributionChannels = ["x","search","external","unattributed"];
const pageKinds = ["home","article","archive","serial","other"];
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;
function currentJstDay(now = Date.now()) {
  const serial = Math.floor((now + JST_OFFSET_MS) / DAY_MS);
  return {serial, date: new Date(serial * DAY_MS).toISOString().slice(0,10)};
}
function currentJstHour(now = Date.now()) {
  const shifted=new Date(now+JST_OFFSET_MS).toISOString();
  return {date:shifted.slice(0,10),hour:shifted.slice(11,13),stamp:shifted.slice(0,13)};
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
    this.sql.exec("CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;");
    const counterSchema=this.sql.exec(
      "SELECT sql FROM sqlite_master WHERE type='table' AND name='counters'"
    ).toArray()[0]?.sql||"";
    if(counterSchema && !/WITHOUT\s+ROWID/i.test(counterSchema)){
      // Older deployments used a rowid table plus a PRIMARY KEY index. Rewrite
      // it once as WITHOUT ROWID so hot counter updates touch only one stored row.
      ctx.storage.transactionSync(()=>{
        this.sql.exec("CREATE TABLE counters_compact (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;");
        this.sql.exec("INSERT INTO counters_compact(key,value) SELECT key,value FROM counters;");
        this.sql.exec("DROP TABLE counters;");
        this.sql.exec("ALTER TABLE counters_compact RENAME TO counters;");
      });
    }
    // Display/session IDs already use their PRIMARY KEY as table storage.
    // Secondary day indexes would add an index-row write on every insert and
    // again on cleanup. Daily pruning can afford a bounded 14-day scan instead.
    this.sql.exec("CREATE TABLE IF NOT EXISTS pageview_events (id TEXT PRIMARY KEY, day TEXT NOT NULL) WITHOUT ROWID;");
    this.sql.exec("DROP INDEX IF EXISTS pageview_events_day;");
    // Session IDs are random client-generated UUIDs with a 30-minute inactivity
    // timeout. They are retained only for retry deduplication, never as identity.
    this.sql.exec("CREATE TABLE IF NOT EXISTS session_events (id TEXT PRIMARY KEY, day TEXT NOT NULL) WITHOUT ROWID;");
    this.sql.exec("DROP INDEX IF EXISTS session_events_day;");
  }
  count(key) {
    return this.sql.exec("SELECT value FROM counters WHERE key=?", key).toArray()[0]?.value ?? 0;
  }
  counts(keys) {
    const unique=[...new Set(keys)];
    const result=new Map();
    // Cloudflare SQLite Durable Objects allow at most 100 bound parameters
    // per statement. Stay below that limit while still collapsing hundreds of
    // individual counter reads into a few bounded queries.
    for(let offset=0;offset<unique.length;offset+=90){
      const batch=unique.slice(offset,offset+90);
      const placeholders=batch.map(()=>"?").join(",");
      for(const row of this.sql.exec(
        "SELECT key,value FROM counters WHERE key IN ("+placeholders+")",...batch
      ).toArray())result.set(row.key,row.value);
    }
    return result;
  }
  increment(key, delta) {
    this.sql.exec("INSERT INTO counters(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=MAX(0,value+excluded.value)",key,delta);
  }
  rememberEarliest(key, value) {
    // A normal later event must not rewrite an already-initialized timestamp.
    // The WHERE keeps rowsWritten at zero on the common conflict path while
    // still correcting the value if an earlier request finishes out of order.
    this.sql.exec(
      "INSERT INTO counters(key,value) VALUES (?,?) "+
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value "+
      "WHERE excluded.value < counters.value",
      key,value
    );
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
    this.sql.exec("DELETE FROM counters WHERE key LIKE 'hour:%' AND key < ?","hour:"+oldest);
    this.sql.exec("DELETE FROM pageview_events WHERE day < ?",oldest);
    this.sql.exec("DELETE FROM session_events WHERE day < ?",oldest);
    this.sql.exec("INSERT INTO counters(key,value) VALUES ('maintenance:daily-pruned',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",serial);
  }
  dayCoverage(day, currentDay, startedAt) {
    const startDay=startedAt ? currentJstDay(startedAt) : null;
    if(!startDay || day.date<startDay.date)return "unmeasured";
    if(day.date===currentDay.date)return "in-progress";
    if(day.date===startDay.date && startedAt>startDay.serial*DAY_MS-JST_OFFSET_MS)return "partial";
    return "complete";
  }
  dailySnapshot(day, currentDay, pageViewStartedAt, sessionStartedAt) {
    const key="day:"+day.date+":";
    const pvCoverage=this.dayCoverage(day,currentDay,pageViewStartedAt);
    const sessionCoverage=this.dayCoverage(day,currentDay,sessionStartedAt);
    return {
      dateJst:day.date,
      visitors:this.count(key+"visitors"),
      articleReads:this.count(key+"articleReads"),
      pageViews:pvCoverage==="unmeasured" ? null : this.count(key+"pageViews"),
      pageViewsCoverage:pvCoverage,
      sessions:sessionCoverage==="unmeasured" ? null : this.count(key+"sessions"),
      sessionsCoverage:sessionCoverage
    };
  }
  last7DaysPageViews(day, startedAt, article="") {
    if(!startedAt)return null;
    const firstSerial=day.serial-6;
    const keys=Array.from({length:7},(_,i)=>
      "day:"+new Date((firstSerial+i)*DAY_MS).toISOString().slice(0,10)+
      ":pageViews"+(article ? ":"+article : ""));
    return this.sql.exec(
      "SELECT COALESCE(SUM(value),0) AS n FROM counters WHERE key IN (?,?,?,?,?,?,?)",...keys
    ).one().n;
  }
  pageViewSummary(day, startedAt) {
    const firstSerial=day.serial-6;
    const firstDate=new Date(firstSerial*DAY_MS).toISOString().slice(0,10);
    return {
      pageViews:startedAt ? this.count("pageViews") : null,
      pageViewTracking:{
        startedAtUtc:startedAt ? new Date(startedAt).toISOString() : null,
        timezone:"Asia/Tokyo",
        unit:"page-view"
      },
      last7Days:{
        startDateJst:firstDate,
        endDateJst:day.date,
        pageViews:this.last7DaysPageViews(day,startedAt),
        measuredDays:startedAt ? Math.max(0,Math.min(7,day.serial-currentJstDay(startedAt).serial+1)) : 0,
        // This window includes today through now, not seven completed days.
        // A partial first day is not a fully covered seven-date window.
        complete:Boolean(startedAt && startedAt<=firstSerial*DAY_MS-JST_OFFSET_MS)
      }
    };
  }
  sessionSummary(startedAt) {
    return {
      sessions:startedAt ? this.count("sessions") : null,
      sessionTracking:{
        startedAtUtc:startedAt ? new Date(startedAt).toISOString() : null,
        timezone:"Asia/Tokyo",
        unit:"session",
        inactivityTimeoutMinutes:30
      }
    };
  }
  hourCoverage(stamp, currentHour, startedAt) {
    if(stamp>currentHour.stamp)return "future";
    const shiftedStart=Date.parse(stamp+":00:00Z");
    const actualStart=shiftedStart-JST_OFFSET_MS;
    const actualEnd=actualStart+HOUR_MS;
    if(!startedAt || actualEnd<=startedAt)return "unmeasured";
    if(actualStart<startedAt && startedAt<actualEnd)return "partial";
    if(stamp===currentHour.stamp)return "in-progress";
    return "complete";
  }
  hourlyDaySnapshot(date, currentHour, startedAt) {
    const result=[];
    for(let h=0;h<24;h++){
      const hour=String(h).padStart(2,"0");
      const stamp=date+"T"+hour;
      const coverage=this.hourCoverage(stamp,currentHour,startedAt);
      const value=coverage==="unmeasured" || coverage==="future"
        ? null : this.count("hour:"+date+":"+hour+":pageViews");
      result.push({hourJst:stamp+":00",pageViews:value,pageViewsCoverage:coverage});
    }
    return result;
  }
  topArticleRows(prefix, limit=10) {
    return this.sql.exec(
      "SELECT substr(key,?) AS article,value AS pageViews FROM counters WHERE key LIKE ? AND value>0 ORDER BY value DESC,key ASC LIMIT ?",
      prefix.length+1,prefix+"%",limit
    ).toArray().filter(row=>validArticle(row.article) && Number.isInteger(row.pageViews) && row.pageViews>0);
  }
  topArticleRowsForWeek(day, limit=10) {
    const patterns=Array.from({length:7},(_,i)=>{
      const date=new Date((day.serial-6+i)*DAY_MS).toISOString().slice(0,10);
      return "day:"+date+":pageViews:%";
    });
    const where=patterns.map(()=>"key LIKE ?").join(" OR ");
    // Article-counter prefixes are fixed-width ASCII:
    // "day:YYYY-MM-DD:pageViews:" is 25 characters, so the slug starts at 26.
    return this.sql.exec(
      "SELECT substr(key,26) AS article,SUM(value) AS pageViews FROM counters "+
      "WHERE ("+where+") AND value>0 GROUP BY article "+
      "ORDER BY pageViews DESC,article ASC LIMIT ?",
      ...patterns,limit
    ).toArray().filter(row=>validArticle(row.article) &&
      Number.isInteger(row.pageViews) && row.pageViews>0);
  }
  hourlyDimensionTotals(date, marker, dimensions) {
    const totals=Object.fromEntries(dimensions.map(key=>[key,0]));
    const prefix="hour:"+date+":";
    const rows=this.sql.exec(
      "SELECT key,value FROM counters WHERE key LIKE ? AND value>0",
      prefix+"%:"+marker+":%"
    ).toArray();
    for(const row of rows){
      const dimension=row.key.slice(row.key.lastIndexOf(":")+1);
      if(Object.hasOwn(totals,dimension) && Number.isInteger(row.value) && row.value>0)
        totals[dimension]+=row.value;
    }
    return totals;
  }
  pageViewSourceSummary(day, currentHour, startedAt) {
    const today=startedAt
      ? this.hourlyDimensionTotals(day.date,"pageViewSource",attributionChannels)
      : Object.fromEntries(attributionChannels.map(source=>[source,null]));
    const currentHourCounts={};
    for(const source of attributionChannels)currentHourCounts[source]=startedAt ? this.count(
      "hour:"+currentHour.date+":"+currentHour.hour+":pageViewSource:"+source) : null;
    return {
      pageViewSourcesToday:today,
      pageViewSourcesCurrentHour:currentHourCounts,
      pageViewSourceTracking:{
        startedAtUtc:startedAt ? new Date(startedAt).toISOString() : null,
        timezone:"Asia/Tokyo",
        unit:"page-view",
        attribution:"session-acquisition-channel"
      }
    };
  }
  pageKindSummary(day, currentHour, startedAt) {
    const today=startedAt
      ? this.hourlyDimensionTotals(day.date,"pageViewKind",pageKinds)
      : Object.fromEntries(pageKinds.map(kind=>[kind,null]));
    const currentHourCounts={};
    for(const kind of pageKinds)currentHourCounts[kind]=startedAt ? this.count(
      "hour:"+currentHour.date+":"+currentHour.hour+":pageViewKind:"+kind) : null;
    return {
      pageViewKindsToday:today,
      pageViewKindsCurrentHour:currentHourCounts,
      pageViewKindTracking:{
        startedAtUtc:startedAt ? new Date(startedAt).toISOString() : null,
        timezone:"Asia/Tokyo",
        unit:"page-view",
        categories:pageKinds
      }
    };
  }
  publicSnapshot(url, day) {
    // Reader-facing pages need only the counters they actually render. This
    // compact shape is also returned by successful visit POSTs so a normal
    // page display does not need an immediate second API request.
    const articles=(url.searchParams.get("articles")||"").split(",").filter(validArticle).slice(0,20);
    const articleSet=[...new Set(articles)];
    const previousDate=new Date((day.serial-1)*DAY_MS).toISOString().slice(0,10);
    const firstSerial=day.serial-6;
    const dates=Array.from({length:7},(_,i)=>
      new Date((firstSerial+i)*DAY_MS).toISOString().slice(0,10));
    const keys=[
      "pageViews:startedAt","pageViews","visitors",
      "day:"+day.date+":visitors","day:"+day.date+":articleReads","day:"+day.date+":pageViews",
      "day:"+previousDate+":visitors","day:"+previousDate+":articleReads","day:"+previousDate+":pageViews",
      ...dates.map(date=>"day:"+date+":pageViews")
    ];
    for(const slug of articleSet)keys.push(
      "likes:"+slug,
      "readers:"+slug,
      "day:"+day.date+":reader:"+slug,
      "pageViews:"+slug,
      "day:"+day.date+":pageViews:"+slug,
      "predictions:plausible:"+slug,
      "predictions:unlikely:"+slug,
      ...dates.map(date=>"day:"+date+":pageViews:"+slug)
    );
    const values=this.counts(keys);
    const value=key=>values.get(key)??0;
    const publicStartedAt=value("pageViews:startedAt");
    const coverage=date=>this.dayCoverage({date},day,publicStartedAt);
    const publicDay=date=>{
      const pageViewsCoverage=coverage(date);
      return {
        dateJst:date,
        visitors:value("day:"+date+":visitors"),
        articleReads:value("day:"+date+":articleReads"),
        pageViews:pageViewsCoverage==="unmeasured" ? null : value("day:"+date+":pageViews"),
        pageViewsCoverage
      };
    };
    const publicArticles=Object.fromEntries(articleSet.map(slug=>[
      slug,{
        likes:value("likes:"+slug),
        readers:value("readers:"+slug),
        todayReaders:value("day:"+day.date+":reader:"+slug),
        pageViews:publicStartedAt ? value("pageViews:"+slug) : null,
        todayPageViews:publicStartedAt ? value("day:"+day.date+":pageViews:"+slug) : null,
        last7DaysPageViews:publicStartedAt
          ? dates.reduce((sum,date)=>sum+value("day:"+date+":pageViews:"+slug),0)
          : null,
        votes:{
          plausible:value("predictions:plausible:"+slug),
          unlikely:value("predictions:unlikely:"+slug)
        }
      }
    ]));
    let popularRows=null;
    if(url.searchParams.get("popular")==="1"){
      const rows=this.topArticleRowsForWeek(day,1000);
      const todayValues=this.counts(rows.map(row=>"day:"+day.date+":pageViews:"+row.article));
      popularRows=rows.map(row=>({
        ...row,
        todayPageViews:todayValues.get("day:"+day.date+":pageViews:"+row.article)??0
      }));
    }
    return {
      version:2,
      unit:"anonymous-browser",
      visitors:value("visitors"),
      articles:publicArticles,
      pageViews:publicStartedAt ? value("pageViews") : null,
      pageViewTracking:{
        startedAtUtc:publicStartedAt ? new Date(publicStartedAt).toISOString() : null,
        timezone:"Asia/Tokyo",
        unit:"page-view"
      },
      last7Days:{
        startDateJst:dates[0],
        endDateJst:day.date,
        pageViews:publicStartedAt
          ? dates.reduce((sum,date)=>sum+value("day:"+date+":pageViews"),0)
          : null,
        measuredDays:publicStartedAt
          ? Math.max(0,Math.min(7,day.serial-currentJstDay(publicStartedAt).serial+1))
          : 0,
        complete:Boolean(publicStartedAt && publicStartedAt<=firstSerial*DAY_MS-JST_OFFSET_MS)
      },
      today:publicDay(day.date),
      yesterday:publicDay(previousDate),
      ...(popularRows!==null ? {
        // Rank once inside the existing Durable Object instead of making the
        // browser fetch every published article just to discover the top few.
        topArticles:{last7Days:popularRows}
      } : {})
    };
  }
  async fetch(req) {
    const url=new URL(req.url);
    const now=Date.now();
    const day=currentJstDay(now);
    const hour=currentJstHour(now);
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
      const requestedHour=url.searchParams.get("hour");
      let selectedHour=null;
      if(requestedHour!==null){
        const match=/^(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3])$/.exec(requestedHour);
        const dayStamp=match ? Date.parse(match[1]+"T00:00:00Z") : NaN;
        const serial=dayStamp/DAY_MS;
        if(!match || !Number.isInteger(serial) ||
           new Date(dayStamp).toISOString().slice(0,10)!==match[1] ||
           serial<day.serial-13 || serial>day.serial ||
           requestedHour>hour.stamp)
          return json({error:"hour outside available JST history"},400);
        selectedHour={date:match[1],hour:match[2],stamp:requestedHour};
      }
      if(url.searchParams.get("view")==="public")
        return json(this.publicSnapshot(url,day));
      const articles=(url.searchParams.get("articles")||"").split(",").filter(validArticle).slice(0,20);
      const startedAt=this.count("pageViews:startedAt");
      const hourlyStartedAt=this.count("hourlyPageViews:startedAt");
      const sessionStartedAt=this.count("sessions:startedAt");
      const sourceStartedAt=this.count("pageViewSources:startedAt");
      const kindStartedAt=this.count("pageViewKinds:startedAt");
      const details={};
      for (const slug of new Set(articles)) {
        details[slug]={
          likes:this.count("likes:"+slug),
          readers:this.count("readers:"+slug),
          todayReaders:this.count("day:"+day.date+":reader:"+slug),
          pageViews:startedAt ? this.count("pageViews:"+slug) : null,
          todayPageViews:startedAt ? this.count("day:"+day.date+":pageViews:"+slug) : null,
          ...(requestedDay===null ? {} : {
            requestedDayPageViews:this.dayCoverage({date:requestedDay},day,startedAt)==="unmeasured"
              ? null : this.count("day:"+requestedDay+":pageViews:"+slug)
          }),
          // Same Japan-date window and coverage as the site's last7Days.
          // Repeated displays count; historical browser IDs never fill gaps.
          last7DaysPageViews:this.last7DaysPageViews(day,startedAt,slug),
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
      const topArticles={
        currentHour:hourlyStartedAt
          ? this.topArticleRows("hour:"+hour.date+":"+hour.hour+":pageViews:") : [],
        today:this.topArticleRows("day:"+day.date+":pageViews:"),
        last7Days:this.topArticleRowsForWeek(day),
        ...(selectedHour ? {
          selectedHour:hourlyStartedAt
            ? this.topArticleRows("hour:"+selectedHour.date+":"+selectedHour.hour+":pageViews:") : []
        } : {})
      };
      if(requestedDay!==null)
        topArticles.day=this.topArticleRows("day:"+requestedDay+":pageViews:");
      return json({version:2,unit:"anonymous-browser",visitors:this.count("visitors"),articles:details,
        ...this.pageViewSummary(day,startedAt),
        ...this.sessionSummary(sessionStartedAt),
        ...this.pageViewSourceSummary(day,hour,sourceStartedAt),
        ...this.pageKindSummary(day,hour,kindStartedAt),
        ...(requestedDay===null ? {} : {
          pageViewSourcesDay:this.dayCoverage({date:requestedDay},day,sourceStartedAt)==="unmeasured"
            ? null : this.hourlyDimensionTotals(requestedDay,"pageViewSource",attributionChannels)
        }),
        ...(requestedDay===null ? {} : {
          pageViewKindsDay:this.dayCoverage({date:requestedDay},day,kindStartedAt)==="unmeasured"
            ? null : this.hourlyDimensionTotals(requestedDay,"pageViewKind",pageKinds)
        }),
        ...(selectedHour===null ? {} : (()=>{
          const pvCoverage=this.hourCoverage(selectedHour.stamp,hour,hourlyStartedAt);
          const sourceCoverage=this.hourCoverage(selectedHour.stamp,hour,sourceStartedAt);
          return {
            selectedHour:{
              hourJst:selectedHour.stamp+":00",
              pageViews:pvCoverage==="unmeasured" || pvCoverage==="future"
                ? null : this.count("hour:"+selectedHour.date+":"+selectedHour.hour+":pageViews"),
              pageViewsCoverage:pvCoverage,
              pageViewSources:sourceCoverage==="unmeasured" || sourceCoverage==="future"
                ? null : Object.fromEntries(attributionChannels.map(source=>[
                    source,this.count("hour:"+selectedHour.date+":"+selectedHour.hour+":pageViewSource:"+source)
                  ])),
              pageViewSourcesCoverage:sourceCoverage,
              pageViewKinds:(()=>{
                const coverage=this.hourCoverage(selectedHour.stamp,hour,kindStartedAt);
                return coverage==="unmeasured" || coverage==="future"
                  ? null : Object.fromEntries(pageKinds.map(kind=>[
                      kind,this.count("hour:"+selectedHour.date+":"+selectedHour.hour+":pageViewKind:"+kind)
                    ]));
              })(),
              pageViewKindsCoverage:this.hourCoverage(selectedHour.stamp,hour,kindStartedAt)
            }
          };
        })()),
        hourly:{
          timezone:"Asia/Tokyo",
          startedAtUtc:hourlyStartedAt ? new Date(hourlyStartedAt).toISOString() : null,
          today:this.hourlyDaySnapshot(day.date,hour,hourlyStartedAt),
          yesterday:this.hourlyDaySnapshot(previousDate,hour,hourlyStartedAt),
          ...(requestedDay===null ? {} : {
            day:this.hourlyDaySnapshot(requestedDay,hour,hourlyStartedAt)
          })
        },
        topArticles,
        today:this.dailySnapshot(day,day,startedAt,sessionStartedAt),
        yesterday:this.dailySnapshot({date:previousDate},day,startedAt,sessionStartedAt),
        ...(requestedDay===null ? {} : {
          day:this.dailySnapshot({date:requestedDay},day,startedAt,sessionStartedAt)
        }),
        channels,channelScope:"distinct-browser-per-observed-channel-since-activation"});
    }
    if(req.method!=="POST")return json({error:"method not allowed"},405);
    if(Number(req.headers.get("content-length")||0)>512)return json({error:"payload too large"},413);
    let data;
    try{data=await req.json();}catch(_){return json({error:"invalid payload"},400);}
    if(!data || JSON.stringify(data).length>512 || !idPattern.test(data.clientId||""))return json({error:"invalid client"},400);
    if(!["visit","like","share","vote"].includes(data.action))return json({error:"invalid action"},400);
    if(data.viewId!==undefined &&
       (data.action!=="visit" || typeof data.viewId!=="string" || !idPattern.test(data.viewId)))
      return json({error:"invalid page view id"},400);
    const hasSession=data.sessionId!==undefined || data.sessionChannel!==undefined;
    if(hasSession && (data.action!=="visit" || typeof data.sessionId!=="string" ||
       !idPattern.test(data.sessionId) || !attributionChannels.includes(data.sessionChannel)))
      return json({error:"invalid session"},400);
    if(data.pageKind!==undefined &&
       (data.action!=="visit" || !pageKinds.includes(data.pageKind)))
      return json({error:"invalid page kind"},400);
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
    const viewHash=data.viewId===undefined ? null : await crypto.subtle.digest(
      "SHA-256",new TextEncoder().encode("page-view:"+data.viewId.toLowerCase())
    );
    const pageViewId=viewHash ? Array.from(new Uint8Array(viewHash),v=>v.toString(16).padStart(2,"0")).join("") : null;
    const sessionHash=data.sessionId===undefined ? null : await crypto.subtle.digest(
      "SHA-256",new TextEncoder().encode("session:"+data.sessionId.toLowerCase())
    );
    const sessionId=sessionHash ? Array.from(new Uint8Array(sessionHash),v=>v.toString(16).padStart(2,"0")).join("") : null;
    const sessionChannel=sessionId ? data.sessionChannel : "";
    const pageKind=data.action==="visit" ? (data.pageKind||"") : "";
    const purpose=(req.headers.get("sec-purpose")||"")+" "+(req.headers.get("purpose")||"");
    const automated=/Googlebot|bingbot|Baiduspider|YandexBot|DuckDuckBot|Twitterbot|facebookexternalhit|Slackbot|Discordbot|Applebot|HeadlessChrome|Playwright/i.test(req.headers.get("user-agent")||"");
    const ignoredVisit=data.action==="visit" && (/prefetch|prerender/i.test(purpose) || automated);
    let liked=null,selectedVote=null,alreadyVoted=false;
    this.ctx.storage.transactionSync(()=>{
      if(data.action==="visit" && !ignoredVisit) {
        this.pruneDailyCounts(day.serial);
        const dayPrefix="day:"+day.date+":";
        const hourPrefix="hour:"+hour.date+":"+hour.hour+":";
        // No reconstruction from old distinct readers. Only new display IDs
        // count, once per event; actual reloads receive different display IDs.
        if(pageViewId){
          this.sql.exec("INSERT OR IGNORE INTO pageview_events(id,day) VALUES (?,?)",pageViewId,day.date);
          // A retransmission is not a visit on the following day either.
          if(this.sql.exec("SELECT changes() AS n").one().n===0)return;
          // Request bodies and digests can finish out of order across midnight.
          // Keep the earliest accepted display's arrival, not whichever writes first.
          this.rememberEarliest("pageViews:startedAt",now);
          this.increment("pageViews",1);
          this.increment(dayPrefix+"pageViews",1);
          this.rememberEarliest("hourlyPageViews:startedAt",now);
          this.increment(hourPrefix+"pageViews",1);
          if(article){
            this.increment("pageViews:"+article,1);
            this.increment(dayPrefix+"pageViews:"+article,1);
            this.increment(hourPrefix+"pageViews:"+article,1);
          }
          if(pageKind){
            this.rememberEarliest("pageViewKinds:startedAt",now);
            this.increment(hourPrefix+"pageViewKind:"+pageKind,1);
          }
          // New clients carry the acquisition channel across their 30-minute
          // session so internal page views keep the landing source instead of
          // being mislabeled as direct/unattributed.
          if(sessionChannel){
            this.rememberEarliest("pageViewSources:startedAt",now);
            this.increment(hourPrefix+"pageViewSource:"+sessionChannel,1);
          }
        }
        if(sessionId){
          this.sql.exec("INSERT OR IGNORE INTO session_events(id,day) VALUES (?,?)",sessionId,day.date);
          if(this.sql.exec("SELECT changes() AS n").one().n>0){
            this.rememberEarliest("sessions:startedAt",now);
            this.increment("sessions",1);
            this.increment(dayPrefix+"sessions",1);

          }
        }
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
    const responseMode=url.searchParams.get("response");
    if(responseMode==="public" && data.action==="visit"){
      const snapshot=this.publicSnapshot(url,day);
      if(ignoredVisit)snapshot.ignored=true;
      return json(snapshot);
    }
    if(responseMode==="action" && data.action==="like")
      return json({ok:true,liked,likes:this.count("likes:"+article)});
    if(responseMode==="action" && data.action==="vote")
      return json({ok:true,vote:selectedVote,alreadyVoted,votes:{
        plausible:this.count("predictions:plausible:"+article),
        unlikely:this.count("predictions:unlikely:"+article)
      }});
    // Backward-compatible ACK for already-deployed clients and cheap shares.
    if(responseMode==="ack" && (data.action==="visit" || data.action==="share"))
      return json({ok:true,...(ignoredVisit ? {ignored:true} : {})});
    const startedAt=this.count("pageViews:startedAt");
    const hourlyStartedAt=this.count("hourlyPageViews:startedAt");
    const sessionStartedAt=this.count("sessions:startedAt");
    const sourceStartedAt=this.count("pageViewSources:startedAt");
    const kindStartedAt=this.count("pageViewKinds:startedAt");
    const previousDate=new Date((day.serial-1)*DAY_MS).toISOString().slice(0,10);
    return json({version:2,unit:"anonymous-browser",visitors:this.count("visitors"),article,
      ...this.pageViewSummary(day,startedAt),
      ...this.sessionSummary(sessionStartedAt),
      ...this.pageViewSourceSummary(day,hour,sourceStartedAt),
      ...this.pageKindSummary(day,hour,kindStartedAt),
      hourly:{timezone:"Asia/Tokyo",
        startedAtUtc:hourlyStartedAt ? new Date(hourlyStartedAt).toISOString() : null,
        today:this.hourlyDaySnapshot(day.date,hour,hourlyStartedAt),
        yesterday:this.hourlyDaySnapshot(previousDate,hour,hourlyStartedAt)},
      topArticles:{
        currentHour:hourlyStartedAt
          ? this.topArticleRows("hour:"+hour.date+":"+hour.hour+":pageViews:") : [],
        today:this.topArticleRows("day:"+day.date+":pageViews:"),
        last7Days:this.topArticleRowsForWeek(day)
      },
      today:this.dailySnapshot(day,day,startedAt,sessionStartedAt),
      yesterday:this.dailySnapshot({date:previousDate},day,startedAt,sessionStartedAt),
      articlePageViews:article ? (startedAt ? this.count("pageViews:"+article) : null) : undefined,
      todayArticlePageViews:article ? (startedAt ? this.count("day:"+day.date+":pageViews:"+article) : null) : undefined,
      ...(ignoredVisit ? {ignored:true} : {}),
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

