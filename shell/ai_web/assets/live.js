"use strict";
const live = {timer:null, busy:false, generation:0, alertVersion:0, alertController:null, alertPage:1, alerts:null, lastSuccess:null, failures:0, lastServices:0, hidden:{}};
function pauseLive(){ $('live-enabled').checked=false;clearTimeout(live.timer);live.generation++;$('live-label').textContent='Исторический просмотр';$('live-dot').classList.remove('on'); }
function resetLive(){pauseLive();live.alertVersion++;live.alertController?.abort();live.alerts=null;live.lastSuccess=null;$('alert-dialog').close();$('alert-detail').replaceChildren();$('alert-list').replaceChildren();$('alert-count').textContent='—';$('alert-status').textContent='Ожидаем данные OAP.';}
function scheduleLive(){clearTimeout(live.timer);const enabled=$('live-enabled').checked;$('live-label').textContent=enabled?'LIVE · последний час':'Исторический просмотр';$('live-dot').classList.toggle('on',enabled&&!document.hidden);if(enabled&&state.csrf&&!document.hidden)live.timer=setTimeout(refreshLive,Number($('live-interval').value)*1000*Math.min(4,1+live.failures));}
function setInputs(start,end){const value=$('timezone').value;const sign=value[0]==='-'?-1:1;const [h,m]=value.slice(1).split(':').map(Number);const offset=sign*(h*60+m)*60000;for(const[id,date]of [['start',start],['end',end]])$(id).value=new Date(date.getTime()+offset).toISOString().slice(0,16);}
function readAlertPeriod(){const offset=$('timezone').value;return {start:$('start').value+':00'+offset,end:$('end').value+':00'+offset};}
async function refreshLive(){
  if(!state.csrf||document.hidden||live.busy||state.busy){scheduleLive();return;}
  live.busy=true;const generation=live.generation;
  try{
    if($('live-enabled').checked)$('last-hour').click();
    if(Date.now()-live.lastServices>60000){const data=await api('/api/ai/services');if(generation!==live.generation||!state.csrf)return;const selected=$('service').value;const ids=new Set(data.services.map(s=>s.id));if(selected&&!ids.has(selected))throw new Error('Выбранный сервис больше не доступен в GENERAL');$('service').replaceChildren(...data.services.map(s=>{const o=el('option',s.name);o.value=s.id;return o;}));if(selected)$('service').value=selected;live.lastServices=Date.now();$('search').disabled=!$('service').value;}
    if(!$('service').value){const ok=await loadAlerts(readAlertPeriod(),1);if(!ok)throw new Error('Алерты недоступны');$('live-status').textContent='Алерты обновлены; ожидаем сервисы GENERAL';return;}
    const query=readQuery();state.query=query;
    $('live-status').textContent='Обновляем метрики и алерты…';
    const work=[loadDashboard(query,true),loadAlerts(query,1)];
    // Preserve selected trace, SQL, scroll and AI context during background refresh.
    if(!state.selected&&state.page===1){const v=++state.listVersion;state.listController?.abort();const c=new AbortController();state.listController=c;work.push(api('/api/ai/traces?'+new URLSearchParams({...query,page:1}),c).then(data=>{if(generation===live.generation&&v===state.listVersion){state.result=data;renderResults();}return true;}));}
    const result=await Promise.allSettled(work);
    if(generation!==live.generation||!state.csrf)return;
    if(result.some(r=>r.status==='rejected'||r.value!==true))throw new Error('Часть данных недоступна. Сохранён предыдущий результат.');
    live.lastSuccess=new Date();live.failures=0;
    $('live-status').textContent='Обновлено '+live.lastSuccess.toLocaleTimeString('ru-RU')+(state.selected?' · трассировка закреплена':'');
  }catch(e){if(generation===live.generation){live.failures++;$('live-status').textContent='Нет свежих данных · '+(live.lastSuccess?'последнее обновление '+live.lastSuccess.toLocaleTimeString('ru-RU'):'обновление не подтверждено')+' · '+e.message;}}
  finally{live.busy=false;scheduleLive();}
}
async function loadAlerts(query,page=1){
  live.alertController?.abort();const c=new AbortController();live.alertController=c;const v=++live.alertVersion;live.alertPage=page;
  $('alert-prev').disabled=true;$('alert-next').disabled=true;
  try{
    const data=await api('/api/ai/alerts?'+new URLSearchParams({start:query.start,end:query.end,severity:$('alert-severity').value,page,page_size:12}),c);
    if(v!==live.alertVersion||!state.csrf)return false;
    live.alerts=data;renderAlerts();return true;
  }catch(e){if(v===live.alertVersion){$('alert-status').textContent='Алерты недоступны; предыдущий список может быть устаревшим. '+e.message;$('alert-status').classList.add('error');}return false;}
}
function renderAlerts(){
  const d=live.alerts;$('alert-status').classList.remove('error');$('alert-status').textContent='Получено '+new Date(d.fetched_at).toLocaleTimeString('ru-RU')+' · '+dateText(d.period.start)+' → '+dateText(d.period.end_exclusive);
  $('alert-count').textContent=d.records.length+' на странице';$('alert-page').textContent='Страница '+d.pagination.page;
  $('alert-prev').disabled=d.pagination.page<=1;$('alert-next').disabled=!d.pagination.may_have_more;
  const names={CRITICAL:'Критичный',HIGH:'Высокий',WARNING:'Предупреждение',INFO:'Информация',UNKNOWN:'Уровень неизвестен'};
  const cards=d.records.map(a=>{const b=el('button',null,'alert-card '+a.severity.toLowerCase());b.type='button';const h=el('div',null,'alert-heading');h.append(badge(names[a.severity],a.severity==='CRITICAL'?'bad':''),el('time',dateText(a.time),'muted small'));b.append(h,el('strong',a.message),el('span',(a.scope||'Объект')+' · '+a.entity_id,'muted small'));b.onclick=()=>openAlarm(a);return b;});
  if(!cards.length)cards.push(el('div','На этой странице нет срабатываний выбранного уровня. Проверьте период и теги правил; это не подтверждение отсутствия проблем.','empty'));
  $('alert-list').replaceChildren(...cards);
}
function openAlarm(a){
  const box=$('alert-detail');box.replaceChildren(el('h3',a.message),metricLine('Время:',dateText(a.time)),metricLine('Уровень:',a.level_raw||'Не задан'),metricLine('Объект:',a.entity_id),metricLine('Область:',a.scope||'Не указана'),el('pre',JSON.stringify(a.tags,null,2)),el('p','Состояние устранения неизвестно. Событие сообщает о срабатывании правила.','callout'));
  // Match only an exact service entity; do not infer an API/SQL or service from message text.
  const known=a.scope==='Service'&&Array.from($('service').options).some(o=>o.value===a.entity_id);
  if(!known)box.append(el('p','Сервис не сопоставлен автоматически. Для исследования используется сервис, выбранный в фильтре.','muted small'));
  $('alert-investigate').disabled=!$('service').value&&!known;
  $('alert-investigate').onclick=()=>{pauseLive();if(known)$('service').value=a.entity_id;const t=Math.floor(a.time_ms/60000)*60000;setInputs(new Date(t-5*60000),new Date(t+6*60000));$('alert-dialog').close();search(1,true);$('explorer').scrollIntoView({behavior:'smooth'});};
  $('alert-dialog').showModal();
}
function drawChart(id,points,series,unit){
  const box=$(id);box.replaceChildren();const hidden=live.hidden[id]||new Set();live.hidden[id]=hidden;
  const toggles=el('div',null,'chart-toggles');for(const[key,color,label]of series){const b=el('button',label,'subtle');b.type='button';b.setAttribute('aria-pressed',String(!hidden.has(key)));b.style.borderBottom='2px solid '+color;b.onclick=()=>{hidden.has(key)?hidden.delete(key):hidden.add(key);drawChart(id,points,series,unit);};toggles.append(b);}box.append(toggles);
  const visible=series.filter(([k])=>!hidden.has(k)),numbers=points.flatMap(p=>visible.map(([k])=>p[k])).filter(v=>v!==null&&Number.isFinite(v));
  if(!numbers.length){box.append(el('div',visible.length?'Нет наблюдений с положительной нагрузкой':'Выберите ряд на графике','empty'));return;}
  const max=Math.max(...numbers,1),W=600,H=220,L=50,R=12,T=15,B=33,x=i=>L+i/Math.max(1,points.length-1)*(W-L-R),y=v=>H-B-v/max*(H-T-B);
  const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'group',tabindex:0,'aria-label':`${unit}. Стрелки: выбрать минуту. Enter: исследовать интервал.`});
  for(let j=0;j<=3;j++){const value=max*j/3,yy=y(value);svg.append(svgNode('line',{x1:L,y1:yy,x2:W-R,y2:yy,class:'grid-line'}));const n=svgNode('text',{x:L-8,y:yy+4,'text-anchor':'end',class:'axis-label'});n.textContent=value>=1000?(value/1000).toFixed(1)+'k':Number(value.toFixed(1));svg.append(n);}
  for(const[key,color]of visible){let path='',active=false;for(let i=0;i<points.length;i++){const v=points[i][key];if(v===null||!Number.isFinite(v)){active=false;continue;}path+=(active?'L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)+' ';active=true;}svg.append(svgNode('path',{d:path,fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round',class:'metric-path'}));}
  for(const i of new Set([0,Math.floor((points.length-1)/2),points.length-1])){const n=svgNode('text',{x:x(i),y:H-9,'text-anchor':i===0?'start':i===points.length-1?'end':'middle',class:'axis-label'});n.textContent=points[i].time.slice(11,16);svg.append(n);}
  const cross=svgNode('line',{x1:L,y1:T,x2:L,y2:H-B,class:'crosshair'});svg.append(cross);
  const tip=el('div','Наведите для значений · нажмите для исследования ±2 минуты','chart-tooltip');let index=0;
  function focus(i){index=Math.max(0,Math.min(points.length-1,i));const p=points[index];cross.setAttribute('x1',x(index));cross.setAttribute('x2',x(index));tip.textContent=dateText(p.time)+' · '+visible.map(([key,,label])=>`${label}: ${p[key]===null?'нет данных':Number(p[key]).toLocaleString('ru-RU')} ${unit}`).join(' · ');}
  function select(){pauseLive();const t=Date.parse(points[index].time);setInputs(new Date(t-120000),new Date(t+180000));search(1,true);$('explorer').scrollIntoView({behavior:'smooth'});}
  svg.addEventListener('pointermove',e=>{const r=svg.getBoundingClientRect();focus(Math.round(((e.clientX-r.left)/r.width*W-L)/(W-L-R)*(points.length-1)));});svg.addEventListener('click',select);svg.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Enter'].includes(e.key)){e.preventDefault();if(e.key==='Enter')select();else focus(index+(e.key==='ArrowRight'?1:-1));}});box.append(svg,tip);
}
$('live-enabled').onchange=()=>{live.generation++;if($('live-enabled').checked)refreshLive();else pauseLive();scheduleLive();};$('live-interval').onchange=scheduleLive;$('refresh-now').onclick=refreshLive;
for(const id of ['start','end','timezone','service','threshold','errors-only'])$(id).addEventListener('change',()=>{pauseLive();state.dashboardVersion++;state.dashboardController?.abort();live.alertVersion++;live.alertController?.abort();state.listVersion++;state.listController?.abort();setBusy(false);$('live-status').textContent='Фильтры изменены — нажмите «Обновить обзор»';});
$('whole-day').addEventListener('click',pauseLive);
$('alert-severity').onchange=()=>{loadAlerts(state.query||readAlertPeriod(),1);};
$('alert-prev').onclick=()=>{pauseLive();if(state.query)loadAlerts(state.query,live.alertPage-1);};$('alert-next').onclick=()=>{pauseLive();if(state.query)loadAlerts(state.query,live.alertPage+1);};$('alert-close').onclick=()=>$('alert-dialog').close();
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(live.timer);$('live-dot').classList.remove('on');$('live-status').textContent='Автообновление приостановлено: вкладка скрыта';}else if($('live-enabled').checked)refreshLive();});

(async()=>{try{const session=await api('/api/ai/auth/session');state.csrf=session.csrf_token;await initialize();}catch{showLogin();}})();
