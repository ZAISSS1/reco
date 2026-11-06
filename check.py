# 每日進場檢查工具：只做W・週四不進場（100點版）＋20日漲跌篩選＋120MA條件
import urllib.request, urllib.parse, csv, io, json, datetime, os, sys, webbrowser, html, re, time
from collections import defaultdict

if sys.stdout is None:  # 用 pythonw（排程）執行時沒有主控台
    sys.stdout = sys.stderr = open(os.devnull, 'w', encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = next((p for p in (os.path.join(HERE, 'finmind_token.txt'), os.path.join(os.path.dirname(HERE), 'finmind_token.txt'),
                               os.path.join(os.path.dirname(os.path.dirname(HERE)), 'finmind_token.txt'))
                   if os.path.exists(p)), '')
CLOUD = '--cloud' in sys.argv     # 雲端（GitHub）模式：不開瀏覽器、不跳視窗、輸出到 site/index.html
now_tw = lambda: datetime.datetime.utcnow() + datetime.timedelta(hours=8)   # 一律用台灣時間
today_tw = lambda: now_tw().date()
LOW, HIGH = -1.0, 3.0            # 20日漲跌在這個區間 → 不進場
RULE = {0: 7, 1: 6, 2: 5, 4: 3}  # 週一剩7、週二剩6、週三剩5、週五剩3；週四不進場
WD = '一二三四五六日'
SINGLE_LOTS = 2                  # 只剩一腳能買時，把另一腳預算加進來買幾口（改成 1 就是 A+ 原版）


def taifex_daily(start, end):
    """期交所一次最多查一個月，分段下載。"""
    out, s = {}, start
    while s <= end:
        e = min(s + datetime.timedelta(27), end)
        out.update(_taifex_chunk(s, e))
        s = e + datetime.timedelta(1)
    return out


def _taifex_chunk(start, end):
    data = urllib.parse.urlencode(dict(down_type=1, commodity_id='TX', commodity_id2='',
                                       queryStartDate=start.strftime('%Y/%m/%d'),
                                       queryEndDate=end.strftime('%Y/%m/%d'))).encode()
    raw = urllib.request.urlopen(urllib.request.Request('https://www.taifex.com.tw/cht/3/futDataDown', data=data),
                                 timeout=120).read()
    by = defaultdict(list)
    for x in csv.reader(io.StringIO(raw.decode('big5', 'replace'))):
        if len(x) > 17 and x[1].strip() == 'TX' and x[17].strip() == '一般' and '/' not in x[2]:
            try:
                by[x[0].strip()].append((int(x[9] or 0), float(x[6]), x[2].strip()))
            except ValueError:
                pass
    return {d: max(L)[1:] for d, L in by.items()}  # 成交量最大的月份（近月）收盤


def finmind_bars(day, tok):
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesTick&data_id=TX&start_date={day}"
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok})
    js = json.loads(urllib.request.urlopen(req, timeout=180).read())
    rows = [x for x in js.get('data', []) if x['date'][:10] == day and '08:45' <= x['date'][11:16] <= '13:45'
            and x['contract_date'].isdigit()]
    if not rows:
        return {}
    near = min(x['contract_date'] for x in rows)
    bars = {}
    for x in rows:
        if x['contract_date'] != near:
            continue
        hh, mm = int(x['date'][11:13]), int(x['date'][14:16])
        if (hh, mm) == (13, 45):
            hh, mm = 13, 44
        m = hh * 60 + mm
        b = m - (m - 525) % 5
        k = f'{b // 60:02d}:{b % 60:02d}'
        p = x['price']
        v = bars.get(k)
        if v is None:
            bars[k] = [p, p, p, p]
        else:
            v[1] = max(v[1], p); v[2] = min(v[2], p); v[3] = p
    return bars


