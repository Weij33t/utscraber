export function stamp(seconds) {
  const n=Math.max(0,Math.floor(Number(seconds)||0)), h=Math.floor(n/3600), m=Math.floor(n%3600/60), s=n%60;
  return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`;
}
export function classifyInput(input) {
  const text=input.trim();if(!text)return {type:'invalid'};
  if(/^[a-zA-Z0-9_-]{11}$/.test(text))return {type:'video',value:text};
  if(/^@[\w.%-]{1,100}$/u.test(text)||/^UC[a-zA-Z0-9_-]{22}$/.test(text))return {type:'channel',value:text};
  if(/^@[^\s/]{1,100}$/u.test(text))return {type:'channel',value:'https://www.youtube.com/'+encodeURI(text)};
  let url;try{url=new URL(text.includes('://')?text:'https://'+text);}catch{return {type:'search',value:text};}
  const host=url.hostname.toLowerCase(), parts=url.pathname.split('/').filter(Boolean);
  if(['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtu.be'].includes(host)){
    let id=['youtu.be','www.youtu.be'].includes(host)?parts[0]:parts[0]==='watch'?url.searchParams.get('v'):['shorts','embed','live'].includes(parts[0])?parts[1]:null;
    if(id&&/^[a-zA-Z0-9_-]{11}$/.test(id))return {type:'video',value:id};
    if(!['youtu.be','www.youtu.be'].includes(host)&&(parts[0]?.startsWith('@')||(['channel','c','user'].includes(parts[0])&&parts[1])))return {type:'channel',value:url.href};
    return {type:'invalid'};
  }
  if(text.includes('://')||/^(?:www\.)?(?:youtube\.com|youtu\.be)[/?.]/i.test(text))return {type:'invalid'};
  return {type:'search',value:text};
}
export function validVideo(v) {return v&&/^[a-zA-Z0-9_-]{11}$/.test(v.id)&&typeof v.title==='string'&&typeof v.channel==='string';}
export function validChannel(c) {return c&&typeof c.id==='string'&&typeof c.title==='string'&&typeof c.url==='string'&&classifyInput(c.url).type==='channel';}
export function cleanLibrary(data) {
  if(!data||!Array.isArray(data.videos)||!Array.isArray(data.channels))return null;
  const videos=data.videos.filter(validVideo).slice(0,1000).map(v=>{
    const clean={id:v.id,title:v.title.slice(0,1000),channel:v.channel.slice(0,300),duration:Number(v.duration)||null,views:Number(v.views)||null};
    if(typeof v.channelId==='string'&&/^UC[a-zA-Z0-9_-]{22}$/.test(v.channelId))clean.channelId=v.channelId;
    if(typeof v.channelUrl==='string'&&classifyInput(v.channelUrl).type==='channel')clean.channelUrl=v.channelUrl;
    const t=v.transcript;
    if(t&&typeof t.language==='string'&&Array.isArray(t.languages)&&Array.isArray(t.segments)&&t.segments.length<=100000&&t.segments.every(s=>Number.isFinite(s.start)&&s.start>=0&&typeof s.text==='string'&&s.text.length<50000)){
      clean.transcript={language:t.language,generated:!!t.generated,languages:t.languages.filter(l=>l&&typeof l.code==='string'&&typeof l.name==='string').map(l=>({code:l.code,name:l.name})),segments:t.segments.map(s=>({start:s.start,duration:Number(s.duration)||0,text:s.text}))};
    }
    return clean;
  });
  return {videos,channels:data.channels.filter(validChannel).slice(0,1000).map(c=>({id:c.id,title:c.title.slice(0,300),url:c.url}))};
}
