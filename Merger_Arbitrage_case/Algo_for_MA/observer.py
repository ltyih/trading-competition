# -*- coding: utf-8 -*-
"""Observation monitor for full sub-heat - logs all data for analysis."""

import requests
import time
import sys

API_KEY = {'X-API-Key': 'AJDSYHVC'}
BASE = 'http://localhost:10000/v1'

DEALS = {
    'D1': {'target': 'TGX', 'acquirer': 'PHR', 'structure': 'ALL_CASH', 'cash': 50.00, 'ratio': 0.0, 'p0': 0.70, 't_start': 43.70, 'a_start': 47.50, 'deal_mult': 1.00},
    'D2': {'target': 'BYL', 'acquirer': 'CLD', 'structure': 'STOCK_FOR_STOCK', 'cash': 0.0, 'ratio': 0.75, 'p0': 0.55, 't_start': 43.50, 'a_start': 79.30, 'deal_mult': 1.05},
    'D3': {'target': 'GGD', 'acquirer': 'PNR', 'structure': 'MIXED', 'cash': 33.00, 'ratio': 0.20, 'p0': 0.50, 't_start': 31.50, 'a_start': 59.80, 'deal_mult': 1.10},
    'D4': {'target': 'FSR', 'acquirer': 'ATB', 'structure': 'ALL_CASH', 'cash': 40.00, 'ratio': 0.0, 'p0': 0.38, 't_start': 30.50, 'a_start': 62.20, 'deal_mult': 1.30},
    'D5': {'target': 'SPK', 'acquirer': 'EEC', 'structure': 'STOCK_FOR_STOCK', 'cash': 0.0, 'ratio': 1.20, 'p0': 0.45, 't_start': 52.80, 'a_start': 48.00, 'deal_mult': 1.15},
}

standalone = {}
for did, d in DEALS.items():
    K0 = d['cash'] + d['ratio'] * d['a_start']
    V = (d['t_start'] - d['p0'] * K0) / (1 - d['p0'])
    standalone[did] = V


def deal_value(did, ap):
    d = DEALS[did]
    return d['cash'] + d['ratio'] * ap


def implied_prob(did, tp, ap):
    K = deal_value(did, ap)
    V = standalone[did]
    denom = K - V
    if abs(denom) < 0.01:
        return None
    return max(0.0, min(1.0, (tp - V) / denom))


def main():
    last_news_id = 0
    all_news = []
    nlv_history = []

    # Skip existing news
    try:
        r = requests.get(BASE + '/news', headers=API_KEY, params={'limit': 200}, timeout=5)
        if r.ok and r.json():
            last_news_id = max(n.get('news_id', 0) for n in r.json())
            print('Skipped %d old news items (last_id=%d)' % (len(r.json()), last_news_id))
    except Exception:
        pass

    print('=' * 100)
    print('OBSERVATION MONITOR - FULL SUB-HEAT')
    print('=' * 100)
    sys.stdout.flush()

    tick = 0
    cycle = 0
    last_full_report = 0

    while tick < 600:
        try:
            r = requests.get(BASE + '/case', headers=API_KEY, timeout=5)
            if not r.ok:
                time.sleep(0.5)
                continue
            case = r.json()
            tick = case.get('tick', 0)
            status = case.get('status', '')
            if status not in ('ACTIVE', 'RUNNING'):
                if status == 'STOPPED':
                    print('Sub-heat ended at tick %d' % tick)
                    break
                time.sleep(1)
                continue

            # Get securities
            r_sec = requests.get(BASE + '/securities', headers=API_KEY, timeout=5)
            prices = {}
            positions = {}
            if r_sec.ok:
                for sec in r_sec.json():
                    t = sec.get('ticker')
                    bid = sec.get('bid', 0)
                    ask = sec.get('ask', 0)
                    last_px = sec.get('last', 0)
                    if bid and ask:
                        prices[t] = {'mid': round((bid + ask) / 2, 4), 'bid': bid, 'ask': ask, 'last': last_px}
                    elif last_px > 0:
                        prices[t] = {'mid': last_px, 'bid': bid, 'ask': ask, 'last': last_px}
                    positions[t] = sec.get('position', 0)

            # Get NLV
            nlv = 0
            try:
                r_trader = requests.get(BASE + '/trader', headers=API_KEY, timeout=5)
                if r_trader.ok:
                    nlv = r_trader.json().get('nlv', 0)
            except Exception:
                pass

            # Get news
            r_news = requests.get(BASE + '/news', headers=API_KEY,
                                  params={'since': last_news_id, 'limit': 100}, timeout=5)
            new_news = []
            if r_news.ok:
                for n in r_news.json():
                    nid = n.get('news_id', 0)
                    if nid > last_news_id:
                        new_news.append(n)
                        last_news_id = max(last_news_id, nid)

            # Log new news
            for n in sorted(new_news, key=lambda x: x.get('news_id', 0)):
                headline = n.get('headline', '')
                body = n.get('body', '')
                nid = n.get('news_id', 0)
                print('')
                print('*** NEWS #%d at tick %d ***' % (nid, tick))
                print('  HEADLINE: %s' % headline)
                if body:
                    print('  BODY: %s' % body[:300])
                all_news.append({'tick': tick, 'id': nid, 'headline': headline, 'body': body[:300]})
                sys.stdout.flush()

            # Full report every 30 ticks or on news
            if tick - last_full_report >= 30 or new_news:
                print('')
                print('--- TICK %3d | NLV: $%s ---' % (tick, '{:,.2f}'.format(nlv)))
                for did in ['D1', 'D2', 'D3', 'D4', 'D5']:
                    d = DEALS[did]
                    tp_data = prices.get(d['target'], {})
                    ap_data = prices.get(d['acquirer'], {})
                    tp = tp_data.get('mid', 0)
                    ap = ap_data.get('mid', 0)
                    t_pos = positions.get(d['target'], 0)
                    a_pos = positions.get(d['acquirer'], 0)
                    if tp > 0 and ap > 0:
                        K = deal_value(did, ap)
                        mp = implied_prob(did, tp, ap)
                        spread = K - tp
                        mp_str = '%.1f%%' % (mp * 100) if mp is not None else 'N/A'
                        print('  %s: T=$%.2f A=$%.2f K=$%.2f V=$%.2f mktP=%s spread=$%.2f tPos=%+d aPos=%+d'
                              % (did, tp, ap, K, standalone[did], mp_str, spread, int(t_pos), int(a_pos)))

                gross = sum(abs(v) for v in positions.values())
                net = sum(v for v in positions.values())
                print('  Gross=%.0f/100k Net=%.0f/50k' % (gross, net))
                last_full_report = tick
                sys.stdout.flush()

            # Store NLV snapshot
            if cycle % 10 == 0:
                nlv_history.append({'tick': tick, 'nlv': nlv})

            cycle += 1
            time.sleep(0.4)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print('Error: %s' % e)
            time.sleep(1)

    # Final summary
    print('')
    print('=' * 100)
    print('OBSERVATION SUMMARY')
    print('=' * 100)
    print('Total news items seen: %d' % len(all_news))
    print('Final NLV: $%s' % '{:,.2f}'.format(nlv))
    print('')
    print('NLV history:')
    for entry in nlv_history:
        print('  Tick %3d: $%s' % (entry['tick'], '{:,.2f}'.format(entry['nlv'])))
    print('')
    print('All news this heat:')
    for n in all_news:
        print('  Tick %3d #%d: %s' % (n['tick'], n['id'], n['headline'][:120]))
    print('=' * 100)


if __name__ == '__main__':
    main()
