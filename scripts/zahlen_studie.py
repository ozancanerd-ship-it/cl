#!/usr/bin/env python3
"""Zahlen-Studie: Zwei-Tage-Bewegung um Quartalszahlen (beschreibend).

Erwartet im Arbeitsordner kal.jsonl (Nasdaq-Kalender je Tag: {"tag", "zeilen": [[symbol, zeit]]})
und $BT/aktien_d1.json. Schreibt gaps.json; daraus entsteht
src/trading_agent/refdata/zahlen_bewegung.json."""
import json, os
import numpy as np
BT = os.environ.get('BT', '/tmp/claude-0/bt')
kal = {}
for z in open('kal.jsonl'):
    d = json.loads(z)
    for sym, t in d['zeilen']:
        kal.setdefault(sym, []).append(d['tag'])
kurse = json.load(open(BT + '/aktien_d1.json'))
bew, rat, per, gegen = [], [], {}, []
for sym, liste in kurse.items():
    tage = [x[0] for x in liste]; c = np.array([x[1] for x in liste], float)
    idx = {t: i for i, t in enumerate(tage)}
    for tag in sorted(set(kal.get(sym, []))):
        i = idx.get(tag)
        if i is None or i < 42 or i + 1 >= len(c): continue
        zwei = (c[i+1] / c[i-1] - 1) * 100            # Schluss vor dem Termin -> Schluss danach
        norm = np.median([abs(c[k] / c[k-2] - 1) * 100 for k in range(i-40, i-1)])
        bew.append(abs(zwei)); rat.append(abs(zwei) / norm if norm > 0 else np.nan)
        per.setdefault(sym, []).append(abs(zwei))
        gegen.append(zwei)
bew = np.array(bew); rat = np.array(rat)
print('Termine', len(bew), 'Aktien', len(per))
for q in (50, 75, 90, 95):
    print(f'  Zwei-Tage-Bewegung {q}. Perzentil: {np.percentile(bew, q):.1f} %   x normal: {np.nanpercentile(rat, q):.1f}')
for s in (5, 10, 15, 20, 25):
    print(f'  Anteil > {s} %: {np.mean(bew > s)*100:.1f} %')
print('  Anteil nach unten > 10 %:', f'{np.mean(np.array(gegen) < -10)*100:.1f} %')
out = {'n': int(len(bew)), 'aktien': len(per), 'median': round(float(np.median(bew)),2), 'p75': round(float(np.percentile(bew,75)),2),
       'p90': round(float(np.percentile(bew,90)),2), 'p95': round(float(np.percentile(bew,95)),2),
       'x_normal_median': round(float(np.nanmedian(rat)),2), 'x_normal_p90': round(float(np.nanpercentile(rat,90)),2),
       'anteil_ueber': {str(s): round(float(np.mean(bew > s)),3) for s in (5,10,15,20,25)},
       'anteil_runter_10': round(float(np.mean(np.array(gegen) < -10)),3),
       'je_aktie': {k: {'n': len(v), 'median': round(float(np.median(v)),2), 'max': round(float(max(v)),2)} for k, v in per.items()}}
json.dump(out, open('gaps.json','w'), indent=1)
for k in ('TSM','NFLX','META','AMZN','MELI','AXON','NVDA','PLTR'):
    v = per.get(k)
    if v: print(f'  {k}: n={len(v)} median {np.median(v):.1f} % max {max(v):.1f} %')
