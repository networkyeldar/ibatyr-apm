"use strict";
// Original inline SVG icons; no remote font, CDN or tracking request.
const iconPaths={
 users:['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2','M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M20 8v6m-3-3h6'],
 overview:['M3 3h7v7H3z','M14 3h7v7h-7z','M3 14h7v7H3z','M14 14h7v7h-7z'],
 alerts:['M12 3 2 21h20L12 3Z','M12 9v5','M12 18h.01'],
 jvm:['M7 7h10v10H7z','M9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4'],
 trace:['M4 4v16h16','M4 8h12m-8 4h12m-8 4h6'],
 settings:['M12 3v18M3 7h18M3 17h18','M8 4v6m8 4v6'],
 license:['M12 2 4 5v6c0 5 8 11 8 11s8-6 8-11V5l-8-3Z','m8 12 3 3 5-6'],
 cpu:['M3 12h4l3-8 4 16 3-8h4'],
 memory:['M3 5h18v14H3z','M7 9v6m5-6v6m5-6v6'],
 threads:['M4 5h16M4 12h16M4 19h16','M8 3v4m8 3v4m-6 3v4'],
 gc:['M4 10a8 8 0 0 1 14-5l2 2','M20 3v4h-4','M20 14a8 8 0 0 1-14 5l-2-2','M4 21v-4h4'],
 ai:['m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z'],
 classes:['m12 3 9 5-9 5-9-5 9-5Z','m3 12 9 5 9-5M3 16l9 5 9-5'],
 blocked:['M6 10h12v11H6z','M8 10V6a4 4 0 0 1 8 0v4'],
 clock:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M12 7v5l4 2'],
};
function sectionIcon(name){const svg=svgNode('svg',{viewBox:'0 0 24 24',class:'section-icon','aria-hidden':'true'});for(const d of iconPaths[name]||iconPaths.overview)svg.append(svgNode('path',{d}));return svg;}
for(const[selector,key,label]of [['a[href="#overview"]','overview','Обзор'],['.alerts-nav','alerts','Алерты'],['.jvm-nav','jvm','JVM'],['a[href="#explorer"]','trace','Трассировки'],['#settings-nav','settings','Подключения AI'],['#license-nav','license','Лицензия'],['#users-nav','users','Пользователи']]){const node=document.querySelector('.rail '+selector);if(node)node.replaceChildren(sectionIcon(key),el('span',label));}
for(const card of document.querySelectorAll('.chart-card')){const id=card.querySelector('.plot')?.id||'',title=card.querySelector('h3');const key=/cpu/.test(id)?'cpu':/memory|nonheap/.test(id)?'memory':/blocked/.test(id)?'blocked':/threads/.test(id)?'threads':/gc/.test(id)?'gc':/classes/.test(id)?'classes':/latency/.test(id)?'clock':/errors/.test(id)?'alerts':'cpu';if(title)title.prepend(sectionIcon(key));}
for(const[id,key]of [['alerts-title','alerts'],['jvm-title','jvm'],['report-title','ai']]){const node=$(id);if(node){node.querySelector('.alerts-symbol')?.remove();node.prepend(sectionIcon(key));}}
