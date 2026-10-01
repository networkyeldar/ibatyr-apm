"use strict";
const $ = id => document.getElementById(id);
const state = {view: 'monitor', license: null, licenseVersion: 0, page: 1, query: null, result: null, listVersion: 0, detailVersion: 0, selected: null, detail: null, tab: 'sql', busy: false, listController: null, detailController: null, csrf: '', providers: [], provider: 'external', aiVersion: 0, snapshot: null, dashboardVersion: 0, dashboardController: null};
function el(tag, text, className) { const n = document.createElement(tag); if(text !== undefined && text !== null) n.textContent = String(text); if(className) n.className = className; return n; }
function duration(ms) { if(!Number.isFinite(Number(ms))) return '—'; ms=Number(ms); if(ms<1000) return `${ms.toLocaleString('ru-RU')} мс`; if(ms<60000) return `${(ms/1000).toLocaleString('ru-RU',{maximumFractionDigits:3})} с`; return `${Math.floor(ms/60000)} мин ${(ms%60000/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} с`; }
function dateText(iso) { return iso ? String(iso).replace('T',' ').replace(/\.\d+/, '') : '—'; }
function status(text, error=false) { $('search-message').textContent=text; $('search-message').className='message'+(error?' error':''); }
function badge(text, kind='') { return el('span',text,'pill '+kind); }
async function api(path, controller = new AbortController(), options = {}) {
  const timer=setTimeout(()=>controller.abort(),path.includes('/llm/')?165000:45000);
  try {
    const response=await fetch(path,{...options,signal:controller.signal,cache:'no-store',headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(state.csrf?{'X-CSRF-Token':state.csrf}:{}),...(options.headers||{})}});
    const data=await response.json();
    if(response.status===401 && path!='/api/ai/auth/login') showLogin();
    if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));
    return data;
  } finally { clearTimeout(timer); }
}
function setBusy(value) { state.busy=value; $('search').disabled=value||(state.view!=='alerts'&&!$('service').value); $('search').textContent=value?'Загрузка…':state.view==='alerts'?'Обновить алерты':state.view==='jvm'?'Обновить JVM':'Обновить обзор'; updatePager(); }
function updatePager() { $('previous').disabled=state.busy||!state.result||state.page<=1; $('next').disabled=state.busy||!state.result||!state.result.pagination.may_have_more; $('page-label').textContent=`Страница ${state.page}`; }
function invalidateDetails() { $('export-trace').disabled=true;invalidateAI(); state.detailController?.abort(); state.detailVersion++; state.detail=null; state.selected=null; syncAI(); $('detail').replaceChildren(el('div','Выберите вызов слева','empty detail-empty')); }
function readPeriod() {
  const start=$('start').value, end=$('end').value, offset=$('timezone').value;
  const addSeconds = value => value.length===16?value+':00':value;
  const from=addSeconds(start)+offset, until=addSeconds(end)+offset;
  const delta=Date.parse(until)-Date.parse(from);
  if(!Number.isFinite(delta)||delta<=0||delta>86400000) throw new Error('Выберите интервал от одной секунды до 24 часов.');
  return {start:from,end:until};
}
function readQuery() {
  const period=readPeriod();
  const threshold=Number($('threshold').value);
  if(!Number.isInteger(threshold)||threshold<0) throw new Error('Порог должен быть целым неотрицательным числом.');
  if(!$('service').value) throw new Error('Выберите сервис.');
  return {...period,service_id:$('service').value,min_duration_ms:threshold,errors_only:$('errors-only').checked,page_size:20};
}
async function search(page, newQuery=false) {
  let query;
  try { query=newQuery?readQuery():state.query; if(!query) return; } catch(error) { status(error.message,true);$('live-status').textContent=error.message;return; }
  state.listController?.abort(); const controller=new AbortController(); state.listController=controller;
  const version=++state.listVersion; invalidateDetails(); state.page=page; state.query=query; state.result=null; $('records').replaceChildren(); $('sample-stats').replaceChildren(); $('list-warnings').replaceChildren(); $('period').textContent='Выполняется поиск…';
  setBusy(true); if(newQuery) {loadDashboard(query);} status('Загружаем сохранённые сегменты…');
  try {
    const params=new URLSearchParams({...query,page});
    const data=await api('/api/ai/traces?'+params,controller); if(version!==state.listVersion) return;
    state.result=data; $('endpoint-filter').value=''; renderResults();
    status(data.records.length?'Выберите операцию, чтобы увидеть SQL и ошибки.':'В выбранной странице нет записей. Это не доказывает отсутствие задержек.');
  } catch(error) { if(version!==state.listVersion) return; $('period').textContent='Данные не загружены'; status(error.name==='AbortError'?'Время ожидания истекло. Повторите поиск или сократите интервал.':error.message,true); }
  finally { if(version===state.listVersion) setBusy(false); }
}
function renderResults() {
  const data=state.result; if(!data) return;
  const records=data.records, ids=new Set(records.flatMap(r=>r.trace_ids||[]));
  const metrics=[['Сегментов на странице',records.length],['Максимум на странице',records.length?duration(Math.max(...records.map(r=>r.duration_ms))):'—'],['С ошибками на странице',records.filter(r=>r.has_error===true).length],['Trace ID на странице',ids.size]];
  $('sample-stats').replaceChildren(...metrics.map(([title,value])=>{const n=el('div',null,'stat');n.append(el('div',title,'stat-label'),el('div',value,'stat-value'));return n;}));
  $('period').textContent=dateText(data.period.start)+' → '+dateText(data.period.end_exclusive)+' · конец не включён';
  $('list-warnings').replaceChildren(...(data.warnings||[]).map(w=>el('p',w)));
  renderRows(); updatePager();
}
function renderRows() {
  if(!state.result) return;
  const keyword=$('endpoint-filter').value.toLowerCase();
  const records=state.result.records.filter(r=>(r.endpoints||[]).join(' ').toLowerCase().includes(keyword));
  const rows=records.map(r=>{
    const row=el('tr',null,'record'+(state.selected===r.segment_id?' selected':''));
    const first=el('td'); const button=el('button',(r.endpoints||[]).join(' · ')||'Без имени','operation');
    button.disabled=!(r.trace_ids||[]).length; button.addEventListener('click',()=>{state.selected=r.segment_id;renderRows();openTrace(r.trace_ids[0],r.trace_ids);});
    first.append(button,el('div',dateText(r.start),'record-date'));
    row.append(first,el('td',duration(r.duration_ms),'duration'),el('td',r.has_error===true?'Есть':r.has_error===false?'Не отмечены':'Неизвестно',r.has_error?'bad-text':'muted'));return row;
  });
  if(!rows.length){const row=el('tr'),cell=el('td','Нет записей по выбранным условиям.','empty');cell.colSpan=3;row.append(cell);rows.push(row);}
  $('records').replaceChildren(...rows);
}
async function openTrace(id, ids=[id]) {
  state.detailController?.abort(); const controller=new AbortController(); state.detailController=controller; const version=++state.detailVersion;
  invalidateAI(); $('detail').replaceChildren(el('div','Загружаем трассировку…','empty')); state.detail=null; syncAI();
  try { const data=await api('/api/ai/traces/'+encodeURIComponent(id),controller); if(version!==state.detailVersion) return; state.detail=data; state.tab='sql'; syncAI(); renderDetail(ids); }
  catch(error) { if(version!==state.detailVersion)return; $('detail').replaceChildren(el('div',error.name==='AbortError'?'Время ожидания истекло. Выберите вызов повторно.':error.message,'empty bad-text')); }
}
function metricLine(label,value,kind='') { const n=el('div',null,kind);n.append(el('span',label+' '),el('strong',value));return n; }
function renderDetail(ids) {
  const data=state.detail, body=el('div',null,'detail-body');
  const first=data.entries?.[0]; body.append(el('h3',first?.operation||'Трассировка без входящего HTTP-вызова','detail-title'),el('div',data.trace_id,'trace-id'));
  const meta=el('div',null,'detail-meta');meta.append(badge((data.services||[]).join(', ')),badge(`${data.span_count} spans`),badge(`${data.database_span_count} операций БД`),badge(`${data.error_span_count} spans с ошибкой`,data.error_span_count?'bad':''));body.append(meta);
  if(ids.length>1){const options=el('div',null,'trace-options');ids.forEach((id,i)=>{const b=el('button',`Trace ${i+1}`);b.disabled=id===data.trace_id;b.onclick=()=>openTrace(id,ids);options.append(b);});body.append(options);}
  if(!data.has_http_entry_in_trace)body.append(el('div','Вызывающий API неизвестен: в полученных данных нет входящего HTTP span.','callout'));
  for(const entry of data.entries||[]) {
    const card=el('div',null,'coverage-card');card.append(el('h3',entry.operation||'Входящая операция'));
    card.append(metricLine('Длительность:',duration(entry.duration_ms)));
    const meta=el('div',null,'detail-meta');meta.append(badge(`HTTP ${entry.http_status??'не указан'}`),badge(`Входящий span: ${entry.entry_has_error===true?'ошибка':entry.entry_has_error===false?'без отметки ошибки':'неизвестно'}`));card.append(meta);
    const covered=entry.covered_by_children_ms, total=entry.duration_ms, unknown=entry.not_covered_by_children_ms;
    const bar=el('div',null,'coverage-bar'), a=el('div',null,'covered'), b=el('div',null,'uncovered');
    a.style.width=(total>0?Math.max(0,Math.min(100,covered/total*100)):0)+'%';b.style.flex='1';bar.append(a,b);card.append(bar);
    const legend=el('div',null,'legend');legend.append(el('span',`Записанные дочерние операции: ${duration(covered)} · ${total?(covered/total*100).toFixed(1):'0'}%`),el('span',`Не покрыто дочерними spans: ${duration(unknown)} · ${total?(unknown/total*100).toFixed(1):'0'}%`));card.append(legend);
    card.append(el('p','Непокрытое время не является измерением CPU. Учитываются прямые дочерние spans того же сегмента.','muted small'));
    const gaps=el('ul',null,'gap-list');for(const gap of entry.longest_uncovered_intervals||[])gaps.append(el('li',`${dateText(gap.start)} → ${dateText(gap.end)} · ${duration(gap.duration_ms)}`));if(gaps.childNodes.length)card.append(gaps);body.append(card);
  }
  const tabs=el('div',null,'tabs'), content=el('div');
  const views=[['sql','SQL / БД'],['errors','Ошибки'],['timeline','Все операции']];
  for(const [id,label] of views){const button=el('button',label,state.tab===id?'active':'');button.setAttribute('aria-pressed',String(state.tab===id));button.onclick=()=>{state.tab=id;for(const n of tabs.children){const active=n===button;n.classList.toggle('active',active);n.setAttribute('aria-pressed',String(active));}renderDetailTab(content);};tabs.append(button);}
  body.append(tabs,content);renderDetailTab(content);
  const warnings=el('details');warnings.append(el('summary','Ограничения данных'));for(const w of data.warnings||[])warnings.append(el('p',w));body.append(warnings);$('detail').replaceChildren(body);
}
function renderDetailTab(container) {
  const spans=state.detail.spans||[];container.replaceChildren();
  if(state.tab==='timeline') {
    const wrap=el('div',null,'table-scroll'), table=el('table',null,'all-spans'), head=el('thead'), hr=el('tr');['Начало / span','Операция','Время'].forEach(s=>hr.append(el('th',s)));head.append(hr);table.append(head);const tbody=el('tbody');
    for(const span of spans){const row=el('tr');row.append(el('td',`${dateText(span.start)} · #${span.span_id}`),el('td',`${span.type||''} · ${span.operation||'—'}`),el('td',duration(span.duration_ms)));row.id=spanDomId(span);tbody.append(row);}table.append(tbody);wrap.append(table);container.append(wrap,el('p','Вложенные длительности могут перекрываться. Не складывайте их как полное время запроса.','muted small'));return;
  }
  const chosen=spans.filter(s=>state.tab==='sql'?s.is_database:(s.has_error||(s.error_events||[]).length)).sort((a,b)=>b.duration_ms-a.duration_ms);
  if(!chosen.length){container.append(el('div',state.tab==='sql'?'В полученных spans нет операций БД.':'Ошибки в полученных spans не отмечены.','empty'));return;}
  for(const span of chosen){const card=el('div',null,'span-card'), head=el('div',null,'span-head');head.append(el('strong',span.operation||'Операция'),el('span',duration(span.duration_ms),'duration'));card.append(head,el('div',`${span.service||'—'} · ${span.database||'—'} · ${span.peer||'—'}`,'span-peer'),el('div',`Экземпляр: ${span.instance||'—'} · span #${span.span_id}`,'span-peer'),el('div',dateText(span.start),'span-peer'));
    if(span.sql)card.append(el('pre',span.sql));else if(span.is_database)card.append(el('p','Текст SQL не записан.','muted small'));
    for(const event of span.error_events||[]){const error=el('div',null,'error-box');error.append(el('strong',event.kind||'Ошибка'),el('div',event.message||'Сообщение отсутствует'));card.append(error);}if(span.has_error&&!(span.error_events||[]).length)card.append(el('div','Span отмечен ошибкой; подробности не записаны.','error-box'));
    card.id=spanDomId(span);container.append(card);
  }
}
$('search-form').addEventListener('submit',event=>{event.preventDefault();pauseLive();cancelViewRequests();refreshCurrent();});
$('endpoint-filter').addEventListener('input',renderRows);
$('previous').onclick=()=>{pauseLive();search(state.page-1);};
$('next').onclick=()=>{pauseLive();search(state.page+1);};
$('whole-day').onclick=()=>applyPreset('day');
$('last-day').onclick=()=>applyPreset('24h');

