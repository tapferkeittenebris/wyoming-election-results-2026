#!/usr/bin/env python3
"""Idempotently preserve minimal election-night dashboard UI enhancements.

This intentionally avoids layout redesigns. It:
- keeps mathematically called races in the existing badge location as WINNER
- separates Democratic-only legislative winners into a political-blue segment
  in the House and Senate outlook meters, matching the map treatment
"""
from pathlib import Path

p = Path(__file__).resolve().parent / 'index.html'
s = p.read_text(encoding='utf-8')
original = s

# Legislative race cards: show a conservative mathematical call as WINNER.
s = s.replace(
    "const lead=uncontested||(m>0&&c.votes===m),label=uncontested?'WON':lead?'LEADER':'';",
    "const lead=uncontested||(m>0&&c.votes===m),called=!!r.winner&&c.candidate===r.winner,label=uncontested?'WON':called?'WINNER':lead?'LEADER':'';"
)
s = s.replace(
    "${label?`<span class=\"badge ${uncontested?'won':'lead'}\">${label}</span>`:''}",
    "${label?`<span class=\"badge ${uncontested||called?'won':'lead'}\">${label}</span>`:''}"
)

# Statewide/federal race cards: same WINNER behavior.
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

# Democratic meter colors. --dem already exists when the map-color patch is live.
s = s.replace(
    '.fc-count{color:#11788A}.other-count{color:#9B1C2D}.pending-count{color:#888}',
    '.fc-count{color:#11788A}.other-count{color:#9B1C2D}.dem-count{color:var(--dem)}.pending-count{color:#888}'
)
s = s.replace(
    '.fc-seg{background:var(--fc)}.other-seg{background:var(--nonfc)}.pending-seg{background:#ddd}',
    '.fc-seg{background:var(--fc)}.other-seg{background:var(--nonfc)}.dem-seg{background:var(--dem)}.pending-seg{background:#ddd}'
)

# Add D counts and blue segments to the existing House and Senate meters.
s = s.replace(
    '<span class="fc-count" id="hfc">FC 0</span><span class="other-count" id="hot">Non-FC 0</span><span class="pending-count" id="hpd">Pending 62</span>',
    '<span class="fc-count" id="hfc">FC 0</span><span class="other-count" id="hot">Non-FC R 0</span><span class="dem-count" id="hdm">D 0</span><span class="pending-count" id="hpd">Pending 62</span>'
)
s = s.replace(
    '<div class="fc-seg" id="hfs"></div><div class="other-seg" id="hos"></div><div class="pending-seg" id="hps"></div>',
    '<div class="fc-seg" id="hfs"></div><div class="other-seg" id="hos"></div><div class="dem-seg" id="hds"></div><div class="pending-seg" id="hps"></div>'
)
s = s.replace(
    '<span class="fc-count" id="sfc">FC 0</span><span class="other-count" id="sot">Non-FC 0</span><span class="pending-count" id="spd">Pending 31</span>',
    '<span class="fc-count" id="sfc">FC 0</span><span class="other-count" id="sot">Non-FC R 0</span><span class="dem-count" id="sdm">D 0</span><span class="pending-count" id="spd">Pending 31</span>'
)
s = s.replace(
    '<div class="fc-seg" id="sfs"></div><div class="other-seg" id="sos"></div><div class="pending-seg" id="sps"></div>',
    '<div class="fc-seg" id="sfs"></div><div class="other-seg" id="sos"></div><div class="dem-seg" id="sds"></div><div class="pending-seg" id="sps"></div>'
)

# Make the seat outlook classify Democratic-only races separately. A contested
# D primary stays Pending until the conservative winner-call layer calls it.
old_seat = "function seatOutlook(d,ch,total){const groups=racesByDistrict(d,ch),inc=(d.incumbents&&d.incumbents[ch])||{};let fc=0,other=0,pending=0;for(let district=1;district<=total;district++){const rs=groups[district]||groups[String(district)]||[],rep=rs.find(r=>r.party==='Republican');let person=null;if(rep){person=winnerForRace(rep)}else if(rs.length===0&&inc[district]){person=inc[district]}else if(ch==='Senate'&&district%2===0&&district!==6&&inc[district]){person=inc[district]}else if(inc[district]&&rs.every(r=>r.party!=='Republican')){person=inc[district]}if(!person){pending++;continue}person.is_fc?fc++:other++}return{fc,other,pending,total}}"
new_seat = "function seatOutlook(d,ch,total){const groups=racesByDistrict(d,ch),inc=(d.incumbents&&d.incumbents[ch])||{};let fc=0,other=0,dem=0,pending=0;for(let district=1;district<=total;district++){const rs=groups[district]||groups[String(district)]||[],rep=rs.find(r=>r.party==='Republican'),dr=rs.find(r=>r.party==='Democratic');let person=null,party=null;if(rep){person=winnerForRace(rep);party='Republican'}else if(dr){if(dr.candidates.length===1)person=dr.candidates[0];else if(dr.winner)person=dr.candidates.find(c=>c.candidate===dr.winner)||null;party='Democratic'}else if(rs.length===0&&inc[district]){person=inc[district];party=person.party||null}else if(ch==='Senate'&&district%2===0&&district!==6&&inc[district]){person=inc[district];party=person.party||null}else if(inc[district]&&rs.every(r=>r.party!=='Republican')){person=inc[district];party=person.party||null}if(!person){pending++;continue}if(party==='Democratic')dem++;else person.is_fc?fc++:other++}return{fc,other,dem,pending,total}}"
s = s.replace(old_seat, new_seat)

old_meter = "function setMeter(p,c){document.getElementById(p+'fc').textContent='FC '+c.fc;document.getElementById(p+'ot').textContent='Non-FC '+c.other;document.getElementById(p+'pd').textContent='Pending '+c.pending;document.getElementById(p+'fs').style.width=(100*c.fc/c.total)+'%';document.getElementById(p+'os').style.width=(100*c.other/c.total)+'%';document.getElementById(p+'ps').style.width=(100*c.pending/c.total)+'%'}"
new_meter = "function setMeter(p,c){document.getElementById(p+'fc').textContent='FC '+c.fc;document.getElementById(p+'ot').textContent='Non-FC R '+c.other;document.getElementById(p+'dm').textContent='D '+c.dem;document.getElementById(p+'pd').textContent='Pending '+c.pending;document.getElementById(p+'fs').style.width=(100*c.fc/c.total)+'%';document.getElementById(p+'os').style.width=(100*c.other/c.total)+'%';document.getElementById(p+'ds').style.width=(100*c.dem/c.total)+'%';document.getElementById(p+'ps').style.width=(100*c.pending/c.total)+'%'}"
s = s.replace(old_meter, new_meter)

# Keep short explanatory copy aligned with the four-segment meter.
s = s.replace(
    'Uses uncontested incumbents/seats and Republican primary outcomes; Democratic primaries do not change this meter when an R primary exists.',
    'Uses uncontested seats and primary outcomes; Democratic-only winners are shown separately in blue.'
)
s = s.replace(
    'Includes seats not up in 2026 and uncontested incumbents; election seats use the Republican primary outcome.',
    'Includes seats not up in 2026 and uncontested seats; Democratic-only winners are shown separately in blue.'
)

if s != original:
    p.write_text(s, encoding='utf-8')
    print('Applied minimal WINNER/Democratic-meter UI patch')
else:
    print('WINNER/Democratic-meter UI already patched or expected source pattern not present')
