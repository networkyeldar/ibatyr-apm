"use strict";
const live = {timer:null, busy:false, generation:0, alertVersion:0, alertController:null, alertPage:1, alerts:null, lastSuccess:null, failures:0, lastServices:0, hidden:{}};
function pauseLive(){ $('live-enabled').checked=false;clearTimeout(live.timer);live.generation++;$('live-label').textContent='Исторический просмотр';$('live-dot').classList.remove('on'); }
function resetLive(){pauseLive();live.alertVersion++;live.alertController?.abort();$('alerts-refresh').disabled=false;$('alerts-panel').setAttribute('aria-busy','false');live.alerts=null;live.lastSuccess=null;$('alert-dialog').close();$('alert-detail').replaceChildren();$('alert-list').replaceChildren();$('alert-count').textContent='—';$('alert-status').textContent='Ожидаем данные OAP.';}
function scheduleLive(){clearTimeout(live.timer);const enabled=$('live-enabled').checked;$('live-label').textContent=enabled?'LIVE · последний час':'Исторический просмотр';$('live-dot').classList.toggle('on',enabled&&!document.hidden);if(enabled&&state.csrf&&!document.hidden)live.timer=setTimeout(refreshLive,Number($('live-interval').value)*1000*Math.min(4,1+live.failures));}
function setInputs(start,end){const value=$('timezone').value;const sign=value[0]==='-'?-1:1;const [h,m]=value.slice(1).split(':').map(Number);const offset=sign*(h*60+m)*60000;for(const[id,date]of [['start',start],['end',end]])$(id).value=new Date(date.getTime()+offset).toISOString().slice(0,16);}
function readAlertPeriod(){return readPeriod();}
async function refreshLive(){
  if(!state.csrf||document.hidden||live.busy||state.busy){scheduleLive();return;}
  live.busy=true;const generation=live.generation;
  try{
    if($('live-enabled').checked)setRollingPeriod(60);
    if(state.view==='alerts'){const ok=await refreshAlerts();if(generation!==live.generation||!state.csrf)return;if(!ok)throw new Error('Алерты недоступны');live.lastSuccess=new Date();live.failures=0;$('live-status').textContent='Алерты обновлены '+live.lastSuccess.toLocaleTimeString('ru-RU');return;}
    if(Date.now()-live.lastServices>60000){const data=await api('/api/ai/services');if(generation!==live.generation||!state.csrf)return;const selected=$('service').value;const ids=new Set(data.services.map(s=>s.id));if(selected&&!ids.has(selected))throw new Error('Выбранный сервис больше не доступен в GENERAL');$('service').replaceChildren(...data.services.map(s=>{const o=el('option',s.name);o.value=s.id;return o;}));if(selected)$('service').value=selected;live.lastServices=Date.now();$('search').disabled=!$('service').value;}
    if(!$('service').value){$('live-status').textContent='Ожидаем сервисы GENERAL';return;}
    const query=readQuery();state.query=query;
    $('live-status').textContent='Обновляем метрики…';
    const work=[loadDashboard(query,true)];
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
  $('alert-prev').disabled=true;$('alert-next').disabled=true;$('alerts-refresh').disabled=true;$('alerts-panel').setAttribute('aria-busy','true');$('alert-status').textContent='Загружаем алерты за выбранный период…';
  try{
    const data=await api('/api/ai/alerts?'+new URLSearchParams({start:query.start,end:query.end,severity:$('alert-severity').value,page,page_size:12}),c);
    if(v!==live.alertVersion||!state.csrf)return false;
    live.alerts=data;renderAlerts();return true;
  }catch(e){if(v===live.alertVersion){$('alert-status').textContent='Алерты недоступны; предыдущий список может быть устаревшим. '+e.message;$('alert-status').classList.add('error');}return false;}
  finally{if(v===live.alertVersion){$('alerts-refresh').disabled=false;$('alerts-panel').setAttribute('aria-busy','false');}}
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
  $('alert-investigate').onclick=()=>{pauseLive();if(known)$('service').value=a.entity_id;const t=Math.floor(a.time_ms/60000)*60000;setInputs(new Date(t-5*60000),new Date(t+6*60000));$('alert-dialog').close();openMonitor();search(1,true);$('explorer').scrollIntoView({behavior:'smooth'});};
  $('alert-dialog').showModal();
}
function drawChart(id,points,series,unit){
  const box=$(id);box.replaceChildren();
  const hidden=live.hidden[id]||new Set();live.hidden[id]=hidden;
  const valid=v=>typeof v==='number'&&Number.isFinite(v);
  const numeric=v=>Number(v.toFixed(2)).toLocaleString('ru-RU');
  function format(v,axis=false){
    if(!valid(v))return 'Нет данных';
    if(unit==='мс')return v>=60000?numeric(v/60000)+' мин':v>=1000?numeric(v/1000)+' с':numeric(v)+' мс';
    if(unit==='%')return numeric(v)+'%';
    return (axis&&v>=1000?numeric(v/1000)+' тыс.':numeric(v))+(axis?'':' '+unit);
  }
  const latest=points.at(-1);
  const toggles=el('div',null,'chart-toggles');
  for(const[key,color,label]of series){
    const b=el('button',null,'chart-series');b.type='button';b.setAttribute('aria-pressed',String(!hidden.has(key)));b.style.setProperty('--series-color',color);
    b.append(el('span',label,'chart-series-label'),el('strong',format(latest?.[key]),'chart-series-value'));
    b.title='Последняя минута интервала · нажмите, чтобы скрыть или показать ряд';
    b.onclick=()=>{hidden.has(key)?hidden.delete(key):hidden.add(key);drawChart(id,points,series,unit);};toggles.append(b);
  }
  box.append(toggles,el('div',latest?'Последняя минута · '+dateText(latest.time):'Нет наблюдений','chart-latest-time'));
  const visible=series.filter(([k])=>!hidden.has(k));
  const numbers=points.flatMap(p=>visible.map(([k])=>p[k])).filter(valid);
  if(!numbers.length){box.append(el('div',visible.length?'Нет наблюдений с положительной нагрузкой':'Выберите ряд на графике','empty'));return;}
  const peak=Math.max(...numbers,1);
  const rawStep=peak/4,magnitude=10**Math.floor(Math.log10(rawStep));
  const step=[1,2,2.5,5,10].find(n=>n*magnitude>=rawStep)*magnitude;
  const max=step*4,W=640,H=320,L=96,R=22,T=18,B=44;
  const x=i=>L+i/Math.max(1,points.length-1)*(W-L-R),y=v=>H-B-v/max*(H-T-B);
  const stage=el('div',null,'chart-stage');
  const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'group',tabindex:0,'aria-label':`${unit}. Стрелки: выбрать минуту. Enter: исследовать. Мышью перетащите для выбора интервала.`});
  const defs=svgNode('defs');svg.append(defs);
  for(let j=0;j<=4;j++){
    const value=step*j,yy=y(value);svg.append(svgNode('line',{x1:L,y1:yy,x2:W-R,y2:yy,class:'grid-line'}));
    const n=svgNode('text',{x:L-10,y:yy+4,'text-anchor':'end',class:'axis-label'});n.textContent=format(value,true);svg.append(n);
  }
  visible.forEach(([key,color],row)=>{
    const gradientId=id+'-fill-'+row,gradient=svgNode('linearGradient',{id:gradientId,x1:0,y1:0,x2:0,y2:1});
    gradient.append(svgNode('stop',{offset:'0%','stop-color':color,'stop-opacity':'.25'}),svgNode('stop',{offset:'100%','stop-color':color,'stop-opacity':'.015'}));defs.append(gradient);
    // Separate paths at missing samples; do not draw across missing observations.
    let run=[];
    function flush(){
      if(!run.length)return;
      const path=run.map(([i,v],j)=>(j?'L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)).join(' ');
      svg.append(svgNode('path',{d:path+` L${x(run.at(-1)[0])},${H-B} L${x(run[0][0])},${H-B} Z`,fill:`url(#${gradientId})`,class:'chart-area'}));
      svg.append(svgNode('path',{d:path,fill:'none',stroke:color,'stroke-width':2.5,'stroke-linecap':'round','stroke-linejoin':'round',class:'metric-path'}));
      if(run.length===1)svg.append(svgNode('circle',{cx:x(run[0][0]),cy:y(run[0][1]),r:3,fill:color}));run=[];
    }
    points.forEach((p,i)=>{if(valid(p[key]))run.push([i,p[key]]);else flush();});flush();
  });
  for(const i of new Set([0,Math.round((points.length-1)/3),Math.round((points.length-1)*2/3),points.length-1])){
    const n=svgNode('text',{x:x(i),y:H-12,'text-anchor':i===0?'start':i===points.length-1?'end':'middle',class:'axis-label'});n.textContent=points[i].time.slice(11,16);svg.append(n);
  }
  const selection=svgNode('rect',{x:L,y:T,width:0,height:H-B-T,class:'chart-selection'});svg.append(selection);
  const cross=svgNode('line',{x1:L,y1:T,x2:L,y2:H-B,class:'crosshair',visibility:'hidden'});svg.append(cross);
  const dots=visible.map(([,color])=>{const d=svgNode('circle',{r:4.5,fill:color,stroke:'var(--panel)','stroke-width':2,visibility:'hidden'});svg.append(d);return d;});
  const tip=el('div',null,'chart-floating-tip');tip.hidden=true;
  const hint=el('div','Наведите для значений · клик: ±2 мин · перетяните для выбора периода','chart-tooltip');
  let index=points.length-1,dragStart=null,dragX=null,skipClick=false;
  function focus(i){
    index=Math.max(0,Math.min(points.length-1,i));const p=points[index];
    cross.setAttribute('x1',x(index));cross.setAttribute('x2',x(index));cross.setAttribute('visibility','visible');
    tip.replaceChildren(el('strong',dateText(p.time),'chart-tip-time'));
    visible.forEach(([key,color,label],j)=>{
      const row=el('div',null,'chart-tip-row'),name=el('span',label);name.style.color=color;row.append(name,el('strong',format(p[key])));tip.append(row);
      dots[j].setAttribute('visibility',valid(p[key])?'visible':'hidden');
      if(valid(p[key])){dots[j].setAttribute('cx',x(index));dots[j].setAttribute('cy',y(p[key]));}
    });
    tip.hidden=false;
    const width=stage.clientWidth,pixel=x(index)/W*width;
    tip.style.left=Math.max(0,Math.min(width-tip.offsetWidth,pixel>width/2?pixel-tip.offsetWidth-14:pixel+14))+'px';
  }
  function hide(){if(dragStart!==null)return;tip.hidden=true;cross.setAttribute('visibility','hidden');dots.forEach(d=>d.setAttribute('visibility','hidden'));}
  function select(from=index,to=index,range=false){
    pauseLive();const a=Date.parse(points[Math.min(from,to)].time),b=Date.parse(points[Math.max(from,to)].time);
    setInputs(new Date(a-(range?0:120000)),new Date(b+(range?60000:180000)));
    search(1,true);$('explorer').scrollIntoView({behavior:'smooth'});
  }
  function at(e){const r=svg.getBoundingClientRect();return Math.max(0,Math.min(points.length-1,Math.round(((e.clientX-r.left)/r.width*W-L)/(W-L-R)*(points.length-1))));}
  svg.addEventListener('pointermove',e=>{focus(at(e));if(dragStart!==null){selection.setAttribute('x',x(Math.min(dragStart,index)));selection.setAttribute('width',Math.abs(x(dragStart)-x(index)));}});
  svg.addEventListener('pointerleave',hide);
  svg.addEventListener('pointerdown',e=>{if(e.button!==0||e.pointerType==='touch')return;skipClick=false;dragStart=at(e);dragX=e.clientX;svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointerup',e=>{if(dragStart===null)return;const from=dragStart,to=at(e),moved=Math.abs(e.clientX-dragX)>6;dragStart=null;selection.setAttribute('width',0);if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);if(moved&&from!==to){skipClick=true;select(from,to,true);}});
  svg.addEventListener('pointercancel',()=>{dragStart=null;selection.setAttribute('width',0);hide();});
  svg.addEventListener('click',e=>{if(skipClick){skipClick=false;return;}focus(at(e));select();});
  svg.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Enter','Escape'].includes(e.key)){e.preventDefault();if(e.key==='Enter')select();else if(e.key==='Escape')hide();else focus(index+(e.key==='ArrowRight'?1:-1));}});
  svg.addEventListener('blur',hide);
  stage.append(svg,tip);box.append(stage,hint);
}
$('live-enabled').onchange=()=>{live.generation++;if($('live-enabled').checked)refreshLive();else pauseLive();scheduleLive();};$('live-interval').onchange=scheduleLive;$('refresh-now').onclick=refreshLive;
for(const id of ['start','end','timezone','service','threshold','errors-only'])$(id).addEventListener('change',()=>{pauseLive();state.dashboardVersion++;state.dashboardController?.abort();live.alertVersion++;live.alertController?.abort();$('alerts-refresh').disabled=false;$('alerts-panel').setAttribute('aria-busy','false');state.listVersion++;state.listController?.abort();setBusy(false);$('live-status').textContent='Фильтры изменены — нажмите «'+(state.view==='alerts'?'Обновить алерты':'Обновить обзор')+'»';});
$('alerts-refresh').onclick=refreshAlerts;
$('alert-severity').onchange=refreshAlerts;
$('alert-prev').onclick=()=>{pauseLive();loadAlerts(live.alerts?.period?{start:live.alerts.period.start,end:live.alerts.period.end_exclusive}:readAlertPeriod(),live.alertPage-1);};$('alert-next').onclick=()=>{pauseLive();loadAlerts(live.alerts?.period?{start:live.alerts.period.start,end:live.alerts.period.end_exclusive}:readAlertPeriod(),live.alertPage+1);};$('alert-close').onclick=()=>$('alert-dialog').close();
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(live.timer);$('live-dot').classList.remove('on');$('live-status').textContent='Автообновление приостановлено: вкладка скрыта';}else if($('live-enabled').checked)refreshLive();});

