"""Optional bounded periodic trigger; uses the same audited agent and dedup state."""
import argparse,asyncio,json
from datetime import datetime
from run import ROOT,run
from app.tools import TZ

async def scheduled(request,config,rounds,interval_minutes,live=False):
    if type(rounds) is not int or not 1<=rounds<=24:raise ValueError('rounds must be 1..24')
    if not 1<=interval_minutes<=1440:raise ValueError('interval_minutes must be 1..1440')
    results=[]
    for index in range(rounds):
        name=datetime.now(TZ).strftime('%Y%m%d-%H%M%S')+'-scheduled-'+str(index+1)
        results.append(await run(request,config,ROOT/'evidence'/name,live))
        if index+1<rounds:await asyncio.sleep(interval_minutes*60)
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--rounds',type=int,default=1)
    parser.add_argument('--interval-minutes',type=int,default=60);parser.add_argument('--live',action='store_true')
    parser.add_argument('--config',default=str(ROOT/'config.local.json'));args=parser.parse_args()
    asyncio.run(scheduled(args.request,json.loads(__import__('pathlib').Path(args.config).read_text(encoding='utf-8-sig')),args.rounds,args.interval_minutes,args.live))
