"""Fixed device-side gateway; no hardware adapter, cloud secret or arbitrary URL."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path

import httpx
import uvicorn

from ai_cat_controller.mini_gateway.app import create_app as business_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.motion import LocalMotionController
from ai_cat_controller.mini_gateway.product import LocalProductService
from ai_cat_controller.mini_gateway.dialog import LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_control import LocalDialogController
from ai_cat_controller.mini_gateway.preferences import LocalPreferencesService
from .app import create_app
from .configuration import load_configuration,check_existing_state,UPSTREAM
from .discovery import DiscoveryPublisher
from .protocol import parse_json
from .status_reader import DeviceStatusReader
from .store import LocalPairingStore

LOGGER=logging.getLogger(__name__)


def build_parser():
    parser=argparse.ArgumentParser(description='Device-local paired gateway; fixed encrypted LAN interface',allow_abbrev=False)
    parser.add_argument('--config-file',required=True,type=Path)
    parser.add_argument('--check',action='store_true',help='Read-only private configuration and existing state validation')
    return parser


async def verify_authority(config,*,transport=None):
    await asyncio.wait_for(_verify_authority(config,transport=transport),3)


async def _verify_authority(config,*,transport=None):
    async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=2.5,transport=transport) as client:
        async with client.stream('GET',UPSTREAM+'/api/v1/pets',headers={
            'X-API-Key':config.api_key,'X-Local-Gateway':config.local_delegation,
            'Accept':'application/json','Accept-Encoding':'identity'}) as response:
            if response.status_code!=200 or response.headers.get('content-encoding','identity') not in {'','identity'}:
                raise ValueError('Local owner authorization unavailable')
            raw=bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw)>131072:
                    raise ValueError('Local owner authorization unavailable')
    value=parse_json(bytes(raw),maximum=131072)
    if value.get('success') is not True or type(value.get('data')) is not list or len(value['data'])!=1:
        raise ValueError('Local owner authorization unavailable')
    pet=value['data'][0]
    if type(pet) is not dict or any(pet.get(field)!=expected for field,expected in {
            'pet_id':config.product_pet_id,'owner_user_id':config.owner_user_id,
            'serial_number':config.device_serial,'bound_at':config.binding_epoch}.items()):
        raise ValueError('Local owner authorization unavailable')
    # Internet/device online=false is deliberately not a reason to reject local
    # physical pairing: first-time Wi-Fi provisioning must work offline.


def _pending(resources):
    values=set()
    for resource in resources:
        tasks=getattr(resource,'_tasks',{})
        values.update(tasks.values() if isinstance(tasks,dict) else tasks)
        values.update(getattr(resource,'_stop_tasks',set()))
    return {task for task in values if not task.done()}


async def close_business_resources(resources,reader,*,timeout=12):
    deadline=asyncio.get_running_loop().time()+timeout
    while True:
        pending=_pending(resources)
        if not pending:
            break
        remaining=deadline-asyncio.get_running_loop().time()
        if remaining<=0:
            LOGGER.error('Local gateway shutdown has unsettled requests; ledgers were preserved, no forced close.')
            raise RuntimeError('Local gateway requests did not settle')
        await asyncio.wait(pending,timeout=remaining)
    for resource in reversed(resources):
        resource.close()
    reader.close()


def build_app(config):
    check_existing_state(config)
    store=LocalPairingStore(config.sessions_path,card=config.card,serial=config.device_serial)
    resources=[]; reader=None; provisioning=None
    discovery=DiscoveryPublisher(device_id=config.device_id,card_id=config.card_id,enabled=config.discovery)
    try:
        common=dict(upstream_url=UPSTREAM,api_key=config.api_key,expected_serial=config.device_serial,
            local_delegation=config.local_delegation,product_pet_id=config.product_pet_id)
        motion=LocalMotionController(db_path=config.state_dir/'motion.sqlite3',enabled=config.enabled['motion'],**common)
        resources.append(motion)
        product=LocalProductService(db_path=config.state_dir/'product.sqlite3',interactions_enabled=config.enabled['interactions'],**common)
        resources.append(product)
        reader=LocalDialogReader(**common)
        dialog=LocalDialogController(db_path=config.state_dir/'dialog.sqlite3',enabled=config.enabled['dialog'],reader=reader,**common)
        resources.append(dialog)
        preferences=LocalPreferencesService(db_path=config.state_dir/'preferences.sqlite3',
            settings_enabled=config.enabled['preferences'],feedback_enabled=config.enabled['feedback'],**common)
        resources.append(preferences)
        from .provisioning import LocalProvisioningCoordinator
        from .network_cues import NetworkCuePlayer
        provisioning=LocalProvisioningCoordinator(db_path=config.state_dir/'provisioning.sqlite3',device_id=config.device_id,
            notify=NetworkCuePlayer(), review_identity={'cardId': config.card_id,
                'userId': config.owner_user_id, 'bindingEpoch': config.binding_epoch})
        status_reader=DeviceStatusReader(api_key=config.api_key,local_delegation=config.local_delegation,
            expected_serial=config.device_serial)
        inner=business_app(MiniGatewayConfig(db_path=store.path,status_pet_id=store.pet_id),store=store,
            status_reader=status_reader,motion_controller=motion,product_service=product,
            dialog_reader=reader,dialog_controller=dialog,preferences_service=preferences)
        async def verify():
            await verify_authority(config)
        async def close():
            await close_business_resources(resources,reader)
        from .bootstrap import create_offline_bootstrap
        async def verify_setup():
            await verify()
            await status_reader.verify_network_idle()
        startup_hook=create_offline_bootstrap(config,provisioning,verify_setup)
        app=create_app(store=store,gateway=inner,verify_authority=verify,close_resources=close,
            provisioning=provisioning,startup_hook=startup_hook,verify_network_idle=status_reader.verify_network_idle,
            verify_network_quiescent=status_reader.verify_network_quiescent)
        previous_lifespan=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            try:
                await discovery.start()
                async with previous_lifespan(application):
                    yield
            finally:
                await discovery.aclose()
        app.router.lifespan_context=lifespan
        return app
    except Exception:
        if provisioning is not None:
            provisioning.close()
        for resource in reversed(resources):
            resource.close()
        if reader is not None:
            reader.close()
        store.close()
        raise


def main(argv=None):
    parser=build_parser(); args=parser.parse_args(argv)
    try:
        config=load_configuration(args.config_file)
        if args.check:
            count=check_existing_state(config)
            print('Local gateway configuration and existing private state verified; databases='+str(count)+'; network=0; services=0.')
            return 0
        app=build_app(config)
    except Exception:
        parser.exit(2,'Local gateway configuration or private state is invalid; details suppressed.\n')
    uvicorn.run(app,host='0.0.0.0',port=8789,access_log=False,proxy_headers=False,
        limit_concurrency=8,backlog=16,timeout_keep_alive=2,timeout_graceful_shutdown=20,
        h11_max_incomplete_event_size=8192,server_header=False)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
