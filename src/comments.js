import { DurableObject } from 'cloudflare:workers';

// An independent SQLite Durable Object on Workers Free. Never stores IP addresses.
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const URL_LIKE = /https?:\/\/|www\.|(?:[a-z0-9-]+\.)+(?:com|net|org|jp|io|xyz|info|biz|co|me)(?:\b|\/)|@[a-z0-9_.-]+/iu;
const MAX_ARTICLE_COMMENTS = 100;

function reply(value, status=200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      'content-type':'application/json; charset=utf-8',
      'cache-control':'no-store',
      'x-content-type-options':'nosniff'
    }
  });
}
function validSlug(s) { return typeof s==='string' && s.length<=100 && SLUG.test(s); }
function validId(s) { return typeof s==='string' && UUID.test(s); }
function publicComment(row) {
  return {id:row.id, nickname:row.nickname, body:row.body,
    createdAt:new Date(row.created_ms).toISOString()};
}
function normalizeInput(data) {
  const nickname=typeof data.nickname==='string' ? data.nickname.replace(/\s+/gu,' ').trim() : '';
  const body=typeof data.body==='string' ? data.body.replace(/\r\n?/g,'\n').trim() : '';
  if (Array.from(nickname).length>16 || /[<>\u0000-\u001f\u007f]/u.test(nickname) || URL_LIKE.test(nickname)) return null;
  if (Array.from(body).length<2 || Array.from(body).length>180 || /[<>\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(body)) return null;
  const normalized=(nickname+' '+body).normalize('NFKC');
  // Reject common contact details before they become publicly visible.
  // This is not a complete personal-information detector.
  if (/(?:0[789]0|050)[-ー‐–\s]?\d{4}[-ー‐–\s]?\d{4}/u.test(normalized)) return null;
  if ((body.match(/\n/g)||[]).length>3 || URL_LIKE.test(body) || /(.)\1{12,}/u.test(body)) return null;
  return {nickname:nickname||'匿名の未来人',body};
}
async function digestClientId(id) {
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(id));
  return Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
}

