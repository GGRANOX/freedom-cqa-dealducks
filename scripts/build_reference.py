"""data/reference_raw.txt -> data/reference.json (эталон только для измерения точности)."""
import json
from backend.scoring import compute_score
keys = ['c01', 'c02', 'c03', 'c04', 'c05', 'c06', 'c07', 'c08']
m = {'y': 'yes', 'n': 'no', 'na': 'na'}
cl = json.load(open('backend/checklist_default.json', encoding='utf-8'))
L = {}
for line in open('data/reference_raw.txt', encoding='utf-8'):
    if line.startswith('#') or not line.strip():
        continue
    cid, vals, note = [x.strip() for x in line.split('|')]
    d = {}
    for k, x in zip(keys, vals.split()):
        if k in ('c04', 'c06'):
            d[k] = {'answer': 'na'} if x == 'na' else {'answer': 'scale', 'value': int(x)}
        else:
            d[k] = {'answer': m[x]}
    L[cid] = {'judgments': d, 'note': note, 'total_score': compute_score(cl, d)['total_score']}
json.dump(L, open('data/reference.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(len(L), 'записей')
