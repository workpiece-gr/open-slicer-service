import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app import main
from app.native_supervisor import NativeSupervisor

class NativeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.supervisor=NativeSupervisor()
        self.patch=patch.object(main,'NATIVE',self.supervisor); self.patch.start()
        self.tmp=tempfile.TemporaryDirectory()
    async def asyncTearDown(self):
        await self.supervisor.shutdown(); self.patch.stop(); self.tmp.cleanup()
    async def test_authenticated_reservation_spans_export_and_verify(self):
        with patch.object(main,'ENABLE_EXPERIMENTAL_PROJECT_API',True),patch.object(main,'WORKPIECE_PROJECT_API_TOKEN','test-only'):
            dep=main.project_access('Bearer test-only'); await anext(dep)
            self.assertTrue(self.supervisor._slot.locked())
            for phase in ['export','verify']:
                result=await main.disconnect_guard(None,main.run_orca([sys.executable,'-c',f"print('{phase}')"],cwd=Path(self.tmp.name),timeout=1,env=os.environ))
                self.assertIn(phase,result.stdout)
                self.assertTrue(self.supervisor._slot.locked())
            await dep.aclose(); self.assertFalse(self.supervisor._slot.locked())
    async def test_unauthenticated_project_cannot_admit(self):
        with patch.object(main,'ENABLE_EXPERIMENTAL_PROJECT_API',True),patch.object(main,'WORKPIECE_PROJECT_API_TOKEN','test-only'):
            for header in [None,'Bearer wrong']:
                with self.assertRaises(HTTPException) as error: await anext(main.project_access(header))
                self.assertEqual(error.exception.status_code,401)
        self.assertFalse(self.supervisor._slot.locked())
    async def test_native_timeout_response_and_partial_cleanup(self):
        job=None
        with self.assertRaises(HTTPException) as error:
            with main.native_job('test-native-api-') as name:
                job=Path(name); (job/'partial.3mf').write_bytes(b'incomplete')
                async with self.supervisor.reservation(1):
                    await main.run_orca([sys.executable,'-c','import time; time.sleep(20)'],cwd=job,timeout=.1,env=os.environ)
        self.assertEqual(error.exception.status_code,504)
        self.assertEqual(error.exception.detail['code'],'NATIVE_TIMEOUT')
        self.assertFalse(job.exists())
    async def test_crash_response_not_success(self):
        async with self.supervisor.reservation(1):
            with self.assertRaises(HTTPException) as error:
                await main.run_orca([sys.executable,'-c','raise SystemExit(3)'],cwd=Path(self.tmp.name),timeout=1,env=os.environ)
        self.assertEqual(error.exception.status_code,422)
    async def test_poisoned_jobs_retained_and_health_fails_closed(self):
        with main.native_job('test-native-poison-') as name:
            job=Path(name); (job/'partial').write_text('diagnostic'); self.supervisor._poisoned=True
        self.assertTrue(job.exists())
        with self.assertRaises(HTTPException) as error: main.health()
        self.assertEqual(error.exception.status_code,503)
        self.supervisor._poisoned=False
        import shutil; shutil.rmtree(job)
    async def test_disconnect_contains_worker_before_response(self):
        event=asyncio.Event()
        class Request:
            async def receive(self):
                await event.wait(); return {'type':'http.disconnect'}
        async with self.supervisor.reservation(1):
            task=asyncio.create_task(main.disconnect_guard(Request(),main.run_orca([sys.executable,'-c','import time; time.sleep(20)'],cwd=Path(self.tmp.name),timeout=3,env=os.environ)))
            await asyncio.sleep(.1); event.set()
            with self.assertRaises(HTTPException) as error: await task
        self.assertEqual(error.exception.status_code,499)
        self.assertFalse(Path(f"/proc/{self.supervisor.last_execution['pid']}").exists())
    async def test_cleanup_failure_poisoned(self):
        job=None
        with patch.object(main.shutil,'rmtree',side_effect=PermissionError('denied')):
            with self.assertRaises(HTTPException) as error:
                with main.native_job('test-native-cleanup-') as name: job=Path(name)
        self.assertEqual(error.exception.status_code,503); self.assertTrue(self.supervisor.poisoned)
        import shutil; shutil.rmtree(job)

if __name__=='__main__': unittest.main()
