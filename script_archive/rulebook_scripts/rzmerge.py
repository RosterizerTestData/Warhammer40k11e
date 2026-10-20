"""Python port of Rosterizer dependency merge (compose-dependencies.ts + shared object-merge slotLayer),
plus an overlay generator: make_overlay(base, target) -> overlay such that merge(base, overlay) == target."""
import copy, json

PERMISSION = ("allowed", "disallowed", "constraints")


def ident(x):
    return x if isinstance(x, str) else x.get("item")


def _is_obj(v):
    return isinstance(v, dict)


def slot_layer(base, overlay):
    from collections import OrderedDict
    bs, os_ = OrderedDict(), OrderedDict()
    for a in base:
        bs.setdefault(ident(a), []).append(a)
    for a in overlay:
        os_.setdefault(ident(a), []).append(a)
    out, seen = [], set()
    for i, be in bs.items():
        seen.add(i)
        oe = os_.get(i, [])
        for k in range(max(len(be), len(oe))):
            b = be[k] if k < len(be) else None
            o = oe[k] if k < len(oe) else None
            if b is not None and o is not None and _is_obj(b) and _is_obj(o):
                out.append(merge_slot(b, o))
            else:
                out.append(o if o is not None else b)
    used = {i: min(len(os_.get(i, [])), max(len(be), len(os_.get(i, [])))) for i, be in bs.items()}
    for e in overlay:
        i = ident(e)
        if i in seen and used.get(i, 0) > 0:
            used[i] -= 1
            continue
        out.append(e)
    return out


def merge_children(base, overlay, removals=None):
    res = {}
    for div in ("included", "traits"):
        b = list((base or {}).get(div) or [])
        for r in (removals or {}).get(div) or []:
            for idx, c in enumerate(b):
                if c == r or (_is_obj(c) and c.get("item") == r):
                    b.pop(idx)
                    break
        res[div] = slot_layer(b, (overlay or {}).get(div) or [])
    return res


def merge_slot(base, overlay):
    out = dict(base)
    for k, v in overlay.items():
        if k == "assets":
            out[k] = merge_children(base.get("assets"), v)
        elif k == "keywords" and _is_obj(v):
            bk = base.get("keywords") or {}
            m = dict(bk)
            for c in v:
                m[c] = list(dict.fromkeys((bk.get(c) or []) + (v.get(c) or [])))
            out[k] = m
        else:
            out[k] = v
    return out


def merge_value(key, b, o, in_kw, in_stats):
    if key == "text" and isinstance(b, str) and isinstance(o, str):
        if not b:
            return o
        if not o or b == o:
            return b
        return f"{b}\n\n{o}"
    if key in PERMISSION and _is_obj(b) and _is_obj(o):
        m = dict(b)
        for pk, ov in o.items():
            bv = b.get(pk)
            m[pk] = list(dict.fromkeys(bv + ov)) if isinstance(bv, list) and isinstance(ov, list) else ov
        return m
    if key == "assets" and _is_obj(b) and _is_obj(o):
        return merge_children(b, o)
    if in_stats and key == "ranks" and _is_obj(b) and _is_obj(o):
        m = {k: dict(v) for k, v in b.items()}
        hi = max([v.get("order", 0) for v in b.values() if isinstance(v.get("order"), (int, float))] or [0])
        for k, v in o.items():
            if k in b:
                m[k] = {**b[k], **v}
            else:
                m[k] = {**v, "order": hi + (v.get("order", 0) if isinstance(v.get("order"), (int, float)) else 0)}
        return m  # (renumbering ignored for equality: we compare rank sets/contents w/o order)
    if _is_obj(b) and _is_obj(o):
        return merge_entry(b, o, in_kw, in_stats)
    if (in_kw or key == "aliases") and isinstance(b, list) and isinstance(o, list):
        return list(dict.fromkeys(b + o))
    return o


def strip_removals(base, rem):
    out = dict(base)
    if _is_obj(out.get("keywords")) and _is_obj(rem.get("keywords")):
        out["keywords"] = {c: ([x for x in v if x not in rem["keywords"].get(c, [])] if isinstance(v, list) else v)
                           for c, v in out["keywords"].items()}
    if _is_obj(out.get("assets")) and _is_obj(rem.get("assets")):
        out["assets"] = merge_children(out["assets"], {}, rem["assets"])
    for f in PERMISSION:
        if _is_obj(out.get(f)) and _is_obj(rem.get(f)):
            out[f] = {c: ([x for x in v if x not in rem[f].get(c, [])] if isinstance(v, list) else v)
                      for c, v in out[f].items()}
    for f in ("stats", "rules", "meta"):
        if _is_obj(out.get(f)) and _is_obj(rem.get(f)):
            out[f] = {k: v for k, v in out[f].items() if k not in rem[f]}
    if "text" in rem:
        out["text"] = ""
    return out


def merge_entry(base, overlay, in_kw=False, in_stats=False):
    rem = overlay.get("removed") if _is_obj(overlay.get("removed")) else None
    res = dict(strip_removals(base, rem) if rem else base)
    for k, o in overlay.items():
        if k == "removed" and not in_stats:
            continue
        res[k] = merge_value(k, res.get(k), o, in_kw or k == "keywords", in_stats or k == "stats")
    return res


# ---------------------------------------------------------------- normalised comparison
def canon(a):
    """Comparison form: rank orders dropped, keyword arrays sorted, empty containers dropped."""
    a = copy.deepcopy(a)

    def walk(x, path=()):
        if _is_obj(x):
            out = {}
            for k, v in x.items():
                if k == "order" and len(path) >= 2 and path[-2] == "ranks":
                    continue
                if k == "_layers":
                    continue
                v = walk(v, path + (k,))
                if v in ({}, [], "", None) and k not in ("value",):
                    continue
                out[k] = v
            return out
        if isinstance(x, list):
            v = [walk(i, path) for i in x]
            if "keywords" in path or (len(path) and path[-1] in ("items", "classifications", "any", "none", "not")):
                return sorted(v, key=lambda z: json.dumps(z, sort_keys=True, ensure_ascii=False))
            return v
        return x
    return walk(a)


