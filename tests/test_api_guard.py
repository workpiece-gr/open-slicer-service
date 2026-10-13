import asyncio
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch
from app.api_guard import ApiGuard,GuardLimits

TOKEN='only-test-secret'

def scope(path='/v1/slice',headers=None,method='POST',root_path=''):
    return {'type':'http','path':path,'method':method,'headers':headers if headers is not None else [(b'authorization',b'Bearer '+TOKEN.encode())],'root_path':root_path}

class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]; self.reads=0
        async def app(s,receive,send):
            self.calls.append(s['path'])
            body=b''
            if s['method']=='POST':
                while True:
                    message=await receive(); body+=message.get('body',b'')
                    if not message.get('more_body'): break
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':body})
        self.app=app
        self.gate=ApiGuard(app,token_getter=lambda:TOKEN)
    async def request(self,s=None,messages=None,gate=None):
        sent=[]; queue=list(messages or [{'type':'http.request','body':b'abc','more_body':False}])
        async def receive():
            self.reads+=1
            if queue: return queue.pop(0)
            await asyncio.Future()
        await (gate or self.gate)(s or scope(),receive,sent.append_async if hasattr(sent,'append_async') else self.sender(sent))
        return sent[0]['status'],sent[-1].get('body',b'')
    def sender(self,sent):
        async def send(message): sent.append(message)
        return send
    async def test_valid_slice_and_project_replay(self):
        for path in ['/v1/slice','/v1/project']:
            status,body=await self.request(scope(path)); self.assertEqual((status,body),(200,b'abc'))
    async def test_auth_rejected_before_receive(self):
        for headers in [[],[(b'authorization',b'Bearer wrong')],[(b'authorization',b'Basic nope')],[(b'authorization',b'Bearer ')],[(b'authorization',b'Bearer '+TOKEN.encode()+b' ')],[(b'authorization',b'Bearer \xff')]]:
            status,body=await self.request(scope(headers=headers)); self.assertEqual(status,401); self.assertNotIn(TOKEN.encode(),body)
        self.assertEqual(self.reads,0); self.assertEqual(self.calls,[])
    async def test_duplicate_and_long_auth_rejected(self):
        for headers in [[(b'authorization',b'Bearer '+TOKEN.encode())]*2,[(b'authorization',b'x'*4097)]]:
            self.assertEqual((await self.request(scope(headers=headers)))[0],401)
        self.assertEqual(self.reads,0)
    async def test_missing_config_503(self):
        gate=ApiGuard(self.app,token_getter=lambda:'')
        self.assertEqual((await self.request(gate=gate))[0],503); self.assertEqual(self.reads,0)
    async def test_unknown_paths_trailing_slashes_and_root_path_gated(self):
        for path,root in [('/v1/slice/',''),('/v1/project/',''),('/v1/unknown',''),('/v1',''),('/prefix/v1/slice','/prefix')]:
            self.assertEqual((await self.request(scope(path,headers=[],root_path=root)))[0],401)
        self.assertEqual(self.reads,0)
    async def test_public_healthz_source_unaffected_detailed_health_protected(self):
        for path in ['/healthz','/source','/']:
            self.assertEqual((await self.request(scope(path,headers=[],method='GET')))[0],200)
        self.assertEqual((await self.request(scope('/health',headers=[],method='GET')))[0],401)
        self.assertEqual((await self.request(scope('/health/',headers=[],method='GET')))[0],401)
    async def test_unauthorized_global_attempt_budget(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,clock=lambda:0)
        for index in range(40):
            status,_=await self.request(scope(headers=[(b'x-forwarded-for',str(index).encode())]),gate=gate); self.assertEqual(status,401)
        self.assertEqual((await self.request(scope(headers=[]),gate=gate))[0],429)
        self.assertEqual(self.reads,0)
        self.assertEqual((await self.request(scope('/health',method='GET'),gate=gate))[0],200)
    async def test_content_length_rejected_without_receive(self):
        for extra,status in [([(b'content-length',b'999999999')],413),([(b'content-length',b'-1')],400),([(b'content-length',b'1')]*2,400),([(b'content-length',b'3'),(b'transfer-encoding',b'chunked')],400)]:
            headers=scope()['headers']+extra
            self.assertEqual((await self.request(scope(headers=headers)))[0],status)
        self.assertEqual(self.reads,0)
    async def test_chunked_actual_body_limit(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),body_bytes=5))
        messages=[{'type':'http.request','body':b'abc','more_body':True},{'type':'http.request','body':b'def','more_body':False}]
        self.assertEqual((await self.request(messages=messages,gate=gate))[0],413); self.assertEqual(self.calls,[])
        self.assertFalse(gate.lock.locked())
    async def test_body_length_mismatch(self):
        self.assertEqual((await self.request(scope(headers=scope()['headers']+[(b'content-length',b'2')])))[0],400)
    async def test_body_deadline(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),body_seconds=.03))
        sent=[]
        async def slow(): await asyncio.sleep(1)
        await gate(scope(),slow,self.sender(sent))
        self.assertEqual(sent[0]['status'],408); self.assertFalse(gate.lock.locked()); self.assertEqual(gate.admitted,0)
    async def test_upload_disconnect(self):
        self.assertEqual((await self.request(messages=[{'type':'http.disconnect'}]))[0],499)
        self.assertFalse(self.gate.lock.locked())
    async def test_global_rate_and_refill(self):
        clock=[0]
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),request_burst=1),clock=lambda:clock[0])
        self.assertEqual((await self.request(gate=gate))[0],200)
        self.assertEqual((await self.request(scope('/v1/project',headers=scope()['headers']+[(b'x-forwarded-for',b'other')]),gate=gate))[0],429)
        clock[0]=1; self.assertEqual((await self.request(gate=gate))[0],200)
    async def test_global_byte_budget(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),byte_burst=4),clock=lambda:0)
        self.assertEqual((await self.request(gate=gate))[0],200)
        self.assertEqual((await self.request(gate=gate))[0],429); self.assertEqual(self.calls,['/v1/slice'])
    async def test_health_not_starved_by_workload_budget(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),request_burst=1),clock=lambda:0)
        await self.request(gate=gate)
        self.assertEqual((await self.request(scope('/health',method='GET'),gate=gate))[0],200)
    async def test_service_one_active_two_pending_across_paths(self):
        entered=asyncio.Event(); release=asyncio.Event(); active=0; peak=0
        async def held(s,receive,send):
            nonlocal active,peak
            active+=1; peak=max(peak,active); entered.set(); await release.wait(); active-=1
            await send({'type':'http.response.start','status':200,'headers':[]}); await send({'type':'http.response.body','body':b''})
        gate=ApiGuard(held,token_getter=lambda:TOKEN)
        tasks=[asyncio.create_task(self.request(scope('/v1/slice'),gate=gate))]; await entered.wait()
        tasks+=[asyncio.create_task(self.request(scope('/v1/project'),gate=gate)) for _ in range(2)]
        await asyncio.sleep(.02)
        before=self.reads
        self.assertEqual((await self.request(gate=gate))[0],429); self.assertEqual(self.reads,before)
        release.set(); self.assertTrue(all(item[0]==200 for item in await asyncio.gather(*tasks))); self.assertEqual(peak,1); self.assertEqual(gate.admitted,0)
    async def test_pending_queue_timeout_no_body_read(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),queue_seconds=.02))
        await gate.lock.acquire(); gate.admitted=1
        self.assertEqual((await self.request(gate=gate))[0],503); self.assertEqual(self.reads,0); self.assertEqual(gate.admitted,1); gate.lock.release()
    async def test_pending_cancel_no_admission_leak(self):
        await self.gate.lock.acquire(); self.gate.admitted=1
        task=asyncio.create_task(self.request()); await asyncio.sleep(.01); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.gate.admitted,1); self.assertEqual(self.gate.pending,0); self.gate.lock.release()
    async def test_spool_always_closed_after_failure(self):
        import tempfile
        original=tempfile.TemporaryFile; files=[]
        def create(*args,**kwargs):
            file=original(*args,**kwargs); files.append(file); return file
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),body_bytes=2))
        with patch('app.api_guard.tempfile.TemporaryFile',side_effect=create):
            for _ in range(5): self.assertEqual((await self.request(gate=gate))[0],413)
        self.assertTrue(files); self.assertTrue(all(file.closed for file in files)); self.assertEqual(self.calls,[])
    async def test_no_fd_growth_repeated_upload_rejection(self):
        before=len(os.listdir('/proc/self/fd'))
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),body_bytes=2))
        for _ in range(10): await self.request(gate=gate)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)
    async def test_replay_disconnection_continues_original_receive(self):
        async def app(s,receive,send):
            self.assertEqual((await receive())['body'],b'abc')
            self.assertEqual((await receive())['type'],'http.disconnect')
            await send({'type':'http.response.start','status':200,'headers':[]}); await send({'type':'http.response.body','body':b''})
        gate=ApiGuard(app,token_getter=lambda:TOKEN)
        self.assertEqual((await self.request(messages=[{'type':'http.request','body':b'abc'},{'type':'http.disconnect'}],gate=gate))[0],200)

    async def test_real_native_work_serialized_and_capacity_recovers(self):
        import sys,tempfile
        from pathlib import Path
        from app.native_supervisor import NativeSupervisor
        supervisor=NativeSupervisor(); peak=0; active=0; pids=[]
        with tempfile.TemporaryDirectory() as directory:
            async def native_app(s,receive,send):
                nonlocal active,peak
                async with supervisor.reservation(1):
                    active+=1; peak=max(peak,active)
                    result=await supervisor.run([sys.executable,'-c','import time; time.sleep(.05)'],cwd=Path(directory),timeout=1,env={})
                    self.assertEqual(result.returncode,0); pids.append(supervisor.last_execution['pid']); active-=1
                await send({'type':'http.response.start','status':200,'headers':[]}); await send({'type':'http.response.body','body':b''})
            gate=ApiGuard(native_app,token_getter=lambda:TOKEN)
            results=await asyncio.gather(*[self.request(scope(path),gate=gate) for path in ['/v1/slice','/v1/project','/v1/slice']])
            self.assertTrue(all(result[0]==200 for result in results)); self.assertEqual(peak,1)
            self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in pids))
            self.assertFalse(gate.lock.locked()); self.assertEqual(gate.admitted,0)
        await supervisor.shutdown()
    async def test_invalid_environment_limits_fail_closed(self):
        for name,value in [('FDM_MAX_PENDING_REQUESTS','999'),('FDM_BODY_TIMEOUT_SECONDS','nan'),('FDM_RESPONSE_TIMEOUT_SECONDS','inf'),('FDM_ADMISSION_TIMEOUT_SECONDS','inf'),('FDM_BYTE_BURST','9999999999999'),('FDM_REQUEST_BURST','0')]:
            with patch.dict(os.environ,{name:value}):
                with self.assertRaises(ValueError): GuardLimits.from_environment(50*1024*1024)
    async def test_empty_body_replayed_once(self):
        self.assertEqual((await self.request(messages=[{'type':'http.request','body':b''}]))[0],200)
    async def test_cancel_active_upload_closes_spool_and_releases(self):
        entered=asyncio.Event(); sent=[]
        async def stalled(): entered.set(); await asyncio.Future()
        task=asyncio.create_task(self.gate(scope(),stalled,self.sender(sent)))
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(self.gate.lock.locked()); self.assertEqual(self.gate.admitted,0)

    async def test_unavailable_native_rejected_before_body(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,unavailable_getter=lambda:True)
        self.assertEqual((await self.request(gate=gate))[0],503); self.assertEqual(self.reads,0)
        self.assertEqual((await self.request(scope('/health',method='GET'),gate=gate))[0],200)
    async def test_spool_creation_failure_is_bounded_structured(self):
        with patch('app.api_guard.tempfile.TemporaryFile',side_effect=OSError('full')):
            status,body=await self.request()
        self.assertEqual(status,503); self.assertIn(b'UPLOAD_STORAGE_UNAVAILABLE',body)
        self.assertFalse(self.gate.lock.locked()); self.assertEqual(self.gate.admitted,0)
        self.assertEqual(self.reads,0)

    def multipart(self,parts,boundary=b'bounded-test'):
        body=b''
        for name,filename,value in parts:
            disposition=b'Content-Disposition: form-data; name="'+name+b'"'
            if filename is not None: disposition+=b'; filename="'+filename+b'"'
            body+=b'--'+boundary+b'\r\n'+disposition+b'\r\n\r\n'+value+b'\r\n'
        return body+b'--'+boundary+b'--\r\n'
    async def multipart_request(self,body,boundary=b'bounded-test'):
        headers=scope()['headers']+[(b'content-type',b'multipart/form-data; boundary='+boundary)]
        return await self.request(scope(headers=headers),messages=[{'type':'http.request','body':body,'more_body':False}])
    async def test_multipart_valid_single_file_and_fields_replay(self):
        body=self.multipart([(b'file',b'synthetic.stl',b'synthetic-stl'),(b'quantity',None,b'1'),(b'verify',None,b'true')])
        self.assertEqual(await self.multipart_request(body),(200,body))
    async def test_multipart_file_count_rejected_before_app(self):
        body=self.multipart([(b'file',b'a.stl',b'a'),(b'file',b'b.stl',b'b')])
        status,payload=await self.multipart_request(body)
        self.assertEqual(status,413); self.assertIn(b'MULTIPART_PART_LIMIT',payload); self.assertEqual(self.calls,[])
    async def test_multipart_field_count_rejected_before_app(self):
        body=self.multipart([(str(index).encode(),None,b'value') for index in range(17)])
        self.assertEqual((await self.multipart_request(body))[0],413); self.assertEqual(self.calls,[])
    async def test_multipart_field_bytes_rejected(self):
        body=self.multipart([(b'field',None,b'x'*8193)])
        self.assertEqual((await self.multipart_request(body))[0],413); self.assertEqual(self.calls,[])
    async def test_multipart_part_headers_rejected(self):
        body=self.multipart([(b'file',b'x'*8193,b'stl')])
        status,payload=await self.multipart_request(body)
        self.assertEqual(status,413); self.assertIn(b'MULTIPART_HEADERS_TOO_LARGE',payload); self.assertEqual(self.calls,[])
    async def test_multipart_boundary_length_rejected(self):
        boundary=b'x'*201
        self.assertEqual((await self.multipart_request(self.multipart([],boundary),boundary))[0],400); self.assertEqual(self.calls,[])
    async def test_multipart_truncated_body_rejected(self):
        body=self.multipart([(b'file',b'a.stl',b'a')])
        self.assertEqual((await self.multipart_request(body[:-20]))[0],400); self.assertEqual(self.calls,[])
    async def test_multipart_duplicate_part_headers_rejected(self):
        body=b'--bounded-test\r\nContent-Disposition: form-data; name="field"\r\nContent-Disposition: form-data; name="other"\r\n\r\nx\r\n--bounded-test--\r\n'
        self.assertEqual((await self.multipart_request(body))[0],400); self.assertEqual(self.calls,[])
    async def test_alternative_form_encoding_rejected_before_receive(self):
        headers=scope()['headers']+[(b'content-type',b'application/x-www-form-urlencoded')]
        self.assertEqual((await self.request(scope(headers=headers)))[0],415); self.assertEqual(self.reads,0); self.assertEqual(self.calls,[])
    async def test_multipart_failures_do_not_leak_fds(self):
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(8):
            body=self.multipart([(b'file',b'a',b'a'),(b'file',b'b',b'b')])
            self.assertEqual((await self.multipart_request(body))[0],413)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before); self.assertEqual(self.calls,[])
    async def test_rejection_requests_connection_close(self):
        sent=[]
        async def forbidden(): raise AssertionError('body received')
        await self.gate(scope(headers=[]),forbidden,self.sender(sent))
        self.assertIn((b'connection',b'close'),sent[0]['headers'])

    async def test_response_deadline_closes_without_false_completion(self):
        sent=[]
        async def blocked_send(message):
            sent.append(message)
            if message['type']=='http.response.body': await asyncio.Future()
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),response_seconds=.02))
        async def receive(): return {'type':'http.request','body':b'abc','more_body':False}
        with self.assertRaisesRegex(RuntimeError,'response send deadline'):
            await gate(scope(),receive,blocked_send)
        self.assertEqual(sum(message['type']=='http.response.start' for message in sent),1)
        self.assertEqual(len(sent),2); self.assertFalse(gate.lock.locked()); self.assertEqual(gate.admitted,0)
    async def test_response_absolute_deadline_not_reset_each_chunk(self):
        sent=[]
        async def streaming(s,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            for _ in range(5):
                await send({'type':'http.response.body','body':b'part','more_body':True})
        async def slow_send(message):
            sent.append(message)
            if message['type']=='http.response.body': await asyncio.sleep(.015)
        gate=ApiGuard(streaming,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),response_seconds=.03))
        async def receive(): return {'type':'http.request','body':b'abc','more_body':False}
        with self.assertRaises(RuntimeError): await gate(scope(),receive,slow_send)
        self.assertLess(len(sent),6); self.assertFalse(gate.lock.locked())
        self.assertFalse(any(message.get('more_body') is False for message in sent))
    async def test_queued_request_poison_rechecked_before_receive(self):
        poisoned=[False]
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,unavailable_getter=lambda:poisoned[0])
        await gate.lock.acquire(); gate.admitted=1
        task=asyncio.create_task(self.request(gate=gate)); await asyncio.sleep(.01)
        poisoned[0]=True; gate.admitted-=1; gate.lock.release()
        self.assertEqual((await task)[0],503); self.assertEqual(self.reads,0)
        self.assertFalse(gate.lock.locked()); self.assertEqual(gate.admitted,0)

    async def test_invalid_authenticated_attempts_consume_global_budget(self):
        gate=ApiGuard(self.app,token_getter=lambda:TOKEN,limits=replace(GuardLimits(),request_burst=1),clock=lambda:0)
        self.assertEqual((await self.request(scope(headers=scope()['headers']+[(b'content-type',b'application/json')]),gate=gate))[0],415)
        self.assertEqual((await self.request(gate=gate))[0],429)
        self.assertEqual(self.reads,0)
        self.assertEqual(self.calls,[])


if __name__=='__main__': unittest.main()
