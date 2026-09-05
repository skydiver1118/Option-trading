import os, json, math, time
from pathlib import Path
import requests, numpy as np, pandas as pd

SYMBOLS=['SMH','SPMO','VGT','SPY','TQQQ']
START='2000-01-01'; END='2026-09-04'; COST=0.0005
OUT=Path('artifacts/tradier_cpa_v2'); OUT.mkdir(parents=True,exist_ok=True)
TOKEN=os.getenv('TRADIER_TOKEN')
if not TOKEN: raise SystemExit('Missing TRADIER_TOKEN')
HEADERS={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}
BASE='https://api.tradier.com/v1/markets/history'

def fetch(sym):
 r=requests.get(BASE,headers=HEADERS,params={'symbol':sym,'interval':'daily','start':START,'end':END},timeout=60); r.raise_for_status()
 d=(((r.json() or {}).get('history') or {}).get('day'))
 if not d: raise RuntimeError(f'No history {sym}')
 if isinstance(d,dict): d=[d]
 x=pd.DataFrame(d); x['date']=pd.to_datetime(x['date'])
 for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.dropna().sort_values('date').set_index('date')[['open','high','low','close','volume']]

def ind(x):
 x=x.copy(); pc=x.close.shift(1)
 tr=pd.concat([(x.high-x.low),(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
 x['atr14']=tr.rolling(14).mean(); x['atrpct']=x.atr14/x.close; x['atrpct10']=x.atrpct.rolling(10).mean()
 x['ema10']=x.close.ewm(span=10,adjust=False).mean(); x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['sma50']=x.close.rolling(50).mean(); x['sma200']=x.close.rolling(200).mean()
 x['range']=x.high-x.low; x['range10']=x.range.rolling(10).mean(); x['vol20']=x.volume.rolling(20).mean(); x['high10p']=x.high.shift(1).rolling(10).max(); x['high20p']=x.high.shift(1).rolling(20).max(); x['low10p']=x.low.shift(1).rolling(10).min()
 # weekly context mapped back to daily without look-ahead: completed Friday week values are forward-filled from following trading day
 w=x[['close']].resample('W-FRI').last(); w['wema10']=w.close.ewm(span=10,adjust=False).mean(); w['wema20']=w.close.ewm(span=20,adjust=False).mean(); w['wext']=(w.close-w.wema10)/w.close
 wm=w[['wema10','wema20','wext']].shift(1)
 for c in wm: x[c]=wm[c].reindex(x.index,method='ffill')
 x['down_ext']=(x.ema10-x.close)/x.atr14
 x['up_ext']=(x.close-x.ema10)/x.atr14
 return x

def detect(x):
 events=[]; states=[]; state='DOWNTREND'; rev_i=None; wp_i=None; cross_done=False; base_count=0; ext_count=0; base_start=None
 n=len(x)
 for i in range(n):
  r=x.iloc[i]; ev=[]
  if i<200 or pd.isna(r.atr14): states.append(state); events.append(''); continue
  # 1 Reversal Extension: meaningful downside stretch + established weakness + reversal character
  if state in ('DOWNTREND','WEDGE_DROP'):
   weak=(r.close<r.ema20) and (x.ema10.iloc[i]<x.ema20.iloc[i])
   reversal=(r.down_ext>=1.5) and weak and ((r.close-r.low)/(max(r.high-r.low,1e-9))>=0.55)
   if reversal:
    state='REVERSAL_EXTENSION'; rev_i=i; ev.append('REVERSAL_EXTENSION')
  elif state=='REVERSAL_EXTENSION':
   age=i-rev_i
   if age>30 or r.close < x.low.iloc[rev_i]-0.5*r.atr14:
    state='DOWNTREND'
   else:
    # volatility/range contraction after rebound + EMA cluster tightening + pivot break/reclaim
    recent=x.iloc[max(rev_i,i-8):i+1]
    contraction=(r.range < r.range10) and (r.atrpct <= r.atrpct10*1.05)
    tight=abs(r.ema10-r.ema20) <= 0.55*r.atr14
    reclaim=(r.close>r.ema10) and (r.close>r.ema20)
    pivot = r.close > x.high.shift(1).rolling(5).max().iloc[i]
    if age>=3 and contraction and tight and reclaim and pivot:
     state='WEDGE_POP'; wp_i=i; cross_done=False; base_count=0; ext_count=0; base_start=None; ev.append('WEDGE_POP')
  elif state in ('WEDGE_POP','EARLY_UPTREND','ESTABLISHED_UPTREND','MATURE_UPTREND','EXHAUSTION'):
   # Wedge Drop only in a valid established cycle, not any EMA loss
   mature=(base_count>=1 or ext_count>=1 or (wp_i is not None and i-wp_i>=12))
   lose=(r.close<r.ema10 and r.close<r.ema20)
   failure=(r.close < x.low.shift(1).rolling(3).min().iloc[i])
   if mature and lose and failure:
    state='WEDGE_DROP'; ev.append('WEDGE_DROP'); rev_i=None; wp_i=None; cross_done=False; base_count=0; ext_count=0; base_start=None
   else:
    # first EMA Crossback only after Wedge Pop
    if not cross_done and wp_i is not None and i-wp_i>=2:
     zone_hi=max(r.ema10,r.ema20); zone_lo=min(r.ema10,r.ema20)
     touched=(r.low<=zone_hi) and (r.close>=zone_lo)
     hold=(r.close>r.ema20) and (r.close>r.open or r.close>x.close.iloc[i-1])
     if touched and hold:
      cross_done=True; state='ESTABLISHED_UPTREND'; ev.append('EMA_CROSSBACK')
    # Base n Break: 4-15 bar consolidation, mostly above EMA20, contraction, breakout
    if cross_done:
     for L in range(4,16):
      if i-L<max(wp_i or 0,0): continue
      b=x.iloc[i-L:i]
      above=(b.close>b.ema20).mean()>=0.7
      contract=(b.range.iloc[-3:].mean() <= b.range.mean()*0.95)
      breakout=r.close>b.high.max() and r.close>r.ema10 and r.close>r.ema20
      if above and contract and breakout:
       if base_start is None or i-base_start>L:
        base_count+=1; base_start=i; state='MATURE_UPTREND' if base_count>=2 else 'ESTABLISHED_UPTREND'; ev.append(f'BASE_N_BREAK_{base_count}')
       break
    # Exhaustion extension: late cycle + ATR extension; weekly extension adds confirmation
    if (base_count>=1 or (wp_i is not None and i-wp_i>=20)) and r.up_ext>=1.8:
     weekly_confirm=(not pd.isna(r.wext) and r.wext>0.04 and r.wema10>r.wema20)
     if weekly_confirm or r.up_ext>=2.3:
      if not events or (i>0 and 'EXHAUSTION' not in events[-1]):
       ext_count+=1; state='EXHAUSTION' if ext_count>=2 else state; ev.append(f'EXHAUSTION_{ext_count}')
    if state=='WEDGE_POP' and i-wp_i>=1: state='EARLY_UPTREND'
  states.append(state); events.append('|'.join(ev))
 out=x.copy(); out['cpa_state']=states; out['cpa_event']=events
 return out

def backtest(x,trim=False):
 n=len(x); exposure=np.zeros(n); ret=np.zeros(n); trades=[]; inpos=False; ep=None; ed=None; cur_exp=0.0
 for i in range(1,n):
  e=x.cpa_event.iloc[i-1]
  # state transitions executed next open
  if 'WEDGE_POP' in e and not inpos:
   inpos=True; cur_exp=1.0; ep=float(x.open.iloc[i])*(1+COST); ed=x.index[i]
  if inpos and trim and ('EXHAUSTION_2' in e or 'EXHAUSTION_3' in e): cur_exp=0.5
  if inpos and 'WEDGE_DROP' in e:
   xp=float(x.open.iloc[i])*(1-COST); trades.append({'entry_date':ed,'exit_date':x.index[i],'entry_price':ep,'exit_price':xp,'return':xp/ep-1}); inpos=False; cur_exp=0; ep=ed=None
  if inpos:
   dayret=(float(x.close.iloc[i])/float(x.open.iloc[i])-1) if (ed==x.index[i]) else (float(x.close.iloc[i])/float(x.close.iloc[i-1])-1)
   ret[i]=cur_exp*dayret
  exposure[i]=cur_exp
 if inpos:
  xp=float(x.close.iloc[-1])*(1-COST); trades.append({'entry_date':ed,'exit_date':x.index[-1],'entry_price':ep,'exit_price':xp,'return':xp/ep-1})
 eq=pd.Series((1+ret).cumprod(),index=x.index); return eq,pd.DataFrame(trades),pd.Series(exposure,index=x.index)

def metr(eq,tr,pos):
 dr=eq.pct_change().fillna(0); yrs=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25); total=eq.iloc[-1]-1; cagr=eq.iloc[-1]**(1/yrs)-1; dd=eq/eq.cummax()-1; mdd=dd.min(); sd=dr.std(); sh=dr.mean()/sd*math.sqrt(252) if sd>0 else np.nan; dn=dr[dr<0].std(); so=dr.mean()/dn*math.sqrt(252) if dn and dn>0 else np.nan; ca=cagr/abs(mdd) if mdd<0 else np.nan
 if len(tr):
  w=tr[tr['return']>0]; l=tr[tr['return']<=0]; wr=len(w)/len(tr); pf=w['return'].sum()/abs(l['return'].sum()) if len(l) and l['return'].sum()!=0 else np.nan
 else: wr=pf=np.nan
 return {'CAGR':cagr,'TotalReturn':total,'MaxDD':mdd,'Sharpe':sh,'Sortino':so,'Calmar':ca,'Trades':len(tr),'WinRate':wr,'ProfitFactor':pf,'Exposure':pos.mean()}

rows=[]; meta={}
for s in SYMBOLS:
 raw=fetch(s); x=detect(ind(raw).dropna().copy()); x.to_csv(OUT/f'{s}_states.csv')
 meta[s]={'raw_start':str(raw.index.min().date()),'raw_end':str(raw.index.max().date()),'test_start':str(x.index.min().date()),'rows':len(x),'events':x.cpa_event[x.cpa_event!=''].value_counts().to_dict()}
 for name,trim in [('CPA_v2_Full',False),('CPA_v2_Trim',True)]:
  eq,tr,pos=backtest(x,trim); m=metr(eq,tr,pos); m.update({'Symbol':s,'Strategy':name}); rows.append(m); tr.to_csv(OUT/f'{s}_{name}_trades.csv',index=False)
 bh=(x.close/x.close.iloc[0]); m=metr(bh,pd.DataFrame(),pd.Series(1.0,index=x.index)); m.update({'Symbol':s,'Strategy':'BuyHold'}); rows.append(m)
 time.sleep(.15)
summary=pd.DataFrame(rows); summary.to_csv(OUT/'summary.csv',index=False); (OUT/'meta.json').write_text(json.dumps(meta,indent=2)); print(summary.to_string(index=False)); print(json.dumps(meta,indent=2))
