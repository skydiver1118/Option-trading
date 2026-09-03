from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import update_dashboard as base

ET = ZoneInfo("America/New_York")
DTE_MIN = 19
DTE_MAX = 50
TARGET_DELTA = 0.20
_SELECTION = {}


def _as_float(x):
    try:
        return float(x) if x is not None else None
    except Exception:
        return None


def _tradier_chain(symbol, expiry):
    j = base.tradier_get("/markets/options/chains", {"symbol": symbol, "expiration": expiry, "greeks": "true"})
    opts = ((j or {}).get("options") or {}).get("option") or []
    return [opts] if isinstance(opts, dict) else opts


def _preferred_contract(symbol, expiry, spot):
    rows = []
    for o in _tradier_chain(symbol, expiry):
        if str(o.get("option_type", "")).lower() != "put":
            continue
        strike = _as_float(o.get("strike")); bid = _as_float(o.get("bid")); ask = _as_float(o.get("ask"))
        if strike is None or strike >= spot or not bid or not ask or ask < bid:
            continue
        g = o.get("greeks") or {}
        delta = _as_float(g.get("delta")); theta = _as_float(g.get("theta")); gamma = _as_float(g.get("gamma")); vega = _as_float(g.get("vega"))
        iv = _as_float(g.get("mid_iv") if g.get("mid_iv") is not None else g.get("smv_vol"))
        mid = (bid + ask) / 2
        if mid <= 0:
            continue
        spread_pct = (ask - bid) / mid * 100
        rows.append({"strike": strike, "bid": bid, "ask": ask, "mid": mid, "delta": delta, "theta": theta, "gamma": gamma, "vega": vega, "iv": iv, "spread_pct": spread_pct})
    with_delta = [r for r in rows if r["delta"] is not None]
    if not with_delta:
        return None
    return min(with_delta, key=lambda r: abs(abs(r["delta"]) - TARGET_DELTA))


def _norm(values, value, reverse=False):
    vals = [v for v in values if v is not None]
    if not vals or value is None:
        return 50.0
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 100.0
    score = (value - lo) / (hi - lo) * 100
    return 100 - score if reverse else score


