#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, re, hashlib
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parent; UA={'User-Agent':'Mozilla/5.0 WyomingElectionDashboardReplay/2.0'}
CANDIDATE_CSV='https://sos.wyo.gov/Elections/Docs/2024/2024_WY_Primary_Election_Candidates.csv'; SOS_RESULTS='https://sos.wyo.gov/Elections/Docs/2024/2024PrimaryResults.aspx'
def norm(s):return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+',' ',str(s).lower().replace('&',' and '))).strip()
def get(s,u):r=s.get(u,headers=UA,timeout=25,allow_redirects=True);r.raise_for_status();return r
def pdf_text(b):
 try:return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(b)).pages)
 except Exception:return ''
def aliases(name):
 n=norm(name);p=n.split();o={n}
 if len(p)>=2:o|={p[0]+' '+p[-1],p[-1]+' '+p[0]}
 return sorted(o,key=len,reverse=True)
def candidate_rows():
 rows=list(csv.DictReader(io.StringIO(get(requests.Session(),CANDIDATE_CSV).content.decode('utf-8-sig',errors='replace'))));out=[]
 for row in rows:
  low={norm(k):str(v or '').strip() for k,v in row.items()};office=low.get('office sought','');party=(low.get('party affiliation','') or '').upper();cand=low.get('ballot name','') or ' '.join(x for x in [low.get('candidate first name',''),low.get('candidate middle name',''),low.get('candidate last name','')] if x);o=norm(office)
  chamber='Senate' if 'state senator' in o else 'House' if 'state representative' in o else None
  if not chamber or not cand:continue
  m=re.search(r'(\d{1,2})',o)
  if not m:continue
  p='Republican' if party in ('REP','REPUBLICAN') or 'republican' in o else 'Democratic' if party in ('DEM','DEMOCRATIC') or 'democratic' in o else ''
  if p:out.append({'chamber':chamber,'district':int(m.group(1)),'party':p,'candidate':cand})
 print('Parsed',len(out),'2024 legislative candidate rows');return out
def load_sources():return json.loads((ROOT/'sources.json').read_text())
def score_link(t,u):
 s=norm(t+' '+u);sc=(14 if '2024' in s else 0)+(10 if 'primary' in s else 0)+(8 if ('result' in s or 'return' in s) else 0)+(3 if ('unofficial' in s or 'official' in s) else 0)+(2 if 'summary' in s else 0)+(2 if u.lower().split('?')[0].endswith('.pdf') else 0)
 if any(x in s for x in ('2026','2022','2020','general election','sample ballot','audit','recount')):sc-=18
 return sc
def same_site(a,b):return urlparse(a).netloc.lower().removeprefix('www.')==urlparse(b).netloc.lower().removeprefix('www.')
def discover(s,landing,max_depth=2,max_pages=30):
 out=[];seen=set();q=deque([(landing,0,50)])
 while q and len(seen)<max_pages:
  u,depth,parent=q.popleft()
  if u in seen:continue
  seen.add(u)
  try:r=get(s,u)
  except Exception:continue
  ct=r.headers.get('content-type','').lower();final=r.url
  if 'pdf' in ct or final.lower().split('?')[0].endswith('.pdf'):
   out.append((max(parent,20),final,r.content,ct));continue
  soup=BeautifulSoup(r.content,'html.parser');page=norm(soup.get_text(' ',strip=True))
  if '2024' in page and ('primary' in page or 'result' in page):out.append((max(10,score_link(page[:2500],final)),final,r.content,ct))
  if depth>=max_depth:continue
  for a in soup.find_all('a',href=True):
   nxt=urljoin(final,a['href']);txt=' '.join(a.stripped_strings);trail=norm(txt+' '+nxt);sc=score_link(txt,nxt)
   if not same_site(landing,nxt):continue
   follow=sc>4 or any(k in trail for k in ('election result','results archive','previous election','historical election','elections voting','county clerk elections'))
   if follow and not any(x in trail for x in ('2026','2022','2020','general election','sample ballot','audit','recount')):q.append((nxt,depth+1,max(sc,parent-8)))
 ded={}
 for x in out:
  if x[1] not in ded or x[0]>ded[x[1]][0]:ded[x[1]]=x
 return sorted(ded.values(),key=lambda x:x[0],reverse=True)
