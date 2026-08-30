from __future__ import annotations

import calendar
import json
import math
import os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf

ET = ZoneInfo("America/New_York")
TICKERS = ["SOXL", "LITE", "AAOI", "MRVL", "MU", "AVGO", "QTUM", "DRAM", "SMH"]
PUT_NAMES = ["SOXL", "LITE", "AAOI", "MRVL", "MU", "AVGO", "QTUM", "DRAM"]
ETF_TICKERS = {"SOXL", "QTUM", "DRAM", "SMH"}
SHORT_PUT_ELIGIBLE_RATINGS = {"BUY", "STRONG BUY"}
RISK_FREE = 0.04
TRADIER_BASE = "https://api.tradier.com/v1"
TRADIER_TOKEN = os.getenv("TRADIER_TOKEN", "").strip()
CANONICAL_URL = os.getenv("STOCK_CANONICAL_URL", "https://raw.githubusercontent.com/skydiver1118/Automation/main/stock-project-v2/canonical_market.json")
SCORES_URL = os.getenv("STOCK_SCORES_URL", "https://raw.githubusercontent.com/skydiver1118/Automation/main/stock-project-v2/latest_scores.json")


def f(x):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return round(float(x), 2)
    except Exception:
        return None


def long_term_rating(score) -> str:
    try: x = float(score)
    except Exception: return "UNRATED"
    if x >= 65: return "STRONG BUY"
    if x >= 55: return "BUY"
    if x >= 45: return "HOLD"
    return "AVOID"


def entry_quality(score) -> str:
    try: x = float(score)
    except Exception: return "UNRATED"
    if x >= 75: return "EXCELLENT"
    if x >= 65: return "GOOD"
    if x >= 55: return "FAIR"
    return "WAIT"


def short_put_eligible(rating) -> bool:
    return str(rating or "").strip().upper() in SHORT_PUT_ELIGIBLE_RATINGS


def load_json_url(url: str) -> dict | list:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "option-dashboard/2.0"})
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def load_stock_v2_layer() -> tuple[dict, str, str | None]:
    errors = []
    try:
        j = load_json_url(CANONICAL_URL)
        stocks = (j or {}).get("stocks") or {}
        if stocks:
            return stocks, "Stock V2 canonical", (j or {}).get("as_of")
    except Exception as exc:
        errors.append(f"canonical: {exc}")
    try:
        rows = load_json_url(SCORES_URL)
        stocks = {}
        for row in rows if isinstance(rows, list) else []:
            ticker = str(row.get("ticker", "")); lt = row.get("long_term_score"); es = row.get("entry_score", row.get("short_term_score"))
            rating = row.get("long_term_rating") or long_term_rating(lt); quality = row.get("entry_quality") or entry_quality(es)
            stocks[ticker] = {"ticker": ticker, "as_of": row.get("as_of"), "price": row.get("price"), "long_term_score": lt, "long_term_rating": rating, "entry_score": es, "entry_quality": quality, "short_put_eligible": short_put_eligible(rating), "technical": {"rsi14": row.get("rsi14"), "macd_hist": row.get("macd_hist"), "adx14": row.get("adx14"), "trend": "MIXED"}, "support": {}, "diagnostic_sentiment": {}}
        as_of = next((x.get("as_of") for x in stocks.values() if x.get("as_of")), None)
        if stocks:
            return stocks, "Stock V2 scores fallback", as_of
    except Exception as exc:
        errors.append(f"scores: {exc}")
    print("WARN Stock V2 layer unavailable: " + " | ".join(errors))
    return {}, "Stock V2 unavailable", None


def fibs(low, high):
    r = high - low
    return {"23.6%": high - .236*r, "38.2%": high - .382*r, "50%": high - .5*r, "61.8%": high - .618*r, "78.6%": high - .786*r}


def support_below(price, fib):
    vals = [float(v) for v in fib.values() if v is not None and float(v) <= price]
    return max(vals) if vals else float(fib["78.6%"])


def third_friday(y, m):
    c = calendar.Calendar().monthdatescalendar(y, m)
    return [d for w in c for d in w if d.month == m and d.weekday() == 4][2]


def is_monthly_expiry(s):
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date(); return d == third_friday(d.year, d.month)
    except Exception: return False


