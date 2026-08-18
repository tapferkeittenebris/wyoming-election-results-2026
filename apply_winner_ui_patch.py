#!/usr/bin/env python3
"""Idempotently add WINNER badge support to the existing dashboard UI.

This intentionally changes no layout. A mathematically called race uses the
same existing badge location and green 'won' style; otherwise LEADER behavior
is unchanged.
"""
from pathlib import Path

p = Path(__file__).resolve().parent / 'index.html'
s = p.read_text(encoding='utf-8')
original = s

# Legislative race cards.
s = s.replace(
    "const lead=uncontested||(m>0&&c.votes===m),label=uncontested?'WON':lead?'LEADER':'';",
    "const lead=uncontested||(m>0&&c.votes===m),called=!!r.winner&&c.candidate===r.winner,label=uncontested?'WON':called?'WINNER':lead?'LEADER':'';"
)
s = s.replace(
    "${label?`<span class=\"badge ${uncontested?'won':'lead'}\">${label}</span>`:''}",
    "${label?`<span class=\"badge ${uncontested||called?'won':'lead'}\">${label}</span>`:''}"
)

# Statewide/federal race cards.
start = s.find('function office(r){')
end = s.find('async function fetchLatest', start)
if start != -1 and end != -1:
    block = s[start:end]
    block = block.replace(
        "const lead=uncontested||(m>0&&c.votes===m);return",
        "const lead=uncontested||(m>0&&c.votes===m),called=!!r.winner&&c.candidate===r.winner;return"
    )
    block = block.replace(
        "${lead?`<span class=\"badge ${uncontested?'won':'lead'}\">${uncontested?'WON':'LEADER'}</span>`:''}",
        "${(lead||called)?`<span class=\"badge ${uncontested||called?'won':'lead'}\">${uncontested?'WON':called?'WINNER':'LEADER'}</span>`:''}"
    )
    s = s[:start] + block + s[end:]

if s != original:
    p.write_text(s, encoding='utf-8')
    print('Applied minimal WINNER badge UI patch')
else:
    print('WINNER badge UI already patched or expected source pattern not present')