function cancelViewRequests(){
  live.generation++;state.dashboardVersion++;state.dashboardController?.abort();
  state.listVersion++;state.listController?.abort();live.alertVersion++;live.alertController?.abort();
  $('alerts-refresh').disabled=false;$('alerts-panel').setAttribute('aria-busy','false');setBusy(false);
}
function showView(hash=location.hash,load=true){
  const next=hash==='#alerts-panel'?'alerts':'monitor',changed=state.view!==next;
  if(changed)cancelViewRequests();state.view=next;
  const alerts=next==='alerts';$('alerts-panel').hidden=!alerts;$('monitor-view').hidden=alerts;
  document.querySelectorAll('[data-monitor-filter]').forEach(n=>{n.hidden=alerts;});$('service').required=!alerts;
  $('page-title').textContent=alerts?'Алерты мониторинга':'Производительность под контролем';
  $('page-description').textContent=alerts?'Срабатывания по всем сервисам. Выберите период и уровень критичности.':'Находите медленные вызовы. Проверяйте SQL. Объясняйте задержки.';
  document.querySelectorAll('.rail nav a').forEach(n=>{const active=n.getAttribute('href')===(alerts?'#alerts-panel':hash==='#explorer'?'#explorer':'#overview');if(active)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
  const shortcut=document.querySelector('.alerts-shortcut');shortcut.href=alerts?'#overview':'#alerts-panel';shortcut.textContent=alerts?'◫ Обзор':'◉ Алерты';
  $('live-help').textContent=alerts?'Автообновление срабатываний за последний час · возможна задержка поступления событий':'Минутные метрики · задержка поступления возможна · выбранная трассировка сохраняется при автообновлении';
  setBusy(state.busy);
  if(changed&&load&&state.csrf)refreshCurrent();
  if(hash==='#explorer')$('explorer').scrollIntoView({behavior:'smooth'});
}
function openMonitor(){history.pushState(null,'','#explorer');showView('#explorer',false);}
async function refreshAlerts(){
  try{const ok=await loadAlerts(readPeriod(),1);if(ok&&state.view==='alerts'){$('live-status').textContent='Алерты получены '+new Date().toLocaleTimeString('ru-RU');}return ok;}
  catch(e){$('live-status').textContent=e.message;$('alert-status').textContent=e.message;$('alert-status').classList.add('error');return false;}
}
async function refreshCurrent(){$('live-status').textContent='Загружаем выбранный период…';return state.view==='alerts'?refreshAlerts():search(1,true);}
function applyPreset(kind){
  pauseLive();cancelViewRequests();
  if(kind==='day'){
    const day=$('start').value.slice(0,10),date=new Date(day+'T00:00:00Z');
    if(!Number.isFinite(date.getTime())){status('Сначала выберите дату начала.',true);$('alert-status').textContent='Сначала выберите дату начала.';return;}
    date.setUTCDate(date.getUTCDate()+1);$('start').value=day+'T00:00';$('end').value=date.toISOString().slice(0,10)+'T00:00';
  }else setRollingPeriod(kind==='24h'?1440:60);
  refreshCurrent();
}
window.addEventListener('hashchange',()=>showView());
showView(location.hash,false);

setRollingPeriod(60);
(async()=>{try{const session=await api('/api/ai/auth/session');state.csrf=session.csrf_token;await initialize();}catch{showLogin();}})();
