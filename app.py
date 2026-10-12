"""EU Careers Tracker v2. Self-hosted, no affiliation with EPSO."""
import csv, io, json, os, re, sqlite3, threading, time, smtplib, ssl, hashlib, hmac
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('DB_PATH',str(ROOT/'tracker.sqlite3')))
SOURCES={'Commission européenne':'https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/ec_vacancies','Institutions et agences':'https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast'}
HOSTS={'selection.eu-careers.europa.eu','eu-careers.europa.eu'}
UA='Mozilla/5.0 (compatible; EUCareersPersonalTracker/2.0)'
LOCK=threading.Lock(); STATE={'running':False,'last':None,'message':'Prêt','errors':[],'sources':{}}
MAX_PAGES=int(os.getenv('MAX_PAGES','40'))
MAX_PDFS=int(os.getenv('MAX_PDFS_PER_SYNC','15'))
PDF_BYTES=8_000_000

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def conn():
 DB.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS jobs(url TEXT PRIMARY KEY,title TEXT,domain TEXT,org TEXT,grade TEXT,location TEXT,published TEXT,deadline TEXT,source TEXT,description TEXT DEFAULT '',first_seen TEXT,last_seen TEXT,active INTEGER DEFAULT 1,favorite INTEGER DEFAULT 0,pdf_url TEXT DEFAULT '',pdf_text TEXT DEFAULT '',pdf_status TEXT DEFAULT '',pdf_checked TEXT DEFAULT '',content_hash TEXT DEFAULT '',deadline_iso TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY AUTOINCREMENT,job_url TEXT,at TEXT,kind TEXT,details TEXT);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,job_url TEXT,kind TEXT,unique_key TEXT UNIQUE,created TEXT);''')
 # Migration from v1.1, retaining favorites and history.
 columns={r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
 for k,typ in {'pdf_url':'TEXT DEFAULT \'\'','pdf_text':'TEXT DEFAULT \'\'','pdf_status':'TEXT DEFAULT \'\'','pdf_checked':'TEXT DEFAULT \'\'','content_hash':'TEXT DEFAULT \'\'','deadline_iso':'TEXT DEFAULT \'\''}.items():
  if k not in columns:c.execute(f'ALTER TABLE jobs ADD COLUMN {k} {typ}')
 c.commit();return c

def valid(url):
 p=urlparse(url);return p.scheme=='https' and p.hostname in HOSTS and not p.username and not p.password

def fetch(url,max_bytes=3_000_000):
 if not valid(url):raise ValueError('URL hors domaines officiels EU Careers')
 req=Request(url,headers={'User-Agent':UA,'Accept-Language':'en','Accept':'text/html,application/pdf,*/*'})
 with urlopen(req,timeout=25) as r:
  if not valid(r.geturl()):raise ValueError('Redirection hors EU Careers')
  raw=r.read(max_bytes+1)
  if len(raw)>max_bytes:raise ValueError('Document trop volumineux')
  return raw,r.geturl(),r.headers.get('Content-Type','')

def txt(el):return ' '.join(el.stripped_strings) if el else ''
def date_iso(s):
 s=s or ''
 m=re.search(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})',s)
 if m:
  try:return f'{int(m[3]):04d}-{int(m[2]):02d}-{int(m[1]):02d}'
  except ValueError:pass
 m=re.search(r'(\d{4})-(\d{2})-(\d{2})',s)
 return m.group(0) if m else ''

def grade_ok(g):return bool(re.search(r'(?<![A-Z])FG\s*(?:I|II|III|IV)(?![A-Z])',g or '',re.I))
def parse(html,source,url):
 soup=BeautifulSoup(html,'html.parser');items=[]
 for table in soup.find_all('table'):
  headers=[txt(x).lower() for x in table.select('thead th')]
  if not headers:headers=[txt(x).lower() for x in table.find_all('th',limit=15)]
  if not any('grade' in h for h in headers):continue
  for tr in table.select('tbody tr'):
   cells=tr.find_all('td',recursive=False)
   if not cells:continue
   fields={headers[i]:txt(cell) for i,cell in enumerate(cells) if i<len(headers)}
   def get(*names):
    for n in names:
     for k,v in fields.items():
      if n in k:return v
    return ''
   grade=get('grade')
   if not grade_ok(grade):continue
   link=next((a for a in cells[0].find_all('a',href=True) if valid(urljoin(url,a['href']))),None)
   if not link:continue
   dest=urljoin(url,link['href'])
   items.append(dict(url=dest,title=txt(link) or txt(cells[0]),domain=get('domain'),org=get('dg','institution/eu body','institution'),grade=grade,location=get('location'),published=get('publication'),deadline=get('deadline'),deadline_iso=date_iso(get('deadline')),source=source))
 return items,soup

def next_url(soup,url):
 for a in soup.select('a[href]'):
  label=' '.join([txt(a),a.get('aria-label',''),a.get('title',''),' '.join(a.get('rel',[]))]).lower()
  if re.search(r'\bnext\b|\bolder\b|›|»',label):
   dest=urljoin(url,a['href'])
   if valid(dest) and urlparse(dest).path==urlparse(url).path:return dest
 return None

def pdf_links(html,base):
 soup=BeautifulSoup(html,'html.parser');links=[]
 for a in soup.select('a[href]'):
  u=urljoin(base,a['href'])
  if valid(u) and ('.pdf' in urlparse(u).path.lower() or 'pdf' in txt(a).lower()):links.append(u)
 return list(dict.fromkeys(links))

def enrich(c,url,force=False):
 j=c.execute('SELECT * FROM jobs WHERE url=?',(url,)).fetchone()
 if not j:return {'error':'Annonce introuvable'}
 if j['pdf_checked'] and not force:return {'status':j['pdf_status'],'cached':True}
 try:
  raw,final,typ=fetch(url)
  html=raw.decode('utf-8','replace');soup=BeautifulSoup(html,'html.parser')
  main=soup.select_one('main') or soup.select_one('article') or soup
  for t in main.select('script,style,nav,footer,header'):t.decompose()
  description=txt(main)[:14000]
  links=pdf_links(html,final);pdf_url='';pdf_text='';status='page_only'
  for link in links[:3]:
   try:
    data,real,content_type=fetch(link,PDF_BYTES)
    if not (data.startswith(b'%PDF') or 'pdf' in content_type.lower()):continue
    reader=PdfReader(io.BytesIO(data));pdf_text='\n'.join((p.extract_text() or '') for p in reader.pages[:40])[:90000]
    pdf_url=real;status='pdf_extracted' if pdf_text.strip() else 'pdf_no_text';break
   except Exception:continue
  c.execute('UPDATE jobs SET description=?,pdf_url=?,pdf_text=?,pdf_status=?,pdf_checked=? WHERE url=?',(description,pdf_url,pdf_text,status,now(),url));c.commit()
  return {'status':status,'pdf_url':pdf_url,'characters':len(pdf_text)}
 except Exception as e:
  c.execute('UPDATE jobs SET pdf_status=?,pdf_checked=? WHERE url=?',('error: '+str(e)[:150],now(),url));c.commit();return {'error':str(e)}

def log(c,url,kind,details):c.execute('INSERT INTO changes(job_url,at,kind,details) VALUES(?,?,?,?)',(url,now(),kind,details))
def sync():
 if not LOCK.acquire(blocking=False):return
 STATE.update(running=True,message='Synchronisation en cours',errors=[])
 try:
  c=conn();total=0;all_errors=[];sources={};stamp=now();new=[]
  for name,base in SOURCES.items():
   seen_pages=set();seen_jobs=set();url=base;count=0;complete=False;errors=[]
   while url and url not in seen_pages and count<MAX_PAGES:
    seen_pages.add(url);count+=1
    try:
     raw,final,_=fetch(url);items,soup=parse(raw.decode('utf-8','replace'),name,final)
     if count==1 and not soup.find('table'):raise ValueError('Aucun tableau détecté : structure modifiée')
     for j in items:
      seen_jobs.add(j['url']);total+=1
      old=c.execute('SELECT * FROM jobs WHERE url=?',(j['url'],)).fetchone()
      signature=hashlib.sha256(json.dumps([j[k] for k in ('title','domain','org','grade','location','published','deadline')],ensure_ascii=False).encode()).hexdigest()
      if not old:log(c,j['url'],'new',j['title']);new.append(j)
      elif old['content_hash'] and old['content_hash']!=signature:log(c,j['url'],'changed','Métadonnées de l’annonce modifiées')
      c.execute('''INSERT INTO jobs(url,title,domain,org,grade,location,published,deadline,source,first_seen,last_seen,active,content_hash,deadline_iso) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)
      ON CONFLICT(url) DO UPDATE SET title=excluded.title,domain=excluded.domain,org=excluded.org,grade=excluded.grade,location=excluded.location,published=excluded.published,deadline=excluded.deadline,source=excluded.source,last_seen=excluded.last_seen,active=1,content_hash=excluded.content_hash,deadline_iso=excluded.deadline_iso''',(j['url'],j['title'],j['domain'],j['org'],j['grade'],j['location'],j['published'],j['deadline'],j['source'],stamp,stamp,signature,j['deadline_iso']))
     c.commit();url=next_url(soup,final)
    except Exception as e:errors.append(str(e));break
   if url and count>=MAX_PAGES:errors.append('Limite de pagination atteinte')
   complete=not errors
   if complete:
    previous=c.execute('SELECT url FROM jobs WHERE source=? AND active=1 AND last_seen<>?',(name,stamp)).fetchall()
    for old in previous:log(c,old['url'],'missing','Absente de la dernière collecte complète')
    c.execute('UPDATE jobs SET active=0 WHERE source=? AND last_seen<>?',(name,stamp));c.commit()
   else:all_errors.append(name+': '+'; '.join(errors))
   sources[name]={'pages':count,'found':len(seen_jobs),'complete':complete,'errors':errors}
  # Limited automatic enrichment; manual button available for any vacancy.
  for row in c.execute("SELECT url FROM jobs WHERE active=1 AND (pdf_checked IS NULL OR pdf_checked='') ORDER BY first_seen DESC LIMIT ?",(MAX_PDFS,)).fetchall():enrich(c,row['url'])
  c.commit();c.close();STATE.update(last=stamp,sources=sources,errors=all_errors,message=f'{total} offres contractuelles relevées. '+('Collecte partielle : '+' | '.join(all_errors) if all_errors else 'Collecte terminée.'))
  notify_new(new)
 except Exception as e:STATE.update(message='Erreur : '+str(e),errors=[str(e)])
 finally:STATE['running']=False;LOCK.release()

def smtp_send(subject,body):
 host=os.getenv('SMTP_HOST');recipient=os.getenv('ALERT_TO');sender=os.getenv('ALERT_FROM')
 if not all((host,recipient,sender)):return False
 msg=EmailMessage();msg['Subject']=subject;msg['From']=sender;msg['To']=recipient;msg.set_content(body)
 port=int(os.getenv('SMTP_PORT','587'))
 if port==465:
  s=smtplib.SMTP_SSL(host,port,timeout=20,context=ssl.create_default_context())
 else:
  s=smtplib.SMTP(host,port,timeout=20);s.starttls(context=ssl.create_default_context())
 with s:
  if os.getenv('SMTP_USER'):s.login(os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD',''))
  s.send_message(msg)
 return True

def notify_new(items):
 if not items:return
 c=conn();items=[j for j in items if re.search(r'\bFG\s*IV\b',j['grade'],re.I)]
 if not items:c.close();return
 unsent=[j for j in items if not c.execute('SELECT 1 FROM alerts WHERE unique_key=?',('new:'+j['url'],)).fetchone()]
 if unsent:
  body='\n\n'.join(f"{j['title']} | {j['org']} | {j['grade']} | {j['deadline']}\n{j['url']}" for j in unsent[:40])
  try:
   if smtp_send(f'EU Careers : {len(unsent)} nouvelles offres FG IV',body):
    for j in unsent:c.execute('INSERT OR IGNORE INTO alerts(job_url,kind,unique_key,created) VALUES(?,?,?,?)',(j['url'],'new','new:'+j['url'],now()))
    c.commit()
  except Exception as e:STATE['errors'].append('Email : '+str(e))
 c.close()

def deadlines():
 if not os.getenv('ALERT_TO'):return
 today=datetime.now(timezone.utc).date();c=conn()
 for days in (7,3,1):
  date=(today+timedelta(days=days)).isoformat()
  for j in c.execute('SELECT * FROM jobs WHERE active=1 AND deadline_iso=? AND grade LIKE ?', (date,'%IV%')).fetchall():
   key=f'deadline:{days}:{j["url"]}:{date}'
   if c.execute('SELECT 1 FROM alerts WHERE unique_key=?',(key,)).fetchone():continue
   try:
    if smtp_send(f'EU Careers : échéance J-{days} : {j["title"]}',f"{j['title']}\n{j['deadline']}\n{j['url']}\nVérifie l'heure exacte sur l'annonce officielle."):
     c.execute('INSERT OR IGNORE INTO alerts(job_url,kind,unique_key,created) VALUES(?,?,?,?)',(j['url'],'deadline',key,now()));c.commit()
   except Exception as e:STATE['errors'].append('Email : '+str(e))
 c.close()

def loop():
 time.sleep(10)
 while True:
  sync();deadlines();time.sleep(max(1,float(os.getenv('AUTO_SYNC_HOURS','12')))*3600)

HTML=Path(ROOT/'index.html').read_text(encoding='utf-8')
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,obj,ctype='application/json',status=200):
  b=(json.dumps(obj,ensure_ascii=False).encode() if isinstance(obj,(dict,list)) else obj.encode() if isinstance(obj,str) else obj)
  self.send_response(status);self.send_header('Content-Type',ctype+'; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(b)
 def authorized(self):
  password=os.getenv('TRACKER_PASSWORD','')
  if not password:return True
  import base64
  h=self.headers.get('Authorization','')
  try:actual=base64.b64decode(h.split(' ',1)[1]).decode().split(':',1)[1] if h.startswith('Basic ') else ''
  except Exception:actual=''
  if hmac.compare_digest(actual,password):return True
  self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="EU Careers Tracker"');self.end_headers();return False
 def do_GET(self):
  if urlparse(self.path).path=='/health':return self.send({'ok':True})
  if not self.authorized():return
  p=urlparse(self.path);path=p.path
  if path=='/':return self.send(HTML,'text/html')
  if path=='/health':return self.send({'ok':True})
  if path=='/api':
   c=conn();jobs=[dict(j) for j in c.execute('SELECT url,title,domain,org,grade,location,published,deadline,deadline_iso,source,description,pdf_text,first_seen,last_seen,active,favorite,pdf_url,pdf_status,pdf_checked FROM jobs ORDER BY first_seen DESC')];c.close()
   return self.send({'jobs':jobs,'state':STATE,'email_configured':bool(os.getenv('SMTP_HOST') and os.getenv('ALERT_TO'))})
  if path=='/job':
   u=parse_qs(p.query).get('url',[''])[0];c=conn();j=c.execute('SELECT * FROM jobs WHERE url=?',(u,)).fetchone();history=[dict(x) for x in c.execute('SELECT at,kind,details FROM changes WHERE job_url=? ORDER BY id DESC LIMIT 30',(u,))];c.close()
   return self.send({'job':dict(j),'history':history} if j else {'error':'Introuvable'},status=200 if j else 404)
  if path=='/history':
   c=conn();rows=[dict(x) for x in c.execute('SELECT changes.at,changes.kind,changes.details,jobs.title,changes.job_url FROM changes LEFT JOIN jobs ON jobs.url=changes.job_url ORDER BY changes.id DESC LIMIT 250')];c.close();return self.send(rows)
  if path=='/export.csv':
   c=conn();rows=c.execute('SELECT title,org,domain,grade,location,published,deadline,source,url,active,favorite,pdf_url,pdf_status FROM jobs').fetchall();c.close()
   out=io.StringIO();w=csv.writer(out);w.writerow(['Poste','Institution','Domaine','Grade','Lieu','Publication','Cloture','Source','URL','Actif','Favori','PDF','Extraction'])
   for r in rows:w.writerow([("'"+str(v) if str(v).startswith(('=','+','-','@')) else v) for v in r])
   return self.send('\ufeff'+out.getvalue(),'text/csv')
  return self.send({'error':'Route inconnue'},status=404)
 def do_POST(self):
  if not self.authorized():return
  path=urlparse(self.path).path
  n=int(self.headers.get('Content-Length','0') or 0)
  if n>20000:return self.send({'error':'Trop volumineux'},status=413)
  try:payload=json.loads(self.rfile.read(n) or b'{}')
  except Exception:return self.send({'error':'JSON invalide'},status=400)
  if path=='/sync':
   if not STATE['running']:threading.Thread(target=sync,daemon=True).start()
   return self.send({'ok':True})
  if path in ('/favorite','/enrich'):
   u=payload.get('url','');c=conn()
   if not c.execute('SELECT 1 FROM jobs WHERE url=?',(u,)).fetchone():c.close();return self.send({'error':'Annonce inconnue'},status=404)
   if path=='/favorite':c.execute('UPDATE jobs SET favorite=1-favorite WHERE url=?',(u,));c.commit();result={'ok':True}
   else:result=enrich(c,u,force=bool(payload.get('force')))
   c.close();return self.send(result)
  if path=='/alerts/check':
   threading.Thread(target=deadlines,daemon=True).start();return self.send({'ok':True})
  return self.send({'error':'Route inconnue'},status=404)

if __name__=='__main__':
 conn().close();port=int(os.getenv('PORT','8765'))
 if os.getenv('AUTO_SYNC_HOURS','12')!='0':threading.Thread(target=loop,daemon=True).start()
 host='0.0.0.0' if os.getenv('PORT') else '127.0.0.1'
 print(f'EU Careers Tracker listening on {host}:{port}',flush=True)
 ThreadingHTTPServer((host,port),Handler).serve_forever()
