#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, re, hashlib
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
YEAR = '2024'
UA = {'User-Agent':'Mozilla/5.0 WyomingElectionDashboardReplay/1.0'}
CANDIDATE_CSV = 'https://sos.wyo.gov/Elections/Docs/2024/2024_WY_Primary_Election_Candidates.csv'
SOS_RESULTS = 'https://sos.wyo.gov/Elections/Docs/2024/2024PrimaryResults.aspx'


def norm(s):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+',' ',str(s).lower().replace('&',' and '))).strip()

def get(session,url):
    r=session.get(url,headers=UA,timeout=25,allow_redirects=True)
    r.raise_for_status()
    return r

def pdf_text(content):
    try:return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:return ''

def aliases(name):
    n=norm(name); p=n.split(); out={n}
    if len(p)>=2: out|={p[0]+' '+p[-1],p[-1]+' '+p[0]}
    return sorted(out,key=len,reverse=True)

def candidate_rows():
    s=requests.Session(); r=get(s,CANDIDATE_CSV)
    text=r.content.decode('utf-8-sig',errors='replace')
    rows=list(csv.DictReader(io.StringIO(text)))
    out=[]
    for row in rows:
        low={norm(k):str(v or '').strip() for k,v in row.items()}
        office=' '.join([low.get('office',''),low.get('office sought',''),low.get('position','')]).strip()
        party=low.get('party','') or low.get('political party','')
        district=low.get('district','') or low.get('district number','')
        cand=low.get('candidate','') or low.get('candidate name','') or low.get('name','')
        if not cand:
            first=low.get('first name','') or low.get('first','')
            middle=low.get('middle name','') or low.get('middle','')
            last=low.get('last name','') or low.get('last','')
            cand=' '.join(x for x in [first,middle,last] if x)
        blob=norm(' '.join(str(v or '') for v in row.values()))
        chamber=None
        if 'state senate' in norm(office) or 'senate district' in blob or 'state senator' in blob: chamber='Senate'
        elif 'state house' in norm(office) or 'house district' in blob or 'state representative' in blob: chamber='House'
        if not chamber or not cand: continue
        if not district:
            m=re.search(r'\b(?:district|dist)\s*(\d{1,2})\b',blob)
            district=m.group(1) if m else ''
        try: d=int(re.sub(r'\D','',district))
        except Exception: continue
        p='Republican' if 'republican' in norm(party or blob) else 'Democratic' if 'democrat' in norm(party or blob) else party.title()
        if p not in ('Republican','Democratic'): continue
        out.append({'chamber':chamber,'district':d,'party':p,'candidate':cand})
    if not out:
        raise RuntimeError('Could not parse legislative candidates from 2024 SOS candidate CSV; columns='+repr(list(rows[0].keys()) if rows else []))
    return out

def load_sources(): return json.loads((ROOT/'sources.json').read_text())

def score_link(text,url):
    s=norm(text+' '+url)
    score=0
    if '2024' in s: score+=14
    if 'primary' in s: score+=10
    if 'result' in s or 'return' in s: score+=8
    if 'unofficial' in s or 'official' in s: score+=3
    if 'summary' in s: score+=2
    if url.lower().endswith('.pdf'): score+=2
    if any(x in s for x in ('2026','2022','2020','general election','sample ballot','audit','recount')): score-=18
    return score

def discover(session,landing):
    r=get(session,landing); ct=r.headers.get('content-type','').lower(); out=[]
    if 'pdf' in ct or r.url.lower().endswith('.pdf'):
        return [(50,r.url,r.content,ct)]
    soup=BeautifulSoup(r.content,'html.parser')
    page=norm(soup.get_text(' ',strip=True))
    if '2024' in page and ('primary' in page or 'result' in page): out.append((10,r.url,r.content,ct))
    for a in soup.find_all('a',href=True):
        u=urljoin(r.url,a['href']); sc=score_link(' '.join(a.stripped_strings),u)
        if sc>5:
            try:
                rr=get(session,u)
                out.append((sc,rr.url,rr.content,rr.headers.get('content-type','').lower()))
            except Exception: pass
    return sorted(out,key=lambda x:x[0],reverse=True)

