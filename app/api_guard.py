"""Single-process ASGI admission, authentication and upload bounds before parsing."""
import asyncio
from dataclasses import dataclass
import json
import math
import os
import secrets
import tempfile
import time


def bounded_setting(name, default, minimum, maximum, *, integer=False):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a bounded number") from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its allowed bounds")
    return value


@dataclass(frozen=True)
class GuardLimits:
    body_bytes: int = 51 * 1024 * 1024
    body_seconds: float = 15
    response_seconds: float = 15
    pending: int = 2
    queue_seconds: float = 30
    requests_per_minute: float = 60
    request_burst: float = 30
    bytes_per_minute: float = 256 * 1024 * 1024
    byte_burst: float = 128 * 1024 * 1024

    @classmethod
    def from_environment(cls, max_upload_bytes):
        return cls(body_bytes=max_upload_bytes + 1024 * 1024,
            body_seconds=bounded_setting("FDM_BODY_TIMEOUT_SECONDS",15,1,30),
            response_seconds=bounded_setting("FDM_RESPONSE_TIMEOUT_SECONDS",15,1,30),
            pending=bounded_setting("FDM_MAX_PENDING_REQUESTS",2,0,4,integer=True),
            queue_seconds=bounded_setting("FDM_ADMISSION_TIMEOUT_SECONDS",30,1,30),
            requests_per_minute=bounded_setting("FDM_REQUESTS_PER_MINUTE",60,1,120),
            request_burst=bounded_setting("FDM_REQUEST_BURST",30,1,60),
            bytes_per_minute=bounded_setting("FDM_BYTES_PER_MINUTE",256*1024*1024,1024*1024,512*1024*1024),
            byte_burst=bounded_setting("FDM_BYTE_BURST",128*1024*1024,1024*1024,256*1024*1024))


class GateError(Exception):
    def __init__(self,status,code,message):
        self.status=status; self.code=code; self.message=message


