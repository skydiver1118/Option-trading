"""IS-era chart review, using a fixed reference, without computing performance."""
from dataclasses import asdict
from pathlib import Path
import hashlib,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import pandas as pd
from cpa_v2_engine import Parameters,detect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/cpa_v2_20260904'


def main():
    p=Parameters()
    raw=pd.read_csv(ROOT/'data/cpa_v2_20260904_source/TSLA.csv',parse_dates=['date']).set_index('date')
    # This separate example file ends in 2021. Never consult study held-out files.
    assert raw.index.max()<=pd.Timestamp('2021-12-31')
    x=detect(raw,p).loc['2020-01-01':'2020-12-31']
    dest=OUT/'morphology';dest.mkdir(exist_ok=True,parents=True)
    x.to_csv(dest/'TSLA_2020_reference_states.csv')
    labels={'REVERSAL_EXTENSION':'RE','WEDGE_POP':'WP','EMA_CROSSBACK':'CB',
            'BASE_N_BREAK_1':'B1','BASE_N_BREAK_2':'B2','EXHAUSTION_EXTENSION_1':'E1',
            'EXHAUSTION_EXTENSION_2':'E2','WEDGE_DROP':'WD','CYCLE_FAILURE':'FAIL'}
    for tag,start,end in [('full','2020-01-01','2020-12-31'),('spring','2020-03-01','2020-07-10')]:
        b=x.loc[start:end];fig,(ax,vol)=plt.subplots(2,1,figsize=(14,7),sharex=True,
                    gridspec_kw={'height_ratios':[4,1]},layout='constrained')
        for d,r in b.iterrows():
            xx=mdates.date2num(d);color='#087e8b' if r.close>=r.open else '#c7463b'
            ax.vlines(xx,r.low,r.high,color=color,lw=.7)
            ax.add_patch(Rectangle((xx-.35,min(r.open,r.close)),.7,max(abs(r.close-r.open),.01),color=color,alpha=.9))
            vol.bar(d,r.volume/1e6,color=color,width=.8,alpha=.6)
        ax.plot(b.index,b.ema10,color='#ed9b40',lw=1.25,label='10 EMA')
        ax.plot(b.index,b.ema20,color='#4d65b4',lw=1.25,label='20 EMA')
        for j,(d,r) in enumerate(b[b.cpa_event!=''].iterrows()):
            offset={'BASE_N_BREAK_2':(-25,35),'EXHAUSTION_EXTENSION_2':(15,55)}.get(r.cpa_event,(0,16+(j%2)*16))
            ax.annotate(labels.get(r.cpa_event,r.cpa_event),(d,r.high),xytext=offset,
                        textcoords='offset points',ha='center',fontsize=9,fontweight='bold',
                        arrowprops=dict(arrowstyle='-',color='#555555',lw=.7))
        ax.set_title('TSLA 2020 | CPA_v2 fixed reference detection — NOT OPTIMIZED',loc='left',fontweight='bold',fontsize=14)
        ax.set_ylabel('Tradier price, log scale');ax.set_yscale('log');ax.legend(loc='upper left')
        ax.grid(alpha=.15);vol.set_ylabel('Volume\n(millions)');vol.grid(alpha=.1)
        vol.xaxis.set_major_locator(mdates.MonthLocator());vol.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        fig.supxlabel('RE reversal • WP wedge pop • CB first crossback • B base/break • E extension • WD wedge drop\n'
                      'Source example: Oliver Kell / TraderLion, YouTube VNvb0_zkYqw. Exact author-date visual match remains unverified.',fontsize=9)
        fig.savefig(dest/f'TSLA_2020_{tag}.png',dpi=160);plt.close(fig)
    events=[dict(date=str(d.date()),event=r.cpa_event,cycle=int(r.cycle_id)) for d,r in x[x.cpa_event!=''].iterrows()]
    review=dict(accepted=False,status='PROVISIONAL_SOURCE_MATCH_NOT_ESTABLISHED',
                engine_sha256=hashlib.sha256((ROOT/'scripts/cpa_v2_engine.py').read_bytes()).hexdigest(),
                parameters=asdict(p),performance_used=False,reference_optimized=False,
                example='TSLA 2020',source='https://www.youtube.com/watch?v=VNvb0_zkYqw',events=events,
                matches=['Provisional March downside reversal, subsequent EMA reclaim, first supported pullback, repeated bases and extensions are present in causal order.'],
                limitations=['Primary video captions were available but chart frames did not render reliably; exact annotated source event dates remain unverified.',
                             'No initial exhaustion label is inferred for the early-2020 top without a previously detected complete cycle.',
                             'Strict reversal-first state initialization may miss rounded-bottom and reentry examples from Kell.',
                             'Detected April Wedge Pop may be later than the discretionary inside-bar/gap entry; timing cannot be signed off from captions.',
                             'The August Wedge Drop precedes a later rally; its agreement with the source timing is unverified.',
                             'This reference is not a parameter set selected from IS performance.'])
    (OUT/'visual_review.json').write_text(json.dumps(review,indent=2))
    print(json.dumps(dict(status=review['status'],detected_events=events,performance_computed=False),indent=2))


if __name__=='__main__':main()
