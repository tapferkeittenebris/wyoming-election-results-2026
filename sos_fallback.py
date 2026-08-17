#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo('America/Denver')
SOS_RESULTS_PAGE='https://sos.wyo.gov/Elections/Docs/2026/2026PrimaryResults.aspx'
UA={'User-Agent':'Mozilla/5.0 WyomingElectionDashboard/2.0 (+SOS-fallback)'}


def norm(s):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+',' ',str(s).lower().replace('&',' and '))).strip()


def aliases(name):
    n=norm(name); p=n.split(); out={n}
    if len(p)>=2: out|={p[0]+' '+p[-1],p[-1]+' '+p[0]}
    return sorted(out,key=len,reverse=True)


def candidate_names():
    names=[]
    with open(ROOT/'candidates.csv',newline='',encoding='utf-8') as f:
        names.extend(r['candidate'] for r in csv.DictReader(f))
    with open(ROOT/'statewide_candidates.csv',newline='',encoding='utf-8') as f:
        names.extend(r['candidate'] for r in csv.DictReader(f))
    return list(dict.fromkeys(names))


def get(session,url,timeout=22):
    r=session.get(url,headers=UA,timeout=timeout,allow_redirects=True)
    r.raise_for_status(); return r


def pdf_text(content):
    try:
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ''


def sos_county_links(session):
    try:
        r=get(session,SOS_RESULTS_PAGE)
    except Exception as e:
        print('SOS fallback unavailable:',repr(e)); return {},None
    soup=BeautifulSoup(r.content,'html.parser')
    page=norm(soup.get_text(' ',strip=True))
    if '2026' not in page or 'primary' not in page:
        print('SOS 2026 primary results page not yet populated'); return {},r.url
    links={}; current=None
    for node in soup.find_all(['h4','a']):
        if node.name=='h4':
            t=' '.join(node.stripped_strings).strip()
            m=re.match(r'(.+?)\s+County$',t,re.I)
            current=m.group(1).strip() if m else None
        elif current and node.get('href'):
            label=norm(' '.join(node.stripped_strings))
            if 'precinct by precinct' in label and 'result' in label and current not in links:
                links[current]=urljoin(r.url,node['href'])
    if len(links)<10:
        for a in soup.find_all('a',href=True):
            label=norm(' '.join(a.stripped_strings))
            if 'precinct by precinct' not in label or 'result' not in label: continue
            parent=a.parent
            for _ in range(5):
                if not parent: break
                txt=' '.join(parent.stripped_strings)
                m=re.search(r'([A-Za-z ]+?)\s+County',txt)
                if m:
                    links.setdefault(m.group(1).strip(),urljoin(r.url,a['href'])); break
                parent=parent.parent
    return links,r.url


def parse_votes(text,names):
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    nlines=[norm(x) for x in lines]
    out={}
    for name in names:
        best=None
        for alias in aliases(name):
            for i,nline in enumerate(nlines):
                if alias not in nline: continue
                sample=' '.join(lines[i:i+3])
                nums=[]
                for token in re.findall(r'(?<![A-Za-z])\d[\d,]*(?![A-Za-z])',sample):
                    try:v=int(token.replace(',',''))
                    except Exception:continue
                    if 0<=v<150000: nums.append(v)
                if nums: best=max(best or 0,max(nums))
        if best is not None: out[norm(name)]=best
    return out


def export_live(state):
    import github_updater as live
    incumbents={}
    try: incumbents=json.loads((ROOT/'incumbents.json').read_text(encoding='utf-8'))
    except Exception: incumbents=live.refresh_incumbents()
    live.export(state,live.load_candidates(),incumbents,live.load_statewide())


def main():
    state_path=ROOT/'state.json'
    if not state_path.exists():
        print('No state.json; SOS fallback skipped'); return
    state=json.loads(state_path.read_text(encoding='utf-8'))
    missing=[c for c,info in state.get('counties',{}).items() if not info.get('votes')]
    if not missing:
        print('All counties already have results; SOS fallback not needed'); return
    s=requests.Session(); links,page=sos_county_links(s)
    if not links:
        print('No SOS county precinct links available yet'); return
    names=candidate_names(); changed=False; filled=[]
    lower_links={norm(k):v for k,v in links.items()}
    for county in missing:
        url=lower_links.get(norm(county))
        if not url: continue
        try:
            r=get(s,url); ct=r.headers.get('content-type','').lower()
            text=pdf_text(r.content) if ('pdf' in ct or r.url.lower().split('?')[0].endswith('.pdf')) else BeautifulSoup(r.content,'html.parser').get_text('\n',strip=True)
            votes=parse_votes(text,names)
            if not votes: continue
            info=state['counties'][county]
            info.update({'status':'Reporting via Wyoming SOS fallback','result_url':r.url,'votes':votes,'source':'Wyoming Secretary of State county precinct summary','last_success':datetime.now(TZ).isoformat(timespec='seconds')})
            filled.append(county); changed=True
        except Exception as e:
            print('SOS fallback failed for',county,repr(e))
    if changed:
        state['last_updated']=datetime.now(TZ).isoformat(timespec='seconds')
        state.setdefault('sos_validation',{})['fallback_counties']=filled
        state['sos_validation']['fallback_page']=page
        state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        export_live(state)
        print('SOS fallback populated',len(filled),'counties:',', '.join(filled))
    else:
        print('SOS fallback found no additional parseable county totals')

if __name__=='__main__': main()