def parse_votes(text,cands):
 clean='\n'.join(x.strip() for x in text.splitlines() if x.strip());out={}
 for c in cands:
  best=None
  for alias in aliases(c['candidate']):
   ap='\\s+'.join(map(re.escape,alias.split()))
   for pat in [rf'(?i)\b{ap}\b[^\n]{{0,110}}?([0-9][0-9,]*)\b',rf'(?i)\b{ap}\b\s*\n(?:[^\n]*\n){{0,3}}?\s*([0-9][0-9,]*)\b']:
    for m in re.finditer(pat,clean):
     v=int(m.group(1).replace(',',''))
     if 0<=v<100000:best=max(best or 0,v)
  if best is not None:out[norm(c['candidate'])]=best
 return out
def county_check(src,cands):
 s=requests.Session();attempts=[]
 for landing in src['landing_urls']:
  try:
   for sc,url,content,ct in discover(s,landing)[:16]:
    text=pdf_text(content) if ('pdf' in ct or url.lower().split('?')[0].endswith('.pdf')) else BeautifulSoup(content,'html.parser').get_text('\n',strip=True);low=norm(text[:20000]+' '+url)
    if '2024' not in low or 'primary' not in low:continue
    votes=parse_votes(text,cands);attempts.append({'url':url,'score':sc,'matches':len(votes)})
    if votes:return {'county':src['county'],'status':'Parsed archived 2024 county results','url':url,'votes':votes,'matches':len(votes),'hash':hashlib.sha256(content).hexdigest()[:16],'attempts':attempts}
  except Exception as e:attempts.append({'url':landing,'error':repr(e)})
 return {'county':src['county'],'status':'No parseable 2024 county archive found','url':src['landing_urls'][0],'votes':{},'matches':0,'attempts':attempts}
def fc_names():
 try:
  with open(ROOT/'freedom_caucus_candidates.csv',newline='',encoding='utf-8') as f:return {norm(r['candidate']) for r in csv.DictReader(f)}
 except Exception:return set()
def aggregate(counties,cands):
 byname={norm(c['candidate']):c for c in cands};tot=defaultdict(lambda:{'votes':0,'counties':[]});fc=fc_names();contests=defaultdict(list)
 for c in cands:contests[(c['chamber'],c['district'],c['party'])].append(c)
 for info in counties:
  for n,v in info['votes'].items():
   if n in byname:tot[n]['votes']+=int(v);tot[n]['counties'].append(info['county'])
 races=[]
 for key in sorted(contests,key=lambda k:(0 if k[0]=='House' else 1,k[1],0 if k[2]=='Republican' else 1)):
  chamber,district,party=key;cs=[]
  for c in contests[key]:
   d=tot[norm(c['candidate'])];cs.append({'candidate':c['candidate'],'votes':d['votes'],'counties':sorted(set(d['counties'])),'is_fc':norm(c['candidate']) in fc})
  races.append({'chamber':chamber,'district':district,'party':party,'candidates':cs})
 return races
def main():
 cands=candidate_rows();sources=load_sources();from concurrent.futures import ThreadPoolExecutor,as_completed;counties=[]
 with ThreadPoolExecutor(max_workers=8) as pool:
  fs=[pool.submit(county_check,s,cands) for s in sources]
  for f in as_completed(fs):counties.append(f.result())
 counties=sorted(counties,key=lambda x:x['county']);direct=sum(bool(c['votes']) for c in counties);payload={'replay_year':2024,'test_mode':True,'source_note':'Archived 2024 county election pages; recursive same-domain discovery; candidate roster from Wyoming Secretary of State','sos_validation_url':SOS_RESULTS,'reporting_counties':direct,'county_status':[{'county':c['county'],'status':c['status'],'url':c['url'],'reporting':bool(c['votes']),'matches':c['matches']} for c in counties],'incumbents':{'House':{},'Senate':{}},'races':aggregate(counties,cands),'statewide_races':[]}
 (ROOT/'test_2024_results.json').write_text(json.dumps(payload,indent=2));(ROOT/'test_2024_diagnostics.json').write_text(json.dumps({'counties':counties},indent=2));print('2024 recursive replay built:',direct,'of 23 county archives parsed;',len(payload['races']),'party contests')
if __name__=='__main__':main()