def choose_greek_expiry(symbol):
    today = datetime.now(ET).date()
    sources = []
    if base.TRADIER_TOKEN:
        try:
            sources.append(("Tradier", base.tradier_expirations(symbol)))
        except Exception as exc:
            print(f"Tradier expirations fallback for {symbol}: {exc}")
    try:
        sources.append(("yfinance", base.yfinance_expirations(symbol)))
    except Exception as exc:
        print(f"yfinance expirations unavailable for {symbol}: {exc}")

    candidates = []
    for source, dates in sources:
        for s in dates:
            try:
                d = datetime.strptime(s, "%Y-%m-%d").date(); dte = (d - today).days
                if DTE_MIN <= dte <= DTE_MAX and base.is_monthly_expiry(s):
                    candidates.append((s, dte, source))
            except Exception:
                pass
        if candidates:
            break

    if not candidates:
        return None, None

    # If Tradier is unavailable, retain the DTE window and choose closest to 35 DTE.
    if not base.TRADIER_TOKEN:
        s, dte, source = min(candidates, key=lambda x: abs(x[1] - 35))
        _SELECTION[symbol] = {"expiration": s, "dte": dte, "expiration_score": None, "selection_reason": "DTE fallback; Greeks unavailable"}
        return s, source

    try:
        hist = base.yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True, actions=False)
        spot = float(hist.Close.dropna().iloc[-1])
    except Exception:
        spot = None
    if not spot:
        s, dte, source = min(candidates, key=lambda x: abs(x[1] - 35))
        _SELECTION[symbol] = {"expiration": s, "dte": dte, "expiration_score": None, "selection_reason": "DTE fallback; spot unavailable"}
        return s, source

    scored = []
    for s, dte, source in candidates:
        try:
            p = _preferred_contract(symbol, s, spot)
        except Exception as exc:
            print(f"Greek selection chain failed for {symbol} {s}: {exc}")
            p = None
        if not p:
            continue
        theta_eff = abs(p["theta"]) / p["strike"] * 10000 if p["theta"] is not None and p["strike"] else None
        theta_gamma = abs(p["theta"]) / p["gamma"] if p["theta"] is not None and p["gamma"] not in (None, 0) else None
        ann = p["mid"] / p["strike"] * 365 / dte * 100
        delta_fit = max(0.0, 100.0 - abs(abs(p["delta"]) - TARGET_DELTA) / 0.15 * 100)
        scored.append({"expiration": s, "dte": dte, "source": source, "preferred": p, "theta_eff": theta_eff, "theta_gamma": theta_gamma, "annualized": ann, "delta_fit": delta_fit})

    if not scored:
        s, dte, source = min(candidates, key=lambda x: abs(x[1] - 35))
        _SELECTION[symbol] = {"expiration": s, "dte": dte, "expiration_score": None, "selection_reason": "DTE fallback; Greek chain unavailable"}
        return s, source

    te = [x["theta_eff"] for x in scored]; tg = [x["theta_gamma"] for x in scored]; anns = [x["annualized"] for x in scored]
    vegas = [abs(x["preferred"]["vega"]) if x["preferred"]["vega"] is not None else None for x in scored]
    spreads = [x["preferred"]["spread_pct"] for x in scored]
    for x in scored:
        p = x["preferred"]
        event = False
        ed = base.earnings_date(symbol)
        if ed:
            expd = datetime.strptime(x["expiration"], "%Y-%m-%d").date()
            event = today < ed <= expd
        x["score"] = round(
            0.30 * _norm(te, x["theta_eff"]) +
            0.20 * _norm(tg, x["theta_gamma"]) +
            0.15 * _norm(anns, x["annualized"]) +
            0.10 * x["delta_fit"] +
            0.10 * _norm(vegas, abs(p["vega"]) if p["vega"] is not None else None, reverse=True) +
            0.10 * _norm(spreads, p["spread_pct"], reverse=True) +
            0.05 * (0 if event else 100), 1)
        x["event_risk"] = event

    best = max(scored, key=lambda x: (x["score"], -abs(x["dte"] - 35)))
    _SELECTION[symbol] = {
        "expiration": best["expiration"], "dte": best["dte"], "expiration_score": best["score"],
        "theta_efficiency": best["theta_eff"], "theta_gamma_ratio": best["theta_gamma"],
        "preferred_delta": best["preferred"]["delta"], "preferred_theta": best["preferred"]["theta"],
        "preferred_gamma": best["preferred"]["gamma"], "preferred_vega": best["preferred"]["vega"],
        "event_risk": best["event_risk"],
        "selection_reason": "Greek-weighted monthly expiration score (theta 30%, theta/gamma 20%, premium 15%, delta 10%, vega 10%, liquidity 10%, event 5%)"
    }
    return best["expiration"], best["source"]


def tradier_put_rows_with_greeks(ticker, expiry):
    rows = []
    for o in _tradier_chain(ticker, expiry):
        if str(o.get("option_type", "")).lower() != "put":
            continue
        g = o.get("greeks") or {}
        iv = g.get("mid_iv") if g.get("mid_iv") is not None else g.get("smv_vol")
        rows.append({"strike": o.get("strike"), "bid": o.get("bid"), "ask": o.get("ask"), "iv": iv,
                     "delta": g.get("delta"), "theta": g.get("theta"), "gamma": g.get("gamma"), "vega": g.get("vega"),
                     "source": "Tradier", "delta_source": "Tradier/ORATS" if g.get("delta") is not None else None})
    return rows