def tradier_get(path, params):
    if not TRADIER_TOKEN: raise RuntimeError("TRADIER_TOKEN not configured")
    req = Request(f"{TRADIER_BASE}{path}?{urlencode(params)}", headers={"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json", "User-Agent": "option-dashboard/2.0"})
    with urlopen(req, timeout=20) as resp: return json.loads(resp.read().decode())


def tradier_expirations(symbol):
    j = tradier_get("/markets/options/expirations", {"symbol": symbol, "includeAllRoots": "false", "strikes": "false"}); dates = ((j or {}).get("expirations") or {}).get("date") or []
    return [dates] if isinstance(dates, str) else list(dates)


def yfinance_expirations(symbol): return list(yf.Ticker(symbol).options)


def choose_expiry(symbol):
    today = datetime.now(ET).date(); sources = []
    if TRADIER_TOKEN:
        try: sources = [("Tradier", tradier_expirations(symbol))]
        except Exception as exc: print(f"Tradier expirations fallback for {symbol}: {exc}")
    sources.append(("yfinance", yfinance_expirations(symbol)))
    for source, dates in sources:
        monthly = []
        for s in dates:
            try:
                d = datetime.strptime(s, "%Y-%m-%d").date()
                if (d-today).days > 7 and is_monthly_expiry(s): monthly.append((d,s))
            except Exception: pass
        if monthly: return min(monthly, key=lambda x:x[0])[1], source
    return None, None


def norm_cdf(x): return .5*(1+math.erf(x/math.sqrt(2)))


def put_delta_bs(spot, strike, iv, dte):
    try:
        sigma=max(float(iv),1e-6); t=max(dte/365,1e-6); d1=(math.log(spot/strike)+(RISK_FREE+.5*sigma*sigma)*t)/(sigma*math.sqrt(t)); return norm_cdf(d1)-1
    except Exception: return None


def earnings_date(ticker):
    if ticker in ETF_TICKERS: return None
    try:
        cal=yf.Ticker(ticker).calendar; ed=cal.get("Earnings Date") if isinstance(cal,dict) else None
        if not ed: return None
        return pd.Timestamp(ed[0] if isinstance(ed,list) else ed).date()
    except Exception: return None


def tradier_put_rows(ticker, expiry):
    j=tradier_get("/markets/options/chains", {"symbol":ticker,"expiration":expiry,"greeks":"true"}); opts=((j or {}).get("options") or {}).get("option") or []; opts=[opts] if isinstance(opts,dict) else opts; rows=[]
    for o in opts:
        if str(o.get("option_type","")).lower()!="put": continue
        g=o.get("greeks") or {}; iv=g.get("mid_iv") if g.get("mid_iv") is not None else g.get("smv_vol"); delta=g.get("delta")
        rows.append({"strike":o.get("strike"),"bid":o.get("bid"),"ask":o.get("ask"),"iv":iv,"delta":delta,"source":"Tradier","delta_source":"Tradier/ORATS" if delta is not None else None})
    return rows


def yfinance_put_rows(ticker, expiry):
    p=yf.Ticker(ticker).option_chain(expiry).puts
    return [{"strike":r.strike,"bid":r.bid,"ask":r.ask,"iv":r.impliedVolatility if pd.notna(r.impliedVolatility) else None,"delta":None,"source":"yfinance","delta_source":None} for _,r in p.iterrows()]


