"""Browser regression against deterministic synthetic API fixtures; no LLM calls."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from datetime import datetime, timedelta, timezone
import json, sys, math
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1]/'shell/ai_web'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(WEB),**kw)
    def do_GET(self):
        self.path=self.path.removeprefix('/ai');super().do_GET()
    def log_message(self,*args):pass

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
counts={};queries={};fail_dashboard=False;missing_jvm=False

def fixture(route):
    global fail_dashboard
    u=urlsplit(route.request.url);p=u.path;q={k:v[0] for k,v in parse_qs(u.query).items()};counts[p]=counts.get(p,0)+1
    start=q.get('start','2026-10-01T09:00:00+05:00');end=q.get('end','2026-10-01T10:00:00+05:00')
    base=datetime.fromisoformat(start);queries[p]=q
    minute_count=max(1,int((datetime.fromisoformat(end)-base).total_seconds()/60))
    data={}
    if p.endswith('/auth/session'):data={'csrf_token':'fixture-csrf'}
    elif p.endswith('/auth/logout'):data={'ok':True}
    elif p.endswith('/license'):data={'status':'active','ai_enabled':True,'days_remaining':30,'message':'Тестовая лицензия','installation_id':'fixture'}
    elif p.endswith('/license/history'):data={'events':[]}
    elif p.endswith('/services'):data={'services':[{'id':'demo','name':'Synergy Demo'}]}
    elif p.endswith('/llm/providers'):data={'providers':[]}
    elif p.endswith('/jvm'):
        instance={'id':q.get('instance_id','jvm1'),'name':'JVM demo '+q.get('instance_id','jvm1')}
        points=[{'time':(base+timedelta(minutes=i)).isoformat(),'cpu_percent':20+8*math.sin(i*.2),'heap_gib':19+math.sin(i*.1),'heap_max_gib':30,'heap_percent':64,'nonheap_gib':.7,'metaspace_gib':.3,'threads_live':2800+30*math.sin(i*.2),'threads_blocked':68+10*math.sin(i*.1),'threads_runnable':120,'threads_waiting':400,'threads_timed_waiting':2200,'young_gc_ms':1200+400*math.sin(i*.3),'old_gc_ms':0,'normal_gc_ms':0,'young_gc_count':5,'old_gc_count':0,'normal_gc_count':0,'classes_loaded':45000} for i in range(minute_count)]
        data={'service':{'id':'demo','name':'Synergy Demo'},'instances':[{'id':'jvm1','name':'JVM demo jvm1'},{'id':'jvm2','name':'JVM demo jvm2'}],'instance':instance,'points':points,'latest':points[-1],'period':{'start':start,'end_exclusive':end},'coverage':{'requested_minutes':minute_count,'minutes_with_jvm_evidence':minute_count},'warnings':['Synthetic fixture. Not production measurements.']}
        if missing_jvm:data['instance']=None;data['points']=[];data['latest']=None;data['warnings']=['Выбранный экземпляр не найден.']
    elif p.endswith('/dashboard'):
        if fail_dashboard:route.fulfill(status=502,json={'detail':'Test upstream offline'});return
        points=[{'time':(base+timedelta(minutes=i)).isoformat(),'calls_per_minute':round(450+140*math.sin(i*.25)+35*math.cos(i*.9)),'mean_latency_ms':round(260+90*math.sin(i*.3)+(650 if 28<=i<=32 else 0)),'p95_ms':round(740+230*math.sin(i*.3)+(2000 if 28<=i<=32 else 0)),'error_rate_percent':round(.12+(.7 if 28<=i<=32 else 0)+.08*abs(math.sin(i*.4)),2)} for i in range(minute_count)]
        data={'kpis':{'estimated_calls':20000,'estimated_mean_latency_ms':640,'max_minute_p95_ms':4800,'estimated_error_rate_percent':2.1},'points':points,'coverage':{'minutes_with_positive_traffic':60,'requested_minutes':60},'warnings':[]}
    elif p.endswith('/alerts'):
        page=int(q.get('page',1));sev=q.get('severity','CRITICAL');level='UNKNOWN' if sev=='ALL' else sev
        data={'records':[{'key':str(i),'entity_id':'demo','scope':'Service','time':(base+timedelta(minutes=20+i)).isoformat(),'time_ms':int((base+timedelta(minutes=20+i)).timestamp()*1000),'message':['Service latency above 5 seconds','Database lock contention detected','Success rate below 95%'][i],'severity':level,'level_raw':None if level=='UNKNOWN' else level,'tags':{'level':level}} for i in range(3)],'fetched_at':datetime.now(timezone.utc).isoformat(),'period':{'start':start,'end_exclusive':end},'pagination':{'page':page,'may_have_more':page==1}}
    elif p.endswith('/traces'):
        data={'records':[{'segment_id':'seg1','trace_ids':['trace1'],'endpoints':['/api/orders'],'start':start,'duration_ms':7000,'has_error':True}],'period':{'start':start,'end_exclusive':end},'pagination':{'may_have_more':False},'warnings':[]}
    elif '/traces/' in p:
        data={'trace_id':'trace1','services':['Synergy Demo'],'span_count':1,'database_span_count':1,'error_span_count':1,'has_http_entry_in_trace':False,'entries':[],'spans':[{'segment_id':'seg1','span_id':0,'service':'Synergy Demo','operation':'JDBC SELECT','duration_ms':7000,'is_database':True,'sql':'SELECT ?','has_error':True,'error_events':[]}],'warnings':[]}
    else:raise AssertionError('Unexpected API '+p)
    route.fulfill(json=data)

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1500,'height':1100},reduced_motion='reduce');errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/api/ai/**',fixture)
        page.goto(f'http://127.0.0.1:{server.server_port}/ai/')
        page.locator('#latency-chart svg').wait_for()
        assert page.locator('#alerts-panel').is_hidden()
        assert counts.get('/api/ai/alerts',0)==0
        page.locator('.alerts-nav').click();page.locator('.alert-card').first.wait_for()
        assert page.url.endswith('#alerts-panel')
        assert page.locator('#monitor-view').is_hidden()
        assert page.locator('.alert-card').count()==3
        page.locator('#live-enabled').uncheck()
        dashboard_before=counts.get('/api/ai/dashboard',0)
        traces_before=counts.get('/api/ai/traces',0)
        with page.expect_response('**/api/ai/alerts?*'):
            page.locator('#alerts-refresh').click()
        page.wait_for_function("!document.getElementById('alerts-refresh').disabled")
        assert counts.get('/api/ai/dashboard',0)==dashboard_before
        assert counts.get('/api/ai/traces',0)==traces_before
        # Full selected day must fetch immediately with exact timezone boundaries.
        page.locator('#start').fill('2026-09-30T12:30')
        with page.expect_response('**/api/ai/alerts?*'):
            page.locator('#whole-day').click()
        assert queries['/api/ai/alerts']['start']=='2026-09-30T00:00:00+05:00'
        assert queries['/api/ai/alerts']['end']=='2026-10-01T00:00:00+05:00'
        assert counts.get('/api/ai/dashboard',0)==dashboard_before
        assert counts.get('/api/ai/traces',0)==traces_before
        page.locator('#alert-severity').select_option('ALL');page.locator('.alert-card.unknown').first.wait_for()
        page.screenshot(path='/tmp/ibatyr-alerts-page.png',full_page=False)
        # Investigation explicitly leaves Alerts and opens trace investigation.
        page.locator('.alert-card').first.click();assert page.locator('#alert-dialog').is_visible();page.locator('#alert-investigate').click()
        assert page.locator('#alerts-panel').is_hidden()
        assert page.locator('#monitor-view').is_visible()
        assert not page.locator('#live-enabled').is_checked()
        page.locator('#records .operation').first.click();page.locator('#detail pre').wait_for();before=page.locator('#detail').inner_text()
        page.locator('#live-enabled').check();page.wait_for_function('!live.busy');assert page.locator('#detail').inner_text()==before
        page.locator('#live-enabled').uncheck()
        # Rolling 24h triggers both metrics and trace requests with a 24h window.
        alerts_before=counts.get('/api/ai/alerts',0)
        with page.expect_response('**/api/ai/dashboard?*'):
            page.locator('#last-day').click()
        page.wait_for_function('!state.busy')
        for endpoint in ['/api/ai/dashboard','/api/ai/traces']:
            q=queries[endpoint]
            assert (datetime.fromisoformat(q['end'])-datetime.fromisoformat(q['start'])).total_seconds()==86400
        assert counts.get('/api/ai/alerts',0)==alerts_before
        assert page.locator('#latency-chart svg').count()==1
        assert not page.locator('#live-enabled').is_checked()
        # The calendar day preset also loads automatically on the monitoring page.
        page.locator('#start').fill('2026-09-29T11:00')
        with page.expect_response('**/api/ai/dashboard?*'):
            page.locator('#whole-day').click()
        page.wait_for_function('!state.busy')
        assert queries['/api/ai/dashboard']['start']=='2026-09-29T00:00:00+05:00'
        assert queries['/api/ai/dashboard']['end']=='2026-09-30T00:00:00+05:00'
        with page.expect_response('**/api/ai/dashboard?*'):
            page.locator('#last-hour').click()
        page.wait_for_function('!state.busy')
        page.locator('#overview').evaluate("e => e.scrollIntoView({block:'start',behavior:'instant'})")
        page.screenshot(path='/tmp/ibatyr-monitor-page.png',full_page=False)
        svg=page.locator('#latency-chart svg');svg.focus();svg.press('ArrowLeft');svg.press('Enter');page.wait_for_function('!state.busy')
        assert not page.locator('#live-enabled').is_checked()
        page.locator('#latency-chart .chart-toggles button').last.click();assert page.locator('#latency-chart .chart-toggles button').last.get_attribute('aria-pressed')=='false'
        with page.expect_response('**/api/ai/dashboard?*'):
            page.locator('#last-hour').click()
        page.wait_for_function('!state.busy')
        svg=page.locator('#traffic-chart svg');svg.scroll_into_view_if_needed();rect=svg.bounding_box()
        page.mouse.move(rect['x']+rect['width']*.5,rect['y']+rect['height']*.4)
        assert page.locator('#traffic-chart .chart-floating-tip').is_visible()
        assert 'Вызовы' in page.locator('#traffic-chart .chart-floating-tip').inner_text()
        page.mouse.move(rect['x']+rect['width']*.3,rect['y']+rect['height']*.5)
        page.mouse.down();page.mouse.move(rect['x']+rect['width']*.6,rect['y']+rect['height']*.5,steps=10);page.mouse.up()
        page.wait_for_function('!state.busy')
        selected_minutes=page.evaluate("(Date.parse(state.query.end)-Date.parse(state.query.start))/60000")
        assert 10<selected_minutes<30,selected_minutes
        fail_dashboard=True;page.locator('#refresh-now').click();page.wait_for_function('!live.busy');assert 'Нет свежих данных' in page.locator('#live-status').inner_text();assert page.locator('#latency-chart svg').count()==1
        fail_dashboard=False
        # Editing a date pauses polling and prevents stale requests from overwriting filters.
        page.locator('#start').fill('2026-09-20T09:00');page.locator('#start').dispatch_event('change');assert not page.locator('#live-enabled').is_checked()
        page.locator('#theme-toggle').click();assert page.locator('html').get_attribute('data-theme')=='light';page.locator('#theme-toggle').click()
        page.locator('#last-hour').click();page.wait_for_function('!state.busy')
        for width in [1500,800,390]:
            page.set_viewport_size({'width':width,'height':1100})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Overflow {width}'
            if width==390:
                assert page.locator('.alerts-shortcut').is_visible()
                page.locator('.alerts-shortcut').click()
                assert page.url.endswith('#alerts-panel')
                assert page.locator('#monitor-view').is_hidden()
                page.locator('.alerts-shortcut').click()
                assert page.url.endswith('#overview')
                assert page.locator('#alerts-panel').is_hidden()
        page.set_viewport_size({'width':1500,'height':1100});page.screenshot(path='/tmp/ibatyr-live-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/ibatyr-live-mobile.png',full_page=True)
        page.locator('#logout').click();page.locator('#login-screen').wait_for(state='visible');assert page.locator('#alert-list').inner_text()==''
        # A direct link opens Alerts without fetching the dashboard or traces.
        counts_before=dict(counts)
        direct=browser.new_page(viewport={'width':390,'height':844})
        direct.on('pageerror',lambda e:errors.append(str(e)));direct.route('**/api/ai/**',fixture)
        direct.goto(f'http://127.0.0.1:{server.server_port}/ai/#alerts-panel')
        direct.locator('.alert-card').first.wait_for()
        assert direct.locator('#monitor-view').is_hidden()
        assert counts.get('/api/ai/dashboard',0)==counts_before.get('/api/ai/dashboard',0)
        assert counts.get('/api/ai/traces',0)==counts_before.get('/api/ai/traces',0)
        direct.locator('#refresh-now').click();direct.wait_for_function('!live.busy')
        assert counts.get('/api/ai/dashboard',0)==counts_before.get('/api/ai/dashboard',0)
        assert counts.get('/api/ai/traces',0)==counts_before.get('/api/ai/traces',0)
        direct.screenshot(path='/tmp/ibatyr-alerts-mobile.png',full_page=True)
        # JVM deep link isolates requests and supports period/instance changes.
        counts_before=dict(counts)
        direct.goto(f'http://127.0.0.1:{server.server_port}/ai/#jvm')
        direct.locator('#jvm-cpu svg').wait_for()
        assert direct.locator('#monitor-view').is_hidden()
        assert direct.locator('#alerts-panel').is_hidden()
        assert counts.get('/api/ai/dashboard',0)==counts_before.get('/api/ai/dashboard',0)
        assert counts.get('/api/ai/traces',0)==counts_before.get('/api/ai/traces',0)
        with direct.expect_response('**/api/ai/jvm?*'):
            direct.locator('#jvm-instance').select_option('jvm2')
        direct.wait_for_function('!state.busy')
        assert queries['/api/ai/jvm']['instance_id']=='jvm2'
        with direct.expect_response('**/api/ai/jvm?*'):
            direct.locator('#last-day').click()
        direct.wait_for_function('!state.busy')
        q=queries['/api/ai/jvm']
        assert (datetime.fromisoformat(q['end'])-datetime.fromisoformat(q['start'])).total_seconds()==86400
        direct.locator('#jvm-cpu svg').focus();direct.keyboard.press('ArrowLeft')
        assert direct.locator('#jvm-cpu .chart-floating-tip').is_visible()
        with direct.expect_response('**/api/ai/jvm?*'):
            direct.keyboard.press('Enter')
        direct.wait_for_function('!state.busy')
        q=queries['/api/ai/jvm']
        assert (datetime.fromisoformat(q['end'])-datetime.fromisoformat(q['start'])).total_seconds()==300
        assert direct.url.endswith('#jvm')
        with direct.expect_response('**/api/ai/jvm?*'):
            direct.locator('#live-enabled').check()
        direct.wait_for_function('!live.busy')
        assert queries['/api/ai/jvm']['instance_id']=='jvm2'
        direct.locator('#live-enabled').uncheck()
        for width in [1500,800,390]:
            direct.set_viewport_size({'width':width,'height':1100})
            assert direct.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'JVM overflow {width}'
        direct.set_viewport_size({'width':1500,'height':1100})
        direct.evaluate('window.scrollTo(0,0)')
        direct.screenshot(path='/tmp/ibatyr-jvm-desktop.png',full_page=True)
        missing_jvm=True
        with direct.expect_response('**/api/ai/jvm?*'):
            direct.locator('#refresh-now').click()
        direct.wait_for_function('!live.busy')
        assert direct.locator('#jvm-cpu svg').count()==0
        assert direct.locator('#jvm-stats').inner_text()==''
        direct.locator('#logout').click();direct.locator('#login-screen').wait_for(state='visible')
        assert direct.locator('#jvm-stats').inner_text()==''
        assert not errors,errors
        browser.close()
    print('Browser OK: separate pages, calendar-day/24h auto-load, isolated requests, alerts/drilldown, live pinned trace, chart keyboard/toggles, stale state, history pause, themes, 3 widths, logout; no JS errors.')
finally:server.shutdown()
