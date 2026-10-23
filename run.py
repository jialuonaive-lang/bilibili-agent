"""Run one bounded recommendation task; --live is the explicit delivery switch."""
import argparse,asyncio,hashlib,json,re,time
from pathlib import Path
from app.bilibili_adapter import BilibiliAdapter
from app.codex_rpc import CodexClient
from app.tools import TOOLS,ToolHost,Trace
ROOT=Path(__file__).resolve().parent

async def run(request,config,folder,live=False,adapter=None,followups=()):
    if len(followups)>2:raise ValueError('At most two follow-up turns per bounded session')
    instructions=(ROOT/'prompts/system.txt').read_text(encoding='utf-8')
    trace=Trace(folder);trace.record('input',request=request,delivery_mode='live' if live else 'preview',model=config.get('model','gpt-5.6-sol'),
        prompt_sha256=hashlib.sha256(instructions.encode()).hexdigest())
    (trace.folder/'prompt-used.txt').write_text(instructions,encoding='utf-8')
    (trace.folder/'tools-used.json').write_text(json.dumps(TOOLS,ensure_ascii=False,indent=2),encoding='utf-8')
    adapter=adapter or BilibiliAdapter(config,ROOT)
    host=ToolHost(adapter,config,ROOT/'data/recommendation-state.json',trace,live=live)
    for bvid in re.findall(r'(?<![A-Za-z0-9])BV[A-Za-z0-9]{10}(?![A-Za-z0-9])','\n'.join((request,*followups))):
        host.candidates[bvid]=dict(bvid=bvid,title='用户明确给定的视频')
    client=CodexClient(ROOT,config.get('model','gpt-5.6-sol'),config.get('effort','xhigh'))
    summary=dict(status='running',mode='live' if live else 'preview',model=client.model,effort=client.effort)
    try:
        result=await client.run(request,TOOLS,host.call,instructions,timeout=config.get('run_timeout',360),followups=followups,record=trace.record)
        summary.update(status='completed',final=result,tool_calls=host.calls,token_usage=client.usage,turns=client.completed_turns)
        trace.record('final',text=result,tool_calls=host.calls,token_usage=client.usage)
    except Exception as error:
        summary.update(status='failed',error=type(error).__name__,tool_calls=host.calls);trace.record('failure',error=type(error).__name__)
        raise
    finally:
        summary['elapsed_seconds']=round(time.monotonic()-trace.started,3)
        (trace.folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--config',default=str(ROOT/'config.local.json'))
    parser.add_argument('--live',action='store_true');parser.add_argument('--run-id',default=time.strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--followup',action='append',default=[],help='Scripted user clarification in the same thread; at most two')
    args=parser.parse_args()
    if not args.run_id.replace('-','').replace('_','').isalnum():parser.error('run-id must be alphanumeric')
    if len(args.followup)>2:parser.error('at most two follow-up turns')
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    result=asyncio.run(run(args.request,config,ROOT/'evidence'/args.run_id,args.live,followups=args.followup));print(result['final'])
if __name__=='__main__':main()
