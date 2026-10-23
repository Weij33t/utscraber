import { classifyInput, stamp, validVideo, validChannel, cleanLibrary } from './core.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const STORE = 'tekstom-library-v1';
let library = { videos: [], channels: [] };
let apiOrigin = window.TEKSTOM_API || '';
try { library = cleanLibrary(JSON.parse(localStorage.getItem(STORE))) || library; apiOrigin = localStorage.getItem('tekstom-api')?.trim() || apiOrigin; } catch { /* private mode or invalid data */ }
let activeView = 'home', backView = 'home', activeRequest, requestGeneration = 0;
let readerData, readerVideo, visibleCards = [], currentChannel, channelPage = null;
let lastSearch = '', toastTimer;

function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4000); }
function persist() {
  try { localStorage.setItem(STORE, JSON.stringify(library)); }
  catch { toast('Не удалось сохранить: память браузера заполнена или недоступна. Экспортируй библиотеку.'); }
  counts();
}
function counts() { $('#saved-count').textContent = library.videos.length; $('#channel-count').textContent = library.channels.length; }
function isSaved(id) { return library.videos.some(v => v.id === id); }
function cancelRequest() { activeRequest?.abort(); activeRequest = null; requestGeneration++; }
function navigate(view, { cancel = true } = {}) {
  if (cancel) cancelRequest();
  activeView = view;
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== `${view}-view`);
  document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const titles = {home:'Обзор',saved:'Сохранённое',channels:'Мои каналы',reader:'Транскрипт'};
  $('#breadcrumb').innerHTML = `Личное пространство / <b>${titles[view]}</b>`;
  if (view === 'saved') renderSaved();
  if (view === 'channels') renderChannels();
  window.scrollTo({top:0});
}
function unavailableAPI() { return !apiOrigin && location.hostname.endsWith('github.io'); }
async function api(path, params = {}, signal) {
  if (unavailableAPI()) throw new Error('API ещё не подключён. Открой настройки и укажи HTTPS-адрес сервера.');
  const url = new URL(`${apiOrigin}${path}`, location.origin);
  Object.entries(params).forEach(([k,v]) => { if (v != null) url.searchParams.set(k,v); });
  let response;
  try { response = await fetch(url, { signal: signal || AbortSignal.timeout(90000), credentials:'omit' }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new Error('Нет связи с API. Проверь адрес сервера, интернет и ALLOWED_ORIGINS.'); }
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) throw new Error('По этому адресу нет API. Укажи адрес сервера в настройках.');
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Сервер отклонил запрос. Проверь введённые данные.');
  return body;
}
function begin() {
  cancelRequest(); activeRequest = new AbortController();
  const controller = activeRequest, generation = requestGeneration;
  const timeout = setTimeout(() => controller.abort('timeout'), 90000);
  return { signal: controller.signal, current: () => generation === requestGeneration, end: () => clearTimeout(timeout) };
}
function errorBlock(message, retry = true) {
  return `<div class="error" role="alert">${esc(message)}${retry ? '<br><button class="secondary" data-action="retry">Попробовать ещё раз ↻</button>' : ''}</div>`;
}
function requestError(error, req) { return req.signal.aborted ? 'Запрос занял слишком много времени. Сервер может просыпаться или YouTube не отвечает.' : error.message; }
function loading(target, message) { target.innerHTML = `<div class="loading" role="status">${esc(message)}</div>`; }

