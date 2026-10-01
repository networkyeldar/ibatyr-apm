"use strict";
const reports={version:0,controller:null};
function resetReports(){reports.version++;reports.controller?.abort();state.aiBusy=false;$('overview-ai-result').replaceChildren();$('overview-ai-result').hidden=true;$('overview-pdf').hidden=true;$('trace-pdf').hidden=true;$('overview-ai-status').textContent='Выберите период и запустите анализ.';}
function syncReports(){const p=state.providers.find(p=>p.id===$('report-provider').value);$('overview-analyze').disabled=!!state.aiBusy||!p?.configured||!state.license?.ai_enabled||!$('service').value;if(!state.aiBusy&&!$('overview-pdf').hidden)return;if(!state.aiBusy)$('overview-ai-status').textContent=!state.license?.ai_enabled?(state.license?.message||'Проверяем лицензию…'):!p?.configured?'Настройте выбранный профиль ИИ.':!$('service').value?'Выберите сервис.':'Нажмите кнопку: данные будут отправлены выбранной модели.';}
function requestId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');}
function reportLink(id,data){const a=$(id);a.href='/api/ai/reports/'+encodeURIComponent(data.report_id)+'.pdf';a.setAttribute('download','ibatyr-apm-report.pdf');a.hidden=false;}
function renderReport(data,target){
  const box=$(target),a=data.analysis;box.classList.remove('empty');box.hidden=false;
  const m=data.metadata||{},period=m.period;
  box.replaceChildren(el('div',[m.service?.name,m.instance?.name,m.trace_id,period?dateText(period.start)+' → '+dateText(period.end_exclusive):null,data.model].filter(Boolean).join(' · '),'report-context'),el('p',a.summary,'analysis-summary'));
  if(a.impact)box.append(el('h3','Влияние'),el('p',a.impact));
  for(const f of a.findings){
    const card=el('article',null,'finding');card.append(el('h3',f.title),el('p',f.interpretation));
    const refs=el('div',null,'evidence-links');
    for(const id of f.evidence_ids){const ref=data.evidence_links[id];const b=el('button',id+' · '+(ref.title||ref.operation||ref.metric||'Доказательство'),'subtle');b.type='button';
      b.onclick=()=>{let details=card.querySelector('details');if(!details){details=el('details');details.append(el('summary','Исходные измерения'),el('pre',JSON.stringify((data.evidence||[]).filter(row=>f.evidence_ids.includes(row.id)),null,2)));card.append(details);}details.open=!details.open;};refs.append(b);
    }card.append(refs);box.append(card);
  }
  for(const[key,title]of [['hypotheses','Гипотезы — требуют подтверждения'],['next_checks','Приоритетные проверки'],['limitations','Ограничения вывода']])if(a[key]?.length){const list=el('ol');for(const text of a[key])list.append(el('li',text));box.append(el('h3',title),list);}
  if(a.conclusion)box.append(el('h3','Вывод'),el('p',a.conclusion));
  const limits=el('details');limits.append(el('summary','Покрытие и ограничения исходных данных'));for(const text of data.limitations||[])limits.append(el('p',text,'small muted'));box.append(limits,el('p',data.notice,'small muted'));
}
async function analyzeReport(kind){
  if(state.aiBusy)return;
  const trace=kind==='trace',output=trace?'ai-result':'overview-ai-result',message=trace?'ai-message':'overview-ai-status',pdf=trace?'trace-pdf':'overview-pdf';
  let body;
  try{
    if(trace){if(!state.detail)return;body={trace_id:state.detail.trace_id,provider:state.provider,question:$('ai-question').value};}
    else{body={...readPeriod(),service_id:$('service').value,provider:$('report-provider').value,question:$('overview-question').value};if(jvm.service===body.service_id&&jvm.instance)body.instance_id=jvm.instance;}
  }catch(e){$(message).textContent=e.message;return;}
  const version=++reports.version,traceVersion=state.aiVersion,c=new AbortController();reports.controller=c;
  const valid=()=>version===reports.version&&state.csrf&&(!trace||traceVersion===state.aiVersion);
  state.aiBusy=true;syncAI();$(pdf).hidden=true;$(output).hidden=false;
  $(output).replaceChildren(el('div','Получаем измерения и формируем подробный разбор…','analysis-working'));
  const started=Date.now(),provider=body.provider==='external'?'внешний API':'локальная модель';
  $(message).textContent='Анализ запущен · '+provider;
  const timer=setInterval(()=>{if(valid())$(message).textContent='Анализ выполняется · '+Math.floor((Date.now()-started)/1000)+' с · '+provider;},1000);
  try{
    const data=await api('/api/ai/llm/'+(trace?'trace-analysis':'overview-analysis'),c,{method:'POST',body:JSON.stringify({...body,request_id:requestId()})});
    if(!valid())return;
    renderReport(data,output);reportLink(pdf,data);
    $(message).textContent=`Готово · ${data.model} · ${data.elapsed_seconds} с`+(data.usage?.total_tokens?' · '+data.usage.total_tokens+' токенов':'');
  }catch(e){if(valid()){$(message).textContent=e.name==='AbortError'?'Время ожидания истекло. Анализ мог продолжиться на сервере; автоматического повтора нет.':e.message;$(output).replaceChildren(el('div','Отчёт не сформирован. Проверьте сообщение выше.','empty'));}}
  finally{clearInterval(timer);if(version===reports.version){state.aiBusy=false;const text=$(message).textContent;syncAI();$(message).textContent=text;}}
}
$('ai-prepare').onclick=()=>analyzeReport('trace');$('overview-analyze').onclick=()=>analyzeReport('overview');
$('report-provider').onchange=syncReports;
$('report-settings').onclick=()=>{$('settings-provider').value=$('report-provider').value;fillProvider();$('settings-dialog').showModal();};
$('service').addEventListener('change',syncReports);
