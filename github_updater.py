#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, io, json, re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo('America/Denver')
START=datetime(2026,8,18,19,0,tzinfo=TZ); END=datetime(2026,8,19,2,0,tzinfo=TZ)
UA={'User-Agent':'Mozilla/5.0 WyomingElectionDashboard/1.0'}
GOOD=('2026','primary','unofficial','result','summary','election night')
BAD=('2024','2022','2020','sample ballot','public test','testing','expected result','audit','recount','canvass')

@dataclass(frozen=True)
class Candidate:
    chamber:str; district:int; party:str; candidate:str

def norm(s:str)->str:
    s=s.lower().replace('&',' and ')
    s=re.sub(r'[^a-z0-9 ]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def load_candidates():
    with open(ROOT/'candidates.csv',newline='',encoding='utf-8') as f:
        return [Candidate(r['chamber'],int(r['district']),r['party'],r['candidate']) for r in csv.DictReader(f)]

def load_sources(): return json.loads((ROOT/'sources.json').read_text())
def fc_names():
    with open(ROOT/'freedom_caucus_candidates.csv',newline='',encoding='utf-8') as f:
        return {norm(r['candidate']) for r in csv.DictReader(f)}

def blank_state(sources):
    return {'last_checked':None,'last_updated':None,'counties':{s['county']:{'source_url':s['landing_urls'][0],'result_url':None,'status':'Waiting for election-night results','votes':{}} for s in sources}}

def load_state(sources):
    p=ROOT/'state.json'
    if p.exists():
        try:
            d=json.loads(p.read_text());
            for s in sources:d.setdefault('counties',{}).setdefault(s['county'],{'source_url':s['landing_urls'][0],'votes':{}})
            return d
        except Exception: pass
    return blank_state(sources)

def get(session,url):
    r=session.get(url,headers=UA,timeout=15,allow_redirects=True); r.raise_for_status(); return r

def html_text(content): return BeautifulSoup(content,'html.parser').get_text('\n',strip=True)
def pdf_text(content):
    try:return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:return ''

def score_link(text,url):
    s=norm(text+' '+url); score=0
    if any(x in s for x in BAD): return -100
    if '2026' in s: score+=8
    if 'primary' in s: score+=7
    if 'unofficial' in s: score+=6
    if 'result' in s: score+=5
    if 'summary' in s: score+=2
    if url.lower().endswith('.pdf'): score+=2
    return score

def discover(session,landing):
    r=get(session,landing); ct=r.headers.get('content-type','').lower()
    if 'pdf' in ct or r.url.lower().endswith('.pdf'): return [(50,r.url,r.content,ct)]
    soup=BeautifulSoup(r.content,'html.parser'); out=[]
    page_norm=norm(soup.get_text(' ',strip=True))
    if '2026' in page_norm and 'result' in page_norm: out.append((10,r.url,r.content,ct))
    for a in soup.find_all('a',href=True):
        u=urljoin(r.url,a['href']); label=' '.join(a.stripped_strings); sc=score_link(label,u)
        if sc>0:
            try:
                rr=get(session,u); out.append((sc,rr.url,rr.content,rr.headers.get('content-type','').lower()))
            except Exception: pass
    return sorted(out,key=lambda x:x[0],reverse=True)

def candidate_aliases(name):
    n=norm(name); parts=n.split(); aliases={n}
    if len(parts)>=2: aliases.add(parts[0]+' '+parts[-1]); aliases.add(parts[-1]+' '+parts[0])
    return sorted(aliases,key=len,reverse=True)

def parse_votes(text,candidates):
    clean='\n'.join(x.strip() for x in text.splitlines() if x.strip()); lines=clean.splitlines(); out={}
    for c in candidates:
        best=None
        for alias in candidate_aliases(c.candidate):
            ap='\\s+'.join(map(re.escape,alias.split()))
            patterns=[rf'(?i)\b{ap}\b[^\n]{{0,90}}?([0-9][0-9,]*)\b',rf'(?i)\b{ap}\b\s*\n(?:[^\n]*\n){{0,2}}?\s*([0-9][0-9,]*)\b']
            for p in patterns:
                for m in re.finditer(p,clean):
                    v=int(m.group(1).replace(',',''))
                    if 0<=v<100000: best=max(best or 0,v)
        if best is not None: out[norm(c.candidate)]=best
    return out

def check_county(source,candidates):
    session=requests.Session(); errors=[]
    for landing in source['landing_urls']:
        try:
            found=discover(session,landing)
            for sc,url,content,ct in found[:8]:
                text=pdf_text(content) if ('pdf' in ct or url.lower().endswith('.pdf')) else html_text(content)
                low=norm(text[:12000])
                if any(b in low for b in BAD) and not ('unofficial' in low and '2026' in low): continue
                votes=parse_votes(text,candidates)
                if votes:return source['county'],'Reporting',url,votes,hashlib.sha256(content).hexdigest()[:16]
        except Exception as e: errors.append(str(e)[:120])
    return source['county'],'Waiting for election-night results',None,{},None

def aggregate(state,candidates):
    totals=defaultdict(lambda:defaultdict(lambda:{'votes':0,'counties':[]})); byname={norm(c.candidate):c for c in candidates}
    for county,info in state['counties'].items():
        for name,v in info.get('votes',{}).items():
            c=byname.get(name)
            if not c: continue
            key=(c.chamber,c.district,c.party); totals[key][name]['votes']+=int(v); totals[key][name]['counties'].append(county)
    races=[]; fc=fc_names()
    contests=defaultdict(list)
    for c in candidates: contests[(c.chamber,c.district,c.party)].append(c)
    for key in sorted(contests,key=lambda k:(0 if k[0]=='Senate' else 1,k[1],0 if k[2]=='Republican' else 1,k[2])):
        chamber,district,party=key; rows=[]
        for c in contests[key]:
            d=totals[key][norm(c.candidate)]
            rows.append({'candidate':c.candidate,'votes':d['votes'],'counties':sorted(d['counties']),'is_fc':norm(c.candidate) in fc})
        races.append({'chamber':chamber,'district':district,'party':party,'candidates':rows})
    return races

def export(state,candidates):
    statuses=[]
    for county,info in sorted(state['counties'].items()):
        statuses.append({'county':county,'status':info.get('status','Waiting for election-night results'),'url':info.get('result_url') or info.get('source_url') or '#','reporting':bool(info.get('votes'))})
    payload={'last_checked':state.get('last_checked'),'last_updated':state.get('last_updated'),'reporting_counties':sum(x['reporting'] for x in statuses),'county_status':statuses,'races':aggregate(state,candidates)}
    (ROOT/'results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')

def main():
    candidates=load_candidates(); sources=load_sources(); state=load_state(sources); now=datetime.now(TZ)
    state['last_checked']=now.isoformat(timespec='seconds')
    if not (START<=now<=END):
        export(state,candidates); (ROOT/'state.json').write_text(json.dumps(state,indent=2)); print('Outside election-night window; feed initialized only.'); return
    changed=False
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(check_county,s,candidates):s for s in sources}
        for f in as_completed(futures):
            county,status,url,votes,digest=f.result(); info=state['counties'][county]; prev=info.get('votes',{})
            if votes:
                info.update({'status':status,'result_url':url,'votes':votes,'result_hash':digest,'last_success':state['last_checked']}); changed|=(votes!=prev)
            elif prev: info['status']='Reporting (last good snapshot; current check unavailable)'
            else: info['status']=status
    if changed: state['last_updated']=state['last_checked']
    (ROOT/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8'); export(state,candidates)
    print('Updated results.json',state['last_checked'])
if __name__=='__main__': main()
