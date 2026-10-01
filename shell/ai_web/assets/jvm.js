"use strict";
const jvm = {version:0, controller:null, service:null, instance:null, data:null};
function cancelJVM(){jvm.version++;jvm.controller?.abort();$('jvm-view').setAttribute('aria-busy','false');}
function clearJVM(){
  jvm.data=null;$('jvm-stats').replaceChildren();
  for(const id of ['cpu','memory','nonheap','threads','blocked','gc-time','gc-count','classes'])$('jvm-'+id).replaceChildren();
  $('jvm-warnings').replaceChildren();$('jvm-investigate').disabled=true;
}
function resetJVM(){cancelJVM();jvm.service=null;jvm.instance=null;clearJVM();$('jvm-instance').replaceChildren();$('jvm-status').textContent='Выберите сервис и период.';}
async function refreshJVM(background=false){
  cancelJVM();const version=jvm.version,c=new AbortController();jvm.controller=c;
  $('jvm-view').setAttribute('aria-busy','true');setBusy(true);
  try{
    const service=$('service').value;if(!service)throw new Error('Выберите сервис.');
    if(jvm.service!==service){jvm.service=service;jvm.instance=null;$('jvm-instance').replaceChildren();}
    const query={...readPeriod(),service_id:service};if(jvm.instance)query.instance_id=jvm.instance;
    if(!background)clearJVM();$('jvm-status').textContent=background?'Обновляем JVM · отображены предыдущие данные…':'Загружаем JVM за выбранный период…';
    const data=await api('/api/ai/jvm?'+new URLSearchParams(query),c);
    if(version!==jvm.version||!state.csrf||state.view!=='jvm')return false;
    jvm.data=data;
    const options=data.instances.map(i=>{const o=el('option',i.name);o.value=i.id;return o;});
    if(!data.instance){const o=el('option','Выберите экземпляр');o.value='';options.unshift(o);}
    $('jvm-instance').replaceChildren(...options);$('jvm-instance').value=data.instance?.id||'';
    jvm.instance=data.instance?.id||null;
    $('jvm-warnings').replaceChildren(...data.warnings.map(w=>el('p',w)));
    if(!data.instance){$('jvm-status').textContent=data.warnings.at(-1);return true;}
    const d=data.latest||{},fmt=(v,suffix='')=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('ru-RU',{maximumFractionDigits:2})+suffix:'—';
    const cards=[['CPU JVM',fmt(d.cpu_percent,'%'),'Минутное среднее'],['Heap',fmt(d.heap_gib,' GiB'),'Из '+fmt(d.heap_max_gib,' GiB')+' · '+fmt(d.heap_percent,'%')],['Потоки',fmt(d.threads_live),'Минутное среднее live'],['BLOCKED',fmt(d.threads_blocked),'Ожидание Java-монитора']];
    $('jvm-stats').replaceChildren(...cards.map(([label,value,note])=>{const box=el('article',null,'stat');box.append(el('span',label,'muted small'),el('strong',value),el('span',note,'muted small'));return box;}));
    const p=data.points;
    drawChart('jvm-cpu',p,[['cpu_percent','#48bfae','CPU JVM']],'%');
    drawChart('jvm-memory',p,[['heap_gib','#48bfae','Heap'],['heap_max_gib','#a996f5','Heap max']],'GiB');
    drawChart('jvm-nonheap',p,[['nonheap_gib','#a996f5','Non-Heap'],['metaspace_gib','#e1c478','Metaspace']],'GiB');
    drawChart('jvm-threads',p,[['threads_live','#48bfae','Live'],['threads_runnable','#a996f5','Runnable'],['threads_waiting','#e1c478','Waiting'],['threads_timed_waiting','#eaa970','Timed waiting']],'потоков');
    drawChart('jvm-blocked',p,[['threads_blocked','#f08b99','Blocked']],'потоков');
    drawChart('jvm-gc-time',p,[['young_gc_ms','#48bfae','Young'],['old_gc_ms','#f08b99','Old'],['normal_gc_ms','#a996f5','Normal']],'мс');
    drawChart('jvm-gc-count',p,[['young_gc_count','#48bfae','Young'],['old_gc_count','#f08b99','Old'],['normal_gc_count','#a996f5','Normal']],'сборок/мин');
    drawChart('jvm-classes',p,[['classes_loaded','#e1c478','Загруженные классы']],'классов');
    $('jvm-status').textContent=dateText(data.period.start)+' → '+dateText(data.period.end_exclusive)+' · признаки данных JVM: '+data.coverage.minutes_with_jvm_evidence+'/'+data.coverage.requested_minutes+' мин · карточки: '+dateText(d.time);
    $('jvm-investigate').disabled=false;$('live-status').textContent='JVM обновлена '+new Date().toLocaleTimeString('ru-RU');return true;
  }catch(e){if(version===jvm.version){if(!background)clearJVM();$('jvm-status').textContent=(background?'Нет свежих данных, графики предыдущего обновления. ':'JVM недоступна: ')+e.message;$('live-status').textContent='Нет свежих данных JVM';}return false;}
  finally{if(version===jvm.version){$('jvm-view').setAttribute('aria-busy','false');setBusy(false);}}
}
$('jvm-instance').onchange=()=>{pauseLive();jvm.instance=$('jvm-instance').value||null;refreshJVM();};
$('jvm-investigate').onclick=()=>{pauseLive();openMonitor();search(1,true);};