export class ReaderComments extends DurableObject {
  constructor(ctx, env) {
    super(ctx,env);
    this.sql=ctx.storage.sql;
    this.env=env;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS comments (
      id TEXT PRIMARY KEY, article TEXT NOT NULL, nickname TEXT NOT NULL,
      body TEXT NOT NULL, visitor TEXT NOT NULL, created_ms INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'visible');`);
    this.sql.exec('CREATE INDEX IF NOT EXISTS comments_article ON comments(article,status,created_ms DESC);');
    this.sql.exec('CREATE INDEX IF NOT EXISTS comments_visitor ON comments(visitor,created_ms DESC);');
    this.sql.exec('CREATE INDEX IF NOT EXISTS comments_recent ON comments(created_ms DESC);');
    this.sql.exec(`CREATE TABLE IF NOT EXISTS comment_reports (
      comment_id TEXT NOT NULL, visitor TEXT NOT NULL,
      PRIMARY KEY(comment_id, visitor));`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS comment_limits (
      bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_ms INTEGER NOT NULL);`);
  }

  countBucket(bucket) {
    return this.sql.exec('SELECT hits FROM comment_limits WHERE bucket=?',bucket).toArray()[0]?.hits??0;
  }
  bump(bucket, expiration) {
    this.sql.exec(`INSERT INTO comment_limits(bucket,hits,expires_ms) VALUES(?,1,?)
      ON CONFLICT(bucket) DO UPDATE SET hits=hits+1`,bucket,expiration);
  }
  async authorized(req) {
    const secret=this.env.COMMENTS_ADMIN_TOKEN;
    if(typeof secret!=='string' || secret.length<24) return false;
    const supplied=(req.headers.get('authorization')||'').replace(/^Bearer\s+/i,'');
    const a=new TextEncoder().encode(supplied), b=new TextEncoder().encode(secret);
    if(a.length!==b.length)return false;
    let difference=0;
    for(let i=0;i<a.length;i++)difference|=a[i]^b[i];
    return difference===0;
  }
  async getPublic(url) {
    const article=url.searchParams.get('article');
    if(!validSlug(article))return reply({error:'記事が指定されていません'},400);
    const rows=this.sql.exec(`SELECT id,nickname,body,created_ms FROM comments
      WHERE article=? AND status='visible' ORDER BY created_ms DESC LIMIT 20`,article).toArray();
    const total=this.sql.exec(`SELECT COUNT(*) AS n FROM comments
      WHERE article=? AND status='visible'`,article).one().n;
    return reply({article,total,comments:rows.map(publicComment)});
  }
  async getSummary(url) {
    // Public aggregate only: no visitor IDs, names or raw comment bodies.
    const raw=(url.searchParams.get('articles')||'').slice(0,1600);
    const articles=[...new Set(raw.split(',').filter(validSlug).slice(0,20))];
    const counts={};
    for(const article of articles)counts[article]=0;
    if(articles.length){
      const placeholders=articles.map(()=>'?').join(',');
      const rows=this.sql.exec(
        `SELECT article, COUNT(*) AS n FROM comments
          WHERE status='visible' AND article IN (${placeholders}) GROUP BY article`,
        ...articles
      ).toArray();
      for(const row of rows)counts[row.article]=row.n;
    }
    return reply({version:1,counts});
  }
  async getAdmin(req) {
    if(!this.env.COMMENTS_ADMIN_TOKEN)return reply({error:'管理用キーが未設定です'},503);
    if(!await this.authorized(req))return reply({error:'認証に失敗しました'},401);
    const rows=this.sql.exec(`SELECT c.id,c.article,c.nickname,c.body,c.status,c.created_ms,
      (SELECT COUNT(*) FROM comment_reports r WHERE r.comment_id=c.id) AS reports
      FROM comments c ORDER BY c.created_ms DESC LIMIT 100`).toArray();
    return reply({comments:rows.map(r=>({ ...publicComment(r),article:r.article,status:r.status,reports:r.reports }))});
  }
  async postAdmin(req) {
    if(!this.env.COMMENTS_ADMIN_TOKEN)return reply({error:'管理用キーが未設定です'},503);
    if(!await this.authorized(req))return reply({error:'認証に失敗しました'},401);
    const raw=await req.text();
    if(raw.length>800)return reply({error:'投稿が大きすぎます'},413);
    let data;
    try{data=JSON.parse(raw);}catch(_){return reply({error:'JSON形式が不正です'},400);}
    if(!data || !validId(data.commentId) || !['hide','restore','delete'].includes(data.action))
      return reply({error:'操作内容が不正です'},400);
    const row=this.sql.exec('SELECT id FROM comments WHERE id=?',data.commentId).toArray()[0];
    if(!row)return reply({error:'対象が見つかりません'},404);
    if(data.action==='delete') {
      this.sql.exec('DELETE FROM comment_reports WHERE comment_id=?',data.commentId);
      this.sql.exec('DELETE FROM comments WHERE id=?',data.commentId);
    } else {
      this.sql.exec('UPDATE comments SET status=? WHERE id=?',data.action==='hide'?'hidden':'visible',data.commentId);
    }
    return reply({ok:true});
  }
  async postPublic(req) {
    if(Number(req.headers.get('content-length')||0)>2048)return reply({error:'投稿が大きすぎます'},413);
    const raw=await req.text();
    if(raw.length>2048)return reply({error:'投稿が大きすぎます'},413);
    let data;
    try{data=JSON.parse(raw);}catch(_){return reply({error:'JSON形式が不正です'},400);}
    if(!data || !validId(data.clientId) || !validSlug(data.article))
      return reply({error:'入力内容を確認してください'},400);
    if(!['post','report','remove'].includes(data.action))return reply({error:'不正な操作です'},400);
    const visitor=await digestClientId(data.clientId);
    if(data.action==='post')return this.addComment(data,visitor);
    if(!validId(data.commentId))return reply({error:'対象が不正です'},400);
    if(data.action==='remove'){
      const existing=this.sql.exec('SELECT visitor FROM comments WHERE id=? AND article=?',data.commentId,data.article).toArray()[0];
      if(!existing || existing.visitor!==visitor)return reply({error:'自分の投稿だけ削除できます'},403);
      this.sql.exec('DELETE FROM comment_reports WHERE comment_id=?',data.commentId);
      this.sql.exec('DELETE FROM comments WHERE id=?',data.commentId);
      return reply({ok:true});
    }
    const existing=this.sql.exec('SELECT visitor,status FROM comments WHERE id=? AND article=?',data.commentId,data.article).toArray()[0];
    if(!existing || existing.status!=='visible')return reply({error:'対象が見つかりません'},404);
    if(existing.visitor===visitor)return reply({error:'自分の投稿は通報できません'},403);
    const exists=this.sql.exec('SELECT 1 FROM comment_reports WHERE comment_id=? AND visitor=?',data.commentId,visitor).toArray().length;
    if(exists)return reply({ok:true,alreadyReported:true});
    const now=Date.now(),day=new Date(now).toISOString().slice(0,10);
    const reportBuckets=[['reports:'+day,200],['reports-user:'+day+':'+visitor,10]];
    if(reportBuckets.some(([key,max])=>this.countBucket(key)>=max))
      return reply({error:'本日の通報上限に達しました'},429);
    this.sql.exec('INSERT INTO comment_reports(comment_id,visitor) VALUES(?,?)',data.commentId,visitor);
    for(const [key] of reportBuckets)this.bump(key,now+3*24*60*60*1000);
    const count=this.sql.exec('SELECT COUNT(*) AS n FROM comment_reports WHERE comment_id=?',data.commentId).one().n;
    if(count>=3)this.sql.exec("UPDATE comments SET status='hidden' WHERE id=?",data.commentId);
    return reply({ok:true,hidden:count>=3});
  }
  addComment(data,visitor) {
    const input=normalizeInput(data);
    if(!input)return reply({error:'本文は2〜180文字。URLやHTML、連続文字は投稿できません'},400);
    const now=Date.now(),iso=new Date(now).toISOString();
    const keys=[['day:'+iso.slice(0,10),100],['hour:'+iso.slice(0,13),20],
      ['user:'+iso.slice(0,10)+':'+visitor,3]];
    let result={status:201};
    this.ctx.storage.transactionSync(()=>{
      if(keys.some(([key,max])=>this.countBucket(key)>=max)){
        result={status:429,error:'投稿が集中しています。しばらく後にお試しください'};
        return;
      }
      const recent=this.sql.exec('SELECT MAX(created_ms) AS last FROM comments WHERE visitor=?',visitor).one().last;
      if(recent && now-recent<5*60*1000){
        result={status:429,error:'連続投稿は5分ほど間隔を空けてください'};
        return;
      }
      const duplicate=this.sql.exec(`SELECT 1 AS found FROM comments
        WHERE visitor=? AND article=? AND body=? LIMIT 1`,visitor,data.article,input.body).toArray().length;
      if(duplicate){result={status:409,error:'同じコメントはすでに投稿されています'};return;}
      const id=crypto.randomUUID();
      this.sql.exec(`INSERT INTO comments(id,article,nickname,body,visitor,created_ms,status)
        VALUES(?,?,?,?,?,?,'visible')`,id,data.article,input.nickname,input.body,visitor,now);
      for(const [key] of keys)this.bump(key,now+3*24*60*60*1000);
      this.sql.exec('DELETE FROM comment_limits WHERE expires_ms<?',now);
      const total=this.sql.exec('SELECT COUNT(*) AS n FROM comments WHERE article=?',data.article).one().n;
      if(total>MAX_ARTICLE_COMMENTS){
        const oldest=this.sql.exec('SELECT id FROM comments WHERE article=? ORDER BY created_ms ASC LIMIT 1',data.article).one();
        if(oldest){
          this.sql.exec('DELETE FROM comment_reports WHERE comment_id=?',oldest.id);
          this.sql.exec('DELETE FROM comments WHERE id=?',oldest.id);
        }
      }
      result={status:201,comment:{id,nickname:input.nickname,body:input.body,createdAt:new Date(now).toISOString()}};
    });
    if(result.error)return reply({error:result.error},result.status);
    return reply({ok:true,comment:result.comment},201);
  }
  async fetch(req) {
    const url=new URL(req.url);
    if(url.pathname==='/api/comments/summary'){
      if(req.method!=='GET')return reply({error:'method not allowed'},405);
      return this.getSummary(url);
    }
    if(url.pathname==='/api/comments/admin'){
      if(req.method==='GET')return this.getAdmin(req);
      if(req.method==='POST')return this.postAdmin(req);
      return reply({error:'method not allowed'},405);
    }
    if(url.pathname!=='/api/comments')return reply({error:'not found'},404);
    if(req.method==='GET')return this.getPublic(url);
    if(req.method==='POST')return this.postPublic(req);
    return reply({error:'method not allowed'},405);
  }
}