def mis_bars_today():
    """期交所行情網：台指期近月今天日盤的1分K → 合成5分K（盤中就有，FinMind 要收盤後才有）。"""
    H = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    def post(ep, body):
        req = urllib.request.Request('https://mis.taifex.com.tw/futures/api/' + ep, data=json.dumps(body).encode(), headers=H)
        return json.loads(urllib.request.urlopen(req, timeout=60).read())['RtData']
    L = post('getQuoteList', {"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
                              "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"})['QuoteList']
    sid = next(x['SymbolID'] for x in L if re.match(r'^TXF[A-L]\d-F$', x['SymbolID']))  # 第一個＝近月
    r = post('getChartData1M', {"SymbolID": sid})
    bars = {}
    for t, o_, h, l, c, v in r.get('Ticks', []):
        m = int(t[:2]) * 60 + int(t[2:4]) - 1          # 時間是這根1分K的結束時間
        if not 525 <= m <= 824:
            continue
        b = m - (m - 525) % 5; k = f'{b // 60:02d}:{b % 60:02d}'
        o_, h, l, c = float(o_), float(h), float(l), float(c)
        x = bars.get(k)
        if x is None: bars[k] = [o_, h, l, c]
        else: x[1] = max(x[1], h); x[2] = min(x[2], l); x[3] = c
    return bars


