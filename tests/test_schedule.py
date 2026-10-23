import sys,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import schedule
class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_round_count_sleep_and_live_scope(self):
        with patch('schedule.run',new=AsyncMock(return_value={'status':'completed'})) as run,patch('schedule.asyncio.sleep',new=AsyncMock()) as sleep:
            result=await schedule.scheduled('AI',{},2,30,False)
            self.assertEqual(len(result),2);self.assertEqual(run.await_count,2);sleep.assert_awaited_once_with(1800)
            self.assertTrue(all(c.args[3] is False for c in run.call_args_list))
    async def test_invalid_budget_never_starts_model(self):
        with patch('schedule.run',new=AsyncMock()) as run:
            for rounds,minutes in ((0,1),(25,1),(1,0)):
                with self.assertRaises(ValueError):await schedule.scheduled('AI',{},rounds,minutes)
            run.assert_not_awaited()
    async def test_last_round_does_not_leave_sleeping_task(self):
        with patch('schedule.run',new=AsyncMock(return_value={})),patch('schedule.asyncio.sleep',new=AsyncMock()) as sleep:
            await schedule.scheduled('AI',{},1,60);sleep.assert_not_awaited()
