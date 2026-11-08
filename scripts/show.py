import json,sys
from backend.transcript import rel_times, fmt_rel
d={r['conversation_id']:r for r in json.load(open('data/dev.json'))['data']['records']}
S={'client':'C','operator':'OP','bot':'B','system':'S'}
for cid in sys.argv[1:]:
    r=d[cid]; t=r['transcript']; rel=rel_times(t)
    print(f"=== {cid} {r['channel']} {r['language']}")
    for i,m in enumerate(t):
        if m['speaker']=='bot' and i<4 and len(m['text'])>120: print(f"[{i}] B: <bot greeting/consent>"); continue
        print(f"[{i}]{fmt_rel(rel[i])} {S[m['speaker']]}: {m['text']}")
