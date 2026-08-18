#!/usr/bin/env python3
"""Conservative race-call postprocessor.

The dashboard only marks a contested race WINNER when the current leader's
margin is larger than a defensible upper bound on remaining ballots, or when
all relevant county result sources explicitly show reporting complete.

This is intentionally conservative. It does not call races from percentage of
precincts reporting, lead percentage, or current vote share alone.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
UA = {'User-Agent': 'Mozilla/5.0 WyomingElectionDashboard/2.0 (+race-call-check)'}


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', str(s).lower().replace('&', ' and '))).strip()


def aliases(name):
    n = norm(name)
    p = n.split()
    out = {n}
    if len(p) >= 2:
        out |= {p[0] + ' ' + p[-1], p[-1] + ' ' + p[0]}
    return sorted(out, key=len, reverse=True)


def pdf_text(content):
    try:
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ''


def fetch_text(session, url):
    r = session.get(url, headers=UA, timeout=22, allow_redirects=True)
    r.raise_for_status()
    ct = r.headers.get('content-type', '').lower()
    if 'pdf' in ct or r.url.lower().split('?')[0].endswith('.pdf'):
        return pdf_text(r.content), r.url
    return BeautifulSoup(r.content, 'html.parser').get_text('\n', strip=True), r.url


def _int(s):
    try:
        return int(str(s).replace(',', '').strip())
    except Exception:
        return None


def reporting_meta(text):
    """Return only conservative, source-explicit reporting metadata.

    We deliberately ignore phrases such as "estimated votes remaining" because
    an estimate is not a mathematical upper bound.
    """
    raw = re.sub(r'\u00a0', ' ', text)
    low = raw.lower()
    meta = {'reporting_complete': False, 'ballots_remaining': None}

    # Exact precinct-completion statements.
    precinct_patterns = [
        r'precincts?\s+reporting\s*[:\-]?\s*(\d+)\s*(?:of|/)\s*(\d+)',
        r'(\d+)\s*(?:of|/)\s*(\d+)\s+precincts?\s+reporting',
        r'precincts?\s+reported\s*[:\-]?\s*(\d+)\s*(?:of|/)\s*(\d+)',
    ]
    for pat in precinct_patterns:
        for m in re.finditer(pat, low, re.I):
            done, total = _int(m.group(1)), _int(m.group(2))
            if done is not None and total and done == total:
                meta['reporting_complete'] = True

    if re.search(r'100(?:\.0+)?\s*%\s+(?:of\s+)?precincts?\s+(?:reporting|reported)', low):
        meta['reporting_complete'] = True
    if re.search(r'all\s+precincts?\s+(?:are\s+)?(?:reporting|reported)', low):
        meta['reporting_complete'] = True

    # Exact remaining-ballot labels. Skip matches explicitly described as estimated.
    remaining_patterns = [
        r'(?<!estimated )(?<!estimated\s)(?:ballots|votes)\s+(?:remaining|outstanding|left\s+to\s+count|uncounted)\s*[:\-]?\s*([0-9][0-9,]*)',
        r'(?<!estimated )(?<!estimated\s)([0-9][0-9,]*)\s+(?:ballots|votes)\s+(?:remaining|outstanding|left\s+to\s+count|uncounted)',
        r'outstanding\s+(?:ballots|votes)\s*[:\-]?\s*([0-9][0-9,]*)',
        r'uncounted\s+(?:ballots|votes)\s*[:\-]?\s*([0-9][0-9,]*)',
    ]
    vals = []
    for pat in remaining_patterns:
        for m in re.finditer(pat, low, re.I):
            start = max(0, m.start() - 24)
            if 'estimat' in low[start:m.start()]:
                continue
            v = _int(m.group(1))
            if v is not None and v >= 0:
                vals.append(v)

    # If a source states both ballots counted and total ballots cast, the difference
    # is also a defensible upper bound on countywide ballots not yet counted.
    counted = []
    totals = []
    for pat in (r'ballots\s+counted\s*[:\-]?\s*([0-9][0-9,]*)', r'votes\s+counted\s*[:\-]?\s*([0-9][0-9,]*)'):
        counted += [_int(m.group(1)) for m in re.finditer(pat, low, re.I)]
    for pat in (r'total\s+ballots\s+cast\s*[:\-]?\s*([0-9][0-9,]*)', r'ballots\s+cast\s*[:\-]?\s*([0-9][0-9,]*)'):
        totals += [_int(m.group(1)) for m in re.finditer(pat, low, re.I)]
    counted = [x for x in counted if x is not None]
    totals = [x for x in totals if x is not None]
    if counted and totals:
        c, t = max(counted), max(totals)
        if t >= c:
            vals.append(t - c)

    if vals:
        # Use the largest explicit figure as the conservative upper bound.
        meta['ballots_remaining'] = max(vals)
    if meta['reporting_complete']:
        meta['ballots_remaining'] = 0
    return meta


def candidate_present(text_norm, name):
    return any(a and a in text_norm for a in aliases(name))


def load_county_docs(state):
    session = requests.Session()
    docs = {}
    for county, info in state.get('counties', {}).items():
        url = info.get('result_url')
        if not url or not info.get('votes'):
            continue
        try:
            text, final = fetch_text(session, url)
            ntext = norm(text)
            docs[county] = {
                'url': final,
                'text_norm': ntext,
                **reporting_meta(text),
            }
        except Exception as e:
            print('Race-call metadata fetch failed for', county, type(e).__name__)
    return docs


def leader_and_margin(race):
    cs = race.get('candidates') or []
    if len(cs) < 2:
        return None, None
    ordered = sorted(cs, key=lambda c: int(c.get('votes') or 0), reverse=True)
    top = int(ordered[0].get('votes') or 0)
    second = int(ordered[1].get('votes') or 0)
    if top <= 0 or top == second:
        return None, None
    return ordered[0], top - second


def known_counties_from_race(race):
    out = set()
    for c in race.get('candidates') or []:
        out.update(c.get('counties') or [])
    return out


def relevant_legislative_counties(race, docs):
    names = [c.get('candidate', '') for c in race.get('candidates') or []]
    relevant = set(known_counties_from_race(race))
    for county, d in docs.items():
        if any(candidate_present(d['text_norm'], n) for n in names):
            relevant.add(county)
    return relevant


def call_race(race, docs, all_counties_ready, statewide=False):
    race.pop('winner', None)
    race.pop('call_method', None)
    race.pop('max_remaining_ballots', None)
    race.pop('call_note', None)

    leader, margin = leader_and_margin(race)
    if not leader:
        return

    # We need result documents from all 23 counties before using candidate-name
    # presence to prove which counties are relevant to a legislative district.
    if statewide:
        relevant = set(docs)
        if not all_counties_ready:
            return
    else:
        if not all_counties_ready:
            return
        relevant = relevant_legislative_counties(race, docs)
        if not relevant:
            return

    remaining = 0
    for county in relevant:
        d = docs.get(county)
        if not d:
            return
        if d.get('reporting_complete'):
            continue
        r = d.get('ballots_remaining')
        if r is None:
            return
        remaining += int(r)

    if margin > remaining:
        race['winner'] = leader.get('candidate')
        race['call_method'] = 'mathematical_clinch'
        race['max_remaining_ballots'] = remaining
        race['call_note'] = (
            f"Mathematically clinched: lead of {margin:,} exceeds the maximum "
            f"{remaining:,} ballots remaining across relevant official county sources."
        )


def main():
    state_path = ROOT / 'state.json'
    results_path = ROOT / 'results.json'
    if not state_path.exists() or not results_path.exists():
        print('Race-call postprocessor skipped; feed files not present')
        return

    state = json.loads(state_path.read_text(encoding='utf-8'))
    results = json.loads(results_path.read_text(encoding='utf-8'))
    docs = load_county_docs(state)
    total_counties = len(state.get('counties', {}))
    all_counties_ready = total_counties == 23 and len(docs) == 23

    for race in results.get('races', []):
        call_race(race, docs, all_counties_ready, statewide=False)
    for race in results.get('statewide_races', []):
        call_race(race, docs, all_counties_ready, statewide=True)

    results['race_call_method'] = {
        'standard': 'mathematical_clinch',
        'description': 'WINNER only when the leader cannot be overtaken using explicit official remaining-ballot or complete-reporting data.',
        'county_sources_evaluated': len(docs),
        'all_counties_ready': all_counties_ready,
    }
    results_path.write_text(json.dumps(results, indent=2), encoding='utf-8')
    called = sum(1 for r in results.get('races', []) + results.get('statewide_races', []) if r.get('winner'))
    print('Race-call postprocessor evaluated', len(docs), 'county sources; contested calls:', called)


if __name__ == '__main__':
    main()