function spanDomId(span){return 'span-'+span.segment_id+'-'+span.span_id;}
function showLogin(){resetLive();state.csrf='';state.license=null;state.licenseVersion++;$('license-dialog').close();$('license-file').value='';$('license-details').replaceChildren();$('license-banner').hidden=true;state.listVersion++;state.dashboardVersion++;state.listController?.abort();state.dashboardController?.abort();invalidateDetails();$('application').hidden=true;$('login-screen').hidden=false;for(const id of ['records','stats','sample-stats','ai-result'])$(id).replaceChildren();$('provider-key').value='';$('preview-json').textContent='';$('preview-system').textContent='';for(const id of ['settings-dialog','preview-dialog'])if($(id).open)$(id).close();}
async function initialize(){
  $('login-screen').hidden=true;$('application').hidden=false;
  await refreshLicense();
  try{const [data,profiles]=await Promise.all([api('/api/ai/services'),api('/api/ai/llm/providers')]);$('service').replaceChildren(...data.services.map(s=>{const option=el('option',s.name);option.value=s.id;return option;}));state.providers=profiles.providers;if(!data.services.length){if(state.view==='alerts')await refreshAlerts();scheduleLive();throw new Error('Нет сервисов GENERAL. Алерты по другим объектам доступны; ожидаем трафик агента.');}$('connection').textContent='Сервисы доступны';$('connection').className='pill good';$('search').disabled=false;syncAI();await refreshCurrent();scheduleLive();}
  catch(error){$('connection').textContent='Нет подключения';$('connection').className='pill bad';status(error.message,true);}
}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();$('login-button').disabled=true;$('login-error').textContent='';try{const data=await api('/api/ai/auth/login',new AbortController(),{method:'POST',body:JSON.stringify({username:$('login-name').value,password:$('login-password').value})});state.csrf=data.csrf_token;$('login-password').value='';await initialize();}catch(error){$('login-error').textContent=error.message;}finally{$('login-button').disabled=false;}});
$('logout').onclick=async()=>{try{await api('/api/ai/auth/logout',new AbortController(),{method:'POST'});showLogin();}catch(error){status(error.message,true);}};
$('theme-toggle').onclick=()=>{const light=document.documentElement.dataset.theme!=='light';document.documentElement.dataset.theme=light?'light':'dark';try{localStorage.setItem('swai_theme',light?'light':'dark');}catch{}};
try{document.documentElement.dataset.theme=localStorage.getItem('swai_theme')||'dark';}catch{}
function setRollingPeriod(minutes=60){const end=new Date(Math.floor((Date.now()-120000)/60000)*60000);setInputs(new Date(end.getTime()-minutes*60000),end);}
$('last-hour').onclick=()=>applyPreset('hour');

