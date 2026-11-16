// A stateless, JSON-only Streamable HTTP adapter for one existing public read.
// https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
// https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
// https://modelcontextprotocol.io/specification/2025-11-25/server/tools
const versions=["2025-06-18","2025-11-25"];
const noncePattern="^[A-Za-z0-9]{1,64}$";
const failureReasons=["github_rate_limit","github_http_401","github_http_403","github_http_429","github_http_5xx",
  "github_http_other","timeout","repository_unavailable","invalid_response","runtime_error"];
const tool={
  name:"read_image_control",
  description:"Read the current public image workflow stop state. Supply a fresh nonce for every call; only active permits image operations.",
  inputSchema:{type:"object",properties:{nonce:{type:"string",pattern:noncePattern}},
    required:["nonce"],additionalProperties:false},
  annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:true}
};
const object=value=>value!==null && typeof value==="object" && !Array.isArray(value);
function response(body,status=200,extra={}) {
  return new Response(body===null ? null : JSON.stringify(body),{status,headers:{
    "content-type":"application/json; charset=utf-8","cache-control":"no-store",
    "x-content-type-options":"nosniff","x-robots-tag":"noindex",...extra
  }});
}
function error(id,code,message,status=200) {
  return response({jsonrpc:"2.0",id,error:{code,message}},status);
}
function accepts(value,type) {
  return value.split(",").some(part=>{
    const [media,...params]=part.trim().toLowerCase().split(";");
    return media.trim()===type && !params.some(param=>/^\s*q\s*=/.test(param) &&
      !(Number(param.split("=")[1])>0));
  });
}

export async function imageControlMcp(req,url,readControl,readBody) {
  const origin=req.headers.get("origin");
  if(origin!==null && origin!==url.origin && origin!=="https://chatgpt.com")
    return error(null,-32600,"Invalid origin",403);
  if(req.method!=="POST")
    return response({error:"method not allowed"},405,{allow:"POST"});
  if(url.search)return error(null,-32600,"Invalid request",400);
  const accept=req.headers.get("accept")||"";
  if(!accepts(accept,"application/json") || !accepts(accept,"text/event-stream"))
    return error(null,-32600,"Accept must include application/json and text/event-stream",406);
  if(!/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/i.test(req.headers.get("content-type")||""))
    return error(null,-32600,"UTF-8 JSON required",415);
  const headerVersion=req.headers.get("mcp-protocol-version");
  if(headerVersion!==null && !versions.includes(headerVersion))
    return error(null,-32600,"Unsupported protocol version",400);
  let message;
  try {
    const body=await readBody(req,8192);
    if(body===null)return error(null,-32600,"Payload too large",413);
    message=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(body));
  } catch(_) { return error(null,-32700,"Parse error",400); }
  if(!object(message) || message.jsonrpc!=="2.0" || typeof message.method!=="string" ||
     "result" in message || "error" in message ||
     (message.params!==undefined && !object(message.params)))
    return error(null,-32600,"Invalid request",400);
  const hasId=Object.hasOwn(message,"id");
  if(hasId && !(typeof message.id==="string" || Number.isSafeInteger(message.id)))
    return error(null,-32600,"Invalid request",400);
  // No sessions or server-to-client requests are created. Unknown notifications
  // are ignored without triggering the reader; no JSON-RPC response is emitted.
  if(!hasId)return response(null,202);
  const {id,method,params={}}=message;
  const result=value=>response({jsonrpc:"2.0",id,result:value});
  const unavailable=reason=>result({content:[{type:"text",text:JSON.stringify({error:"image control unavailable",
    reason:failureReasons.includes(reason) ? reason : "runtime_error"})}],isError:true});
  if(method==="initialize") {
    if(typeof params.protocolVersion!=="string" || !object(params.capabilities) ||
       !object(params.clientInfo) || typeof params.clientInfo.name!=="string" ||
       typeof params.clientInfo.version!=="string")
      return error(id,-32602,"Invalid initialize parameters");
    return result({protocolVersion:versions.includes(params.protocolVersion) ? params.protocolVersion : versions.at(-1),
      capabilities:{tools:{}},serverInfo:{name:"mihassei-image-control",version:"1.0.0"}});
  }
  // With no session state, the transport's missing-header fallback is 2025-03-26.
  // This adapter implements the later single-message revisions explicitly.
  if(headerVersion===null)return error(id,-32600,"Unsupported protocol version",400);
  if(method==="ping")return result({});
  if(method==="tools/list") {
    if(Object.keys(params).some(key=>key!=="_meta"))return error(id,-32602,"Invalid list parameters");
    return result({tools:[tool]});
  }
  if(method!=="tools/call")return error(id,-32601,"Method not found");
  if(params.name!==tool.name || Object.keys(params).some(key=>!["name","arguments","_meta"].includes(key)) ||
     !object(params.arguments))return error(id,-32602,"Invalid tool call");
  const args=params.arguments;
  if(Object.keys(args).length!==1 || typeof args.nonce!=="string" || !new RegExp(noncePattern).test(args.nonce))
    return result({content:[{type:"text",text:'{"error":"invalid nonce"}'}],isError:true});
  try {
    const control=await readControl(args.nonce);
    if(!control.ok)return unavailable(control.headers.get("x-image-control-reason"));
    const data=await control.json();
    return result({content:[{type:"text",text:JSON.stringify(data)}],
      structuredContent:data,isError:false});
  } catch(_) {
    return unavailable("runtime_error");
  }
}