class ApiGuard:
    def __init__(self,app,*,token_getter,limits=None,clock=time.monotonic,unavailable_getter=lambda:False):
        self.app=app; self.token_getter=token_getter; self.unavailable_getter=unavailable_getter; self.limits=limits or GuardLimits()
        self.clock=clock; self.lock=asyncio.Lock(); self.pending=0; self.admitted=0
        self.bad_updated=clock(); self.bad_attempts=40
        self.updated=clock(); self.requests=self.limits.request_burst; self.bytes=self.limits.byte_burst

    def charge(self,requests=0,byte_count=0):
        now=self.clock(); elapsed=max(0,now-self.updated); self.updated=now
        self.requests=min(self.limits.request_burst,self.requests+elapsed*self.limits.requests_per_minute/60)
        self.bytes=min(self.limits.byte_burst,self.bytes+elapsed*self.limits.bytes_per_minute/60)
        if self.requests < requests or self.bytes < byte_count:
            raise GateError(429,"SERVICE_BUDGET_EXHAUSTED","The service request or upload budget is exhausted. Retry later.")
        self.requests-=requests; self.bytes-=byte_count

    def unauthorized_attempt(self):
        now=self.clock(); elapsed=max(0,now-self.bad_updated); self.bad_updated=now
        self.bad_attempts=min(40,self.bad_attempts+elapsed*2)
        if self.bad_attempts<1:
            raise GateError(429,"AUTH_ATTEMPT_BUDGET_EXHAUSTED","Service authentication attempt budget exhausted.")
        self.bad_attempts-=1

    @staticmethod
    async def reject(send,error):
        payload=json.dumps({"detail":{"code":error.code,"message":error.message}},separators=(",",":")).encode()
        headers=[(b'connection',b'close'),(b'content-type',b'application/json'),(b'content-length',str(len(payload)).encode())]
        if error.status==401: headers.append((b'www-authenticate',b'Bearer'))
        if error.status in {429,503}: headers.append((b'retry-after',b'30'))
        await send({'type':'http.response.start','status':error.status,'headers':headers})
        await send({'type':'http.response.body','body':payload})

    def authenticate(self,headers):
        token=self.token_getter()
        if not token:
            raise GateError(503,"SERVICE_AUTH_UNCONFIGURED","Service authentication is not configured.")
        values=[value for name,value in headers if name.lower()==b'authorization']
        if len(values)!=1 or len(values[0])>512:
            raise GateError(401,"SERVICE_AUTH_REQUIRED","A valid service bearer credential is required.")
        try:
            scheme,separator,supplied=values[0].decode('ascii').partition(' ')
        except UnicodeDecodeError:
            raise GateError(401,"SERVICE_AUTH_REQUIRED","A valid service bearer credential is required.")
        if scheme.lower()!='bearer' or not separator or not supplied or supplied!=supplied.strip() or not secrets.compare_digest(supplied.encode("ascii"),token.encode("utf-8")):
            raise GateError(401,"SERVICE_AUTH_REQUIRED","A valid service bearer credential is required.")

    @staticmethod
    def validate_content_type(headers):
        values=[value for name,value in headers if name.lower()==b'content-type']
        if len(values)>1 or any(len(value)>4096 for value in values):
            raise GateError(400,"INVALID_MULTIPART","Ambiguous or oversized content type.")
        if values and not values[0].lower().startswith(b'multipart/form-data'):
            raise GateError(415,"UNSUPPORTED_BODY_TYPE","Manufacturing uploads require multipart/form-data.")

    def content_length(self,headers):
        values=[value for name,value in headers if name.lower()==b'content-length']
        transfer=[value for name,value in headers if name.lower()==b'transfer-encoding']
        if len(values)>1 or (values and transfer):
            raise GateError(400,"INVALID_BODY_FRAMING","Ambiguous request body framing.")
        if not values: return None
        raw=values[0]
        if not raw or len(raw)>20 or not raw.isdigit():
            raise GateError(400,"INVALID_BODY_FRAMING","Invalid request body length.")
        length=int(raw)
        if length>self.limits.body_bytes:
            raise GateError(413,"REQUEST_BODY_TOO_LARGE","Request body exceeds the service upload ceiling.")
        return length

    def multipart_preflight(self,spool,headers):
        content_types=[value for name,value in headers if name.lower()==b'content-type']
        if len(content_types)>1 or any(len(value)>4096 for value in content_types):
            raise GateError(400,"INVALID_MULTIPART","Ambiguous or oversized content type.")
        if not content_types or not content_types[0].lower().startswith(b'multipart/'):
            return
        from python_multipart.multipart import MultipartParser, parse_options_header
        try: media,options=parse_options_header(content_types[0])
        except (ValueError,UnicodeError,AssertionError):
            raise GateError(400,"INVALID_MULTIPART","Invalid multipart content type.")
        boundary=options.get(b'boundary',b'')
        if media!=b'multipart/form-data' or not 1<=len(boundary)<=200 or any(byte<32 or byte>126 for byte in boundary):
            raise GateError(400,"INVALID_MULTIPART","Invalid multipart boundary.")
        state={'files':0,'fields':0,'ended':False,'header_bytes':0,'header_field':b'','header_value':b'','headers':{},'part_bytes':0,'file':False}
        def part_begin():
            state.update(header_bytes=0,header_field=b'',header_value=b'',headers={},part_bytes=0,file=False)
        def header_field(data,start,end):
            size=end-start; state['header_bytes']+=size
            if state['header_bytes']>8192: raise GateError(413,"MULTIPART_HEADERS_TOO_LARGE","Multipart headers exceed their bound.")
            state['header_field']+=data[start:end]
        def header_value(data,start,end):
            size=end-start; state['header_bytes']+=size
            if state['header_bytes']>8192: raise GateError(413,"MULTIPART_HEADERS_TOO_LARGE","Multipart headers exceed their bound.")
            state['header_value']+=data[start:end]
        def header_end():
            name=state['header_field'].lower()
            if name in state['headers']: raise GateError(400,"INVALID_MULTIPART","Duplicate multipart part header.")
            state['headers'][name]=state['header_value']; state['header_field']=b''; state['header_value']=b''
        def headers_finished():
            try: disposition,options=parse_options_header(state['headers'].get(b'content-disposition',b''))
            except (ValueError,UnicodeError,AssertionError):
                raise GateError(400,"INVALID_MULTIPART","Invalid multipart disposition.")
            if disposition!=b'form-data' or not 1<=len(options.get(b'name',b''))<=128:
                raise GateError(400,"INVALID_MULTIPART","Invalid multipart part name.")
            state['file']=b'filename' in options
            if state['file']:
                state['files']+=1
                if state['files']>1: raise GateError(413,"MULTIPART_PART_LIMIT","Only one uploaded file is supported.")
                if options[b'name']!=b'file' or len(options[b'filename'])>255:
                    raise GateError(400,"INVALID_MULTIPART","Unsupported multipart file part.")
            else:
                state['fields']+=1
                if state['fields']>16: raise GateError(413,"MULTIPART_PART_LIMIT","Multipart field count exceeds its bound.")
        def part_data(data,start,end):
            state['part_bytes']+=end-start
            limit=self.limits.body_bytes-1024*1024 if state['file'] else 8192
            if state['part_bytes']>limit:
                raise GateError(413,"MULTIPART_PART_TOO_LARGE","Multipart part exceeds its bound.")
        def end(): state['ended']=True
        parser=MultipartParser(boundary,{'on_part_begin':part_begin,'on_header_field':header_field,
            'on_header_value':header_value,'on_header_end':header_end,'on_headers_finished':headers_finished,
            'on_part_data':part_data,'on_end':end},max_size=self.limits.body_bytes)
        spool.seek(0); deadline=self.clock()+5
        try:
            while chunk:=spool.read(64*1024):
                parser.write(chunk)
                if self.clock()>deadline: raise GateError(408,"MULTIPART_PARSE_TIMEOUT","Multipart parsing deadline expired.")
            parser.finalize()
        except GateError: raise
        except (ValueError,UnicodeError):
            raise GateError(400,"INVALID_MULTIPART","Malformed multipart body.")
        if not state['ended']:
            raise GateError(400,"INVALID_MULTIPART","Incomplete multipart body.")

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        path=scope.get('path','')
        root_path=scope.get('root_path','')
        if root_path and path.startswith(root_path+'/'):
            path=path[len(root_path):]
        protected=path.startswith('/v1/') or path=='/v1' or path.rstrip('/')=='/health'
        if not protected: return await self.app(scope,receive,send)
        try:
            try: self.authenticate(scope.get('headers',[]))
            except GateError as error:
                if error.status==401: self.unauthorized_attempt()
                raise
            if scope.get('method')!='POST' or not path.startswith('/v1/'):
                return await self.app(scope,receive,send)
            if self.unavailable_getter():
                raise GateError(503,"SERVICE_UNAVAILABLE","Native service requires operator reconciliation.")
            self.charge(requests=1)
            self.validate_content_type(scope.get('headers',[]))
            length=self.content_length(scope.get('headers',[]))
            if self.admitted>=self.limits.pending+1:
                raise GateError(429,"ADMISSION_QUEUE_FULL","The service admission queue is full.")
            self.admitted+=1
            self.pending+=1
            try:
                try: await asyncio.wait_for(self.lock.acquire(),self.limits.queue_seconds)
                except asyncio.TimeoutError:
                    raise GateError(503,"ADMISSION_QUEUE_TIMEOUT","The service admission queue wait expired.")
            except BaseException:
                self.admitted-=1
                raise
            finally: self.pending-=1
            try:
                if self.unavailable_getter():
                    raise GateError(503,"SERVICE_UNAVAILABLE","Native service requires operator reconciliation.")
                # Fully bounded spool avoids injecting arbitrary exceptions into
                # multipart parsing, which could otherwise leak upload files.
                try: spool_file=tempfile.TemporaryFile()
                except OSError:
                    raise GateError(503,"UPLOAD_STORAGE_UNAVAILABLE","Bounded upload storage is unavailable.")
                with spool_file as spool:
                    deadline=self.clock()+self.limits.body_seconds; size=0
                    while True:
                        remaining=deadline-self.clock()
                        if remaining<=0: raise GateError(408,"REQUEST_BODY_TIMEOUT","Request body deadline expired.")
                        try: message=await asyncio.wait_for(receive(),remaining)
                        except asyncio.TimeoutError:
                            raise GateError(408,"REQUEST_BODY_TIMEOUT","Request body deadline expired.")
                        if message['type']=='http.disconnect':
                            raise GateError(499,"REQUEST_CANCELLED","Request disconnected during upload.")
                        if message['type']!='http.request' or not isinstance(message.get('body',b''),bytes):
                            raise GateError(400,"INVALID_BODY_FRAMING","Unexpected request body data.")
                        body=message.get('body',b''); size+=len(body)
                        if size>self.limits.body_bytes:
                            raise GateError(413,"REQUEST_BODY_TOO_LARGE","Request body exceeds the service upload ceiling.")
                        self.charge(byte_count=len(body))
                        try: spool.write(body)
                        except OSError:
                            raise GateError(503,"UPLOAD_STORAGE_UNAVAILABLE","Bounded upload storage is unavailable.")
                        if self.clock()>deadline:
                            raise GateError(408,"REQUEST_BODY_TIMEOUT","Request body deadline expired.")
                        if not message.get('more_body',False): break
                    if length is not None and size!=length:
                        raise GateError(400,"INVALID_BODY_FRAMING","Request body length does not match its declaration.")
                    self.multipart_preflight(spool,scope.get('headers',[]))
                    spool.seek(0); replayed=0
                    async def replay():
                        nonlocal replayed
                        if replayed<size:
                            body=spool.read(64*1024); replayed+=len(body)
                            return {'type':'http.request','body':body,'more_body':replayed<size}
                        if size==0 and replayed==0:
                            replayed=1
                            return {'type':'http.request','body':b'','more_body':False}
                        return await receive()
                    response_deadline=None
                    response_started=False
                    async def bounded_send(message):
                        nonlocal response_deadline,response_started
                        if message['type']=='http.response.start':
                            if response_started:
                                raise RuntimeError("Duplicate protected response start.")
                            response_started=True
                            response_deadline=self.clock()+self.limits.response_seconds
                        if response_deadline is None:
                            raise RuntimeError("Protected response body preceded its headers.")
                        remaining=response_deadline-self.clock()
                        if remaining<=0:
                            raise RuntimeError("Protected response send deadline exceeded.")
                        try: await asyncio.wait_for(send(message),remaining)
                        except asyncio.TimeoutError:
                            # A partial response must be closed, never rewritten as
                            # an error or completed as though artifacts were valid.
                            raise RuntimeError("Protected response send deadline exceeded.")
                    await self.app(scope,replay,bounded_send)
            finally:
                self.lock.release()
                self.admitted-=1
        except GateError as error:
            try: await asyncio.wait_for(self.reject(send,error),self.limits.response_seconds)
            except asyncio.TimeoutError:
                raise RuntimeError("Protected rejection send deadline exceeded.")
