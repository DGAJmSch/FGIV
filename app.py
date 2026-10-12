"""EU FG IV Tracker - local-only web application (Python standard library)."""
import csv, io, json, re, sqlite3, threading, webbrowser, os, time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('DB_PATH', str(ROOT/'tracker.sqlite3')))
SOURCES={'Commission européenne':'https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/ec_vacancies', 'Institutions et agences':'https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast'}
UA='Mozilla/5.0 (compatible; PersonalFGIVTracker/1.0)'
lock=threading.Lock()
state={'running':False,'message':'Pas encore synchronise','last':None}

def db():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE IF NOT EXISTS jobs (url TEXT PRIMARY KEY,title TEXT,domain TEXT,org TEXT,grade TEXT,location TEXT,published TEXT,deadline TEXT,source TEXT,description TEXT,first_seen TEXT,last_seen TEXT,active INTEGER DEFAULT 1,favorite INTEGER DEFAULT 0)')
 c.commit();return c

def text(el):
 return ' '.join(el.stripped_strings) if el else ''

def fetch(url):
 req=Request(url,headers={'User-Agent':UA,'Accept-Language':'en'})
 with urlopen(req,timeout=22) as response:
  final=response.geturl()
  if urlparse(final).hostname not in ('eu-careers.europa.eu','selection.eu-careers.europa.eu'):raise ValueError('Redirection hors des domaines officiels EU Careers: '+urlparse(final).hostname)
  return response.read(3000000).decode('utf-8','replace')

def parse_page(html,source,page_url):
 from bs4 import BeautifulSoup
 soup=BeautifulSoup(html,'html.parser')
 rows=[]
 for table in soup.find_all('table'):
  headings=[text(x).lower() for x in table.select('thead th')]
  if not headings:headings=[text(x).lower() for x in table.find_all('th',limit=10)]
  if not any('grade' in h for h in headings):continue
  for tr in table.select('tbody tr'):
   cells=tr.find_all('td',recursive=False)
   if not cells:continue
   fields={headings[i]:text(cell) for i,cell in enumerate(cells) if i<len(headings)}
   def get(*names):
    for name in names:
     for k,v in fields.items():
      if name in k:return v
    return ''
   grade=get('grade')
   if not re.search(r'\bFG\s*IV\b',grade,re.I):continue
   a=cells[0].find('a',href=True)
   if not a:continue
   link=urljoin(page_url,a['href'])
   if urlparse(link).hostname not in ('eu-careers.europa.eu','selection.eu-careers.europa.eu'):continue
   title=text(a) or text(cells[0])
   rows.append(dict(url=link,title=title,domain=get('domain'),org=get('dg','institution/eu body','institution'),grade=grade,location=get('location'),published=get('publication'),deadline=get('deadline'),source=source))
 return rows,soup

def next_page(soup,current):
 for a in soup.select('a[href]'):
  rel=' '.join(a.get('rel',[])).lower()
  label=(' '.join([text(a),a.get('aria-label',''),a.get('title','')])).lower()
  if 'next' in rel or re.search(r'\bnext\b|\bolder\b|›|»',label):
   url=urljoin(current,a['href'])
   if urlparse(url).hostname in ('eu-careers.europa.eu','selection.eu-careers.europa.eu') and urlparse(url).path==urlparse(current).path:return url
 return None

def sync():
 with lock:
  if state['running']:return
  state.update(running=True,message='Synchronisation en cours...')
 try:
  total=0;errors=[];now=datetime.now(timezone.utc).isoformat(timespec='seconds')
  c=db()
  for name,base in SOURCES.items():
   seen=set();url=base;count=0;success=False
   while url and url not in seen and count<40:
    seen.add(url);count+=1
    try:
     html=fetch(url);items,soup=parse_page(html,name,url);success=True
     for j in items:
      c.execute('''INSERT INTO jobs(url,title,domain,org,grade,location,published,deadline,source,description,first_seen,last_seen,active) VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,1)
      ON CONFLICT(url) DO UPDATE SET title=excluded.title,domain=excluded.domain,org=excluded.org,grade=excluded.grade,location=excluded.location,published=excluded.published,deadline=excluded.deadline,source=excluded.source,last_seen=excluded.last_seen,active=1''',
      (j['url'],j['title'],j['domain'],j['org'],j['grade'],j['location'],j['published'],j['deadline'],j['source'],'',now,now))
      total+=1
     url=next_page(soup,url)
    except Exception as e:errors.append(name+': '+str(e));break
   if success and not errors or success and not any(x.startswith(name+':') for x in errors):
    c.execute('UPDATE jobs SET active=0 WHERE source=? AND last_seen<>?',(name,now))
  c.commit();c.close()
  state.update(message=f'{total} annonces FG IV trouvees. '+('Erreurs : '+' | '.join(errors) if errors else 'Synchronisation terminee.'),last=now)
 except Exception as e:state['message']='Erreur : '+str(e)
 finally:state['running']=False

