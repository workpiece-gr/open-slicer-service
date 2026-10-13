import asyncio
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from app.native_supervisor import NativeError, NativeSupervisor

class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.supervisor = NativeSupervisor()
        self.directory = tempfile.TemporaryDirectory()
        self.cwd = Path(self.directory.name)
    async def asyncTearDown(self):
        await self.supervisor.shutdown()
        self.directory.cleanup()
    async def run_native(self, code, timeout=2):
        async with self.supervisor.reservation(1):
            return await self.supervisor.run([sys.executable, '-c', code], cwd=self.cwd, timeout=timeout, env={**os.environ,'WORKPIECE_PROJECT_API_TOKEN':'private-test-value'})
    async def test_success_and_token_not_inherited(self):
        result = await self.run_native("import os; assert 'WORKPIECE_PROJECT_API_TOKEN' not in os.environ; print('complete')")
        self.assertEqual(result.returncode,0)
        self.assertIn('complete',result.stdout)
    async def test_timeout_actual_death_and_recovery(self):
        with self.assertRaises(NativeError) as error:
            await self.run_native('import time; time.sleep(10)',.15)
        self.assertEqual(error.exception.code,'NATIVE_TIMEOUT')
        self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
        self.assertEqual((await self.run_native("print('recovered')")).returncode,0)
    async def test_ignoring_term_and_escaped_descendant(self):
        code="""import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid=os.fork()
if pid==0:
 os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(20)
else:
 open('child','w').write(str(pid)); time.sleep(20)
"""
        with self.assertRaises(NativeError):
            await self.run_native(code,.2)
        child=int((self.cwd/'child').read_text())
        self.assertFalse(Path(f'/proc/{child}').exists())
    async def test_success_parent_with_lingering_child(self):
        code="""import os,time
pid=os.fork()
if pid==0: time.sleep(20)
else:
 open('child','w').write(str(pid)); time.sleep(.1)
"""
        self.assertEqual((await self.run_native(code)).returncode,0)
        self.assertFalse(Path(f"/proc/{int((self.cwd/'child').read_text())}").exists())
    async def test_cancellation_and_capacity_recovery(self):
        task=asyncio.create_task(self.run_native('import time; time.sleep(20)'))
        await asyncio.sleep(.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
        self.assertEqual((await self.run_native('print(1)')).returncode,0)
    async def test_repeated_timeouts(self):
        for _ in range(3):
            with self.assertRaises(NativeError): await self.run_native('import time; time.sleep(20)',.08)
            self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
        self.assertFalse(self.supervisor.poisoned)
    async def test_queue_bounded(self):
        entered=asyncio.Event()
        async def holding():
            async with self.supervisor.reservation(1):
                entered.set(); await asyncio.sleep(.2)
        task=asyncio.create_task(holding()); await entered.wait()
        with self.assertRaises(NativeError) as error:
            async with self.supervisor.reservation(.02): pass
        self.assertEqual(error.exception.code,'NATIVE_QUEUE_BUSY')
        await task
    async def test_shutdown_contains_active_work(self):
        task=asyncio.create_task(self.run_native('import time; time.sleep(20)'))
        await asyncio.sleep(.1); await self.supervisor.shutdown()
        self.assertTrue(task.done())
        self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
    async def test_crash_is_not_success(self):
        self.assertNotEqual((await self.run_native('import os; os._exit(7)')).returncode,0)
    async def test_unrelated_child_not_signalled(self):
        sibling=await asyncio.create_subprocess_exec(sys.executable,'-c','import time; time.sleep(20)')
        try:
            with self.assertRaises(NativeError): await self.run_native('import time; time.sleep(20)',.1)
            self.assertIsNone(sibling.returncode)
        finally:
            sibling.terminate(); await sibling.wait()
    async def test_invalid_deadline_rejected_without_spawn(self):
        for timeout in [0,181,float('inf'),float('nan')]:
            with self.assertRaises(ValueError): await self.run_native('print(1)',timeout)
    async def test_missing_executable_releases_capacity(self):
        with self.assertRaises(NativeError):
            async with self.supervisor.reservation(1):
                await self.supervisor.run(['/missing-native'],cwd=self.cwd,timeout=1,env={})
        self.assertEqual((await self.run_native('print(1)')).returncode,0)
    async def test_poison_rejects_future_admission(self):
        self.supervisor._poisoned=True
        with self.assertRaises(NativeError):
            async with self.supervisor.reservation(1): pass

    async def test_fast_exit_races(self):
        for _ in range(15):
            self.assertEqual((await self.run_native('pass')).returncode,0)
    async def test_unreserved_peer_rejected(self):
        entered=asyncio.Event(); release=asyncio.Event()
        async def owner():
            async with self.supervisor.reservation(1):
                entered.set(); await release.wait()
        task=asyncio.create_task(owner()); await entered.wait()
        try:
            with self.assertRaises(NativeError):
                await self.supervisor.run([sys.executable,'-c','pass'],cwd=self.cwd,timeout=1,env={})
        finally: release.set(); await task
    async def test_inherited_reservation_child_allowed(self):
        async with self.supervisor.reservation(1):
            result=await asyncio.create_task(self.supervisor.run([sys.executable,'-c','pass'],cwd=self.cwd,timeout=1,env={}))
        self.assertEqual(result.returncode,0)
    async def test_pid_reuse_cannot_attribute_descendant(self):
        fake=SimpleNamespace(pid=100,_native_owned={100:10},_native_baseline=set())
        records={100:(os.getpid(),100,11),200:(100,100,12)}
        with patch.object(self.supervisor,'_processes',return_value=records):
            self.assertEqual(self.supervisor._track(fake),{})
        self.assertTrue(fake._native_unattributed)
    async def test_containment_permission_failure_poisons(self):
        fake=SimpleNamespace()
        with patch.object(self.supervisor,'_contain_inner',side_effect=PermissionError('denied')):
            with self.assertRaises(NativeError) as error: await self.supervisor._contain(fake)
        self.assertEqual(error.exception.code,'NATIVE_CONTAINMENT_FAILED')
        self.assertTrue(self.supervisor.poisoned)
    async def test_cancel_during_spawn_adopts_and_terminates(self):
        original=asyncio.create_subprocess_exec
        async def delayed(*args,**kwargs):
            process=await original(*args,**kwargs)
            await asyncio.sleep(.08)
            return process
        with patch('asyncio.create_subprocess_exec',side_effect=delayed):
            task=asyncio.create_task(self.run_native('import time; time.sleep(20)'))
            await asyncio.sleep(.02); task.cancel(); await asyncio.sleep(.02); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
        self.assertEqual((await self.run_native('pass')).returncode,0)


    async def test_tracking_child_exit_during_cgroup_read(self):
        parent=os.getpid(); leader=123456; child=123457
        process=SimpleNamespace(pid=leader,_native_owned={leader:11},_native_baseline=set())
        snapshots=[{leader:(parent,leader,11),child:(leader,leader,12)}, {leader:(parent,leader,11)}]
        with patch.object(self.supervisor,'_processes',side_effect=snapshots), patch.object(self.supervisor,'_cgroup',side_effect=lambda pid: (_ for _ in ()).throw(ProcessLookupError()) if pid==child else '0::/test'):
            self.assertEqual(self.supervisor._track(process),{leader:11})
        self.assertFalse(self.supervisor.poisoned)

    async def test_tracking_live_unverifiable_child_fails_closed(self):
        parent=os.getpid();leader=123456;child=123457
        process=SimpleNamespace(pid=leader,_native_owned={leader:11},_native_baseline=set())
        snapshot={leader:(parent,leader,11),child:(leader,leader,12)}
        with patch.object(self.supervisor,'_processes',return_value=snapshot),patch.object(self.supervisor,'_cgroup',side_effect=lambda pid: (_ for _ in ()).throw(ProcessLookupError()) if pid==child else '0::/test'):
            with self.assertRaises(NativeError):self.supervisor._track(process)
        self.assertTrue(self.supervisor.poisoned)

if __name__=='__main__': unittest.main()
