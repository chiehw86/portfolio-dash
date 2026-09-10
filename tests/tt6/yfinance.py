import datetime, os, json
D = datetime.date
SPEC = json.load(open(os.path.join(os.path.dirname(__file__), 'spec.json')))
def _mk(v): return [(D(*map(int, d.split('-'))), p) for d, p in v]
class _Idx:
    def __init__(s, d): s._d = d; s.tz = None
    def __getitem__(s, i):
        class W:
            def __init__(w, x): w._d = x
            def date(w): return w._d
        return W(s._d[i])
class _S:
    def __init__(s, d, v): s._days, s._vals = d, v; s.index = _Idx(d)
    def dropna(s): return s
    def __len__(s): return len(s._vals)
    def items(s):
        class W:
            def __init__(w, x): w._d = x
            def date(w): return w._d
        return zip((W(x) for x in s._days), s._vals)
    @property
    def iloc(s):
        class L:
            def __getitem__(l, i): return s._vals[i]
        return L()
class _FI:
    def __init__(s, l, p): s.last_price, s.previous_close = l, p
DEFAULT = {"bars": [["2026-08-28", 10.0], ["2026-08-31", 10.1]], "last": 10.1, "pc": 10.0}
class Ticker:
    def __init__(s, y): s.y = y
    @property
    def fast_info(s):
        c = SPEC.get(s.y, DEFAULT)
        if c.get("last") is None: raise RuntimeError("rate limited")
        return _FI(c["last"], c.get("pc"))
    def history(s, **kw):
        c = SPEC.get(s.y, DEFAULT)
        if not c.get("bars"): raise RuntimeError("rate limited")
        b = _mk(c["bars"])
        return {"Close": _S([x[0] for x in b], [x[1] for x in b])}
