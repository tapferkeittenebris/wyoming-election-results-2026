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
SOS_SUMMARIES=(
    'https://sos.wyo.gov/Elections/Docs/2026/Results/Primary/2026_Statewide_Candidates_Summary.pdf',
    'https://sos.wyo.gov/Elections/Docs/2026/Results/Primary/2026_Statewide_House_Candidates_Summary.pdf',
    'https://sos.wyo.gov/Elections/Docs/2026/Results/Primary/2026_Statewide_Senate_Candidates_Summary.pdf',
)
UA={'User-Agent':'Mozilla/5.0 WyomingElectionDashboard/2.0 (+SOS-fallback)'}
COUNTIES=('Albany','Big Horn','Campbell','Carbon','Converse','Crook','Fremont','Goshen','Hot Springs','Johnson','Laramie','Lincoln','Natrona','Niobrara','Park','Platte','Sheridan','Sublette','Sweetwater','Teton','Uinta','Washakie','Weston')


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


def candidate_rows():
    rows=[]
    with open(ROOT/'candidates.csv',newline='',encoding='utf-8') as f:
        rows.extend(csv.DictReader(f))
    with open(ROOT/'statewide_candidates.csv',newline='',encoding='utf-8') as f:
        rows.extend(csv.DictReader(f))
    return rows


def get(session,url,timeout=22):
    r=session.get(url,headers=UA,timeout=timeout,allow_redirects=True)
    r.raise_for_status(); return r


def pdf_text(content):
    try:
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ''


def candidate_key(row):
    return (row.get('office') or row.get('chamber'),int(row.get('district') or 0))


def candidate_surname(name):
    parts=re.findall(r'[A-Za-z]+',name)
    while parts and parts[-1].lower() in {'jr','sr','ii','iii','iv'}:
        parts.pop()
    return parts[-1] if parts else ''


def page_contest(header):
    for chamber in ('House','Senate'):
        m=re.search(rf'{chamber}\s+District\s+(\d+)',header,re.I)
        if m:
            return chamber,int(m.group(1))
    for office in ('United States Senator','United States Representative','Governor','Secretary of State','State Auditor','State Treasurer','Superintendent of Public Instruction'):
        if office.lower() in header.lower():
            return office,0
    return None


def parse_summary_rows(content,rows):
    """Read county totals from the fixed-column tables in SOS statewide summaries."""
    found={}
    try:
        pages=PdfReader(io.BytesIO(content)).pages
    except Exception:
        return found
    by_contest={}
    for row in rows:
        by_contest.setdefault(candidate_key(row),[]).append(row['candidate'])
    for page in pages:
        try:
            lines=page.extract_text(extraction_mode='layout').splitlines()
            first_county=next(i for i,line in enumerate(lines) if line.startswith('Albany'))
        except Exception:
            continue
        contest=page_contest('\n'.join(lines[:first_county]))
        if not contest:
            continue
        anchors=[]
        for name in by_contest.get(contest,[]):
            surname=candidate_surname(name)
            positions=[]
            for line in lines[:first_county]:
                positions.extend(m.start()+len(m.group())/2 for m in re.finditer(rf'\b{re.escape(surname)}\b',line,re.I))
            if positions:
                anchors.append((name,max(positions)))
        for county in COUNTIES:
            line=next((line for line in lines[first_county:] if line.startswith(county) and (len(line)==len(county) or line[len(county)].isspace())),None)
            if not line:
                continue
            numbers=[(m.start()+len(m.group())/2,int(m.group().replace(',',''))) for m in re.finditer(r'\d[\d,]*',line)]
            used=set()
            for name,anchor in anchors:
                choices=[(abs(center-(anchor+9)),index,value) for index,(center,value) in enumerate(numbers) if index not in used]
                if not choices:
                    continue
                distance,index,value=min(choices)
                if distance<=15:
                    used.add(index)
                    found.setdefault(county,{})[norm(name)]=value
    return found


def sos_summary_votes(session):
    votes={}; used=[]; rows=candidate_rows()
    for url in SOS_SUMMARIES:
        try:
            r=get(session,url)
            parsed=parse_summary_rows(r.content,rows)
            if parsed:
                used.append(r.url)
            for county,county_votes in parsed.items():
                votes.setdefault(county,{}).update(county_votes)
        except Exception as e:
            print('SOS summary fallback failed for',url,repr(e))
    return votes,used


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
    s=requests.Session(); changed=False; filled=[]
    summary_votes,summary_urls=sos_summary_votes(s)
    for county in missing:
        votes=summary_votes.get(county,{})
        if not votes:
            continue
        info=state['counties'][county]
        info.update({'status':'Reporting via Wyoming SOS statewide summary','result_url':summary_urls[0] if summary_urls else SOS_RESULTS_PAGE,'votes':votes,'source':'Wyoming Secretary of State statewide summary','last_success':datetime.now(TZ).isoformat(timespec='seconds')})
        filled.append(county); changed=True
    state.setdefault('sos_validation',{})['summary_fallback_counties']=list(filled)
    state['sos_validation']['summary_urls']=summary_urls

    missing=[c for c in missing if not state['counties'][c].get('votes')]
    links,page=sos_county_links(s)
    if not links:
        print('No SOS county precinct links available yet')
    names=candidate_names()
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
