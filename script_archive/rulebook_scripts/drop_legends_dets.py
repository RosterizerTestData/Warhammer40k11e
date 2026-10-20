import json,glob,sys,re
APPLY='--apply' in sys.argv
books={p:json.load(open(p)) for p in sorted(glob.glob('*.rulebook'))}
cat=lambda p:books[p]['rulebook']['assetCatalog']
LEG=lambda a:'Legends' in ((a.get('keywords') or {}).get('Source') or [])
dead=set()
for p in books:
    for k,a in cat(p).items():
        if k.startswith('Detachment§') and LEG(a): dead.add(k.split('§',1)[1])
nn=lambda s:s.replace('’',"'")
deadn={nn(x) for x in dead}
isdead=lambda s:isinstance(s,str) and nn(s) in deadn
log=[];review=[]
# candidate stratagems: assigned by dead detachments
strat_cand=set()
for p in books:
    for k,a in cat(p).items():
        if k.startswith('Detachment§') and isdead(k.split('§',1)[1]):
            for r in (a.get('rules') or {}).values():
                for act in r.get('actions',[]):
                    if isinstance(act.get('value'),str) and act['value'].startswith('Stratagem§'): strat_cand.add(act['value'])
for p in books:
    c=cat(p)
    for k in list(c):
        if '§' not in k: continue
        a=c[k]; cls,nm=k.split('§',1)
        if cls=='Detachment' and isdead(nm): c.pop(k); log.append(f'{p}: -{k}'); continue
        if cls=='Enhancement':
            kw=(a.get('keywords') or {}).get('Keywords') or []
            dk=[x for x in kw if isdead(x)]
            live_det=[x for x in kw if not isdead(x) and any(f'Detachment§{x}' in cat(q) for q in books)]
            if LEG(a) or (dk and not live_det):
                c.pop(k); log.append(f'{p}: -{k}'); continue
            if dk: a['keywords']['Keywords']=[x for x in kw if not isdead(x)]; log.append(f'{p}: {k} kw -{dk}')
    # roster ranks + rules
    r=c.get('Roster§Roster')
    if r:
        rk=(r.get('stats',{}).get('Detachment') or {}).get('ranks') or {}
        for x in list(rk):
            if isdead(x): rk.pop(x); log.append(f'{p}: Roster rank -{x}')
        dv=(r.get('stats',{}).get('Detachment') or {})
        if isinstance(dv.get('value'),str) and isdead(dv['value']):
            dv['value']=next(iter(rk)); log.append(f'{p}: Roster default -> {dv["value"]}')
        if isinstance(dv.get('value'),list) and any(isdead(x) for x in dv['value']):
            dv['value']=[x for x in dv['value'] if not isdead(x)] or ([next(iter(rk))] if rk else [])
            log.append(f'{p}: Roster default -> {dv["value"]}')
        for rn in list(r.get('rules') or {}):
            if rn.startswith('hideLegendsDetachments'): r['rules'].pop(rn); log.append(f'{p}: -rule {rn}')
# scrub rule actions removing dead ranks; flag evals mentioning dead names
for p in books:
    for k,a in list(cat(p).items())+list(books[p]['rulebook'].get('assetTaxonomy',{}).items()):
        for rn,r in list((a.get('rules') or {}).items()):
            acts=r.get('actions')
            if acts:
                keep=[x for x in acts if not any(isdead(seg) for path in x.get('paths',[]) for seg in path) and not isdead(x.get('value'))]
                if len(keep)!=len(acts):
                    log.append(f'{p}: {k} rule {rn} -{len(acts)-len(keep)} actions')
                    if keep: r['actions']=keep
                    else: a['rules'].pop(rn); log.append(f'{p}: {k} -rule {rn} (empty)')
            if any(isdead(ev.get('value')) and ev.get('not') is not True for ev in r.get('evals',[])) and r.get('evaluate','AND')=='AND' and rn in a['rules']:
                a['rules'].pop(rn); log.append(f'{p}: {k} -rule {rn} (gated on dead detachment)'); continue
            for ev in r.get('evals',[]):
                if isdead(ev.get('value')) or any(isdead(seg) for path in ev.get('paths',[]) for seg in path): review.append(f'{p}: {k} rule {rn} eval {ev.get("value")}')
for p in books:
    for k,a in cat(p).items():
        kw=(a.get('keywords') or {}).get('Keywords')
        if kw and any(isdead(x) for x in kw):
            a['keywords']['Keywords']=[x for x in kw if not isdead(x)]; log.append(f'{p}: {k} kw scrub')
# Headhunter Task Force Tank Ace machinery (SM Unit class)
if any(isdead(x) for x in ['Headhunter Task Force']):
    p='Space_Marines.rulebook'; t=books[p]['rulebook']['assetTaxonomy']['Unit']
    for st in ['Tank Ace Character','charactertank']:
        if t.get('stats',{}).pop(st,None) is not None: log.append(f'{p}: Unit class -stat {st}')
    for rn in list(t.get('rules',{})):
        if 'Tank Ace' in json.dumps(t['rules'][rn]) or 'charactertank' in json.dumps(t['rules'][rn]): t['rules'].pop(rn); log.append(f'{p}: Unit class -rule {rn}')
    if cat(p)['Roster§Roster'].get('rules',{}).pop('Only 3 Tank Ace Characters',None): log.append(f'{p}: Roster -rule Only 3 Tank Ace Characters')
# stratagems: drop candidates no longer referenced anywhere
allj={p:json.dumps(books[p],ensure_ascii=False) for p in books}
for s in sorted(strat_cand):
    refs=sum(allj[p].count(f'"{s}"') for p in books)
    owners=[p for p in books if s in cat(p)]
    if refs==len(owners):
        for p in owners: cat(p).pop(s); log.append(f'{p}: -{s}')
# leftover mentions
allj={p:json.dumps(books[p],ensure_ascii=False) for p in books}
for x in sorted(dead):
    for p in books:
        for v in {x,nn(x)}:
            if f'"{v}"' in allj[p] or f'§{v}"' in allj[p]: review.append(f'{p}: still mentions "{v}"')
print('\n'.join(log[-0:]) if '-v' in sys.argv else f'{len(log)} changes'); 
import collections; print(collections.Counter(l.split(': ')[1].split('§')[0].lstrip('-') if '§' in l else l.split(': ')[1][:12] for l in log))
print('REVIEW:'); print('\n'.join(sorted(set(review))))
if APPLY:
    for p,d in books.items(): json.dump(d,open(p,'w'),indent=2,ensure_ascii=False)
    print('written')
