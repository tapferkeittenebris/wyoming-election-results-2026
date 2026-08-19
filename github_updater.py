#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, io, json, re
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo('America/Denver')
START=datetime(2026,8,18,15,0,tzinfo=TZ)
END=datetime(2026,8,19,2,0,tzinfo=TZ)
UA={'User-Agent':'Mozilla/5.0 WyomingElectionDashboard/2.0 (+election-results-monitor)'}
BAD=('2024','2022','2020','sample ballot','public test','test','testing','logic accuracy','expected result','audit','recount','canvass','candidate roster','candidate contact','primary election candidates','precinct committeeman')
LEG_API='https://web.wyoleg.gov/LsoService/api/legislator/2026/{chamber}'
SOS_RESULTS_PAGE='https://sos.wyo.gov/Elections/Docs/2026/2026PrimaryResults.aspx'
SOS_RESULTS_HOME='https://sos.wyo.gov/elections/electionresults.aspx'

@dataclass(frozen=True)
class Candidate:
    chamber:str; district:int; party:str; candidate:str

def norm(s):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+',' ',str(s).lower().replace('&',' and '))).strip()

def load_candidates():
    with open(ROOT/'candidates.csv',newline='',encoding='utf-8') as f:
        return [Candidate(r['chamber'],int(r['district']),r['party'],r['candidate']) for r in csv.DictReader(f)]

def load_statewide():
    with open(ROOT/'statewide_candidates.csv',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))

def statewide_parse_candidates(rows):return [Candidate('Statewide',0,r['party'],r['candidate']) for r in rows]
def load_sources():return json.loads((ROOT/'sources.json').read_text())
def fc_names():
    with open(ROOT/'freedom_caucus_candidates.csv',newline='',encoding='utf-8') as f:return {norm(r['candidate']) for r in csv.DictReader(f)}

def blank_state(sources):
    return {'last_checked':None,'last_updated':None,'sos_validation':{},'counties':{s['county']:{'source_url':s['landing_urls'][0],'result_url':None,'status':'Waiting for election-night results','votes':{}} for s in sources}}

def load_state(sources):
    p=ROOT/'state.json'
    if p.exists():
        try:
            d=json.loads(p.read_text())
            d.setdefault('sos_validation',{})
            for s in sources:d.setdefault('counties',{}).setdefault(s['county'],{'source_url':s['landing_urls'][0],'votes':{}})
            return d
        except Exception:pass
    return blank_state(sources)

def get(session,url,timeout=18):
    r=session.get(url,headers=UA,timeout=timeout,allow_redirects=True)
    r.raise_for_status();return r

def _legislator_dicts(obj):
    found=[]
    def walk(x):
        if isinstance(x,dict):
            kl={str(k).lower():k for k in x};has_d=('district' in kl or 'currentdistrict' in kl);has_n=('name' in kl or 'firstname' in kl or 'lastname' in kl)
            if has_d and has_n:found.append(x)
            else:
                for v in x.values():walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(obj);return found

def _pick(row,*names):
    lower={str(k).lower():v for k,v in row.items()}
    for n in names:
        v=lower.get(n.lower())
        if v not in (None,''):return v
    return ''

