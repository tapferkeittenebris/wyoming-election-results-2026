#!/usr/bin/env python3
from __future__ import annotations
import csv,json,re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

import github_updater as base
import github_updater_hardened as hard

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo('America/Denver')

@dataclass(frozen=True)
class C:
    category:str; office:str; party:str; candidate:str; scope:str

def norm(s):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+',' ',str(s).lower().replace('&',' and '))).strip()

def load_rows():
    with open(ROOT/'sheridan_candidates.csv',newline='',encoding='utf-8') as f:
        return [C(**r) for r in csv.DictReader(f)]

def source():
    for s in json.loads((ROOT/'sources.json').read_text(encoding='utf-8')):
        if s['county']=='Sheridan': return s
    raise RuntimeError('Sheridan source missing')

def parse_text(content,ct,url):
    if 'pdf' in ct or (url.lower().split('?')[0].endswith('.pdf') and 'text/' not in ct):
        return base.pdf_text(content)
    return BeautifulSoup(content,'html.parser').get_text('\n',strip=True)

def main():
    rows=load_rows(); src=source(); session=base.requests.Session(); best=None; errors=[]
    targets=list(src.get('direct_urls',[]))+list(src.get('landing_urls',[]))
    for landing in targets:
        try:
            for score,url,content,ct in hard.discover(session,landing)[:18]:
                text=parse_text(content,ct,url)
                low=norm(text[:24000]+' '+url)
                if '2026' not in low and 'primary' not in low: continue
                votes=base.parse_votes(text,rows)
                if votes:
                    best=(score,url,votes); break
            if best: break
        except Exception as e:
            errors.append(f'{landing}: {type(e).__name__}')
    grouped=defaultdict(lambda:{'category':'','office':'','party':'','scope':'','candidates':[]})
    votes=(best[2] if best else {})
    for r in rows:
        k=(r.category,r.office,r.party,r.scope)
        g=grouped[k]; g.update({'category':r.category,'office':r.office,'party':r.party,'scope':r.scope})
        g['candidates'].append({'candidate':r.candidate,'votes':int(votes.get(norm(r.candidate),0))})
    races=[]
    order={'Federal':0,'Statewide':1,'Legislature':2,'County':3,'Municipal':4}
    for g in grouped.values():
        g['candidates'].sort(key=lambda c:(-c['votes'],c['candidate']))
        races.append(g)
    races.sort(key=lambda r:(order.get(r['category'],99),r['office'],0 if r['party']=='Republican' else 1 if r['party']=='Democratic' else 2))
    payload={
      'last_checked':datetime.now(TZ).isoformat(timespec='seconds'),
      'last_updated':datetime.now(TZ).isoformat(timespec='seconds') if best else None,
      'status':'Reporting' if best else 'Waiting for Sheridan County results',
      'source_url':best[1] if best else src.get('direct_urls',[src['landing_urls'][0]])[0],
      'errors':errors,
      'races':races
    }
    (ROOT/'sheridan-results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(payload['status'],payload['source_url'])

if __name__=='__main__': main()
