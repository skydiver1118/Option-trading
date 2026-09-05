#!/usr/bin/env python3
# trigger 2026-09-04
exec(open('scripts/soxl_daily_tcar_no_qqq_10y.py').read().replace("if __name__=='__main__': main()",''))

def hold_stats(seg):
    r=seg.close.pct_change().fillna(0)
    total=seg.close.iloc[-1]/seg.close.iloc[0]-1
    yrs=max((seg.index[-1]-seg.index[0]).days/365.25,1/365.25)
    cagr=(1+total)**(1/yrs)-1
    curve=seg.close/seg.close.iloc[0]
    mdd=(curve/curve.cummax()-1).min()
    sd=r.std(ddof=0)
    sharpe=r.mean()/sd*np.sqrt(252) if sd else np.nan
    calmar=cagr/abs(mdd) if mdd<0 else np.nan
    return dict(total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,ending_equity=100000*(1+total),exposure=1.0)

def main2():
    x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); seg=x.loc[start:end]
    strat,_,_=run(seg); hold=hold_stats(seg)
    rows=[{'portfolio':'TCAR_no_QQQ',**strat},{'portfolio':'SOXL_buy_hold','start':seg.index[0].date(),'end':seg.index[-1].date(),'trades':np.nan,'win_rate':np.nan,'profit_factor':np.nan,'avg_trade':np.nan,'median_trade':np.nan,'avg_holding_days':np.nan,**hold}]
    pd.DataFrame(rows).to_csv(OUT/'soxl_daily_tcar_no_qqq_vs_hold_10y.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main2()