async function loadDashboard(query,quiet=false){
  state.dashboardController?.abort();const controller=new AbortController();state.dashboardController=controller;const version=++state.dashboardVersion;
  if(!quiet){$('stats').replaceChildren(el('div','Загружаем метрики за интервал…','empty'));['latency-chart','traffic-chart','errors-chart'].forEach(id=>$(id).replaceChildren(el('div','Загрузка…','empty')));$('metric-warnings').replaceChildren();}
  try{const params=new URLSearchParams({start:query.start,end:query.end,service_id:query.service_id});const data=await api('/api/ai/dashboard?'+params,controller);if(version!==state.dashboardVersion)return;
    const k=data.kpis;const metrics=[['Оценка числа вызовов',k.estimated_calls===null?'—':Math.round(k.estimated_calls).toLocaleString('ru-RU'),'По минутам с положительным CPM'],['Средняя задержка ≈',k.estimated_mean_latency_ms===null?'—':duration(k.estimated_mean_latency_ms),'Взвешенная оценка по CPM'],['Макс. минутный P95',k.max_minute_p95_ms===null?'—':duration(k.max_minute_p95_ms),'Не P95 за весь период'],['Оценка доли ошибок',k.estimated_error_rate_percent===null?'—':k.estimated_error_rate_percent.toLocaleString('ru-RU')+'%','По метрике успешности сервиса']];
    $('stats').replaceChildren(...metrics.map(([title,value,note])=>{const card=el('div',null,'stat');card.append(el('div',title,'stat-label'),el('div',value,'stat-value'),el('div',note,'stat-foot'));return card;}));
    drawChart('latency-chart',data.points,[['mean_latency_ms','#48bfae','Среднее'],['p95_ms','#a996f5','P95']],'мс');drawChart('traffic-chart',data.points,[['calls_per_minute','#48bfae','Вызовы']],'выз/мин');drawChart('errors-chart',data.points,[['error_rate_percent','#f08b99','Ошибки']],'%');
    $('metric-warnings').replaceChildren(el('p',`Положительная нагрузка: ${data.coverage.minutes_with_positive_traffic} из ${data.coverage.requested_minutes} минут.`),...(data.warnings||[]).map(w=>el('p',w)));if(!quiet){live.lastSuccess=new Date();$('live-status').textContent='Метрики получены '+live.lastSuccess.toLocaleTimeString('ru-RU');}return true;
  }catch(error){if(version!==state.dashboardVersion)return;if(quiet)return false;$('live-status').textContent='Метрики не загружены: '+(error.name==='AbortError'?'Истекло время ожидания':error.message);$('stats').replaceChildren(el('div','Метрики недоступны: '+error.message,'empty bad-text'));['latency-chart','traffic-chart','errors-chart'].forEach(id=>$(id).replaceChildren(el('div','Нет данных','empty')));}
}
function svgNode(tag,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;}