def date_key(s):
 m=re.search(r'(\d{2})/(\d{2})/(\d{4})',s or '')
 return f'{m[3]}-{m[2]}-{m[1]}' if m else ''

HTML='''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EU FG IV Tracker</title><style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#18283a;background:#f5f7fa}*{box-sizing:border-box}body{margin:0}header{background:#142b4b;color:white;padding:25px max(24px,calc((100vw - 1200px)/2))}h1{margin:0;font-size:26px}header p{opacity:.8;margin:7px 0 0}main{max-width:1200px;margin:24px auto;padding:0 18px}.toolbar,.filters,.stats{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.toolbar{justify-content:space-between;margin-bottom:16px}button,.button{cursor:pointer;border:1px solid #c8d1de;border-radius:8px;background:white;padding:10px 13px;color:#183454;font-weight:600;text-decoration:none}.primary{background:#164a85;color:white;border-color:#164a85}button:hover,.button:hover{filter:brightness(.95)}button:disabled{opacity:.5}.panel{background:white;border:1px solid #dfe5ed;border-radius:12px;padding:16px;margin-bottom:16px}.filters input,.filters select{border:1px solid #c8d1de;border-radius:8px;padding:11px;min-width:145px;flex:1}.filters input{flex:3;min-width:220px}.stats{color:#52647b;font-size:14px;margin-bottom:12px}.job{border-top:1px solid #e6ebf0;padding:16px 0}.job:first-child{border-top:0}.job h3{font-size:17px;margin:0 0 6px}.meta{color:#54677d;font-size:13px;line-height:1.9}.tag{display:inline-block;border-radius:5px;background:#e6effa;color:#174779;padding:3px 7px;font-weight:700;font-size:12px;margin-right:8px}.actions{display:flex;gap:10px;margin-top:10px}.empty{padding:36px;text-align:center;color:#69798b}.muted{color:#64748b;font-size:13px}.warning{background:#fff7e7;border:1px solid #f1ddb2;padding:12px;border-radius:8px;font-size:13px;margin-bottom:15px}#status{font-size:13px;color:#5b6c81}a{color:#1457a0}@media(max-width:650px){header{padding:20px}main{margin:12px auto}.filters{flex-direction:column}.filters input,.filters select{width:100%}}
</style></head><body><header><h1>EU FG IV Tracker</h1><p>Offres officielles · Agents contractuels · Groupe de fonctions IV</p></header><main><div class="toolbar"><div><button class="primary" id="sync">↻ Actualiser les offres</button> <a class="button" href="/export.csv">Exporter CSV</a></div><span id="status">Chargement...</span></div><div class="warning">Les annonces proviennent d'EU Careers. Vérifiez toujours les conditions et dates limites sur l'annonce officielle. Les offres peuvent ne pas être exhaustives si le site change ou bloque la collecte.</div><div class="panel filters"><input id="q" placeholder="Rechercher : poste, DG, domaine..."><select id="source"><option value="">Toutes les institutions</option><option>Commission européenne</option><option>Institutions et agences</option></select><select id="location"><option value="">Tous les lieux</option></select><select id="statusFilter"><option value="active">Annonces présentes à la dernière collecte</option><option value="all">Toutes les annonces enregistrées</option><option value="favorite">Favoris</option></select></div><div class="stats"><b id="count">0 offre</b><span>Grade FG IV vérifié dans la colonne officielle « Grade »</span></div><section class="panel" id="jobs"><div class="empty">Chargement...</div></section><p class="muted">Données stockées uniquement sur cet ordinateur (tracker.sqlite3). Aucune donnée de candidature n'est transmise. Cette application n'est pas affiliée à EPSO.</p></main><script>
let data=[];const $=id=>document.getElementById(id);function safe(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function dt(s){let m=String(s||'').match(/(\\d{2})\\/(\\d{2})\\/(\\d{4})/);return m?m[3]+'-'+m[2]+'-'+m[1]:''}function render(){let q=$('q').value.toLowerCase(),s=$('source').value,l=$('location').value,st=$('statusFilter').value;let jobs=data.filter(j=>(!q||[j.title,j.org,j.domain,j.location].join(' ').toLowerCase().includes(q))&&(!s||j.source===s)&&(!l||j.location===l)&&(st==='all'||(st==='favorite'?j.favorite:j.active)));jobs.sort((a,b)=>dt(a.deadline).localeCompare(dt(b.deadline)));$('count').textContent=jobs.length+' offre'+(jobs.length>1?'s':'');$('jobs').innerHTML=jobs.length?jobs.map(j=>`<article class="job"><h3>${safe(j.title)}</h3><div class="meta"><span class="tag">FG IV</span>${safe(j.org||j.source)} · ${safe(j.location||'Lieu non indiqué')}<div>Domaine : ${safe(j.domain||'Non indiqué')} · Publication : ${safe(j.published||'Non indiquée')} · <b>Clôture : ${safe(j.deadline||'À vérifier')}</b></div><div>Source : ${safe(j.source)} ${j.active?'':' · Non retrouvée lors de la dernière collecte complète'}</div></div><div class="actions"><a class="button" href="${safe(j.url)}" target="_blank" rel="noopener noreferrer">Voir l'annonce officielle ↗</a><button data-fav="${safe(j.url)}">${j.favorite?'★ Favori':'☆ Ajouter aux favoris'}</button></div></article>`).join(''):'<div class="empty">Aucune offre correspondant aux filtres. Cliquez sur « Actualiser les offres » pour lancer une collecte.</div>';document.querySelectorAll('[data-fav]').forEach(b=>b.onclick=async()=>{await fetch('/favorite',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:b.dataset.fav})});await load()})}async function load(){try{let r=await fetch('/api');let v=await r.json();data=v.jobs;$('status').textContent=v.state.message;let old=$('location').value;let places=[...new Set(data.map(j=>j.location).filter(Boolean))].sort();$('location').innerHTML='<option value="">Tous les lieux</option>'+places.map(p=>`<option value="${safe(p)}">${safe(p)}</option>`).join('');$('location').value=old;render();if(v.state.running)setTimeout(load,2000)}catch(e){$('status').textContent='Erreur de chargement : '+e}}$('sync').onclick=async()=>{await fetch('/sync',{method:'POST'});await load()};['q','source','location','statusFilter'].forEach(id=>$(id).addEventListener('input',render));load();</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,body,ctype='application/json',code=200):
  b=body.encode('utf-8') if isinstance(body,str) else body
  self.send_response(code);self.send_header('Content-Type',ctype+'; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  path=urlparse(self.path).path
  if path=='/':return self.send(HTML,'text/html')
  if path=='/api':
   c=db();jobs=[dict(x) for x in c.execute('SELECT * FROM jobs')];c.close();return self.send(json.dumps({'jobs':jobs,'state':state},ensure_ascii=False))
  if path=='/export.csv':
   c=db();rows=c.execute('SELECT title,org,domain,grade,location,published,deadline,source,url,active,favorite FROM jobs ORDER BY deadline').fetchall();c.close()
   out=io.StringIO();w=csv.writer(out);w.writerow(['Poste','Institution/DG','Domaine','Grade','Lieu','Publication','Cloture','Source','Lien','Present derniere collecte','Favori']);w.writerows(rows)
   return self.send('\ufeff'+out.getvalue(),'text/csv')
  self.send('Not found','text/plain',404)
 def do_POST(self):
  path=urlparse(self.path).path
  if path=='/sync':
   if not state['running']:threading.Thread(target=sync,daemon=True).start()
   return self.send('{"ok":true}')
  if path=='/favorite':
   try:
    n=int(self.headers.get('Content-Length','0'));obj=json.loads(self.rfile.read(min(n,10000)));url=obj.get('url','')
    c=db();c.execute('UPDATE jobs SET favorite=1-favorite WHERE url=?',(url,));c.commit();c.close();return self.send('{"ok":true}')
   except Exception as e:return self.send(json.dumps({'error':str(e)}),code=400)
  self.send('Not found','text/plain',404)

def auto_sync_loop():
 hours=float(os.environ.get('AUTO_SYNC_HOURS','0'))
 if hours<=0:return
 time.sleep(10)
 while True:
  sync()
  time.sleep(hours*3600)

if __name__=='__main__':
 DB.parent.mkdir(parents=True,exist_ok=True)
 db().close()
 port=int(os.environ.get('PORT','8765'))
 host='0.0.0.0' if os.environ.get('PORT') else '127.0.0.1'
 server=ThreadingHTTPServer((host,port),Handler)
 print(f'EU FG IV Tracker : http://{host}:{port}',flush=True)
 if os.environ.get('AUTO_SYNC_HOURS'):
  threading.Thread(target=auto_sync_loop,daemon=True).start()
 if not os.environ.get('PORT'):webbrowser.open(f'http://127.0.0.1:{port}')
 try:server.serve_forever()
 except KeyboardInterrupt:pass