def refresh_incumbents():
    fc=fc_names();out={'House':{},'Senate':{}}
    try:
        s=requests.Session()
        for chamber,code in [('House','H'),('Senate','S')]:
            rows=_legislator_dicts(get(s,LEG_API.format(chamber=code)).json())
            for row in rows:
                district=_pick(row,'currentDistrict','district');name=_pick(row,'name') or ' '.join(filter(None,[str(_pick(row,'firstName')).strip(),str(_pick(row,'lastName')).strip()]));party=_pick(row,'party')
                try:d=str(int(str(district).strip()))
                except Exception:continue
                if name:out[chamber][d]={'name':str(name).strip(),'party':str(party).strip(),'is_fc':norm(name) in fc}
        if out['House'] or out['Senate']:(ROOT/'incumbents.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    except Exception as e:print('Incumbent refresh failed:',repr(e))
    p=ROOT/'incumbents.json'
    if p.exists():
        try:return json.loads(p.read_text())
        except Exception:pass
    return out

def pdf_text(content):
    try:return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:return ''

def has_bad_marker(value):
    s=' '+norm(value)+' '
    return any((' '+marker+' ') in s for marker in BAD)

def score_link(text,url):
    s=norm(text+' '+url)
    if has_bad_marker(s):return -100
    sc=(12 if '2026' in s else 0)+(11 if 'primary' in s else 0)+(8 if 'unofficial' in s else 0)+(7 if 'result' in s else 0)+(5 if 'return' in s else 0)+(4 if 'summary' in s else 0)+(3 if 'election' in s else 0)+(3 if url.lower().split('?')[0].endswith('.pdf') else 0)
    return sc

def same_site(a,b):
    ha=urlparse(a).netloc.lower().removeprefix('www.');hb=urlparse(b).netloc.lower().removeprefix('www.')
    return ha==hb

def discover(session,landing,max_depth=2,max_pages=24):
    """Bounded same-domain crawl. County sites often hide result PDFs one page below an archive page."""
    out=[];seen=set();q=deque([(landing,0,50)])
    while q and len(seen)<max_pages:
        url,depth,parent_score=q.popleft()
        if url in seen:continue
        seen.add(url)
        try:r=get(session,url)
        except Exception:continue
        ct=r.headers.get('content-type','').lower();final=r.url
        if 'pdf' in ct or final.lower().split('?')[0].endswith('.pdf'):
            out.append((max(parent_score,20),final,r.content,ct));continue
        soup=BeautifulSoup(r.content,'html.parser');page=norm(soup.get_text(' ',strip=True));ps=score_link(page[:2500],final)
        if '2026' in page and ('result' in page or 'return' in page):out.append((max(10,ps),final,r.content,ct))
        if depth>=max_depth:continue
        for a in soup.find_all('a',href=True):
            u=urljoin(final,a['href']);txt=' '.join(a.stripped_strings);sc=score_link(txt,u)
            if not same_site(landing,u):continue
            # Follow explicit result links, archive/history links, and election pages; reject obvious old-year content.
            trail=norm(txt+' '+u)
            follow=sc>0 or any(k in trail for k in ('election result','results archive','previous election','historical election','elections voting','county clerk elections'))
            if follow and not any(x in trail for x in BAD):q.append((u,depth+1,max(sc,parent_score-8)))
    # Highest-value direct documents first, de-duped by final URL.
    ded={}
    for item in out:
        if item[1] not in ded or item[0]>ded[item[1]][0]:ded[item[1]]=item
    return sorted(ded.values(),key=lambda x:x[0],reverse=True)

def aliases(name):
    n=norm(name);p=n.split();a={n}
    if len(p)>=2:a|={p[0]+' '+p[-1],p[-1]+' '+p[0]}
    a.update({
        'james r schellinger': {'jim schellinger'},
        'kenneth howard fitzpatrick': {'howie fitzpatrick'},
        'william levi dominguez': {'levi dominguez'},
    }.get(n,set()))
    return sorted(a,key=len,reverse=True)

def parse_votes(text,candidates):
    clean='\n'.join(x.strip() for x in text.splitlines() if x.strip());out={}
    for c in candidates:
        best=None
        for alias in aliases(c.candidate):
            ap=r'[^A-Za-z0-9\n]+'.join(map(re.escape,alias.split()))
            for pat in [rf'(?i)\b{ap}\b[^\n]{{0,110}}?([0-9][0-9,]*)\b',rf'(?i)\b{ap}\b\s*\n(?:[^\n]*\n){{0,3}}?\s*([0-9][0-9,]*)\b']:
                for m in re.finditer(pat,clean):
                    v=int(m.group(1).replace(',',''))
                    if 0<=v<150000:best=max(best or 0,v)
        if best is not None:out[norm(c.candidate)]=best
    return out

def check_county(source,candidates):
    session=requests.Session();errors=[]
    # Explicit URLs are tried first when a county-specific direct result target is known.
    targets=list(source.get('direct_urls',[]))+list(source['landing_urls'])
    for landing in targets:
        try:
            for _,url,content,ct in discover(session,landing)[:14]:
                is_pdf='pdf' in ct or (url.lower().split('?')[0].endswith('.pdf') and 'text/' not in ct)
                text=pdf_text(content) if is_pdf else BeautifulSoup(content,'html.parser').get_text('\n',strip=True)
                low=norm(text[:16000]+' '+url)
                if has_bad_marker(low):continue
                if '2026' not in low and 'primary' not in low:continue
                votes=parse_votes(text,candidates)
                if votes:return source['county'],'Reporting',url,votes,hashlib.sha256(content).hexdigest()[:16],errors
        except Exception as e:errors.append(f'{landing}: {type(e).__name__}')
    return source['county'],'Waiting for county results',None,{},None,errors

def check_sos_validation(candidates):
    """SOS is validation/fallback metadata only; county clerk totals remain the primary live feed."""
    s=requests.Session();result={'available':False,'page':SOS_RESULTS_PAGE,'summaries':[],'candidate_matches':0,'status':'2026 SOS primary results page not yet published'}
    pages=[SOS_RESULTS_PAGE,SOS_RESULTS_HOME]
    for page in pages:
        try:
            r=get(s,page);soup=BeautifulSoup(r.content,'html.parser');txt=norm(soup.get_text(' ',strip=True))
            if page==SOS_RESULTS_PAGE and ('2026' not in txt or 'primary' not in txt):continue
            links=[]
            for a in soup.find_all('a',href=True):
                label=norm(' '.join(a.stripped_strings));u=urljoin(r.url,a['href'])
                if any(k in label for k in ('statewide candidates summary','senate districts','house districts','precinct by precinct results')) and ('2026' in norm(u+' '+txt) or page==SOS_RESULTS_PAGE):links.append(u)
            if page==SOS_RESULTS_PAGE or links:
                result.update({'available':True,'page':r.url,'status':'SOS 2026 results page available for validation','summaries':sorted(set(links))[:30]})
                # Candidate-name presence is a sanity check, not a source of vote totals.
                for u in result['summaries'][:6]:
                    try:
                        rr=get(s,u);ct=rr.headers.get('content-type','').lower();body=pdf_text(rr.content) if 'pdf' in ct else BeautifulSoup(rr.content,'html.parser').get_text('\n',strip=True);bn=norm(body)
                        result['candidate_matches']+=sum(1 for c in candidates if norm(c.candidate) in bn)
                    except Exception:pass
                return result
        except Exception:pass
    return result

def aggregate(state,candidates):
    totals=defaultdict(lambda:defaultdict(lambda:{'votes':0,'counties':[]}));byname={norm(c.candidate):c for c in candidates};contests=defaultdict(list);fc=fc_names()
    for c in candidates:contests[(c.chamber,c.district,c.party)].append(c)
    for county,info in state['counties'].items():
        for name,v in info.get('votes',{}).items():
            c=byname.get(name)
            if c:key=(c.chamber,c.district,c.party);totals[key][name]['votes']+=int(v);totals[key][name]['counties'].append(county)
    races=[]
    for key in sorted(contests,key=lambda k:(0 if k[0]=='Senate' else 1,k[1],0 if k[2]=='Republican' else 1,k[2])):
        chamber,district,party=key;rows=[]
        for c in contests[key]:
            d=totals[key][norm(c.candidate)];rows.append({'candidate':c.candidate,'votes':d['votes'],'counties':sorted(d['counties']),'is_fc':norm(c.candidate) in fc})
        races.append({'chamber':chamber,'district':district,'party':party,'candidates':rows})
    return races

def aggregate_statewide(state,rows):
    order=['United States Senator','United States Representative','Governor','Secretary of State','State Auditor','State Treasurer','Superintendent of Public Instruction'];totals=defaultdict(lambda:{'votes':0,'counties':[]});contests=defaultdict(list);names={norm(r['candidate']):r for r in rows}
    for r in rows:contests[(r['office'],r['party'])].append(r)
    for county,info in state['counties'].items():
        for name,v in info.get('votes',{}).items():
            if name in names:totals[name]['votes']+=int(v);totals[name]['counties'].append(county)
    races=[]
    for key in sorted(contests,key=lambda k:(order.index(k[0]) if k[0] in order else 99,0 if k[1]=='Republican' else 1,k[1])):
        office,party=key;cs=[]
        for r in contests[key]:
            d=totals[norm(r['candidate'])];cs.append({'candidate':r['candidate'],'votes':d['votes'],'counties':sorted(set(d['counties'])),'is_fc':str(r.get('is_fc','')).lower()=='true'})
        races.append({'office':office,'party':party,'candidates':cs})
    return races

def export(state,candidates,incumbents,statewide):
    statuses=[]
    for county,info in sorted(state['counties'].items()):
        statuses.append({'county':county,'status':info.get('status','Waiting for county results'),'url':info.get('result_url') or info.get('source_url') or '#','reporting':bool(info.get('votes')),'errors':info.get('errors',[])})
    payload={'last_checked':state.get('last_checked'),'last_updated':state.get('last_updated'),'reporting_counties':sum(x['reporting'] for x in statuses),'county_status':statuses,'sos_validation':state.get('sos_validation',{}),'incumbents':incumbents,'races':aggregate(state,candidates),'statewide_races':aggregate_statewide(state,statewide)}
    (ROOT/'results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')

def main():
    candidates=load_candidates();statewide=load_statewide();parse_candidates=candidates+statewide_parse_candidates(statewide);sources=load_sources();state=load_state(sources);incumbents=refresh_incumbents();now=datetime.now(TZ)
    # Remove snapshots accidentally parsed from test decks, candidate rosters,
    # or contact sheets before a county posted its actual returns.
    for info in state['counties'].values():
        if info.get('votes') and has_bad_marker(info.get('result_url','')):
            info.update({'result_url':None,'status':'Waiting for county results','votes':{}})
            info.pop('result_hash',None);info.pop('last_success',None)
    # Always check whether SOS has published its 2026 validation/fallback page.
    state['sos_validation']=check_sos_validation(parse_candidates)
    if not (START<=now<=END):
        export(state,candidates,incumbents,statewide)
        if not (ROOT/'state.json').exists():(ROOT/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
        print('Outside election-night window; dashboard feeds refreshed only. SOS:',state['sos_validation'].get('status'));return
    state['last_checked']=now.isoformat(timespec='seconds');changed=False
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(check_county,s,parse_candidates) for s in sources]
        for f in as_completed(futures):
            county,status,url,votes,digest,errors=f.result();info=state['counties'][county];prev=info.get('votes',{})
            info['errors']=errors[-4:]
            if votes:
                info.update({'status':status,'result_url':url,'votes':votes,'result_hash':digest,'last_success':state['last_checked']});changed|=(votes!=prev)
            elif prev:info['status']='Reporting (last good county snapshot; current check unavailable)'
            else:info['status']=status+(' · SOS validation available' if state['sos_validation'].get('available') else '')
    if changed:state['last_updated']=state['last_checked']
    (ROOT/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8');export(state,candidates,incumbents,statewide);print('Updated results.json',state['last_checked'],'counties',sum(bool(x.get('votes')) for x in state['counties'].values()),'SOS',state['sos_validation'].get('available'))

if __name__=='__main__':main()