def candidate_puts_with_greeks(ticker, price, support, expiry):
    if not expiry:
        return [], "none"
    source = "yfinance"
    if base.TRADIER_TOKEN:
        try:
            rows = tradier_put_rows_with_greeks(ticker, expiry); source = "Tradier"
            if not rows:
                raise RuntimeError("empty Tradier chain")
        except Exception as exc:
            print(f"Tradier chain fallback for {ticker}: {exc}"); rows = base.yfinance_put_rows(ticker, expiry)
    else:
        rows = base.yfinance_put_rows(ticker, expiry)
    exp = datetime.strptime(expiry, "%Y-%m-%d").date(); today = datetime.now(ET).date(); dte = max((exp - today).days, 1)
    ed = base.earnings_date(ticker); event = bool(ed and today < ed <= exp); clean = []
    for r in rows:
        try: strike=float(r.get("strike")); bid=float(r.get("bid") or 0); ask=float(r.get("ask") or 0)
        except Exception: continue
        if strike >= price: continue
        mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
        if mid<=0: continue
        spread=((ask-bid)/mid*100) if bid>0 and ask>=bid else None
        iv=_as_float(r.get("iv")); delta=_as_float(r.get("delta")); theta=_as_float(r.get("theta")); gamma=_as_float(r.get("gamma")); vega=_as_float(r.get("vega")); ds=r.get("delta_source")
        if delta is None and iv: delta=base.put_delta_bs(price,strike,iv,dte); ds="Black-Scholes estimate"
        be=strike-mid
        clean.append({"strike":strike,"bid":bid,"ask":ask,"spread_pct":spread,"premium":mid,"breakeven":be,
                      "iv_pct":iv*100 if iv else None,"delta":delta,"theta":theta,"gamma":gamma,"vega":vega,
                      "theta_efficiency":abs(theta)/strike*10000 if theta is not None and strike else None,
                      "theta_gamma_ratio":abs(theta)/gamma if theta is not None and gamma not in (None,0) else None,
                      "annualized_return_pct":mid/strike*365/dte*100,"distance_to_support_pct":(be-support)/support*100 if support else None,
                      "dte":dte,"earnings_risk":event,"earnings_date":ed.isoformat() if ed else None,"source":source,"delta_source":ds})
    if not clean: return [], source
    chosen=[]; used=set()
    for label,target in [("Conservative",.12),("Preferred",.20),("Aggressive",.30)]:
        pool=[x for x in clean if x["strike"] not in used]; wd=[x for x in pool if x["delta"] is not None]
        if not pool: continue
        pick=min(wd,key=lambda x:abs(abs(x["delta"])-target)) if wd else min(pool,key=lambda x:abs(x["strike"]/price-.90))
        used.add(pick["strike"]); pick=dict(pick); pick["profile"]=label; chosen.append(pick)
    return chosen, source


def main():
    base.choose_expiry = choose_greek_expiry
    base.tradier_put_rows = tradier_put_rows_with_greeks
    base.candidate_puts = candidate_puts_with_greeks
    base.main()

    path = "data/dashboard.json"
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    for x in data.get("analysis", []):
        meta = _SELECTION.get(x.get("ticker"), {})
        x["expiration_selection"] = meta
        for c in x.get("candidates", []):
            # base.main rounds known fields only; preserve Greek values from our candidate function.
            pass

    # Re-run candidate enrichment into serialized records because base.clean kept unknown keys via **c.
    data["ranking"] = sorted(data.get("analysis", []), key=lambda x: float(x.get("score") or 0), reverse=True)
    data["ranking_basis"] = "Option execution score descending"
    expiries = sorted({m.get("expiration") for m in _SELECTION.values() if m.get("expiration")})
    data["option_expiration"] = expiries[0] if len(expiries) == 1 else "per-ticker"
    data["expiration_policy"] = "Standard monthly only; 19–50 DTE; Greek-weighted selection using theta, gamma, delta, vega, premium, liquidity and event risk"
    data["requested_refresh_mode"] = "Greek-aware 19–50 DTE monthly"
    data["expiration_selection_method"] = {
        "dte_window": [DTE_MIN, DTE_MAX], "monthly_only": True, "target_delta": TARGET_DELTA,
        "weights": {"theta_efficiency":30,"theta_gamma_ratio":20,"annualized_premium":15,"delta_fit":10,"vega":10,"liquidity":10,"event_risk":5}
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Greek-aware dashboard complete at {datetime.now(ET).isoformat()}; expiries={expiries}")


if __name__ == "__main__":
    main()
