"""Server-side PDF, Unicode fonts, text-only model content and measured charts."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import json
import math

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether, Flowable

FONTS=Path(__file__).with_name('report_fonts')
for name,file in [('IB','DejaVuSans.ttf'),('IB-Bold','DejaVuSans-Bold.ttf')]:
    pdfmetrics.registerFont(TTFont(name,str(FONTS/file)))
INK=colors.HexColor('#13243a');MUTED=colors.HexColor('#52647a');TEAL=colors.HexColor('#168b82')
BODY=ParagraphStyle('body',fontName='IB',fontSize=9,leading=14,textColor=INK,spaceAfter=8,splitLongWords=True)
SMALL=ParagraphStyle('small',parent=BODY,fontSize=7.5,leading=11,textColor=MUTED)
H1=ParagraphStyle('h1',parent=BODY,fontName='IB-Bold',fontSize=22,leading=28,spaceAfter=18)
H2=ParagraphStyle('h2',parent=BODY,fontName='IB-Bold',fontSize=14,leading=19,spaceBefore=16,spaceAfter=10,keepWithNext=True)
H3=ParagraphStyle('h3',parent=BODY,fontName='IB-Bold',fontSize=10.5,leading=15,spaceBefore=9,spaceAfter=6,keepWithNext=True)


def safe(text):
    text=str(text).replace('\u2011','-').replace('\u2013','-').replace('\u2014','-')
    glyphs=pdfmetrics.getFont('IB').face.charToGlyph
    text=''.join(c if c in '\n\t' or ord(c) in glyphs else '?' for c in text)
    return escape(text).replace('\n','<br/>')


def paragraph(text,style=BODY):return Paragraph(safe(text),style)


class MetricPlot(Flowable):
    def __init__(self,chart):
        super().__init__();self.chart=chart;self.width=499;self.height=175
    def draw(self):
        c=self.canv;datasets=self.chart['datasets'];points=datasets[0]['points'];W=self.width;L=52;R=12;B=29;T=125
        usable=lambda p:isinstance(p.get('value'),(int,float)) and math.isfinite(p['value'])
        good=[(i,p) for d in datasets for i,p in enumerate(d['points']) if usable(p)]
        c.setFillColor(MUTED);c.setFont('IB',7)
        observed=len({i for i,p in good})
        c.drawString(0,163,'Единица: '+self.chart['unit']+' | Покрытие хотя бы одного ряда: '+str(observed)+'/'+str(len(points))+' мин')
        if not good:c.drawString(L,80,'Нет доступных наблюдений');return
        first,last=min(i for i,p in good),max(i for i,p in good);peak=max(max(p['value'] for _,p in good),1)
        x=lambda i:L+(i-first)/max(1,last-first)*(W-L-R)
        y=lambda v:B+v/peak*(T-B)
        for j in range(4):
            yy=B+(T-B)*j/3;c.setStrokeColor(colors.HexColor('#dfe7ed'));c.setLineWidth(.4);c.line(L,yy,W-R,yy)
            c.setFillColor(MUTED);c.drawRightString(L-6,yy-2,f'{peak*j/3:.2f}'.rstrip('0').rstrip('.'))
        palette=['#168b82','#8660c9','#c36a3c','#d2951d']
        for index,dataset in enumerate(datasets):
            color=colors.HexColor(palette[index%len(palette)])
            c.setFillColor(color);c.drawString((index%2)*245,149-(index//2)*11,dataset['title'])
            c.setStrokeColor(color);c.setLineWidth(1.1);path=c.beginPath();continuing=False
            for i,p in enumerate(dataset['points']):
                if not usable(p):continuing=False;continue
                if not continuing:path.moveTo(x(i),y(p['value']));continuing=True
                else:path.lineTo(x(i),y(p['value']))
            c.drawPath(path)
            if first==last and usable(dataset['points'][first]):c.circle(x(first),y(dataset['points'][first]['value']),2,fill=1,stroke=0)
        c.setFillColor(MUTED)
        c.drawString(L,12,points[first]['time'][:16].replace('T',' '))
        c.drawRightString(W-R,12,points[last]['time'][:16].replace('T',' '))


def evidence_text(row):
    def n(value):return f'{value:.3f}'.rstrip('0').rstrip('.') if isinstance(value,(int,float)) else str(value)
    if row.get('kind')=='metric_summary':
        first,last,peak=row.get('first') or {},row.get('last') or {},row.get('peak') or {}
        return (f"{row['metric']} | {row['unit']} | Наблюдений: {row['samples']}/{row['requested_minutes']}\n"
                f"Min: {n(row['min'])}; среднее минут: {n(row['mean_of_minute_values'])}; max: {n(row['max'])}\n"
                f"Данные: {first.get('time','-')} → {last.get('time','-')}; пик: {peak.get('time','-')}")
    if row.get('kind')=='aligned_minute':
        return row['time']+'\n'+'; '.join(k+': '+n(v) for group in ('service','jvm') for k,v in row[group].items())
    return json.dumps(row,ensure_ascii=False,separators=(', ', ': '))


def render_report(result,charts):
    stream=BytesIO();doc=SimpleDocTemplate(stream,pagesize=A4,rightMargin=48,leftMargin=48,topMargin=63,bottomMargin=48,
        title='iBatyr APM - '+result['metadata']['title'],author='iBatyr APM')
    story=[];meta=result['metadata'];analysis=result['analysis']
    story += [paragraph('iBatyr APM',SMALL),paragraph(meta['title'],H1)]
    for key,label in [('created_at','Сформирован'),('model','Модель'),('provider','Профиль')]:story.append(paragraph(label+': '+str(result.get(key,'-')),SMALL))
    if meta.get('service'):story.append(paragraph('Сервис: '+meta['service']['name']))
    if meta.get('instance'):story.append(paragraph('Экземпляр JVM: '+meta['instance']['name']))
    if meta.get('trace_id'):story.append(paragraph('Trace ID: '+meta['trace_id'],SMALL))
    if meta.get('period'):
        period=meta['period'];story.append(paragraph('Период: '+period['start']+' → '+period['end_exclusive']+' (конец исключён)',SMALL))
    story += [paragraph('Резюме',H2),paragraph(analysis['summary'])]
    if analysis.get('impact'):story += [paragraph('Влияние',H2),paragraph(analysis['impact'])]
    story.append(paragraph('Наблюдения и доказательства',H2))
    for i,finding in enumerate(analysis['findings'],1):
        story += [paragraph(f'{i}. '+finding['title'],H3),paragraph(finding['interpretation']),paragraph('Источники: '+', '.join(finding['evidence_ids']),SMALL)]
    for key,title in [('hypotheses','Гипотезы'),('next_checks','Приоритетные проверки'),('limitations','Ограничения вывода')]:
        if analysis.get(key):
            story.append(paragraph(title,H2))
            for i,item in enumerate(analysis[key],1):story.append(paragraph(f'{i}. {item}'))
    if analysis.get('conclusion'):story += [paragraph('Вывод',H2),paragraph(analysis['conclusion'])]
    story.append(paragraph('Границы исходных данных',H2))
    for warning in result.get('limitations',[]):story.append(paragraph(warning,SMALL))
    story.append(paragraph(result['notice'],SMALL))
    if charts:
        story += [PageBreak(),paragraph('Графики измерений',H1),paragraph('Показан интервал доступных данных. Пропуски внутри периода не соединяются. Графики являются снимком на момент анализа, а не текущими значениями.',SMALL)]
        for chart in charts:story.append(KeepTogether([paragraph(chart['title'],H3),MetricPlot(chart),Spacer(1,9)]))
    story += [PageBreak(),paragraph('Реестр доказательств',H1),paragraph('E-идентификаторы соответствуют ссылкам в выводах модели. Исходные параметры SQL и секреты в отчёт не включаются.',SMALL)]
    for row in result.get('evidence',[]):
        reference=result.get('evidence_links',{}).get(row['id'],{})
        story.append(paragraph(row['id']+' - '+str(reference.get('title') or reference.get('operation') or row.get('kind','')),H3))
        story.append(paragraph(evidence_text(row),SMALL))
    def footer(c,d):
        c.saveState();c.setFillColor(TEAL);c.rect(48,A4[1]-37,A4[0]-96,2,fill=1,stroke=0)
        c.setFont('IB',8);c.setFillColor(MUTED);c.drawString(48,27,'iBatyr APM | Интерпретация ИИ требует проверки')
        c.drawRightString(A4[0]-48,27,str(d.page));c.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