def taifex_quotes():
    """期交所即時行情（日盤）：回傳 W 週選每一檔的 (到期日, 履約價, C/P, 成交價, 買價, 賣價, 時間)。"""
    body = {"MarketType": "0", "SymbolType": "O", "KindID": "1", "CID": "TXO", "ExpireMonth": "",
            "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}
    req = urllib.request.Request('https://mis.taifex.com.tw/futures/api/getQuoteList', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    L = json.loads(urllib.request.urlopen(req, timeout=60).read())['RtData']['QuoteList']
    out = []
    for x in L:
        name = x.get('DispEName', '')
        m = re.match(r'^(\w{3})(\d+)([A-X])\d-O$', x.get('SymbolID', ''))
        e = re.search(r'\((\d{4}/\d{2}/\d{2})\)', name)
        mm = re.match(r'^TXO(\d{2})(\d)$', name)
        if not m:
            continue
        if e and re.search(r'W\d', name):
            exp = e.group(1)
        elif mm:  # 月選：到期日＝該月第三個週三
            y, mo = 2020 + int(mm.group(2)), int(mm.group(1))
            d1 = datetime.date(y, mo, 1)
            exp = (d1 + datetime.timedelta((2 - d1.weekday()) % 7 + 14)).strftime('%Y/%m/%d')
            name = f'TXO {y}/{mo:02d} 月選'
        else:
            continue
        cp = 'C' if m.group(3) <= 'L' else 'P'
        f = lambda v: float(v) if v not in ('', None) and float(v) > 0 else None
        out.append(dict(exp=exp, K=int(m.group(2)), cp=cp, last=f(x.get('CLastPrice')),
                        bid=f(x.get('CBidPrice1')), ask=f(x.get('CAskPrice1')), t=x.get('CTime', ''),
                        name=name if mm else name.split(' ')[0], monthly=bool(mm)))
    return out


def pick_options(D):
    """找下一個週三到期的 W，挑權利金最接近100點（85~110）的 Call 與 Put。"""
    target = next_wed(D)
    q = taifex_quotes()
    exps = sorted({x['exp'] for x in q})
    if not exps:
        return dict(error='期交所即時行情抓不到 W 週選')
    best = min(exps, key=lambda e: abs((datetime.datetime.strptime(e, '%Y/%m/%d').date() - target).days))
    if abs((datetime.datetime.strptime(best, '%Y/%m/%d').date() - target).days) > 2:
        return dict(error=f'找不到 {target:%m/%d} 附近到期的 W（目前掛牌：{", ".join(exps)}）')
    q = [x for x in q if x['exp'] == best]
    if any(not x['monthly'] for x in q):
        q = [x for x in q if not x['monthly']]
    res = dict(exp=best, name=q[0]['name'], monthly=q[0]['monthly'])
    for cp in 'CP':
        cand = []
        for x in q:
            if x['exp'] != best or x['cp'] != cp:
                continue
            px = x['last'] or ((x['bid'] + x['ask']) / 2 if x['bid'] and x['ask'] else None)
            if px:
                cand.append((abs(px - 100), x['K'], px, x))
        if cand:
            _, K, px, x = min(cand, key=lambda a: (a[0], a[1]))
            res[cp] = dict(K=K, px=px, bid=x['bid'], ask=x['ask'], t=x['t'], ten=round(min(px, 100) * 10),
                           five=round(min(px, 100) * 5), inrange=85 <= px <= 110)
        else:
            res[cp] = None
    return res


def popup(title, text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40 | 0x40000)  # 資訊圖示＋置頂
    except Exception:
        pass


def next_wed(D):
    """週五→下週三（剩3日）；週一/二/三→下週的週三。"""
    if D.weekday() == 4:
        return D + datetime.timedelta(5)
    return D + datetime.timedelta((2 - D.weekday()) % 7 + (7 if D.weekday() <= 2 else 0))


def main():
    D = today_tw()
    if len(sys.argv) > 1 and sys.argv[1][:1].isdigit():
        D = datetime.datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    while D.weekday() >= 5:
        D += datetime.timedelta(1)
    Ds = D.strftime('%Y/%m/%d')
    closes = taifex_daily(D - datetime.timedelta(60), D)
    prev = sorted(d for d in closes if d < Ds)
    o = dict(date=Ds, wd=WD[D.weekday()], wd_ok=D.weekday() in RULE)
    # ② 20日漲跌：昨天收盤 vs 今天往回數第20個交易日的收盤
    pc_d, ref_d = prev[-1], prev[-20]
    pc, ref = closes[pc_d][0], closes[ref_d][0]
    chg = (pc / ref - 1) * 100
    o.update(pc_d=pc_d, pc=pc, ref_d=ref_d, ref=ref, chg=chg, chg_ok=not (LOW < chg < HIGH))
    # A+ 短線篩選：10日漲跌（昨收 vs 往回第11個交易日）、3日漲跌（昨收 vs 往回第4個交易日）
    r10 = (pc / closes[prev[-11]][0] - 1) * 100
    r3 = (pc / closes[prev[-4]][0] - 1) * 100
    o.update(r10=r10, r3=r3)
    # ③ 120MA（排程模式下，若今天上午資料還沒齊，每分鐘重試，最多 10 次）
    try:
        tok = os.environ.get('FINMIND_TOKEN') or open(TOKEN_FILE, encoding='utf-8').read().strip()
        past = []
        for d in prev[-3:]:
            past += [(d, k, *v) for k, v in sorted(finmind_bars(d.replace('/', '-'), tok).items())]
        retry = ('--notify' in sys.argv or CLOUD) and o['wd_ok'] and o['chg_ok']
        for attempt in range(10 if retry else 1):
            tb = {}
            if D == today_tw():
                try: tb = mis_bars_today()
                except Exception: tb = {}
            if len([k for k in tb if k < '12:00']) < 39:
                tb = finmind_bars(Ds.replace('/', '-'), tok) or tb
            seq = past + [(Ds, k, *v) for k, v in sorted(tb.items())]
            morning = [i for i, s in enumerate(seq) if s[0] == Ds and s[1] < '12:00']
            if len(morning) >= 39 or D != today_tw():
                break
            time.sleep(60)
        if len(morning) < 35:
            ma = dict(status=f'今天上午的5分K還不夠（目前 {len(morning)} 根，需要 39 根）。請 12:00 之後再執行，或自行看盤確認')
        else:
            below = touch = above = 0
            for i in morning:
                m = sum(s[5] for s in seq[i - 119:i + 1]) / 120
                l, h = seq[i][4], seq[i][3]
                if l <= m <= h: touch += 1
                elif l > m: above += 1
                else: below += 1
            ma = dict(status='OK' if touch == 0 and above == 0 else 'NG', below=below, touch=touch, above=above,
                      n=len(morning), last_ma=round(m))
    except Exception as e:
        ma = dict(status=f'無法取得 FinMind 資料：{e}')
    o['ma'] = ma
    if o['wd_ok']:
        o['contract'] = (f"{next_wed(D):%m/%d}（週三）到期的 W，剩約 {RULE[D.weekday()]} 個交易日；"
                         f"權利金選最接近 100 點（85~110）的 Call 與 Put 各 1 口")
    monthly = o['wd_ok'] and 15 <= next_wed(D).day <= 21   # 第三個週三＝月選結算，沒有 W
    if not o['wd_ok']:
        verdict, why = '不進場', '今天是星期四，規則不做'
    elif monthly:
        verdict, why = '不進場', f'{next_wed(D):%m/%d} 是月選結算日（沒有W週選），規則只買W'
    elif not o['chg_ok']:
        verdict, why = '不進場', f'20日漲跌 {chg:+.2f}%，落在 −1% ~ +3% 之間（慢漲或盤整）'
    elif ma['status'] == 'NG':
        verdict, why = '不進場', '上午的 K 棒有碰到或站上 120MA'
    elif ma['status'] == 'OK':
        verdict, why = '進場', '三個條件全部符合'
    else:
        verdict, why = '待確認', '星期和 20 日漲跌都符合，只剩 120MA 條件要確認'
    o['verdict'], o['why'] = verdict, why
    if True:  # 不進場也列出履約價供參考
        try:
            o['opt'] = pick_options(D)
            op_ = o['opt']
            if op_.get('P'):   # A+：不買 Put ＝ 20日漲>3% 或 3日跌>2%
                if chg >= HIGH: op_['P']['skip'] = '20日漲 > 3%'
                elif r3 < -2: op_['P']['skip'] = f'3日跌 {r3:.1f}%（> 2%）'
            if op_.get('C') and r10 > 3:   # A+：不買 Call ＝ 10日漲>3%
                op_['C']['skip'] = f'10日漲 {r10:.1f}%（> 3%）'
            buy = [cp for cp in 'CP' if op_.get(cp) and op_[cp]['inrange'] and not op_[cp].get('skip')]
            for cp in buy:
                op_[cp]['q'] = SINGLE_LOTS if len(buy) == 1 else 1
            o['buy'] = buy
            if verdict in ('進場', '待確認') and not buy:
                verdict, why = '不進場', '條件符合，但 Call 和 Put 都被篩掉或買不到'
            elif verdict == '進場' and len(buy) == 1:
                nm = 'Call' if buy[0] == 'C' else 'Put'
                why = f'三個條件全部符合・只買 {nm} {SINGLE_LOTS} 口（另一腳預算加進來）'
            elif verdict == '進場':
                why = '三個條件全部符合・Call＋Put 各 1 口'
            o['verdict'], o['why'] = verdict, why
        except Exception as e:
            o['opt'] = dict(error=f'抓即時報價失敗：{e}')
    write_html(o, [(d, closes[d][0]) for d in prev[-25:]])
    lines = [f"{Ds}（週{o['wd']}）→ {verdict}：{why}",
             f"20日漲跌：{pc_d} 收 {pc:,.0f} vs {ref_d} 收 {ref:,.0f} = {chg:+.2f}%",
             f"短線：10日 {r10:+.2f}%、3日 {r3:+.2f}%",
             f"120MA：{ma_text(ma)}"]
    op = o.get('opt')
    if op:
        if op.get('error'):
            lines.append(op['error'])
        else:
            tag = '【僅供參考，今天不進場】' if verdict == '不進場' else ''
            lines.append(f"{tag}合約：{op['name']}（{op['exp']} 到期）")
            for cp, nm in (('C', 'Call'), ('P', 'Put')):
                v = op.get(cp)
                if not v:
                    lines.append(f"  {nm}：抓不到報價")
                elif v.get('skip'):
                    lines.append(f"  {nm}：履約價 {v['K']}，價格 {v['px']:g}（{v['skip']}，A+方案這一腳不買）")
                elif v['inrange']:
                    lines.append(f"  {nm}：履約價 {v['K']}，價格 {v['px']:g}，買 {v.get('q', 1)} 口（10倍出場價 {v['ten']}）")
                else:
                    lines.append(f"  {nm}：最接近100點的是履約價 {v['K']}，價格 {v['px']:g}"
                                 f"（超出85~110，依規則這一腳不買）")
    print('\n'.join(lines))
    if '--notify' in sys.argv:
        popup('Reco-週選擇權工具', '\n'.join(lines))
    if CLOUD or '--mail' in sys.argv:
        send_mail(mail_subject(o), mail_body(o))


def mail_body(o):
    """信件內文：先列進場條件，空一行，再列要買的合約、履約價、價格、口數。"""
    ok = lambda b: '✅' if b else '❌'
    ma = o['ma']
    ma_ok = ma['status'] == 'OK'
    ma_mark = ok(ma_ok) if ma['status'] in ('OK', 'NG') else '⏳'
    L = [f"【{o['verdict']}】{o['why']}", f"{o['date']}（週{o['wd']}）", '',
         '■ 進場條件',
         f"{ok(o['wd_ok'])} ① 星期：週{o['wd']}（週四不做）",
         f"{ok(o['chg_ok'])} ② 20日漲跌：{o['chg']:+.2f}%（不能在 −1% ~ +3%）",
         f"　　　{o['pc_d'][5:]} 收 {o['pc']:,.0f} ／ {o['ref_d'][5:]} 收 {o['ref']:,.0f}",
         f"{ma_mark} ③ 120MA：上午39根要全在MA下方",
         f"　　　{ma_text(ma)}",
         f"🔎 ④ 短線篩選：10日 {o['r10']:+.2f}%、3日 {o['r3']:+.2f}%",
         '　　　（10日漲>3% 不買Call、3日跌>2% 不買Put）', '']
    op = o.get('opt') or {}
    L.append('■ 買什麼' if o['verdict'] == '進場' else '■ 合約（今天不進場，僅供參考）')
    if op.get('error'):
        L.append(op['error'])
    elif op:
        L.append(f"合約：{op['name']}（臺指選擇權 W 週選・{op['exp']} 週三到期）")
        for cp, nm in (('C', 'Call'), ('P', 'Put')):
            v = op.get(cp)
            L.append('')
            if not v:
                L.append(f"▸ {nm}：抓不到報價")
            elif v.get('skip'):
                L.append(f"▸ {nm}：不買（{v['skip']}）")
                L.append(f"　　參考：履約價 {v['K']:,}・價格 {v['px']:g} 點")
            elif not v['inrange']:
                L.append(f"▸ {nm}：不買（最接近100點的價格 {v['px']:g}，超出 85~110）")
                L.append(f"　　參考：履約價 {v['K']:,}")
            else:
                q = v.get('q', 1)
                act = '買' if o['verdict'] == '進場' else '（若進場）買'
                L += [f"▸ {nm}：{act} {q} 口",
                      f"　　履約價：{v['K']:,}",
                      f"　　價格：{v['px']:g} 點（買價 {v['bid'] or '-'}／賣價 {v['ask'] or '-'}）",
                      f"　　金額：約 {v['px'] * 50 * q:,.0f} 元（{v['px']:g} 點 × 50 × {q} 口）",
                      f"　　出場：5倍 {v['five']}・10倍 {v['ten']:,} 點"]
    L += ['', '網頁：https://zaisss1.github.io/reco/']
    return '\n'.join(L)


def mail_subject(o):
    """信件標題：結果＋合約名稱＋要買的腳。例：【進場】11/06 TX2W2（11/11到期）Call 42800 ×2口 @106"""
    op = o.get('opt') or {}
    s = f"【{o['verdict']}】{o['date'][5:]}"
    if op.get('name'):
        s += f" {op['name']}（{op['exp'][5:]}到期）"
    legs = [f"{'Call' if cp == 'C' else 'Put'} {op[cp]['K']} ×{op[cp].get('q', 1)}口 @{op[cp]['px']:g}"
            for cp in o.get('buy', [])] if o['verdict'] == '進場' else []
    return s + (' ' + '＋'.join(legs) if legs else '')


def send_mail(subject, body):
    """用 Gmail 寄信。帳號密碼只從環境變數讀（GitHub Secrets）：GMAIL_USER、GMAIL_APP_PASSWORD；MAIL_TO 可選。"""
    user, pw = os.environ.get('GMAIL_USER', ''), os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not (user and pw):
        print('（沒有設定 GMAIL_USER / GMAIL_APP_PASSWORD，略過寄信）')
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    to = [a.strip() for a in (os.environ.get('MAIL_TO') or user).split(',') if a.strip()]
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'], msg['To'] = user, ', '.join(to)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, to, msg.as_string())
        print('已寄信：' + subject)
    except Exception as e:
        print(f'寄信失敗：{e}')


def ma_text(ma):
    if ma['status'] in ('OK', 'NG'):
        return f"上午{ma['n']}根：MA下{ma['below']}、碰到{ma['touch']}、MA上{ma['above']}"
    return ma['status']


def write_html(o, hist):
    V = o['verdict']
    accent = {'進場': '#0a7d4f', '不進場': '#b4232c', '待確認': '#a16207'}[V]
    en = {'進場': 'ENTER', '不進場': 'STAND BY', '待確認': 'PENDING'}[V]
    ma = o['ma']
    if ma['status'] in ('OK', 'NG'):
        ma_val = f"{ma['below']} / {ma['n']}"
        ma_sub = f"碰到 {ma['touch']}・在上 {ma['above']}・MA≈{ma['last_ma']:,}"
        ma_ok = 'ok' if ma['status'] == 'OK' else 'ng'
    else:
        ma_val, ma_sub, ma_ok = '—', ma['status'], 'wait'

    def cond(idx, name, rule, val, sub, st):
        lab = {'ok': '符合', 'ng': '不符合', 'wait': '待確認'}[st]
        return (f"<div class='cond'><div class='ci'>{idx}</div><div><div class='t'>{name}</div>"
                f"<div class='s'>{rule}</div></div><div class='cv'><div class='t'>{val}</div>"
                f"<div class='s'>{html.escape(sub)}</div></div><div class='chip {st}'>{lab}</div></div>")

    conds = (cond('01', '星期', '週四不做', f"週{o['wd']}", '', 'ok' if o['wd_ok'] else 'ng')
             + cond('02', '20日漲跌', '不在 −1% ~ +3%', f"{o['chg']:+.2f}%",
                    f"{o['pc_d'][5:]} {o['pc']:,.0f} ／ {o['ref_d'][5:]} {o['ref']:,.0f}",
                    'ok' if o['chg_ok'] else 'ng')
             + cond('03', '120MA', '上午39根全在下方', ma_val, ma_sub, ma_ok)
             + cond('04', '短線篩選', 'Call：10日漲>3%不買／Put：3日跌>2%不買', f"{o['r10']:+.2f}% ／ {o['r3']:+.2f}%",
                    '10日 ／ 3日（只決定買哪一腳）', 'ok' if o['r10'] <= 3 and o['r3'] >= -2 else 'wait'))

    op = o.get('opt') or {}
    legs = ''
    if op.get('error'):
        legs = f"<div class='note'>{html.escape(op['error'])}</div>"
    elif op:
        cards = ''
        for cp, nm in (('C', 'CALL'), ('P', 'PUT')):
            v = op.get(cp)
            if not v:
                cards += f"<div class='leg off'><div class='lt'>{nm}</div><div class='strike'>—</div><div class='flag'>抓不到報價</div></div>"
                continue
            ok = v['inrange'] and not v.get('skip')
            other = '預算移到另一腳' if len(o.get('buy', [])) == 1 else ''
            flag = (f"<div class='flag'>{html.escape(v['skip'])}・不買這一腳{('・' + other) if other else ''}</div>" if v.get('skip')
                    else '' if v['inrange'] else f"<div class='flag'>超出 85~110・不買{('・' + other) if other else ''}</div>")
            qty = f"<div class='qty'>× {v.get('q', 1)} 口</div>" if ok else ''
            cards += (f"<div class='leg{'' if ok else ' off'}'>{qty}<div class='lt'>{nm}</div>"
                      f"<div class='strike'>{v['K']:,}</div><div class='px'>{v['px']:g}<span>點</span></div>"
                      f"<div class='grid'><div><span>買／賣</span>{v['bid'] or '—'} / {v['ask'] or '—'}</div>"
                      f"<div><span>5 倍</span>{v['five']:,}</div><div><span>10 倍出場</span>{v['ten']:,}</div></div>{flag}</div>")
        tag = "<span class='ref'>僅供參考・今天不進場</span>" if V == '不進場' else ''
        legs = (f"<div class='sh'><div><div class='k'>CONTRACT</div><div class='h'>{html.escape(op['name'])}"
                f"<span class='exp'>{op['exp']} 到期</span></div></div>{tag}</div><div class='legs'>{cards}</div>")

    cl = [c for _, c in hist]
    lo, hi = min(cl), max(cl)
    W, H = 640, 120
    pts = ' '.join(f"{i * W / (len(cl) - 1):.1f},{H - 8 - (c - lo) / ((hi - lo) or 1) * (H - 16):.1f}"
                   for i, c in enumerate(cl))
    ri = len(cl) - 20 if len(cl) >= 20 else 0
    rx = ri * W / (len(cl) - 1)
    spark = (f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='none' class='spark'>"
             f"<line x1='{rx:.1f}' y1='0' x2='{rx:.1f}' y2='{H}' class='rl'/><polyline points='{pts}' class='pl'/></svg>"
             f"<div class='sx'><span>{hist[0][0][5:]}</span><span>┆ 20日基準 {hist[ri][0][5:]}</span><span>{hist[-1][0][5:]}</span></div>")
    rows = ''.join(f"<tr><td>{d}</td><td>{c:,.0f}</td></tr>" for d, c in reversed(hist))

    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Reco-週選擇權工具</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#f5f4f0;--card:#fff;--ink:#141414;--mute:#8a8a85;--line:#e4e2dc;--acc:{accent}}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111110;--card:#1a1a19;--ink:#f2f1ec;--mute:#8d8c86;--line:#2c2c2a}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Noto Sans TC","Microsoft JhengHei",sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:760px;margin:0 auto;padding:32px 16px 48px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:18px;border-bottom:1.5px solid var(--ink)}}
.brand{{font-size:28px;font-weight:700;letter-spacing:-.03em}}
.brand span{{font-weight:400;color:var(--mute);font-size:14px;margin-left:10px;letter-spacing:.12em}}
.date{{text-align:right;font-size:12px;color:var(--mute);line-height:1.6}} .date b{{display:block;color:var(--ink);font-size:15px;font-weight:600}}
.hero{{padding:36px 0 30px;border-bottom:1px solid var(--line)}}
.k{{font-size:11px;letter-spacing:.2em;color:var(--mute);font-weight:600}}
.verdict{{display:flex;align-items:baseline;gap:14px;margin:10px 0 8px}}
.verdict .v{{font-size:60px;font-weight:700;letter-spacing:-.02em;color:var(--acc);line-height:1}}
.verdict .e{{font-size:12px;letter-spacing:.24em;color:var(--acc);font-weight:600}}
.why{{font-size:15px;opacity:.75}}
section{{padding:28px 0;border-bottom:1px solid var(--line)}}
.sh{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px;gap:10px;flex-wrap:wrap}}
.h{{font-size:20px;font-weight:600;margin-top:4px}} .exp{{font-size:13px;color:var(--mute);font-weight:400;margin-left:10px}}
.ref{{font-size:12px;color:#b4232c;border:1px solid currentColor;padding:4px 12px;border-radius:999px}}
.legs{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.leg{{position:relative;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}}
.qty{{position:absolute;top:18px;right:18px;font-size:13px;font-weight:700;color:#fff;background:var(--acc);padding:4px 12px;border-radius:999px}}
.leg.off .strike,.leg.off .px,.leg.off .grid{{opacity:.45}}
.lt{{font-size:11px;letter-spacing:.24em;font-weight:700;color:var(--mute)}}
.strike{{font-size:42px;font-weight:700;letter-spacing:-.03em;margin:8px 0 2px;font-variant-numeric:tabular-nums}}
.px{{font-size:18px;font-weight:600}} .px span{{font-size:12px;color:var(--mute);margin-left:3px;font-weight:400}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:16px;padding-top:12px;border-top:1px solid var(--line);font-size:13px;font-variant-numeric:tabular-nums}}
.grid span{{display:block;font-size:11px;color:var(--mute);margin-bottom:3px}}
.flag{{margin-top:12px;font-size:12px;color:#b4232c}}
.cond{{display:grid;grid-template-columns:30px 1fr auto 72px;align-items:center;gap:14px;padding:15px 0;border-top:1px solid var(--line)}}
.cond:first-of-type{{border-top:0;padding-top:4px}}
.ci{{font-size:12px;color:var(--mute);font-weight:600}}
.t{{font-size:15px;font-weight:600}} .s{{font-size:12px;color:var(--mute);margin-top:3px}}
.cv{{text-align:right;font-variant-numeric:tabular-nums}}
.chip{{font-size:12px;text-align:center;padding:6px 0;border-radius:999px;font-weight:600}}
.chip.ok{{background:#0a7d4f1c;color:#0a7d4f}} .chip.ng{{background:#b4232c1c;color:#b4232c}} .chip.wait{{background:#a162071c;color:#a16207}}
.spark{{width:100%;height:120px;display:block}}
.pl{{fill:none;stroke:var(--ink);stroke-width:1.8;vector-effect:non-scaling-stroke}}
.rl{{stroke:var(--mute);stroke-dasharray:3 4;vector-effect:non-scaling-stroke}}
.sx{{display:flex;justify-content:space-between;font-size:11px;color:var(--mute);margin-top:8px}}
details{{margin-top:16px;font-size:13px}} summary{{cursor:pointer;color:var(--mute)}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-variant-numeric:tabular-nums}}
td{{padding:7px 0;border-bottom:1px solid var(--line)}} td+td{{text-align:right}}
.note{{font-size:13px;padding:14px 16px;border-radius:12px;background:var(--card);border:1px solid var(--line);color:#a16207}}
footer{{padding-top:22px;font-size:12px;color:var(--mute);line-height:1.8}}
@media (max-width:560px){{.legs{{grid-template-columns:1fr}} .verdict .v{{font-size:46px}}
.cond{{grid-template-columns:24px 1fr auto 62px;gap:8px}} .brand span{{display:block;margin:4px 0 0}}}}
</style></head><body><div class="wrap">
<header><div class="brand">Reco<span>週選擇權工具</span></div>
<div class="date"><b>{o['date']}・週{o['wd']}</b>更新 {now_tw():%H:%M}</div></header>
<div class="hero"><div class="k">TODAY</div><div class="verdict"><div class="v">{V}</div><div class="e">{en}</div></div>
<div class="why">{html.escape(o['why'])}</div></div>
<section>{legs}</section>
<section><div class="sh"><div><div class="k">CHECKLIST</div><div class="h">進場條件</div></div></div>{conds}</section>
<section><div class="sh"><div><div class="k">TAIEX FUTURES</div><div class="h">近 25 日收盤</div></div>
<div class="exp">{lo:,.0f} – {hi:,.0f}</div></div>{spark}
<details><summary>查看每日收盤</summary><table>{rows}</table></details></section>
<footer>規則（A+方案・單腳加碼）｜只做 W 週選・週一剩7／週二剩6／週三剩5／週五剩3・週四不進場・權利金 85~110 點<br>
不買 Put：20日漲 &gt; 3% 或 3日跌 &gt; 2%｜不買 Call：10日漲 &gt; 3%｜只剩一腳時，把另一腳預算加進來買 {SINGLE_LOTS} 口<br>
出場｜開盤跳空 ≥10 倍→開盤價・當晚夜盤或隔天日盤開盤 ≥5 倍→開盤價・盤中碰 10 倍→10 倍價・其餘放到結算<br>
報價來自期交所行情資訊網（可能延遲），下單以券商報價為準。歷史回測不代表未來，非投資建議。</footer>
</div></body></html>"""
    p = os.path.join(HERE, '今日進場檢查.html')
    if CLOUD:
        os.makedirs(os.path.join(HERE, 'site'), exist_ok=True); p = os.path.join(HERE, 'site', 'index.html')
    open(p, 'w', encoding='utf-8').write(page)
    if '--no-open' not in sys.argv and not CLOUD:
        webbrowser.open('file:///' + p.replace('\\', '/'))


def tick(b):
    return '<b style="color:#1a7f37">✔ 符合</b>' if b else '<b style="color:#cf222e">✘ 不符合</b>'


if __name__ == '__main__':
    main()
