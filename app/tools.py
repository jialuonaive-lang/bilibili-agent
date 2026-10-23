"""Business tool schemas and host checks; does not expose arbitrary HTTP or file tools."""
import asyncio,json,re,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from jsonschema import validate,ValidationError

BV=re.compile(r'^BV[0-9A-Za-z]{10}$');TZ=timezone(timedelta(hours=8))

def schema(name,description,properties,required):
    properties=dict(purpose=dict(type='string',minLength=1,maxLength=80),**properties)
    return dict(type='function',name=name,description=description,inputSchema=dict(type='object',properties=properties,required=required,additionalProperties=False))

TOOLS=[
    schema('get_preferences','读取用户兴趣和每轮推荐数量，以及已推荐的视频号。先读取再决定检索关键词。',{},[]),
    schema('search_videos','按自己规划的中文关键词搜索B站候选。limit必须是1到5。搜索结果只有元数据，尚不能据此判断视频内容。',
        dict(query=dict(type='string',minLength=1,maxLength=80),limit=dict(type='integer',minimum=1,maximum=5)),['query','limit']),
    schema('read_video','读取已检索或用户明确给定BV号的视频详情与字幕。若text为空，可在时长和工具预算允许时调用transcribe_audio。',
        dict(bvid=dict(type='string',pattern=BV.pattern)),['bvid']),
    schema('transcribe_audio','仅在已读取的视频缺字幕、时长不超过10分钟时做本地音频转写。转写失败应跳过，不能按标题猜。',
        dict(bvid=dict(type='string',pattern=BV.pattern)),['bvid']),
    schema('send_recommendations','提交最多2条已取得内容依据且未推荐过的视频。evidence_quote必须逐字来自当前字幕/转写。宿主核验后按preview/live模式决定预览或真实发送，仅发给配置的唯一用户。',
        dict(items=dict(type='array',minItems=1,maxItems=2,items=dict(type='object',properties=dict(bvid=dict(type='string',pattern=BV.pattern),
            reason=dict(type='string',minLength=5,maxLength=120),evidence_quote=dict(type='string',minLength=8,maxLength=160)),
            required=['bvid','reason','evidence_quote'],additionalProperties=False))),['items'])
]

class Trace:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=False);self.started=time.monotonic();self.count=0
    def record(self,kind,**fields):
        self.count+=1
        row=dict(seq=self.count,time=datetime.now(TZ).isoformat(timespec='milliseconds'),elapsed=round(time.monotonic()-self.started,3),kind=kind,**fields)
        with (self.folder/'trace.jsonl').open('a',encoding='utf-8') as handle:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(dict(seq=row['seq'],kind=kind,tool=fields.get('tool'),elapsed=row['elapsed']),ensure_ascii=False),flush=True)

