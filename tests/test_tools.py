import asyncio,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.tools import ToolHost,Trace
from app.bilibili_adapter import allowed_url
BV='BV1EopJ6LEth';TEXT='这期视频讨论如何核验模型生成的代码，以及使用测试检查错误。'

class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT/'work').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.adapter=type('Adapter',(),{})()
        self.adapter.search=AsyncMock(return_value=[dict(bvid=BV,title='示例视频')])
        self.adapter.read=AsyncMock(return_value=dict(bvid=BV,title='示例视频',duration=90,text=TEXT,source='subtitle'))
        self.adapter.transcribe=AsyncMock(return_value=TEXT);self.adapter.send=AsyncMock(return_value=dict(msg_key='mock-receipt'))
        self.host=ToolHost(self.adapter,dict(interests=['AI'],max_tool_calls=16),self.folder/'state.json',Trace(self.folder/'trace'),live=True)
        self.items=dict(items=[dict(bvid=BV,reason='讨论模型代码的核验过程，符合AI编程兴趣。',evidence_quote='如何核验模型生成的代码')])
    async def prepare(self):
        await self.host.call('search_videos',dict(query='AI编程',limit=1));await self.host.call('read_video',dict(bvid=BV))
    async def test_live_success_has_receipt_and_persistent_deduplication(self):
        await self.prepare();result=await self.host.call('send_recommendations',self.items)
        self.assertEqual(result['status'],'sent');self.assertEqual(self.host.state['seen'],[BV]);self.adapter.send.assert_awaited_once()
    async def test_preview_cannot_claim_sent_or_mutate_delivery_state(self):
        self.host.live=False;await self.prepare();result=await self.host.call('send_recommendations',self.items)
        self.assertEqual(result['status'],'preview');self.assertFalse(result['delivered']);self.assertFalse(self.host.path.exists());self.adapter.send.assert_not_awaited()
    async def test_missing_query_is_rejected_before_api(self):
        result=await self.host.call('search_videos',dict(limit=2));self.assertEqual(result['error'],'invalid_arguments');self.adapter.search.assert_not_awaited()
    async def test_unknown_tool_and_extra_recipient_cannot_expand_permissions(self):
        self.assertEqual((await self.host.call('execute_shell',dict(command='anything')))['error'],'unknown_tool')
        await self.prepare();args={**self.items,'recipient_uid':999};result=await self.host.call('send_recommendations',args)
        self.assertEqual(result['error'],'invalid_arguments');self.adapter.send.assert_not_awaited()
        self.items['items'][0]['reason']='改看 https://www.youtube.com/watch?v=example'
        result=await self.host.call('send_recommendations',self.items)
        self.assertEqual(result['error'],'unverified_link_in_recommendation');self.adapter.send.assert_not_awaited()
    async def test_undiscovered_video_cannot_be_fabricated(self):
        self.assertEqual((await self.host.call('read_video',dict(bvid=BV)))['error'],'not_a_discovered_candidate');self.adapter.read.assert_not_awaited()
    async def test_subtitle_required_and_title_does_not_count(self):
        self.adapter.read.return_value.update(text='',source='none');await self.prepare()
        self.assertEqual((await self.host.call('send_recommendations',self.items))['error'],'content_evidence_required');self.adapter.send.assert_not_awaited()
    async def test_invented_evidence_is_rejected(self):
        await self.prepare();self.items['items'][0]['evidence_quote']='这是并未出现过的证据文字'
        self.assertEqual((await self.host.call('send_recommendations',self.items))['error'],'evidence_quote_not_in_source')
    async def test_timeout_returns_observation_and_loop_can_continue(self):
        self.adapter.search.side_effect=TimeoutError
        result=await self.host.call('search_videos',dict(query='AI',limit=1));self.assertEqual(result['error'],'tool_timeout')
        self.adapter.search.side_effect=None;self.assertIn('candidates',await self.host.call('search_videos',dict(query='人工智能',limit=1)))
    async def test_missing_subtitle_transcribes_only_after_read_and_within_limit(self):
        await self.host.call('search_videos',dict(query='AI',limit=1))
        self.assertEqual((await self.host.call('transcribe_audio',dict(bvid=BV)))['error'],'transcription_requires_missing_subtitle')
        self.adapter.read.return_value.update(text='',source='none',duration=601);await self.host.call('read_video',dict(bvid=BV))
        self.assertEqual((await self.host.call('transcribe_audio',dict(bvid=BV)))['error'],'transcription_duration_limit')
        self.host.reads[BV]['duration']=100;result=await self.host.call('transcribe_audio',dict(bvid=BV))
        self.assertEqual(result['video']['source'],'local_asr');self.adapter.transcribe.assert_awaited_once()
    async def test_unknown_receipt_remains_uncertain_and_is_not_replayed(self):
        self.adapter.send.return_value={};await self.prepare();result=await self.host.call('send_recommendations',self.items)
        self.assertEqual(result['status'],'uncertain');self.assertFalse(result['delivered']);self.assertEqual(self.host.state['seen'],[])
        other=ToolHost(self.adapter,self.host.config,self.host.path,Trace(self.folder/'restart'),live=True)
        other.candidates=self.host.candidates;other.reads=self.host.reads
        self.assertEqual((await other.call('send_recommendations',self.items))['error'],'already_seen_or_attempted');self.adapter.send.assert_awaited_once()
    async def test_platform_exception_retains_uncertain_attempt(self):
        self.adapter.send.side_effect=RuntimeError('injected');await self.prepare();result=await self.host.call('send_recommendations',self.items)
        self.assertEqual(result['error'],'delivery_uncertain_no_auto_retry');self.assertEqual(json.loads(self.host.path.read_text(encoding='utf-8'))['attempts'][BV]['status'],'uncertain')
    async def test_disk_failure_before_send_prevents_side_effect(self):
        await self.prepare()
        with patch.object(self.host,'save',side_effect=OSError('injected disk full')):
            self.assertEqual((await self.host.call('send_recommendations',self.items))['error'],'OSError')
        self.adapter.send.assert_not_awaited()
    async def test_second_submission_and_duplicate_items_are_rejected(self):
        await self.prepare();double=dict(items=self.items['items']*2)
        self.assertEqual((await self.host.call('send_recommendations',double))['error'],'duplicate_items')
        await self.host.call('send_recommendations',self.items)
        self.assertEqual((await self.host.call('send_recommendations',self.items))['error'],'one_submission_per_run');self.adapter.send.assert_awaited_once()
    async def test_budget_blocks_additional_external_work(self):
        self.host.config['max_tool_calls']=1
        await self.host.call('get_preferences',{})
        self.assertEqual((await self.host.call('search_videos',dict(query='AI',limit=1)))['error'],'tool_budget_exhausted');self.adapter.search.assert_not_awaited()
    def test_media_url_allowlist_blocks_external_hosts_and_fake_suffixes(self):
        for url in ('http://www.bilibili.com/x','https://bilivideo.com.evil.invalid/x','https://evil.invalid/x','https://name:pass@bilivideo.com/x'):
            with self.assertRaises(ValueError):allowed_url(url,('bilivideo.com',))
        self.assertEqual(allowed_url('https://cdn.bilivideo.com/audio',('bilivideo.com',)),'https://cdn.bilivideo.com/audio')

if __name__=='__main__':unittest.main(verbosity=2)