def candidate_puts(ticker, price, support, expiry):
    if not expiry: return [], "none"
    source="yfinance"
    if TRADIER_TOKEN:
        try:
            rows=tradier_put_rows(ticker,expiry); source="Tradier"
            if not rows: raise RuntimeError("empty Tradier chain")
        except Exception as exc:
            print(f"Tradier chain fallback for {ticker}: {exc}"); rows=yfinance_put_rows(ticker,expiry)
    else: rows=yfinance_put_rows(ticker,expiry)
    exp=datetime.strptime(expiry,"%Y-%m-%d").date(); today=datetime.now(ET).date(); dte=max((exp-today).days,1); ed=earnings_date(ticker); event=bool(ed and today<ed<=exp); clean=[]
    for r in rows:
        try: strike=float(r.get("strike")); bid=float(r.get("bid") or 0); ask=float(r.get("ask") or 0)
        except Exception: continue
        if strike>=price: continue
        mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
        if mid<=0: continue
        spread_pct=((ask-bid)/mid*100) if bid>0 and ask>=bid and mid>0 else None
        try: iv=float(r.get("iv")) if r.get("iv") is not None else None
        except Exception: iv=None
        try: delta=float(r.get("delta")) if r.get("delta") is not None else None
        except Exception: delta=None
        ds=r.get("delta_source")
        if delta is None and iv: delta=put_delta_bs(price,strike,iv,dte); ds="Black-Scholes estimate"
        be=strike-mid
        clean.append({"strike":strike,"bid":bid,"ask":ask,"spread_pct":spread_pct,"premium":mid,"breakeven":be,"iv_pct":iv*100 if iv else None,"delta":delta,"annualized_return_pct":mid/strike*365/dte*100,"distance_to_support_pct":(be-support)/support*100 if support else None,"dte":dte,"earnings_risk":event,"earnings_date":ed.isoformat() if ed else None,"source":source,"delta_source":ds})
    if not clean: return [],source
    chosen=[]; used=set()
    for label,target in [("Conservative",.12),("Preferred",.20),("Aggressive",.30)]:
        pool=[x for x in clean if x["strike"] not in used]; wd=[x for x in pool if x["delta"] is not None]
        if not pool: continue
        pick=min(wd,key=lambda x:abs(abs(x["delta"])-target)) if wd else min(pool,key=lambda x:abs(x["strike"]/price-.90)); used.add(pick["strike"]); pick=dict(pick); pick["profile"]=label; chosen.append(pick)
    return chosen,source


def component_scores(pref: dict | None, near_support: bool, stabilizing: bool, vertical: bool, event: bool) -> dict:
    if not pref: return {k:0 for k in ["iv","premium","delta","dte","breakeven_support","liquidity","support_proximity","stabilization","event"]}
    iv=pref.get("iv_pct"); iv_score=100 if iv is not None and iv>=60 else 80 if iv is not None and iv>=40 else 60 if iv is not None and iv>=30 else 40 if iv is not None else 35
    ann=pref.get("annualized_return_pct") or 0; premium_score=100 if ann>=35 else 75 if ann>=25 else 50 if ann>=15 else 25
    delta=abs(pref.get("delta")) if pref.get("delta") is not None else None; delta_score=max(0,100-abs(delta-.20)/.15*100) if delta is not None else 40
    dte=pref.get("dte") or 0; dte_score=100 if 14<=dte<=45 else 70 if 8<=dte<=60 else 35
    bes=pref.get("distance_to_support_pct"); be_score=100 if bes is not None and bes<=0 else 80 if bes is not None and bes<=2 else 60 if bes is not None and bes<=4 else 30
    spread=pref.get("spread_pct"); liquidity_score=100 if spread is not None and spread<=10 else 75 if spread is not None and spread<=20 else 50 if spread is not None and spread<=35 else 25
    return {"iv":round(iv_score),"premium":round(premium_score),"delta":round(delta_score),"dte":round(dte_score),"breakeven_support":round(be_score),"liquidity":round(liquidity_score),"support_proximity":100 if near_support else 45,"stabilization":100 if stabilizing and not vertical else 25 if vertical else 50,"event":0 if event else 100}


def option_setup_score(components: dict) -> int:
    weights={"iv":.10,"premium":.15,"delta":.10,"dte":.10,"breakeven_support":.15,"liquidity":.10,"support_proximity":.15,"stabilization":.10,"event":.05}
    return int(round(sum(float(components.get(k,0))*w for k,w in weights.items())))


def decision_from_setup(rating: str, pref: dict | None, option_score: int, stabilizing: bool, vertical: bool, event: bool) -> tuple[str,str]:
    if not short_put_eligible(rating):
        if rating=="UNRATED": return "NO TRADE","Underlying is not rated BUY/STRONG BUY by Stock V2. Short-put SELL is blocked."
        return "NO TRADE",f"Underlying Long-Term Rating is {rating}; short-put SELL requires BUY or STRONG BUY."
    if not pref: return "WAIT","Long-term ownership gate passes, but no suitable monthly put quote is available."
    if event: return "WAIT","Long-term ownership gate passes, but earnings occurs before expiration."
    if vertical: return "WAIT","Long-term ownership gate passes, but the stock is vertically extended; do not chase put premium after the move."
    if not stabilizing: return "WAIT","Long-term ownership gate passes, but price has not stabilized near support."
    if pref.get("spread_pct") is None or pref.get("spread_pct")>35: return "WAIT","Long-term ownership gate passes, but bid/ask liquidity is insufficient."
    if abs(pref.get("delta") or 0)<.08 or abs(pref.get("delta") or 0)>.32: return "WAIT","Long-term ownership gate passes, but preferred-put delta is outside the 0.08–0.32 risk band."
    if (pref.get("annualized_return_pct") or 0)<35: return "WAIT","Long-term ownership gate passes, but premium yield is below the 35% annualized execution threshold."
    if option_score<70: return "WAIT",f"Long-term ownership gate passes, but option execution score is only {option_score}/100."
    return "SELL","Long-term ownership gate and all option execution gates pass."


