#!/usr/bin/env python3
from __future__ import annotations

import csv, hashlib, io, json, re
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
UA = {'User-Agent': 'Mozilla/5.0 WyomingElectionDashboardRegression/1.0'}
CANDIDATE_CSV = 'https://sos.wyo.gov/Elections/Docs/2024/2024_WY_Primary_Election_Candidates.csv'
MIN_CANDIDATES = 140
MIN_COUNTIES = 10
MIN_CONTESTS = 85


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', str(s).lower().replace('&', ' and '))).strip()


def get(session, url, timeout=20):
    r = session.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r


def pdf_text(content):
    try:
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ''


def same_site(a, b):
    ha = urlparse(a).netloc.lower().removeprefix('www.')
    hb = urlparse(b).netloc.lower().removeprefix('www.')
    return ha == hb


def aliases(name):
    n = norm(name); p = n.split(); out = {n}
    if len(p) >= 2:
        out |= {p[0] + ' ' + p[-1], p[-1] + ' ' + p[0]}
    return sorted(out, key=len, reverse=True)


def candidate_rows():
    r = get(requests.Session(), CANDIDATE_CSV)
    rows = list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig', errors='replace'))))
    out = []
    for row in rows:
        low = {norm(k): str(v or '').strip() for k, v in row.items()}
        office = low.get('office sought', '')
        party = low.get('party affiliation', '')
        cand = low.get('ballot name', '') or ' '.join(x for x in [low.get('candidate first name', ''), low.get('candidate middle name', ''), low.get('candidate last name', '')] if x)
        o = norm(office)
        chamber = 'Senate' if o.startswith('state senator') else 'House' if o.startswith('state representative') else None
        if not chamber or not cand:
            continue
        m = re.search(r'\b(\d{1,2})\b', o)
        if not m:
            continue
        p = 'Republican' if norm(party) in ('rep', 'republican') or 'republican' in o else 'Democratic' if norm(party) in ('dem', 'democratic') or 'democratic' in o else ''
        if p:
            out.append({'chamber': chamber, 'district': int(m.group(1)), 'party': p, 'candidate': cand})
    return out


def score_link(text, url):
    s = norm(text + ' ' + url)
    score = (14 if '2024' in s else 0) + (10 if 'primary' in s else 0) + (8 if ('result' in s or 'return' in s) else 0) + (3 if ('official' in s or 'unofficial' in s) else 0) + (2 if 'summary' in s else 0) + (2 if url.lower().split('?')[0].endswith('.pdf') else 0)
    if any(x in s for x in ('2026', '2022', '2020', 'general election', 'sample ballot', 'audit', 'recount')):
        score -= 18
    return score


def discover(session, landing, max_depth=2, max_pages=24):
    out = []; seen = set(); q = deque([(landing, 0, 50)])
    while q and len(seen) < max_pages:
        url, depth, parent_score = q.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            r = get(session, url)
        except Exception:
            continue
        ct = r.headers.get('content-type', '').lower(); final = r.url
        if 'pdf' in ct or final.lower().split('?')[0].endswith('.pdf'):
            out.append((max(parent_score, 20), final, r.content, ct)); continue
        soup = BeautifulSoup(r.content, 'html.parser'); page = norm(soup.get_text(' ', strip=True))
        ps = score_link(page[:2500], final)
        if '2024' in page and ('primary' in page or 'result' in page):
            out.append((max(10, ps), final, r.content, ct))
        if depth >= max_depth:
            continue
        for a in soup.find_all('a', href=True):
            u = urljoin(final, a['href']); txt = ' '.join(a.stripped_strings); sc = score_link(txt, u); trail = norm(txt + ' ' + u)
            if not same_site(landing, u):
                continue
            follow = sc > 0 or any(k in trail for k in ('election result', 'results archive', 'previous election', 'historical election', 'elections voting', 'county clerk elections'))
            if follow and not any(x in trail for x in ('2026', '2022', '2020', 'general election', 'sample ballot')):
                q.append((u, depth + 1, max(sc, parent_score - 8)))
    ded = {}
    for item in out:
        if item[1] not in ded or item[0] > ded[item[1]][0]:
            ded[item[1]] = item
    return sorted(ded.values(), key=lambda x: x[0], reverse=True)


def parse_votes(text, candidates):
    clean = '\n'.join(x.strip() for x in text.splitlines() if x.strip()); out = {}
    for c in candidates:
        best = None
        for alias in aliases(c['candidate']):
            ap = '\\s+'.join(map(re.escape, alias.split()))
            for pat in [rf'(?i)\b{ap}\b[^\n]{{0,110}}?([0-9][0-9,]*)\b', rf'(?i)\b{ap}\b\s*\n(?:[^\n]*\n){{0,3}}?\s*([0-9][0-9,]*)\b']:
                for m in re.finditer(pat, clean):
                    v = int(m.group(1).replace(',', ''))
                    if 0 <= v < 150000:
                        best = max(best or 0, v)
        if best is not None:
            out[norm(c['candidate'])] = best
    return out


def county_check(source, candidates):
    session = requests.Session()
    for landing in source['landing_urls']:
        try:
            for _, url, content, ct in discover(session, landing)[:14]:
                text = pdf_text(content) if ('pdf' in ct or url.lower().split('?')[0].endswith('.pdf')) else BeautifulSoup(content, 'html.parser').get_text('\n', strip=True)
                low = norm(text[:16000] + ' ' + url)
                if '2024' not in low or 'primary' not in low:
                    continue
                votes = parse_votes(text, candidates)
                if votes:
                    return {'county': source['county'], 'ok': True, 'url': url, 'matches': len(votes), 'hash': hashlib.sha256(content).hexdigest()[:16]}
        except Exception:
            pass
    return {'county': source['county'], 'ok': False, 'url': source['landing_urls'][0], 'matches': 0}


def main():
    candidates = candidate_rows()
    sources = json.loads((ROOT / 'sources.json').read_text())
    counties = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        fs = [pool.submit(county_check, s, candidates) for s in sources]
        for f in as_completed(fs):
            counties.append(f.result())
    contests = len({(c['chamber'], c['district'], c['party']) for c in candidates})
    parsed = sum(1 for c in counties if c['ok'])
    report = {'candidate_rows': len(candidates), 'party_contests': contests, 'parsed_counties': parsed, 'county_results': sorted(counties, key=lambda x: x['county'])}
    print(json.dumps(report, indent=2))
    failures = []
    if len(candidates) < MIN_CANDIDATES: failures.append(f'candidate rows {len(candidates)} < {MIN_CANDIDATES}')
    if contests < MIN_CONTESTS: failures.append(f'party contests {contests} < {MIN_CONTESTS}')
    if parsed < MIN_COUNTIES: failures.append(f'parsed counties {parsed} < {MIN_COUNTIES}')
    if failures:
        raise SystemExit('Historical regression failed: ' + '; '.join(failures))
    print('Historical regression passed.')


if __name__ == '__main__':
    main()
