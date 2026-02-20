#!/usr/bin/env python3
"""
generate.py — IV Tracker Dashboard Generator
从 iv_scanner.db 读取数据，生成可视化 HTML dashboard
"""
import json, os, sqlite3, math
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / '..' / 'iv-scanner' / 'data' / 'iv_scanner.db'
OUTPUT = SCRIPT_DIR / 'index.html'


def score(iv, ratio, pct):
    s = 0
    if ratio:
        if ratio > 1.5: s += 40
        elif ratio > 1.2: s += 30
        elif ratio > 1.0: s += 15
    if iv > 0.6: s += 30
    elif iv > 0.4: s += 20
    elif iv > 0.25: s += 10
    if pct is not None:
        if pct > 80: s += 25
        elif pct > 60: s += 15
        elif pct > 40: s += 5
    return min(s, 100)


def sf(val, n=4):
    """safe float round"""
    if val is None: return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, n)
    except: return None


def load_data():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT d.* FROM daily_iv d
        INNER JOIN (SELECT symbol, MAX(date) as md FROM daily_iv GROUP BY symbol) m
        ON d.symbol = m.symbol AND d.date = m.md
        ORDER BY d.atm_iv DESC
    """).fetchall()

    symbols = []
    for r in rows:
        sym, iv = r['symbol'], r['atm_iv']
        tk = sym.replace('US.', '')

        hv = conn.execute(
            "SELECT hv_20,hv_50,hv_100 FROM historical_volatility WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (sym,)).fetchone()
        hv20 = hv['hv_20'] if hv else None
        hv50 = hv['hv_50'] if hv else None
        hv100 = hv['hv_100'] if hv else None
        ratio = round(iv / hv20, 2) if hv20 and hv20 > 0 else None

        hv_hist = conn.execute(
            "SELECT hv_20 FROM historical_volatility WHERE symbol=? AND hv_20 IS NOT NULL ORDER BY date DESC LIMIT 252",
            (sym,)).fetchall()
        pct = None
        if hv_hist:
            vals = [x['hv_20'] for x in hv_hist]
            pct = round(sum(1 for v in vals if v < iv) / len(vals) * 100, 1)

        sc = score(iv, ratio, pct)

        # HV time series
        hvs = conn.execute("""
            SELECT date,close_price,hv_20,hv_50 FROM historical_volatility
            WHERE symbol=? AND hv_20 IS NOT NULL ORDER BY date DESC LIMIT 130
        """, (sym,)).fetchall()
        hv_chart = [{'d': x['date'], 'p': sf(x['close_price'],2),
                      'h20': sf(x['hv_20']), 'h50': sf(x['hv_50'])}
                     for x in reversed(list(hvs))]

        # Option chain
        chain = conn.execute("""
            SELECT option_type,strike_price,implied_volatility,delta,gamma,theta,vega,
                   bid_price,ask_price,volume,open_interest
            FROM option_chain_snapshot WHERE symbol=? AND date=?
            ORDER BY strike_price,option_type
        """, (sym, r['date'])).fetchall()
        ch = [{'t': c['option_type'][:1], 's': c['strike_price'],
               'iv': sf(c['implied_volatility']), 'd': sf(c['delta'],3),
               'th': sf(c['theta'],3), 'v': sf(c['vega'],3),
               'b': sf(c['bid_price'],2), 'a': sf(c['ask_price'],2),
               'vol': c['volume'] or 0, 'oi': c['open_interest'] or 0}
              for c in chain]

        symbols.append({
            'tk': tk, 'px': r['stock_price'], 'iv': sf(iv),
            'civ': sf(r['call_iv']), 'piv': sf(r['put_iv']),
            'dte': r['atm_dte'], 'exp': r['atm_expiry'],
            'hv20': sf(hv20), 'hv50': sf(hv50), 'hv100': sf(hv100),
            'ratio': ratio, 'pct': pct, 'sc': sc, 'dt': r['date'],
            'hvc': hv_chart, 'ch': ch,
        })

    # ── Near-term chains for Wheel trading ──
    near_term = []
    near_rows = conn.execute("""
        SELECT DISTINCT symbol, expiry_date, dte, stock_price
        FROM option_chain_snapshot
        WHERE dte <= 16 AND date = (SELECT MAX(date) FROM option_chain_snapshot)
        ORDER BY symbol, dte
    """).fetchall()

    for nr in near_rows:
        sym = nr['symbol']
        tk = sym.replace('US.', '')
        chain = conn.execute("""
            SELECT option_type,strike_price,implied_volatility,delta,gamma,theta,vega,
                   bid_price,ask_price,volume,open_interest
            FROM option_chain_snapshot WHERE symbol=? AND expiry_date=?
              AND date=(SELECT MAX(date) FROM option_chain_snapshot WHERE symbol=?)
            ORDER BY strike_price,option_type
        """, (sym, nr['expiry_date'], sym)).fetchall()

        px = nr['stock_price']
        ch = []
        for c in chain:
            iv_val = sf(c['implied_volatility'])
            delta = sf(c['delta'], 3)
            bid = sf(c['bid_price'], 2)
            ask = sf(c['ask_price'], 2)
            mid = round((bid + ask) / 2, 2) if bid is not None and ask is not None else None
            ch.append({
                't': c['option_type'][:1], 's': c['strike_price'],
                'iv': iv_val, 'd': delta,
                'th': sf(c['theta'], 3), 'v': sf(c['vega'], 3),
                'b': bid, 'a': ask, 'm': mid,
                'vol': c['volume'] or 0, 'oi': c['open_interest'] or 0,
            })

        # Compute ATM IV for this expiry
        atm_iv = None
        if ch and px:
            atm_opts = sorted(ch, key=lambda x: abs(x['s'] - px))[:2]
            ivs = [o['iv'] for o in atm_opts if o['iv']]
            atm_iv = sf(sum(ivs) / len(ivs)) if ivs else None

        # Best CSP and CC candidates
        puts = [o for o in ch if o['t'] == 'P' and o['d'] is not None
                and -0.40 <= o['d'] <= -0.15 and o.get('m') and o['m'] > 0.05]
        calls = [o for o in ch if o['t'] == 'C' and o['d'] is not None
                 and 0.15 <= o['d'] <= 0.40 and o.get('m') and o['m'] > 0.05]
        puts.sort(key=lambda x: abs(x['d'] + 0.30))
        calls.sort(key=lambda x: abs(x['d'] - 0.30))

        near_term.append({
            'tk': tk, 'sym': sym, 'px': px,
            'exp': nr['expiry_date'], 'dte': nr['dte'],
            'atm_iv': atm_iv,
            'ch': ch,
            'csp': puts[:5],   # top 5 CSP candidates
            'cc': calls[:5],   # top 5 CC candidates
        })

    stats = {}
    for t in ['daily_iv','option_chain_snapshot','historical_volatility']:
        stats[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()['c']
    conn.close()

    return {'symbols': symbols, 'near': near_term, 'stats': stats,
            'ts': datetime.now().strftime('%Y-%m-%d %H:%M')}


def main():
    data = load_data()
    jdata = json.dumps(data, ensure_ascii=False, separators=(',',':'))

    with open(SCRIPT_DIR / 'template.html', 'r') as f:
        tpl = f.read()

    html = tpl.replace('/*__DATA__*/"placeholder"', jdata)

    with open(OUTPUT, 'w') as f:
        f.write(html)

    n = len(data['symbols'])
    print(f"✅ Generated index.html — {n} symbols, {data['stats']['option_chain_snapshot']} chains")


if __name__ == '__main__':
    main()
