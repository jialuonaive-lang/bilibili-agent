"""Small stdio app-server client: only supplied business tools, no computer tools."""
import asyncio,contextlib,json,os,shutil,subprocess
from pathlib import Path

def executable():
    configured=os.environ.get('CODEX_EXECUTABLE')
    if configured:return configured
    root=Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
    paths=list(root.glob('*/codex.exe'))
    if paths:return str(max(paths,key=lambda p:p.stat().st_mtime))
    discovered=shutil.which('codex')
    if discovered:return discovered
    raise RuntimeError('Codex CLI not found; install it and sign in first')

def restrictions():
    values=['features.shell_tool=false','features.unified_exec=false','features.apply_patch_freeform=false',
        'features.view_image=false','features.image_generation=false','features.apps=false','features.multi_agent=false',
        'features.js_repl=false','features.code_mode.enabled=false','web_search="disabled"']
    # Replace entire tables: desktop-only transports may be invalid in the CLI
    # even if an individual entry is disabled.
    values+=['mcp_servers={}','plugins={}']
    return [part for value in values for part in ('-c',value)]

class CodexClient:
    def __init__(self,workspace,model='gpt-5.6-sol',effort='xhigh'):
        self.workspace=Path(workspace);self.model=model;self.effort=effort
        self.pending={};self.events=asyncio.Queue();self.counter=0;self.tasks=set();self.thread=None;self.usage=None;self.completed_turns=[]

    async def write(self,message):
        self.process.stdin.write((json.dumps(message,ensure_ascii=False)+'\n').encode())
        await self.process.stdin.drain()

    async def rpc(self,method,params,timeout=30):
        self.counter+=1;ident=self.counter
        future=asyncio.get_running_loop().create_future();self.pending[ident]=future
        try:
            await self.write(dict(id=ident,method=method,params=params))
            return await asyncio.wait_for(future,timeout)
        finally:self.pending.pop(ident,None)

    async def read(self):
        try:
            while line:=await self.process.stdout.readline():
                try:row=json.loads(line)
                except ValueError:continue
                if 'id' in row and 'method' not in row:
                    future=self.pending.get(row['id'])
                    if future and not future.done():
                        if 'error' in row:future.set_exception(RuntimeError(str(row['error'].get('message','RPC error'))))
                        else:future.set_result(row.get('result',{}))
                elif 'id' in row:
                    task=asyncio.create_task(self.server_request(row));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
                elif row.get('method')=='thread/tokenUsage/updated':self.usage=row.get('params',{}).get('tokenUsage')
                elif row.get('method') in ('item/completed','turn/completed','error'):
                    # Reasoning and raw model streams are deliberately never recorded.
                    await self.events.put(row)
        finally:
            for future in self.pending.values():
                if not future.done():future.set_exception(RuntimeError('Codex app-server closed'))
            await self.events.put(dict(method='closed'))

    async def server_request(self,row):
        if row['method']=='item/tool/call':
            params=row['params']
            try:
                value=await self.handler(params['tool'],params.get('arguments',{}),params['callId'])
                success=not bool(value.get('error'))
            except Exception as error:value=dict(error=type(error).__name__);success=False
            await self.write(dict(id=row['id'],result=dict(success=success,contentItems=[dict(type='inputText',text=json.dumps(value,ensure_ascii=False))])))
        elif 'requestApproval' in row['method']:
            await self.write(dict(id=row['id'],result=dict(decision='decline')))
        else:await self.write(dict(id=row['id'],error=dict(code=-32601,message='Unsupported host request')))

    async def run(self,prompt,tools,handler,instructions,timeout=360,followups=(),record=None):
        self.handler=handler
        self.process=await asyncio.create_subprocess_exec(executable(),*restrictions(),'app-server',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            limit=16*1024*1024,cwd=str(self.workspace),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.reader=asyncio.create_task(self.read())
        try:
            await self.rpc('initialize',dict(clientInfo=dict(name='bili_coursework',version='1.0.0'),capabilities=dict(experimentalApi=True)))
            await self.write(dict(method='initialized',params={}))
            response=await self.rpc('thread/start',dict(cwd=str(self.workspace),model=self.model,approvalPolicy='never',sandbox='read-only',
                baseInstructions=instructions,dynamicTools=tools,ephemeral=True))
            self.thread=response['thread']['id']
            async with asyncio.timeout(timeout):
                for number,user_text in enumerate((prompt,*followups),1):
                    turn=await self.rpc('turn/start',dict(threadId=self.thread,input=[dict(type='text',text=user_text)],model=self.model,
                        effort=self.effort,approvalPolicy='never',sandboxPolicy=dict(type='readOnly')))
                    self.turn=turn['turn']['id'];text=''
                    if record:record('turn_input',number=number,request=user_text,thread_id=self.thread,turn_id=self.turn)
                    while True:
                        row=await self.events.get();params=row.get('params',{})
                        if row['method']=='item/completed':
                            item=params.get('item',{})
                            if item.get('type')=='agentMessage':text=item.get('text','') or text
                        elif row['method']=='turn/completed':
                            ended=params['turn']
                            if ended.get('status')!='completed':raise RuntimeError('Model turn '+str(ended.get('status')))
                            self.completed_turns.append(dict(number=number,request=user_text,final=text))
                            if record:record('turn_final',number=number,text=text,thread_id=self.thread,turn_id=self.turn)
                            break
                        elif row['method']=='closed':raise RuntimeError('Codex disconnected')
                        elif row['method']=='error' and not params.get('willRetry'):raise RuntimeError('Model request failed')
                return text
        finally:
            if hasattr(self,'turn'):
                with contextlib.suppress(Exception):await self.rpc('turn/interrupt',dict(threadId=self.thread,turnId=self.turn),timeout=2)
            for task in list(self.tasks):task.cancel()
            if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
            if self.process.stdin:
                self.process.stdin.close()
                with contextlib.suppress(Exception):await self.process.stdin.wait_closed()
            if self.process.returncode is None:
                with contextlib.suppress(TimeoutError):await asyncio.wait_for(self.process.wait(),3)
                if self.process.returncode is None:self.process.terminate()
                with contextlib.suppress(TimeoutError):await asyncio.wait_for(self.process.wait(),3)
                if self.process.returncode is None:self.process.kill();await self.process.wait()
            with contextlib.suppress(TimeoutError):await asyncio.wait_for(self.reader,3)