function card(v) {
  const saved = isSaved(v.id);
  return `<article class="video-card"><button class="thumbnail-button" data-open="${v.id}" aria-label="Читать: ${esc(v.title)}"><img src="https://i.ytimg.com/vi/${v.id}/hqdefault.jpg" alt="" loading="lazy">${v.duration ? `<span class="duration">${stamp(v.duration)}</span>` : ''}</button><div class="video-info"><button class="video-title" data-open="${v.id}">${esc(v.title)}</button><button class="save-button ${saved?'saved':''}" data-save="${v.id}" aria-label="${saved?'Убрать из сохранённого':'Сохранить видео'}" aria-pressed="${saved}">${saved?'✓':'＋'}</button></div>${v.channelUrl || v.channelId ? `<button class="channel-link" data-channel-video="${v.id}">${esc(v.channel)} ↗</button>` : `<span class="channel-link">${esc(v.channel)}</span>`}${v.views ? `<small class="views">${new Intl.NumberFormat('ru',{notation:'compact'}).format(v.views)} просмотров</small>` : ''}</article>`;
}
function renderCards(videos) { return `<div class="video-grid">${videos.map(card).join('')}</div>`; }
function renderSaved() {
  $('#saved-list').innerHTML = library.videos.length ? renderCards(library.videos) : '<div class="empty">Здесь будут видео, к которым хочется вернуться.<br>Нажми ＋ у видео или «Сохранить» рядом с текстом.</div>';
}
function findCard(id) { return visibleCards.find(v=>v.id===id) || library.videos.find(v=>v.id===id); }
function renderChannels() {
  $('#channels-list').innerHTML = library.channels.length ? library.channels.map((c,i) => `<div class="channel-row"><span class="channel-avatar">${esc(c.title.slice(0,1).toUpperCase())}</span><div class="channel-details"><button class="channel-name" data-channel-index="${i}">${esc(c.title)} ↗</button><small>${esc(c.url.replace('https://www.youtube.com/',''))}</small></div><button class="secondary" data-unfollow="${i}">Отписаться</button></div>`).join('') : '<div class="empty">Собери свою ленту без алгоритма.<br>Добавь канал по ссылке или открой его из результатов поиска.</div>';
}
function toggleSave(v, transcript) {
  if (isSaved(v.id)) library.videos = library.videos.filter(x => x.id !== v.id);
  else library.videos.unshift({...v, ...(transcript ? {transcript} : {}), savedAt:Date.now()});
  persist();
  toast(isSaved(v.id) ? 'Видео сохранено на этом устройстве' : 'Видео удалено из сохранённого');
  if (activeView === 'saved') renderSaved();
  document.querySelectorAll(`[data-save="${v.id}"]`).forEach(btn => {btn.classList.toggle('saved',isSaved(v.id));btn.setAttribute('aria-pressed',String(isSaved(v.id)));btn.setAttribute('aria-label',isSaved(v.id)?'Убрать из сохранённого':'Сохранить видео');btn.textContent=isSaved(v.id)?'✓':'＋';});
  if (activeView === 'reader' && readerData) renderReader();
}
function subscribed(c) { return library.channels.some(x => x.id === c.id || x.url === c.url); }
function follow(c) {
  if (subscribed(c)) library.channels = library.channels.filter(x => x.id !== c.id && x.url !== c.url);
  else library.channels.unshift(c);
  persist(); toast(subscribed(c) ? 'Канал добавлен в твою ленту' : 'Подписка удалена');
}

