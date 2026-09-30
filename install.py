#!/usr/bin/env python3
"""iBatyr first-install tool. Existing deployments are never overwritten."""
import argparse,json,os,pwd,re,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parent/'tools'))
from artifacts import acquire,extract,manifest
ROOT=Path(__file__).resolve().parent
BASE=Path('/opt/ibatyr')
ETC=Path('/etc/ibatyr-apm')
STATE=Path('/var/lib/ibatyr-apm')


def run(*args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def write(path,text,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:f.write(text)
    path.chmod(mode)
def free(port,host='127.0.0.1'):
    with socket.socket() as s:
        try:s.bind((host,port))
        except OSError:raise RuntimeError(f'Порт {host}:{port} занят или адрес недоступен. Существующий процесс не остановлен.')
def root_checks():
    if os.geteuid()!=0:raise RuntimeError('Для установки служб запустите через sudo')
    os_release=Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in os_release or 'VERSION_ID="24.04"' not in os_release:
        raise RuntimeError('Этот установщик рассчитан на Ubuntu 24.04; другие ОС требуют отдельной проверки')
    if not Path('/run/systemd/system').is_dir():raise RuntimeError('Требуется работающий systemd')
    if sys.version_info<(3,12):raise RuntimeError('Требуется Python 3.12+')
    for name in ('systemctl','runuser','useradd'):
        if not shutil.which(name):raise RuntimeError('Не найдена команда '+name)
def account():
    try:pwd.getpwnam('ibatyr')
    except KeyError:run('useradd','--system','--home-dir',str(STATE),'--shell','/usr/sbin/nologin','ibatyr')
    STATE.mkdir(mode=0o750,parents=True,exist_ok=True)
    shutil.chown(STATE,user='ibatyr',group='ibatyr')
    ETC.mkdir(mode=0o750,parents=True,exist_ok=True)
def unit_exists(name):return Path('/etc/systemd/system/'+name+'.service').exists()
def activate_unit(name,text):
    path=Path('/etc/systemd/system/'+name+'.service');write(path,text)
    run('systemd-analyze','verify',str(path))
    run('systemctl','daemon-reload');run('systemctl','enable','--now',name+'.service')
def envtext(values):
    for value in values.values():
        if any(c in str(value) for c in '\n\r"\\'):raise RuntimeError('Недопустимый символ в значении конфигурации')
    return ''.join(f'{k}="{v}"\n' for k,v in values.items())
def wait_url(url,graphql=False,seconds=90):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            req=Request(url,data=b'{"query":"query { __typename }"}' if graphql else None,headers={'Content-Type':'application/json'})
            with urlopen(req,timeout=3) as r:body=json.load(r)
            if (body.get('data') or {}).get('__typename')=='Query' if graphql else body.get('status')=='ok':return
        except Exception:pass
        time.sleep(2)
    raise RuntimeError('Служба не прошла readiness. Сохранены файлы для диагностики; см. journalctl. Не повторяйте установку поверх.')
def service(name,directory,command,envfile,writable):
    return f'''[Unit]
Description={name}
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=ibatyr
Group=ibatyr
WorkingDirectory={directory}
EnvironmentFile={envfile}
ExecStart={command}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={writable}
[Install]
WantedBy=multi-user.target
'''

def shell(a):
    target=BASE/'shell'/manifest()['product_version'];env=ETC/'shell.env';config=STATE/'settings.json';lic=STATE/'license'
    if target.exists() or env.exists() or unit_exists('ibatyr-apm') or config.exists() or lic.exists():
        raise RuntimeError('Оболочка или её конфигурация уже существует. Установщик не перезаписывает её; см. docs/OPERATIONS.md')
    free(a.port)
    u=urlsplit(a.oap)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment:raise RuntimeError('Некорректный --oap')
    wait_url(a.oap,graphql=True,seconds=5)
    import ensurepip
    if a.import_config and not Path(a.import_config).is_file():raise RuntimeError('Не найден --import-config')
    if a.import_license_dir:
        source=Path(a.import_license_dir)
        if not source.is_dir() or not (source/'state.json').is_file():raise RuntimeError('Не найдено состояние лицензии')
        if any(p.is_symlink() for p in source.rglob('*')):raise RuntimeError('Ссылки в состоянии лицензии запрещены')
    account();shutil.copytree(ROOT/'shell',target,ignore=shutil.ignore_patterns('__pycache__','.venv','.*'))
    run(sys.executable,'-m','venv',target/'.venv')
    py=target/'.venv/bin/python'
    run(py,'-m','pip','install','--upgrade','pip')
    run(py,'-m','pip','install','--only-binary=:all:','-r',target/'requirements.txt')
    if a.import_config:
        data=json.loads(Path(a.import_config).read_text())
        if not data.get('password_hash') or not data.get('providers'):raise RuntimeError('Неверный формат старой конфигурации')
        write(config,json.dumps(data,ensure_ascii=False),0o600);shutil.chown(config,user='ibatyr',group='ibatyr')
    else:
        run('runuser','-u','ibatyr','--','env',f'SW_AI_CONFIG={config}',py,target/'setup_ai.py')
    if a.import_license_dir:
        shutil.copytree(a.import_license_dir,lic)
        for p in [lic,*lic.rglob('*')]:
            shutil.chown(p,user='ibatyr',group='ibatyr');p.chmod(0o700 if p.is_dir() else 0o600)
    values={'SW_AI_CONFIG':str(config),'IBATYR_LICENSE_DIR':str(lic),'IBATYR_OAP_URL':a.oap,'PYTHONDONTWRITEBYTECODE':'1'}
    write(env,envtext(values),0o600)
    if a.public_key:
        # The state is created by the service account, never clone a publisher's license state.
        key=Path(a.public_key).resolve()
        if not key.is_file():raise RuntimeError('Не найден публичный ключ')
        publiccopy=STATE/'publisher-public.pem';shutil.copy2(key,publiccopy);publiccopy.chmod(0o644)
        run('runuser','-u','ibatyr','--','env',*[f'{k}={v}' for k,v in values.items()],py,target/'setup_license.py','--public-key',publiccopy,'--request-output',STATE/'activation-request.json')
    activate_unit('ibatyr-apm',service('iBatyr APM',target,f'{py} -m uvicorn main:app --host 127.0.0.1 --port {a.port} --workers 1',env,STATE))
    wait_url(f'http://127.0.0.1:{a.port}/ready')
    print(f'Готово: http://127.0.0.1:{a.port}/ai/ ; служба ibatyr-apm. Доступ через SSH-туннель.')

def server(a):
    target=BASE/'server'/manifest()['server']['version'];env=ETC/'server.env'
    if target.exists() or env.exists() or unit_exists('ibatyr-oap'):raise RuntimeError('Сервер уже установлен; перезапись запрещена')
    import ipaddress
    ipaddress.IPv4Address(a.agent_bind)
    for port,host in [(12800,'127.0.0.1'),(11800,a.agent_bind),(9090,'127.0.0.1'),(3100,'127.0.0.1'),(12801,'127.0.0.1')]:free(port,host)
    java=shutil.which('java')
    if not java:raise RuntimeError('Установите openjdk-17-jre-headless')
    out=subprocess.run([java,'-version'],capture_output=True,text=True).stderr
    if not re.search(r'version "17\.',out):raise RuntimeError('Для этого пакета нужен Java 17; проверьте java -version')
    if a.storage=='elasticsearch':
        if not a.es_nodes or not re.fullmatch(r'[A-Za-z0-9.:-]+(?::\d+)?(?:,[A-Za-z0-9.:-]+(?::\d+)?)*',a.es_nodes):raise RuntimeError('Укажите --es-nodes host:9200 без логина и пароля')
    archive=acquire('server',a.archive)
    with tempfile.TemporaryDirectory() as tmp:
        extract(archive,tmp)
        candidates=list(Path(tmp).glob('*/oap-libs'))
        if len(candidates)!=1:raise RuntimeError('Неожиданная структура серверного архива')
        upstream=candidates[0].parent
        if not (upstream/'LICENSE').is_file() or not (upstream/'NOTICE').is_file():raise RuntimeError('Нет LICENSE/NOTICE')
        account();shutil.copytree(upstream,target)
    logs=target/'logs';logs.mkdir(exist_ok=True);shutil.chown(logs,user='ibatyr',group='ibatyr')
    values={'SW_STORAGE':'h2' if a.storage=='demo' else 'elasticsearch','SW_CORE_REST_HOST':'127.0.0.1','SW_CORE_GRPC_HOST':a.agent_bind,
       'SW_PROMQL_REST_HOST':'127.0.0.1','SW_LOGQL_REST_HOST':'127.0.0.1','SW_RECEIVER_AWS_FIREHOSE_HTTP_HOST':'127.0.0.1',
       'SW_TELEMETRY_PROMETHEUS_HOST':'127.0.0.1','SW_RECEIVER_ZIPKIN_REST_HOST':'127.0.0.1','SW_QUERY_ZIPKIN_REST_HOST':'127.0.0.1'}
    if a.es_nodes:values['SW_STORAGE_ES_CLUSTER_NODES']=a.es_nodes
    if a.storage=='elasticsearch':
        import getpass
        values['SW_STORAGE_ES_HTTP_PROTOCOL']=a.es_protocol
        if a.es_user:
            values['SW_ES_USER']=a.es_user;values['SW_ES_PASSWORD']=getpass.getpass('Пароль хранилища: ')
    write(env,envtext(values),0o600)
    # Own launcher keeps upstream files unmodified and preserves all notices.
    launcher=target/'ibatyr-oap.sh'
    write(launcher,'#!/bin/sh\nset -eu\nexec '+java+' -Xms512m -Xmx2g -Doap.logDir='+str(logs)+' -Dlog4j2.formatMsgNoLookups=true -cp "config:oap-libs/*" org.apache.skywalking.oap.server.starter.OAPServerStartUp\n',0o755)
    activate_unit('ibatyr-oap',service('iBatyr APM core',target,str(launcher),env,logs))
    wait_url('http://127.0.0.1:12800/graphql',graphql=True,seconds=180)
    print('OAP готов. GraphQL: 127.0.0.1:12800; агенты: '+a.agent_bind+':11800')
    if a.storage=='demo':print('DEMO: H2 в памяти. После остановки данные будут потеряны. Для клиентов нужен внешний persistent storage.')

def agent(a):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+',a.collector):raise RuntimeError('Укажите collector host:port')
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}',a.service_name):raise RuntimeError('Имя сервиса: латиница, цифры, . _ @ -')
    appuser=pwd.getpwnam(a.app_user)
    target=BASE/'agent'/a.service_name
    if target.exists():raise RuntimeError('Агент для этого сервиса уже установлен; перезапись запрещена')
    archive=acquire('agent',a.archive)
    with tempfile.TemporaryDirectory() as tmp:
        extract(archive,tmp)
        jars=list(Path(tmp).rglob('skywalking-agent.jar'))
        if len(jars)!=1:raise RuntimeError('Неожиданная структура agent-архива')
        agentroot=jars[0].parent
        if not (agentroot/'LICENSE').exists() or not (agentroot/'NOTICE').exists():raise RuntimeError('В архиве агента отсутствует LICENSE/NOTICE')
        shutil.copytree(agentroot,target)
    logs=target/'logs';logs.mkdir(exist_ok=True);shutil.chown(logs,user=appuser.pw_uid,group=appuser.pw_gid)
    options=f'-javaagent:{target}/skywalking-agent.jar -Dskywalking.agent.service_name={a.service_name} -Dskywalking.collector.backend_service={a.collector}'
    write(target/'ibatyr-java-options.txt',options+'\n')
    print('iBatyr APM agent установлен. Приложение не перезапущено. Добавьте перед -jar:')
    print(options)
    print('Подробности и пример systemd: docs/AGENT.md. Не подключайте второй javaagent, если агент уже используется.')

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('shell');s.add_argument('--oap',default='http://127.0.0.1:12800/graphql');s.add_argument('--port',type=int,default=8010);s.add_argument('--public-key');s.add_argument('--import-config');s.add_argument('--import-license-dir')
    s=sub.add_parser('server');s.add_argument('--storage',choices=['demo','elasticsearch'],required=True);s.add_argument('--es-nodes');s.add_argument('--es-protocol',choices=['http','https'],default='http');s.add_argument('--es-user');s.add_argument('--agent-bind',default='127.0.0.1');s.add_argument('--archive')
    s=sub.add_parser('agent');s.add_argument('--collector',required=True);s.add_argument('--service-name',required=True);s.add_argument('--app-user',required=True);s.add_argument('--archive')
    a=p.parse_args()
    try:
        root_checks()
        if a.action=='shell' and not 1<=a.port<=65535:raise RuntimeError('Неверный порт')
        globals()[a.action](a)
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.CalledProcessError) as e:
        print('Установка остановлена:',str(e),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