def canon_unordered_traits(a):
    a = canon(a)
    as_ = a.get("assets")
    if _is_obj(as_):
        for d in ("traits", "included"):
            if d in as_:
                as_[d] = sorted(as_[d], key=lambda z: json.dumps(z, sort_keys=True, ensure_ascii=False))
    return a


# ---------------------------------------------------------------- overlay generation
def _diff_obj(b, t, in_stats=False):
    """Overlay for plain nested dicts (stats/aspects/...). Returns (overlay, removed_names)."""
    ov = {}
    for k, tv in t.items():
        bv = b.get(k) if _is_obj(b) else None
        if bv == tv:
            continue
        if _is_obj(bv) and _is_obj(tv) and not (in_stats and k == "ranks"):
            sub = _diff_obj(bv, tv, in_stats)
            if sub:
                ov[k] = sub
        else:
            ov[k] = tv
    return ov


def make_overlay(base, target):
    ov, rem = {}, {}
    keys = set(base) | set(target)
    for k in keys:
        b, t = base.get(k), target.get(k)
        if b == t:
            continue
        if k == "keywords":
            add, drop = {}, {}
            for c in set(b or {}) | set(t or {}):
                bl, tl = (b or {}).get(c, []), (t or {}).get(c, [])
                a_ = [x for x in tl if x not in bl]
                d_ = [x for x in bl if x not in tl]
                if a_:
                    add[c] = a_
                if d_:
                    drop[c] = d_
                if c not in (b or {}) and not a_:
                    add[c] = []
            if add:
                ov["keywords"] = add
            if drop:
                rem["keywords"] = drop
        elif k in PERMISSION:
            add, drop = {}, {}
            for c in set(b or {}) | set(t or {}):
                bl, tl = (b or {}).get(c, []), (t or {}).get(c, [])
                if not isinstance(bl, list) or not isinstance(tl, list):
                    add[c] = tl
                    continue
                a_ = [x for x in tl if x not in bl]
                d_ = [x for x in bl if x not in tl]
                if a_:
                    add[c] = a_
                if d_:
                    drop[c] = d_
            if add:
                ov[k] = add
            if drop:
                rem[k] = drop
        elif k == "assets":
            add, drop = {}, {}
            for d in ("traits", "included"):
                bl = list((b or {}).get(d) or [])
                tl = list((t or {}).get(d) or [])
                rest = list(bl)
                extra = []
                for x in tl:
                    if x in rest:
                        rest.remove(x)
                    else:
                        extra.append(x)
                if rest:  # base entries absent from target -> remove (by identity)
                    drop[d] = [ident(x) for x in rest]
                if extra:
                    add[d] = extra
            if add:
                ov["assets"] = add
            if drop:
                rem["assets"] = drop
        elif k in ("stats", "rules", "meta"):
            bd, td = b or {}, t or {}
            gone = [n for n in bd if n not in td]
            if gone:
                rem[k] = {n: {} for n in gone}
            sub = {}
            for n, tv in td.items():
                if n not in bd:
                    sub[n] = tv
                elif bd[n] != tv:
                    if k == "stats":
                        s = _diff_obj(bd[n], tv, True)
                        if s and canon(merge_value(n, bd[n], s, False, True)) == canon(tv):
                            sub[n] = s
                        else:  # needs deletions inside the stat: remove inherited, re-add whole
                            rem.setdefault(k, {})[n] = {}
                            sub[n] = tv
                    else:
                        rem.setdefault(k, {})[n] = {}
                        sub[n] = tv
            if sub:
                ov[k] = sub
        elif k == "text":
            if t is None or t == "":
                rem["text"] = ""
            elif b and t.startswith(b + "\n\n"):
                ov["text"] = t[len(b) + 2:]
            else:
                if b:
                    rem["text"] = ""
                ov["text"] = t
        else:
            if t is None:
                pass  # unexpected; verification will catch
            elif _is_obj(b) and _is_obj(t):
                ov[k] = _diff_obj(b, t)
            else:
                ov[k] = t
    if rem:
        ov["removed"] = rem
    return ov


def common_core(copies):
    """Intersection of several assets: what is identical/shared across all of them."""
    first = copies[0]
    core = {}
    for k, v in first.items():
        vals = [c.get(k) for c in copies]
        if any(x is None for x in vals):
            continue
        if k == "keywords":
            kw = {}
            for c, lst in v.items():
                if all(c in (x or {}) for x in vals):
                    kw[c] = [i for i in lst if all(i in x[c] for x in vals)]
            core[k] = kw
        elif k in PERMISSION:
            p = {}
            for c, lst in v.items():
                if isinstance(lst, list) and all(isinstance(x.get(c), list) for x in vals):
                    p[c] = [i for i in lst if all(i in x[c] for x in vals)]
            if p:
                core[k] = p
        elif k == "assets":
            a = {}
            for d in ("traits", "included"):
                pools = [list(x.get(d) or []) for x in vals]
                keep = []
                for item in pools[0]:
                    if all(item in pl for pl in pools[1:]):
                        keep.append(item)
                        for pl in pools[1:]:
                            pl.remove(item)
                if keep:
                    a[d] = keep
            core[k] = a
        elif k in ("stats", "rules", "meta", "aspects"):
            core[k] = {n: sv for n, sv in v.items() if all(x.get(n) == sv for x in vals)}
        elif all(x == v for x in vals):
            core[k] = v
    return core