function currentProvider(){return state.providers.find(p=>p.id===state.provider);}
function invalidateAI(){state.aiVersion++;if($('trace-pdf'))$('trace-pdf').hidden=true;state.snapshot=null;$('ai-result')?.replaceChildren(el('div','Выберите трассировку и нажмите «Анализировать трассировку».','empty'));if($('ai-message'))$('ai-message').textContent='';if($('ai-prepare'))$('ai-prepare').disabled=true;if($('preview-dialog')?.open)$('preview-dialog').close();}
function syncAI(){const p=currentProvider();$('choose-external').classList.toggle('active',state.provider==='external');$('choose-local').classList.toggle('active',state.provider==='local');$('provider-status').textContent=p?.configured?(state.provider==='local'?'Локальный · настроен':'Внешний · настроен'):'Требуется настройка';$('provider-status').className='pill '+(p?.configured?'good':'');$('ai-prepare').disabled=!state.detail||!p?.configured||!state.license?.ai_enabled||!!state.aiBusy;$('export-trace').disabled=!state.detail;$('ai-license-hint').textContent=state.license?.ai_enabled?'':(state.license?.message||'Проверяем лицензию…');$('ai-target').textContent=state.detail?`Trace: ${state.detail.trace_id} · ${p?.model||'модель не задана'}`:'Сначала выберите трассировку';syncReports();}
for(const name of ['external','local'])$('choose-'+name).onclick=()=>{if(state.provider===name)return;state.provider=name;invalidateAI();syncAI();};
function fillProvider(){const p=state.providers.find(p=>p.id===$('settings-provider').value)||{};$('provider-url').value=p.base_url||'';$('provider-model').value=p.model||'';$('provider-auth').value=p.auth_mode||'bearer';$('provider-key').value='';$('provider-token-param').value=p.token_parameter||'max_tokens';$('provider-response-format').value=p.response_format||'auto';$('provider-tokens').value=p.max_tokens||2048;$('key-status').textContent=p.has_key?'Ключ сохранён. Пустое поле оставляет его прежним, если адрес не меняется.':'Ключ не сохранён.';$('settings-message').textContent='';}
function openSettings(){$('settings-provider').value=state.provider;fillProvider();$('settings-dialog').showModal();}
$('settings-open').onclick=openSettings;$('settings-nav').onclick=openSettings;$('settings-close').onclick=()=>$('settings-dialog').close();$('settings-dialog').addEventListener('close',()=>{$('provider-key').value='';});$('settings-provider').onchange=fillProvider;
$('provider-form').addEventListener('submit',async event=>{event.preventDefault();const id=$('settings-provider').value;$('provider-save').disabled=true;$('settings-message').textContent='Сохраняем…';try{const data=await api('/api/ai/llm/providers/'+id,new AbortController(),{method:'PUT',body:JSON.stringify({base_url:$('provider-url').value,model:$('provider-model').value,auth_mode:$('provider-auth').value,api_key:$('provider-key').value||null,token_parameter:$('provider-token-param').value,response_format:$('provider-response-format').value,max_tokens:Number($('provider-tokens').value)})});state.providers=state.providers.map(p=>p.id===id?data:p);$('provider-key').value='';$('key-status').textContent=data.has_key?'Ключ сохранён на сервере.':'Авторизация без ключа.';$('settings-message').textContent='Сохранено. Теперь проверьте подключение.';invalidateAI();syncAI();}catch(error){$('settings-message').textContent=error.message;}finally{$('provider-save').disabled=false;}});
$('provider-test').onclick=async()=>{$('provider-test').disabled=true;$('settings-message').textContent='Проверяем сохранённые настройки…';try{const result=await api('/api/ai/llm/providers/'+$('settings-provider').value+'/test',new AbortController(),{method:'POST'});$('settings-message').textContent='Подключение работает: '+result.model;}catch(error){$('settings-message').textContent=error.message;}finally{$('provider-test').disabled=false;}};
const licenseNames={active:'Активна',grace:'Льготный период',expired:'Истекла',unlicensed:'Не активирована',not_configured:'Нужен ключ издателя',invalid:'Недействительна',clock_error:'Проверьте время',state_error:'Ошибка состояния',wrong_installation:'Другая установка',not_yet_valid:'Ещё не действует'};
function renderLicense(){const data=state.license;if(!data)return;const box=$('license-details');box.replaceChildren();const summary=el('div',null,'license-summary');summary.append(el('span',licenseNames[data.status]||data.status,'pill '+(data.ai_enabled?'good':'bad')),el('h3',data.license?.customer||'iBatyr APM'),el('p',data.message,'muted'));box.append(summary);const rows=[['Тариф',data.license?.edition||'Базовый просмотр'],['Установка',data.installation_id||'—'],['Окончание',data.license?new Date(data.license.expires_at*1000).toLocaleString('ru-RU'):'—'],['AI-анализ',data.ai_enabled?'Доступен':'Недоступен']];for(const[label,value]of rows){const row=el('div',null,'license-row');row.append(el('span',label,'muted'),el('strong',value));box.append(row);}const warning=!data.ai_enabled||data.status==='grace'||data.days_remaining<=7;$('license-banner').hidden=!warning;$('license-banner').replaceChildren(el('span',data.message));const button=el('button','Управление лицензией','subtle');button.onclick=openLicense;$('license-banner').append(button);$('license-open').textContent='◇ '+(data.license?.edition||'Лицензия');syncAI();}
async function refreshLicense(){const version=++state.licenseVersion;try{const result=await api('/api/ai/license');if(version!==state.licenseVersion||!state.csrf)return;state.license=result;renderLicense();}catch(error){if(version!==state.licenseVersion||!state.csrf)return;state.license={status:'state_error',ai_enabled:false,message:'Не удалось проверить лицензию: '+error.message};renderLicense();}}
async function openLicense(){$('license-message').textContent='';$('license-dialog').showModal();await refreshLicense();try{const data=await api('/api/ai/license/history');if(!state.csrf)return;$('license-history').replaceChildren(...data.events.map(e=>el('p',new Date(e.time*1000).toLocaleString('ru-RU')+' · '+e.edition+' · '+e.license_id)));if(!data.events.length)$('license-history').textContent='Активаций пока нет.';}catch{}}
$('license-open').onclick=openLicense;$('license-nav').onclick=openLicense;$('license-close').onclick=()=>$('license-dialog').close();$('license-refresh').onclick=refreshLicense;
$('license-request').onclick=async()=>{try{const data=await api('/api/ai/license/request');if(!data.installation_id)throw new Error('Не удалось получить ID установки');downloadJSON('ibatyr-activation-request.json',data);}catch(e){$('license-message').textContent=e.message;}};
$('license-form').onsubmit=async event=>{event.preventDefault();const file=$('license-file').files[0];if(!file)return;if(file.size>20000){$('license-message').textContent='Файл лицензии должен быть не больше 20 КБ.';return;}$('license-activate').disabled=true;const session=state.csrf;try{const result=await api('/api/ai/license/activate',new AbortController(),{method:'POST',body:JSON.stringify({document:await file.text()})});if(!state.csrf||state.csrf!==session)return;state.licenseVersion++;state.license=result;invalidateAI();renderLicense();$('license-message').textContent='Лицензия активирована.';$('license-file').value='';}catch(e){if(state.csrf===session)$('license-message').textContent=e.message;}finally{$('license-activate').disabled=false;}};
$('export-trace').onclick=()=>{if(state.detail)downloadJSON('ibatyr-trace.json',state.detail);};
setInterval(()=>{if(state.csrf&&!document.hidden)refreshLicense();},60000);