async function submitInput(value) {
  const kind = classifyInput(value);
  if (kind.type === 'invalid') { toast('Не удалось распознать ссылку YouTube. Проверь адрес.'); return; }
  if (kind.type === 'video') return openReader(kind.value);
  if (kind.type === 'channel') return openChannel(kind.value);
  return search(kind.value);
}
async function search(query) {
  navigate('home'); lastSearch = query; currentChannel = null; channelPage = null;
  $('#query').value = query; $('#discover').hidden = true; $('#results-section').hidden = false; $('#load-more').hidden = true;
  $('#results-title').textContent = `Поиск: ${query}`;
  loading($('#results'), 'Ищем видео на YouTube…');
  const req = begin();
  try { const data = await api('/api/search',{q:query},req.signal); if (!req.current()) return; visibleCards = data.videos.filter(validVideo); $('#results').innerHTML = visibleCards.length ? renderCards(visibleCards) : '<div class="empty">По этому запросу ничего не нашлось. Попробуй другие слова.</div>'; }
  catch (e) { if (req.current()) $('#results').innerHTML = errorBlock(requestError(e,req)); }
  finally { req.end(); }
}
async function openChannel(url, page = 1) {
  if (page === 1) { navigate('home'); visibleCards = []; }
  currentChannel = {url}; lastSearch = '';
  $('#discover').hidden = true; $('#results-section').hidden = false;
  $('#load-more').hidden = true; $('#results-title').textContent = 'Видео канала';
  if (page === 1) loading($('#results'), 'Загружаем канал…');
  const req = begin();
  try {
    const data = await api('/api/channel',{url,page},req.signal); if (!req.current()) return;
    currentChannel = data.channel; channelPage = data.nextPage;
    visibleCards = [...new Map([...visibleCards,...data.videos.filter(validVideo)].map(v => [v.id,v])).values()];
    $('#results-title').textContent = data.channel.title;
    $('#results').innerHTML = `<div class="channel-row" style="padding:0 0 22px;margin-bottom:22px"><span class="channel-avatar">${esc(data.channel.title.slice(0,1))}</span><div class="channel-details">${esc(data.channel.title)}</div><button class="secondary" data-action="follow-current">${subscribed(currentChannel)?'✓ В моих каналах':'＋ Подписаться'}</button></div>` + (visibleCards.length ? renderCards(visibleCards) : '<div class="empty">У канала пока нет доступных видео.</div>');
    $('#load-more').hidden = !channelPage;
  } catch(e) {
    if (!req.current()) return;
    if (page === 1) $('#results').innerHTML = errorBlock(requestError(e,req));
    else {toast(requestError(e,req)); $('#load-more').hidden = false;}
  } finally {req.end();}
}
async function openReader(id, lang, refresh = false) {
  const known = visibleCards.find(x=>x.id===id) || library.videos.find(x=>x.id===id);
  if (activeView !== 'reader') backView = activeView;
  navigate('reader'); readerData = null; readerVideo = known || {id,title:'Видео YouTube',channel:'Канал YouTube'};
  loading($('#reader-content'), 'Получаем субтитры с тайм-кодами…');
  const saved = library.videos.find(x=>x.id===id)?.transcript;
  if (!refresh && saved && (!lang || saved.language === lang)) {readerData=saved;renderReader();return;}
  const req = begin();
  try {
    const data = await api('/api/transcript',{url:id,lang},req.signal); if(!req.current()) return;
    readerData = data;
    readerVideo = {...readerVideo,...data.video, title:data.video.title==='Видео YouTube' ? readerVideo.title : data.video.title, channel:data.video.channel==='Канал YouTube' ? readerVideo.channel : data.video.channel, channelUrl:data.video.channelUrl || readerVideo.channelUrl};
    const record = library.videos.find(x=>x.id===id); if(record) {Object.assign(record,readerVideo,{transcript:data});persist();}
    renderReader();
  } catch(e) {if(req.current()) $('#reader-content').innerHTML=errorBlock(requestError(e,req));}
  finally {req.end();}
}
function renderReader() {
  const data=readerData, v=readerVideo;
  const languages=[...new Map(data.languages.map(x=>[x.code,x])).values()];
  $('#reader-content').innerHTML=`<div class="reader-heading"><div class="eyebrow">РЕЖИМ ЧТЕНИЯ</div><h1>${esc(v.title)}</h1><div class="reader-meta"><span>${esc(v.channel)}</span><span class="badge">${data.generated == null ? 'Субтитры YouTube' : data.generated ? 'Автосубтитры' : 'Авторские субтитры'}</span><span>${data.segments.length} фрагментов</span></div></div><div class="reader-actions"><button class="secondary" data-action="save-reader">${isSaved(v.id)?'✓ Сохранено':'＋ Сохранить'}</button><button class="secondary" data-action="copy">Копировать текст</button><button class="secondary" data-action="download">Скачать .txt ↓</button><a class="secondary" href="https://www.youtube.com/watch?v=${v.id}" target="_blank" rel="noopener noreferrer">YouTube ↗</a><button class="secondary" data-action="share">Ссылка</button>${v.channelUrl||v.channelId?'<button class="secondary" data-action="reader-channel">Открыть канал ↗</button>':''}</div><div class="reader-toolbar"><label class="sr-only" for="find-text">Поиск по транскрипту</label><input id="find-text" placeholder="Найти в тексте…" type="search"><select id="language" aria-label="Язык субтитров">${languages.map(l=>`<option value="${esc(l.code)}" ${l.code===data.language?'selected':''}>${esc(l.name)}</option>`).join('')}</select><label class="timestamp-toggle"><input id="show-times" type="checkbox" checked> Тайм-коды</label></div><div id="segments" class="transcript"></div>`;
  renderSegments();
  $('#find-text').addEventListener('input', renderSegments);
  $('#show-times').addEventListener('change',()=>$('#segments').classList.toggle('hide-times',!$('#show-times').checked));
  $('#language').addEventListener('change',e=>openReader(v.id,e.target.value,true));
}
function renderSegments() {
  const query=$('#find-text').value.trim(), lower=query.toLocaleLowerCase('ru');
  const segments=readerData.segments.filter(s=>s.text.toLocaleLowerCase('ru').includes(lower));
  $('#segments').innerHTML=segments.length ? segments.map(s=>{
    let text=esc(s.text);
    if(query){const regex=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'giu');text='';let end=0;for(const match of s.text.matchAll(regex)){text+=esc(s.text.slice(end,match.index))+`<mark>${esc(match[0])}</mark>`;end=match.index+match[0].length;}text+=esc(s.text.slice(end));}
    return `<div class="segment"><a href="https://www.youtube.com/watch?v=${readerVideo.id}&t=${Math.floor(s.start)}" target="_blank" rel="noopener noreferrer" aria-label="Открыть видео с ${stamp(s.start)}">${stamp(s.start)}</a><p>${text}</p></div>`;
  }).join(''):'<div class="empty">В тексте нет таких слов.</div>';
}
function transcriptText() { const times=$('#show-times')?.checked;return `${readerVideo.title}\nhttps://www.youtube.com/watch?v=${readerVideo.id}\n\n`+readerData.segments.map(s=>(times?`[${stamp(s.start)}] `:'')+s.text).join('\n'); }
function download(name,text,type) {const url=URL.createObjectURL(new Blob([text],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function copy(text) {try{await navigator.clipboard.writeText(text);toast('Скопировано');}catch{toast('Браузер запретил копирование. Скачай текст файлом.');}}
function settings() {$('#api-url').value=apiOrigin;$('#settings').showModal();}

document.addEventListener('click',e=>{
  const button=e.target.closest('button,a'); if(!button)return;
  const id=button.dataset.open;
  if(id) return openReader(id);
  if(button.dataset.view) return navigate(button.dataset.view);
  if(button.matches('.brand')) {e.preventDefault();navigate('home');return;}
  if(button.dataset.search) return search(button.dataset.search);
  if(button.dataset.featuredChannel) return openChannel(button.dataset.featuredChannel);
  if(button.dataset.save){const v=findCard(button.dataset.save);if(v)toggleSave(v);return;}
  if(button.dataset.channelVideo){const v=findCard(button.dataset.channelVideo);if(v)openChannel(v.channelId||v.channelUrl);return;}
  if(button.dataset.channelIndex!==undefined) return openChannel(library.channels[Number(button.dataset.channelIndex)].url);
  if(button.dataset.unfollow!==undefined){library.channels.splice(Number(button.dataset.unfollow),1);persist();renderChannels();return;}
  switch(button.dataset.action){
    case 'retry':return activeView==='reader'?openReader(readerVideo.id,null,true):currentChannel?openChannel(currentChannel.url):search(lastSearch);
    case 'follow-current':follow(currentChannel);button.textContent=subscribed(currentChannel)?'✓ В моих каналах':'＋ Подписаться';break;
    case 'save-reader':toggleSave(readerVideo,readerData);break;
    case 'copy':copy(transcriptText());break;
    case 'download':download(`${readerVideo.title.replace(/[<>:"/\\|?*]/g,'').slice(0,100)}.txt`,transcriptText(),'text/plain;charset=utf-8');break;
    case 'reader-channel':openChannel(readerVideo.channelId||readerVideo.channelUrl);break;
    case 'share':{const url=new URL(location.href);url.search='';url.hash='';url.searchParams.set('v',readerVideo.id);copy(url.href);break;}
  }
});
$('#search-form').addEventListener('submit',e=>{e.preventDefault();submitInput($('#query').value.trim());});
$('#channel-form').addEventListener('submit',e=>{e.preventDefault();const value=$('#channel-input').value.trim();const kind=classifyInput(value);if(kind.type!=='channel')return toast('Вставь ссылку на канал или @имя.');openChannel(kind.value);});
$('#load-more').addEventListener('click',()=>{if(currentChannel&&channelPage)openChannel(currentChannel.url,channelPage);});
$('#reader-back').addEventListener('click',()=>navigate(backView==='reader'?'home':backView));
$('#paste-focus').addEventListener('click',()=>{$('#query').focus();$('#query').scrollIntoView({block:'center',behavior:'smooth'});});
$('#results-clear').addEventListener('click',()=>{cancelRequest();$('#results-section').hidden=true;$('#discover').hidden=false;$('#query').value='';});
$('#settings-open').addEventListener('click',settings);$('#connect-api').addEventListener('click',settings);
$('#api-form').addEventListener('submit',async e=>{
  e.preventDefault();let value=$('#api-url').value.trim().replace(/\/+$/,'');
  if(value){try{const url=new URL(value);if(url.username||url.password||url.search||url.hash)throw Error();if(url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1'].includes(url.hostname)))throw Error();}catch{$('#api-status').textContent='Нужен HTTPS-адрес сервера без логина, пароля и параметров.';return;}}
  cancelRequest();apiOrigin=value || window.TEKSTOM_API || '';try{localStorage.setItem('tekstom-api',value);}catch{toast('Не удалось запомнить адрес API.');}
  $('#connection-banner').hidden=!unavailableAPI();$('#api-status').textContent='Проверяем соединение…';
  try{const data=await api('/api/health');if(data.service!=='tekstom')throw new Error('По адресу работает другой сервис.');$('#api-status').textContent='Подключено. Можно искать видео и открывать тексты.';$('#connection-banner').hidden=true;}
  catch(error){$('#api-status').textContent=error.message;$('#connection-banner').hidden=false;}
});
$('#export-data').addEventListener('click',()=>download('tekstom-library.json',JSON.stringify({version:1,...library},null,2),'application/json'));
$('#import-data').addEventListener('change',async e=>{
  const file=e.target.files[0];if(!file)return;
  try{if(file.size>10*1024*1024)throw Error('Файл больше 10 МБ.');const data=cleanLibrary(JSON.parse(await file.text()));if(!data)throw Error('Не удалось распознать библиотеку.');library.videos=[...new Map([...library.videos,...data.videos].map(v=>[v.id,v])).values()];library.channels=[...new Map([...library.channels,...data.channels].map(c=>[c.url,c])).values()];persist();if(activeView==='saved')renderSaved();if(activeView==='channels')renderChannels();toast('Библиотека импортирована');}
  catch(error){toast(error.message);}finally{e.target.value='';}
});
counts();$('#connection-banner').hidden=!unavailableAPI();
const initial=new URL(location.href).searchParams.get('v');if(initial&&validVideo({id:initial,title:'',channel:''}))openReader(initial);