def reconciliation_text(ticker, rating, quality, decision, reason): return f"{ticker}: Long-Term {rating} / Entry {quality} → Put {decision}. {reason}"


def market_open_now(now):
    s=mcal.get_calendar("NYSE").schedule(start_date=now.date(),end_date=now.date()); return False if s.empty else s.iloc[0].market_open.tz_convert(ET)<=now<=s.iloc[0].market_close.tz_convert(ET)


def should_run(now):
    if os.getenv("GITHUB_EVENT_NAME","") in {"workflow_dispatch","push"}: return True
    if mcal.get_calendar("NYSE").schedule(start_date=now.date(),end_date=now.date()).empty: return False
    cron=os.getenv("GITHUB_EVENT_SCHEDULE","").strip(); off=now.utcoffset().total_seconds()/3600; valid={"0 14 * * 1-5","0 16 * * 1-5","0 18 * * 1-5"} if off==-4 else {"0 15 * * 1-5","0 17 * * 1-5","0 19 * * 1-5"}; return cron in valid


def main():
    now=datetime.now(ET)
    if not should_run(now): print("Not scheduled"); return
    stock_layer,stock_layer_source,stock_layer_as_of=load_stock_v2_layer(); raw={}; analyses=[]; sources=set()
    for sym in TICKERS:
        h=yf.Ticker(sym).history(period="3mo",interval="1d",auto_adjust=True,actions=False)
        if h.empty: continue
        h=h[["Open","High","Low","Close"]].dropna(); close=float(h.Close.iloc[-1]); prev=float(h.Close.iloc[-2]); recent=h.tail(45); sane=recent[(recent.High<=close*1.65)&(recent.Low>=close*.45)]; recent=sane if len(sane)>=20 else recent; low=float(recent.Low.min()); high=float(recent.High.max()); fb=fibs(low,high); ema20=float(h.Close.ewm(span=20,adjust=False).mean().iloc[-1]); r3=float(h.Close.iloc[-1]/h.Close.iloc[-4]-1)*100 if len(h)>=4 else 0
        raw[sym]={"price":close,"change_pct":(close/prev-1)*100,"return_3d_pct":r3,"low45":low,"high45":high,"fib":fb,"ema20":ema20,"key_support":support_below(close,fb)}
    for sym in PUT_NAMES:
        if sym not in raw: continue
        x=raw[sym]; stock=stock_layer.get(sym) or {}; rating=str(stock.get("long_term_rating") or "UNRATED"); lt_score=stock.get("long_term_score"); es=stock.get("entry_score"); quality=str(stock.get("entry_quality") or "UNRATED"); technical=stock.get("technical") or {}; canonical_support=(stock.get("support") or {}).get("key_support"); support=float(canonical_support) if canonical_support is not None else x["key_support"]; support_source="Stock V2 canonical" if canonical_support is not None else "local 45-day Fibonacci fallback"
        expiry,_=choose_expiry(sym); cands,source=candidate_puts(sym,x["price"],support,expiry); sources.add(source); pref=next((c for c in cands if c["profile"]=="Preferred"),cands[0] if cands else None); near=abs(x["price"]-support)/x["price"]<=.04 if support else False; vertical=x["change_pct"]>=6; falling=x["change_pct"]<=-5 or x["return_3d_pct"]<=-8; stabilizing=not vertical and not falling and (x["change_pct"]>=-3 or x["price"]>=x["ema20"]*.98); event=bool(pref and pref["earnings_risk"]); components=component_scores(pref,near,stabilizing,vertical,event); oscore=option_setup_score(components); decision,decision_reason=decision_from_setup(rating,pref,oscore,stabilizing,vertical,event)
        clean=[{**c,"strike":f(c["strike"]),"bid":f(c["bid"]),"ask":f(c["ask"]),"spread_pct":f(c["spread_pct"]),"premium":f(c["premium"]),"breakeven":f(c["breakeven"]),"iv_pct":f(c["iv_pct"]),"delta":f(c["delta"]),"annualized_return_pct":f(c["annualized_return_pct"]),"distance_to_support_pct":f(c["distance_to_support_pct"])} for c in cands]
        risk="3x daily leverage and volatility drag." if sym=="SOXL" else ("ETF thematic/concentration risk." if sym in {"QTUM","DRAM"} else ("Earnings falls before expiration." if event else "Gap risk and volatility expansion."))
        analyses.append({"ticker":sym,"price":f(x["price"]),"change_pct":f(x["change_pct"]),"decision":decision,"decision_reason":decision_reason,"score":oscore,"long_term_score":f(lt_score),"long_term_rating":rating,"entry_score":f(es),"entry_quality":quality,"underlying_eligible":short_put_eligible(rating),"stock_layer_as_of":stock.get("as_of") or stock_layer_as_of,"canonical_trend":technical.get("trend") or "—","canonical_rsi14":f(technical.get("rsi14")),"canonical_sentiment":(stock.get("diagnostic_sentiment") or {}).get("label") or "—","contract":f"{expiry} ${pref['strike']:.0f}P" if pref else None,"premium":f(pref["premium"]) if pref else None,"breakeven":f(pref["breakeven"]) if pref else None,"key_support":f(support),"support_source":support_source,"fib":{k:f(v) for k,v in x["fib"].items()},"candidates":clean,"option_source":source,"expiration_type":"Standard monthly (third Friday)","option_components":components,"stabilizing":stabilizing,"return_3d_pct":f(x["return_3d_pct"]),"reconciliation":reconciliation_text(sym,rating,quality,decision,decision_reason),"trigger":f"Nearest support ${support:.2f}; require stable/reclaiming tape before entry." if support else "Support unavailable; WAIT.","risk":risk,"note":f"Adjusted 45-day swing ${x['low45']:.2f} → ${x['high45']:.2f}; EMA20 ${x['ema20']:.2f}"})
    decision_order={"SELL":0,"WAIT":1,"NO TRADE":2}; ranking=sorted(analyses,key=lambda z:(decision_order.get(z["decision"],3),-z["score"])); smh=raw.get("SMH",{}); entries=[]
    if smh:
        price=smh["price"]; levels=sorted([v for v in smh["fib"].values() if v<=price],reverse=True); entries=[{"zone":f"${price*.995:.0f}–${price*1.005:.0f}","allocation":20,"label":"starter only"}]
        for i,(label,alloc) in enumerate([("first support",30),("strong support",30),("major accumulation",20)]):
            if i<len(levels): entries.append({"zone":f"${levels[i]-3:.0f}–${levels[i]+3:.0f}","allocation":alloc,"label":label})
    source_label="Tradier real-time" if sources=={"Tradier"} else ("yfinance fallback" if sources=={"yfinance"} else "Tradier + yfinance fallback")
    payload={"updated_et":now.strftime("%Y-%m-%d %I:%M %p ET"),"market_state":"OPEN" if market_open_now(now) else "CLOSED","option_data_source":source_label,"stock_v2_source":stock_layer_source,"stock_v2_as_of":stock_layer_as_of,"policy":"Short-put SELL requires Stock V2 Long-Term BUY or STRONG BUY. Eligible names still must pass option execution gates.","expiration_policy":"Standard monthly options only (third Friday); no weeklies","tickers":[{"ticker":k,"price":f(v["price"]),"change_pct":f(v["change_pct"])} for k,v in raw.items()],"ranking":ranking,"analysis":analyses,"reconciliation":[{"ticker":x["ticker"],"text":x["reconciliation"],"decision":x["decision"]} for x in analyses],"smh":{"price":f(smh.get("price")),"entries":entries}}
    os.makedirs("data",exist_ok=True)
    with open("data/dashboard.json","w",encoding="utf-8") as fh: json.dump(payload,fh,indent=2)
    print(json.dumps(payload,indent=2))


if __name__=="__main__": main()
