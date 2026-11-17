"""Encrypted-only public façade around the existing bounded business gateway.

No raw /v1 routes or plaintext credential/login/control endpoints are exposed.
An owner-authorized, physical per-device card is required before pairing.
"""
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import re

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from starlette.background import BackgroundTask
import httpx

from ai_cat_controller.mini_gateway.auth import utc_expiry
from .protocol import ChallengeBroker, MAX_PACKET, MAX_RESPONSE, LocalProtocolError, canonical, exact, parse_json, require

_ID = r'[A-Za-z0-9_-]{1,128}'
_BASE = r'/v1/pets/' + _ID
_GET = re.compile(r'(?:/v1/session|/v1/pets|' + _BASE + r'/(?:status|motion(?:/history)?|dashboard|intimacy|growth|preferences(?:/requests/' + _ID + r')?|dialog(?:/control|/requests/' + _ID + r'|/conversations(?:/since/[0-9a-f]{64}|/' + _ID + r'(?:/since/[0-9a-f]{64})?)?)?))')
_POST = re.compile(r'(?:/v1/auth/logout|' + _BASE + r'/(?:motion/(?:execute|stop)|interactions|dialog/commands|preferences/commands))')


def allowed_route(method, path):
    return isinstance(path, str) and bool((_GET if method == 'GET' else _POST if method == 'POST' else re.compile(r'(?!)')).fullmatch(path))