class ToolHost:
    def __init__(self,adapter,config,state_path,trace,live=False):
        self.adapter=adapter;self.config=config;self.path=Path(state_path);self.trace=trace;self.live=live
        self.state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else dict(seen=[],attempts={})
        self.candidates={};self.reads={};self.calls=0;self.submitted=False
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)
    async def call(self,name,args,call_id=''):
        self.calls+=1;start=time.monotonic();self.trace.record('action',tool=name,arguments=args,call_id=call_id)
        try:
            if self.calls>self.config.get('max_tool_calls',16):raise ValueError('tool_budget_exhausted')
            definition=next((t for t in TOOLS if t['name']==name),None)
            if not definition:raise ValueError('unknown_tool')
            validate(args,definition['inputSchema'])
            async with asyncio.timeout(180 if name=='transcribe_audio' else 35):result=await self.execute(name,args)
        except ValidationError:result=dict(error='invalid_arguments',retryable=False)
        except TimeoutError:result=dict(error='tool_timeout',retryable=False)
        except (ValueError,RuntimeError) as error:result=dict(error=str(error)[:120],retryable=False)
        except Exception as error:result=dict(error=type(error).__name__,retryable=False)
        self.trace.record('observation',tool=name,result=result,seconds=round(time.monotonic()-start,3),call_id=call_id)
        return result
    async def execute(self,name,args):
        if name=='get_preferences':return dict(interests=self.config.get('interests',[]),max_recommendations=2,seen=self.state['seen'],mode='live' if self.live else 'preview')
        if name=='search_videos':
            rows=await self.adapter.search(args['query'],args['limit'])
            for row in rows:
                if BV.fullmatch(row.get('bvid','')):self.candidates[row['bvid']]=row
            return dict(candidates=[r for r in rows if r.get('bvid') in self.candidates],untrusted=True)
        if name in ('read_video','transcribe_audio'):
            bvid=args['bvid']
            if bvid not in self.candidates:raise ValueError('not_a_discovered_candidate')
            if name=='read_video':
                value=await self.adapter.read(bvid);value['text']=value.get('text','')[:8000];self.reads[bvid]=value
            else:
                value=self.reads.get(bvid)
                if not value or value.get('text'):raise ValueError('transcription_requires_missing_subtitle')
                if not 0<float(value.get('duration') or 0)<=600:raise ValueError('transcription_duration_limit')
                text=await self.adapter.transcribe(bvid)
                value=dict(value,text=text[:8000],source='local_asr' if text else 'none',
                    transcription_status='completed' if text.strip() else 'completed_no_detected_speech',
                    cache_hit=bool(getattr(self.adapter,'last_asr_cache_hit',False)))
                self.reads[bvid]=value
            return dict(video=value,untrusted=True)
        if name=='send_recommendations':
            if self.submitted:raise ValueError('one_submission_per_run')
            items=args['items'];ids=[item['bvid'] for item in items]
            if len(ids)!=len(set(ids)):raise ValueError('duplicate_items')
            for item in items:
                bvid=item['bvid'];video=self.reads.get(bvid)
                if not video or not video.get('text'):raise ValueError('content_evidence_required')
                if item['evidence_quote'] not in video['text']:raise ValueError('evidence_quote_not_in_source')
                if bvid in self.state['seen'] or bvid in self.state['attempts']:raise ValueError('already_seen_or_attempted')
            recommendations=[dict(**item,title=self.reads[item['bvid']]['title'],url=f"https://www.bilibili.com/video/{item['bvid']}",source=self.reads[item['bvid']]['source']) for item in items]
            text='\n\n'.join(f"{r['title'][:50]}\n{r['url']}\n推荐理由：{r['reason']}" for r in recommendations)
            if len(text)>480:raise ValueError('message_too_long')
            allowed_links={r['url'] for r in recommendations}
            output_links=re.findall(r'''(?:https?://|www\.)[^\s<>"']+''',text,re.IGNORECASE)
            if any(url.rstrip('.,;)]}，。、；）') not in allowed_links for url in output_links):
                raise ValueError('unverified_link_in_recommendation')
            if not self.live:
                self.submitted=True;return dict(status='preview',delivered=False,recommendations=recommendations,message=text)
            # Persist the at-most-once attempt before the platform request.
            for bvid in ids:self.state['attempts'][bvid]=dict(status='uncertain',run=self.trace.folder.name)
            self.save();self.submitted=True
            try:
                receipt=await self.adapter.send(text)
                key=receipt.get('msg_key') if isinstance(receipt,dict) else None
            except Exception:
                self.trace.record('delivery',status='uncertain',bvids=ids,reason='platform_exception_after_attempt')
                raise RuntimeError('delivery_uncertain_no_auto_retry')
            status='sent' if key else 'uncertain'
            for bvid in ids:self.state['attempts'][bvid].update(status=status)
            if key:self.state['seen']=sorted(set(self.state['seen'])|set(ids))
            self.save()
            return dict(status=status,delivered=bool(key),receipt=dict(msg_key=key) if key else {},recommendations=recommendations)
