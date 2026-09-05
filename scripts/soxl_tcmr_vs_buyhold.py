#!/usr/bin/env python3
exec(open('scripts/soxl_tcmr_integrated_backtest.py').read().replace("if __name__=='__main__': main()",''))

def hold_metrics(s):
 r=s.close.pct_change().fillna(0); total=s.close.iloc[-1]/s.close.iloc[0]-1; yrs=max((s.index[-1]-s.index[0]).days/365.25,1/365.25); cagr=(1+total)**(1/yrs)-1; curve=s.close/s.close.iloc[0]; mdd=(curve/curve.cummax()-1).min(); sd=r.std(ddof=0); sharpe=r.mean()/sd*np.sqrt(252) if sd else np.nan; return dict(hold_total_return=total,hold_cagr=cagr,hold_sharpe=sharpe,hold_max_drawdown=mdd,hold_calmar=cagr/abs(mdd) if mdd<0 else np.nan,hold_ending_100k=100000*(1+total))
def main2():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1); segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}; rows=[]
 for p,s in segs.items():
  z=bt(s); h=hold_metrics(s); z.update(h); z.update(period=p,start=s.index.min().date(),end=s.index.max().date()); rows.append(z)
 pd.DataFrame(rows).to_csv(OUT/'soxl_tcar_vs_buyhold.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main2()