def parse_votes(text,candidates):
    clean='\n'.join(x.strip() for x in text.splitlines() if x.strip()); out={}
    for c in candidates:
        best=None
        for alias in aliases(c['candidate']):
            ap='\\s+'.join(map(re.escape,alias.split()))
            pats=[rf'(?i)\b{ap}\b[^\n]{{0,100}}?([0-9][0-9,]*)\b',rf'(?i)\b{ap}\b\s*\n(?:[^\n]*\n){{0,3}}?\s*([0-9][0-9,]*)\b']
            for pat in pats:
                for m in re.finditer(pat,clean):
                    v=int(m.group(1).replace(',',''))
                    if 0<=v<100000: best=max(best or 0,v)
        if best is not None: out[norm(c['candidate'])]=best
    return out

def county_check(source,candidates):
    s=requests.Session(); attempts=[]
    for landing in source['landing_urls']:
        try:
            found=discover(s,landing)
            for sc,url,content,ct in found[:12]:
                text=pdf_text(content) if ('pdf' in ct or url.lower().endswith('.pdf')) else BeautifulSoup(content,'html.parser').get_text('\n',strip=True)
                low=norm(text[:16000])
                if '2024' not in norm(url+' '+low) or 'primary' not in norm(url+' '+low): continue
                votes=parse_votes(text,candidates)
                attempts.append({'url':url,'score':sc,'matches':len(votes)})
                if votes:
                    return {'county':source['county'],'status':'Parsed archived 2024 county results','url':url,'votes':votes,'matches':len(votes),'hash':hashlib.sha256(content).hexdigest()[:16],'attempts':attempts}
        except Exception as e:
            attempts.append({'url':landing,'error':repr(e)})
    return {'county':source['county'],'status':'No parseable 2024 county archive found','url':source['landing_urls'][0],'votes':{},'matches':0,'attempts':attempts}

def fc_names():
    try:
        with open(ROOT/'freedom_caucus_candidates.csv',newline='',encoding='utf-8') as f:return {norm(r['candidate']) for r in csv.DictReader(f)}
    except Exception:return set()

def aggregate(counties,candidates):
    byname={norm(c['candidate']):c for c in candidates}; totals=defaultdict(lambda:{'votes':0,'counties':[]}); fc=fc_names()
    for info in counties:
        for n,v in info['votes'].items():
            if n in byname:
                totals[n]['votes']+=int(v); totals[n]['counties'].append(info['county'])
    contests=defaultdict(list)
    for c in candidates: contests[(c['chamber'],c['district'],c['party'])].append(c)
    races=[]
    for key in sorted(contests,key=lambda k:(0 if k[0]=='House' else 1,k[1],0 if k[2]=='Republican' else 1)):
        chamber,district,party=key; cs=[]
        for c in contests[key]:
            d=totals[norm(c['candidate'])]
            cs.append({'candidate':c['candidate'],'votes':d['votes'],'counties':sorted(set(d['counties'])),'is_fc':norm(c['candidate']) in fc})
        races.append({'chamber':chamber,'district':district,'party':party,'candidates':cs})
    return races

def main():
    candidates=candidate_rows(); sources=load_sources()
    from concurrent.futures import ThreadPoolExecutor,as_completed
    counties=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fs=[pool.submit(county_check,s,candidates) for s in sources]
        for f in as_completed(fs): counties.append(f.result())
    counties=sorted(counties,key=lambda x:x['county'])
    payload={'replay_year':2024,'test_mode':True,'source_note':'Archived 2024 county election pages; candidate roster from Wyoming Secretary of State','sos_validation_url':SOS_RESULTS,'reporting_counties':sum(bool(c['votes']) for c in counties),'county_status':[{'county':c['county'],'status':c['status'],'url':c['url'],'reporting':bool(c['votes']),'matches':c['matches']} for c in counties],'incumbents':{'House':{},'Senate':{}},'races':aggregate(counties,candidates),'statewide_races':[]}
    (ROOT/'test_2024_results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    (ROOT/'test_2024_diagnostics.json').write_text(json.dumps({'counties':counties},indent=2),encoding='utf-8')
    print('2024 replay built:',payload['reporting_counties'],'of 23 county archives parsed;',len(payload['races']),'party contests')
if __name__=='__main__': main()
