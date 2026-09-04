#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

SRC=Path('data/williams_r/soxl_daily_v1_source_snapshot.csv')
OUT=Path('quantconnect/soxl_tcar_long_call_midpoint')
PERIODS={
 'IS':('2016-09-06','2022-09-02'),
 'VALIDATION':('2022-09-06','2024-09-03'),
 'OOS':('2024-09-04','2026-09-04')}

def prep():
 x=pd.read_csv(SRC,parse_dates=['date']).set_index('date').sort_index()
 x=x.rename(columns={'soxl_open':'open','soxl_high':'high','soxl_low':'low','soxl_close':'close'})
 hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
 tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md)
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
 up=x.high.diff(); dn=-x.low.diff(); pdm=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=x.index); mdm=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=x.index)
 atr=tr.rolling(20).mean(); pdi=100*pdm.rolling(20).mean()/atr; mdi=100*mdm.rolling(20).mean()/atr; dx=100*(pdi-mdi).abs()/(pdi+mdi); x['adx20']=dx.rolling(20).mean()
 x['prev_high']=x.high.shift(); x['qqq_ema200']=x.qqq_close.ewm(span=200,adjust=False,min_periods=200).mean(); return x

def period_rows(seg):
 pending=None; pos=False; rows=[]; tid=0
 for dt,r in seg.iterrows():
  if pending:
   if pending['action']=='BUY': tid+=1; pos=True; pending['trade_id']=tid
   else: pos=False; pending['trade_id']=tid
   pending['execution_date']=dt.date().isoformat(); rows.append(pending); pending=None
  if not all(pd.notna(v) for v in [r.wr,r.cci,r.adx20,r.qqq_ema200]): continue
  if pos and ((r.close>r.prev_high) or (r.wr>-30)):
   reasons=[]
   if r.close>r.prev_high: reasons.append('close>prev_high')
   if r.wr>-30: reasons.append('wr>-30')
   pending={'action':'SELL','signal_date':dt.date().isoformat(),'exit_reason':'+'.join(reasons)}
  elif (not pos) and (r.wr<-90) and (r.cci<-80) and (r.adx20>=15):
   pending={'action':'BUY','signal_date':dt.date().isoformat(),'regime_weight':1.0 if r.qqq_close>r.qqq_ema200 else 0.5,'wr2':float(r.wr),'cci5':float(r.cci),'adx20':float(r.adx20)}
 return rows

def main():
 x=prep(); out={}
 for name,(a,b) in PERIODS.items(): out[name]=period_rows(x.loc[a:b])
 OUT.mkdir(parents=True,exist_ok=True)
 (OUT/'signal_manifest.py').write_text('SIGNAL_MANIFEST = '+repr(out)+'\n',encoding='utf-8')
 (OUT/'signal_manifest.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
 print({k:len(v) for k,v in out.items()})
if __name__=='__main__': main()
