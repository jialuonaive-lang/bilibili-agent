"""Bilibili SDK adapter; credentials stay in a separate local file."""
import asyncio,html,json,os,platform,re,sys,urllib.parse,urllib.request
from pathlib import Path
# Python 3.14's platform.system() can block in Windows WMI. aiohttp only needs
# the OS name at import time; supply that known value without touching WMI.
_system=platform.system
try:
    if sys.platform=='win32':platform.system=lambda:'Windows'
    from bilibili_api import Credential,search,session,user,video
finally:platform.system=_system

def clean(value):return re.sub(r'\s+',' ',re.sub(r'<[^>]*>','',html.unescape(str(value or '')))).strip()

def allowed_url(url,domains):
    if url.startswith('//'):url='https:'+url
    parsed=urllib.parse.urlsplit(url);host=(parsed.hostname or '').lower()
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not any(host==d or host.endswith('.'+d) for d in domains):
        raise ValueError('unexpected_media_url')
    return url

def download(url,path,domains,maximum):
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            allowed_url(newurl,domains);return super().redirect_request(req,fp,code,msg,headers,newurl)
    req=urllib.request.Request(allowed_url(url,domains),headers={'Referer':'https://www.bilibili.com','User-Agent':'Mozilla/5.0'})
    size=0
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect()).open(req,timeout=25) as response,Path(path).open('wb') as handle:
        while chunk:=response.read(65536):
            size+=len(chunk)
            if size>maximum:raise ValueError('media_too_large')
            handle.write(chunk)

class BilibiliAdapter:
    def __init__(self,config,root):self.config=config;self.root=Path(root)
    def credential(self):
        path=self.config.get('credential_path') or os.environ.get('BILI_CREDENTIAL_FILE','')
        if not path:raise RuntimeError('bilibili_login_required')
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        def field(*names):return next((str(data[n]) for n in names if data.get(n)),None)
        return Credential(sessdata=field('sessdata','SESSDATA'),bili_jct=field('bili_jct','BILI_JCT'),
            buvid3=field('buvid3','BUVID3'),dedeuserid=field('dedeuserid','DedeUserID'))
    async def search(self,query,limit):
        payload=await search.search_by_type(keyword=query,search_type=search.SearchObjectType.VIDEO,page=1,order_type=search.OrderVideo.PUBDATE)
        rows=[]
        for item in (payload or {}).get('result',[]):
            bvid=item.get('bvid','')
            if re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid):
                rows.append(dict(bvid=bvid,title=clean(item.get('title')),author=clean(item.get('author')),description=clean(item.get('description'))[:200]))
            if len(rows)>=limit:break
        return rows
    async def read(self,bvid):
        page=video.Video(bvid=bvid,credential=self.credential());info=await page.get_info()
        cid=info.get('cid') or next((p.get('cid') for p in info.get('pages',[]) if p.get('cid')),None)
        if not cid:raise RuntimeError('missing_video_cid')
        result=dict(bvid=bvid,title=clean(info.get('title')),duration=info.get('duration'),text='',source='none')
        subtitles=(await page.get_subtitle(cid=cid) or {}).get('subtitles') or []
        subtitles=[s for s in subtitles if s.get('subtitle_url')]
        subtitles.sort(key=lambda s:0 if s.get('lan') in ('ai-zh','zh-CN','zh') else 1)
        if subtitles:
            folder=self.root/'data/subtitles';folder.mkdir(parents=True,exist_ok=True);path=folder/(bvid+'.json')
            await asyncio.to_thread(download,subtitles[0]['subtitle_url'],path,('hdslb.com','bilibili.com'),2*1024*1024)
            payload=json.loads(path.read_text(encoding='utf-8'))
            result.update(text='\n'.join(str(r.get('content','')) for r in payload.get('body',[]) if r.get('content'))[:8000],source='subtitle')
        return result
    async def transcribe(self,bvid):
        python=self.config.get('asr_python') or os.environ.get('ASR_PYTHON',sys.executable)
        folder=self.root/'data/transcripts';folder.mkdir(parents=True,exist_ok=True);output=folder/(bvid+'.txt')
        self.last_asr_cache_hit=output.exists()
        if output.exists():return output.read_text(encoding='utf-8')
        page=video.Video(bvid=bvid,credential=self.credential());payload=await page.get_download_url(page_index=0)
        streams=(payload.get('dash') or {}).get('audio') or []
        if not streams:raise RuntimeError('no_audio_stream')
        stream=min(streams,key=lambda row:int(row.get('bandwidth') or 0));audio=folder/(bvid+'.m4a')
        try:
            await asyncio.to_thread(download,stream.get('baseUrl') or stream.get('base_url'),audio,('bilivideo.com','bilivideo.cn','hdslb.com'),50*1024*1024)
            env=dict(os.environ,HF_HUB_OFFLINE='1',PYTHONUTF8='1')
            env['BILI_ASR_MODEL']=self.config.get('asr_model','small')
            if self.config.get('asr_cache'):env['HF_HOME']=self.config['asr_cache']
            process=await asyncio.create_subprocess_exec(python,str(self.root/'app/asr_worker.py'),str(audio),str(output),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE,env=env,
                creationflags=getattr(__import__('subprocess'),'CREATE_NO_WINDOW',0))
            try:_,stderr=await asyncio.wait_for(process.communicate(),150);code=process.returncode
            except (TimeoutError,asyncio.CancelledError):
                process.kill();await process.wait();raise
            if code or not output.exists():
                (folder/'last-worker-error.txt').write_text(stderr.decode('utf-8',errors='replace')[-2400:],encoding='utf-8')
                raise RuntimeError('local_asr_unavailable_or_failed')
            return output.read_text(encoding='utf-8')
        finally:audio.unlink(missing_ok=True)
    async def send(self,text):
        recipient=int(self.config.get('recipient_uid') or 0);sender=int(self.config.get('sender_uid') or 0)
        if not sender or not recipient:raise RuntimeError('sender_and_recipient_must_be_configured')
        credential=self.credential();identity=await user.get_self_info(credential)
        if int(identity.get('mid') or 0)!=sender:raise RuntimeError('sender_identity_mismatch')
        return await session.send_msg(credential,receiver_id=recipient,msg_type=session.EventType.TEXT,content=text)