def _error(status=400):
    return JSONResponse({'success': False, 'message': 'Local request was not verified'}, status_code=status,
        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def create_app(*, store, gateway, verify_authority, close_resources=None, provisioning=None, startup_hook=None,
               verify_network_idle=None, verify_network_quiescent=None):
    broker = ChallengeBroker(store)
    slots = asyncio.Semaphore(8)
    network_tasks = set()
    closing = False

    async def launch_provision(request_id):
        if closing:
            return
        async def guard():
            await verify_authority()
            if verify_network_idle is not None:
                await verify_network_idle()
            return True
        task = asyncio.create_task(provisioning.start(request_id, offline_guard=guard))
        network_tasks.add(task)
        def finished(done):
            network_tasks.discard(done)
            if not done.cancelled():
                done.exception()  # never send raw network/credential failures to logs
        task.add_done_callback(finished)

    @asynccontextmanager
    async def lifespan(_):
        nonlocal closing
        async with gateway.router.lifespan_context(gateway):
            startup_task = asyncio.create_task(startup_hook.run()) if startup_hook is not None else None
            try:
                yield
            finally:
                closing = True
                if startup_hook is not None:
                    await startup_hook.aclose()
                if provisioning is not None:
                    await provisioning.aclose()
                if network_tasks:
                    await asyncio.gather(*tuple(network_tasks), return_exceptions=True)
                if startup_task is not None:
                    await startup_task
                if close_resources is not None:
                    await close_resources()
                store.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware('http')
    async def bounded(request, call_next):
        try:
            require(request.method in {'GET', 'POST'} and not request.scope.get('query_string'))
            require(len(request.scope['headers']) <= 40 and sum(len(k)+len(v)+4 for k,v in request.scope['headers']) <= 8192)
            for name in (b'content-length', b'host', b'content-type'):
                require(sum(k.lower() == name for k,v in request.scope['headers']) <= 1)
            require('transfer-encoding' not in request.headers and 'authorization' not in request.headers)
            length = request.headers.get('content-length', '0')
            require(re.fullmatch(r'[0-9]{1,8}', length) and int(length) <= MAX_PACKET)
            require(request.method != 'GET' or length == '0')
            require(request.url.path in {'/local/health', '/local/v1/packet'} or
                re.fullmatch(r'/local/v1/challenge/[0-9a-f]{32}', request.url.path))
            peer = ipaddress.ip_address(request.client.host)
            require(peer.is_loopback or peer.version == 4 and any(peer in ipaddress.ip_network(n)
                for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))
            require(not slots.locked())
            async with slots:
                response = await asyncio.wait_for(call_next(request), 20 if request.url.path == '/local/v1/packet' else 15)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        except Exception:
            return _error()

    def health():
        return dict(mode='local-device', wechatConfigured=False, localConfigured=True,
            readOnly=not bool(getattr(gateway.state.motion_controller, 'enabled', False)))

    @app.get('/local/health')
    async def public_health():
        return {'success': True, 'data': health()}

    @app.get('/local/v1/challenge/{request_id}')
    async def challenge(request: Request, request_id: str):
        try:
            return {'success': True, 'data': broker.issue(request.client.host, request_id)}
        except Exception:
            return _error(429)

    async def dispatch(value):
        exact(value, ['method', 'path', 'body', 'accessToken'])
        method, path, body, token = (value[k] for k in ('method','path','body','accessToken'))
        require(method in {'GET', 'POST'} and (body is None or type(body) is dict))
        require(token is None or type(token) is str and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token))
        require(method != 'GET' or body is None)
        if method == 'GET' and path == '/health':
            require(token is None)
            return 200, {'success': True, 'data': health()}, None
        if method == 'POST' and path == '/local/pair':
            exact(body, ['confirmation'])
            require(body['confirmation'] == 'pair-local-device' and token is None)
            await verify_authority()
            issued = await asyncio.to_thread(store.issue_local_session)
            return 200, {'success': True, 'data': dict(userId=issued.user_id, expiresAt=utc_expiry(issued.expires_at),
                accessToken=issued.access_token, ttlSeconds=3600)}, None
        if provisioning is not None and path in {'/local/provision/prepare','/local/provision/status',
                '/local/provision/receipt','/local/provision/resume','/local/provision/current',
                '/local/provision/archive-status','/local/provision/archive-prepare','/local/provision/archive-confirm'}:
            require(method == 'POST' and token is not None)
            principal = await asyncio.to_thread(store.resolve_session, token, '')
            require(principal is not None and principal.user_id == store.user_id)
            if path.endswith('/archive-confirm'):
                exact(body, ['requestId', 'windowId', 'networkRequestId', 'reviewToken', 'acknowledgements', 'confirmation'])
                require(body['confirmation'] == 'archive-unknown-provisioning')
                require(verify_network_idle is not None and verify_network_quiescent is not None)
                # Includes the coordinator's idempotent already-appended path,
                # which intentionally does not re-run the quiescence callback.
                await verify_authority()
                async def review_guard():
                    await verify_network_idle()
                    await verify_network_quiescent()
                    # Recheck existing owner after all live checks, directly
                    # before the coordinator's durable append. No network write.
                    await verify_authority()
                    return True
                result = await provisioning.confirm_archive(body['requestId'],
                    review_token=body['reviewToken'], window_id=body['windowId'],
                    network_request_id=body['networkRequestId'], acks=body['acknowledgements'], guard=review_guard)
                return 200, {'success': True, 'data': result}, None
            await verify_authority()
            require(type(body) is dict)
            if path.endswith('/archive-status'):
                exact(body, ['requestId'])
                result = await provisioning.archive_status(body['requestId'])
                if result['eligible']:
                    try:
                        require(verify_network_idle is not None and verify_network_quiescent is not None)
                        await verify_network_idle()
                        await verify_network_quiescent()
                    except Exception:
                        result = {**result, 'eligible': False}
                return 200, {'success': True, 'data': result}, None
            if path.endswith('/archive-prepare'):
                exact(body, ['requestId', 'windowId', 'networkRequestId', 'acknowledgements'])
                require(verify_network_idle is not None and verify_network_quiescent is not None)
                result = await provisioning.prepare_archive(body['requestId'], window_id=body['windowId'],
                    network_request_id=body['networkRequestId'], acks=body['acknowledgements'])
                return 200, {'success': True, 'data': result}, None
            if path.endswith('/current'):
                exact(body, [])
                return 200, {'success': True, 'data': await provisioning.current_window()}, None
            if path.endswith('/prepare'):
                exact(body, ['requestId','confirmation'])
                if verify_network_idle is not None:
                    await verify_network_idle()
                result = await provisioning.prepare(body['requestId'], confirmation=body['confirmation'])
                task = BackgroundTask(launch_provision, body['requestId']) if result['status'] == 'prepared' else None
                return 200, {'success': True, 'data': result}, task
            exact(body, ['requestId'])
            method = provisioning.resume if path.endswith('/resume') else provisioning.get_receipt if path.endswith('/receipt') else provisioning.status
            result = await method(body['requestId'])
            return 200, {'success': True, 'data': result}, None
        require(allowed_route(method, path) and token is not None)
        if path != '/v1/auth/logout':
            # Even static session/pet metadata must stop working immediately
            # when the existing owner binding/delegation is revoked.
            await verify_authority()
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url='http://127.0.0.1', timeout=12) as client:
            kwargs = {'headers': headers, **({'json': body} if body is not None else {})}
            response = await client.request(method, path, **kwargs)
            require(response.status_code in range(200,300) or response.status_code in range(400,600))
            require(len(response.content) <= MAX_RESPONSE)
            payload = parse_json(response.content, maximum=MAX_RESPONSE)
            return response.status_code, payload, None

    @app.post('/local/v1/packet')
    async def packet(request: Request):
        try:
            require(request.headers.get('content-type', '').lower() in {'application/json','application/json; charset=utf-8'})
            raw = bytearray()
            async for part in request.stream():
                raw.extend(part)
                require(len(raw) <= MAX_PACKET)
            frame = parse_json(bytes(raw))
            value = broker.consume(frame)
        except Exception:
            return _error(403)
        background = None
        try:
            timeout = 18 if value.get('path') == '/local/provision/archive-confirm' else 12
            status, payload, background = await asyncio.wait_for(dispatch(value), timeout)
        except Exception:
            status, payload = 503, {'success': False, 'message': 'Local service temporarily unavailable'}
        try:
            encrypted = broker.reply(frame, {'status': status, 'body': payload})
            return JSONResponse(encrypted, background=background)
        except Exception:
            return _error(503)

    return app
