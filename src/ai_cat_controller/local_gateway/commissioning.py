"""Explicit, once-only local commissioning with an existing genuine owner.

This operator tool neither signs a mock login nor assigns ownership. A real
owner session is checked against the still-running fixed local controller.
Secrets are written only to new private files, never stdout or argv. There is
no radio, motion, dialog or service-start side effect.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

import httpx

from ai_cat_controller.mini_gateway.cli import read_device_api_key
from .configuration import load_configuration, private_identity
from .delegation import DelegationStore, _binding
from .protocol import Card, canonical, parse_json, require
from .store import LocalPairingStore


def _write_new(path, raw):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def commission(*, state_root, api_key, owner_session, product_pet_id, serial, transport=None, offline_setup=False):
    require(isinstance(serial, str) and 6 <= len(serial) <= 64)
    require(isinstance(api_key, str) and 1 <= len(api_key) <= 4096 and all(33 <= ord(c) <= 126 for c in api_key))
    require(isinstance(owner_session, str) and 16 <= len(owner_session) <= 256)
    require(type(offline_setup) is bool)
    root = Path(state_root).absolute()
    require(not any(p.is_symlink() for p in (root, *root.parents)))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_identity(root, directory=True)
    config_path = root / 'current.json'
    require(not config_path.exists() and not config_path.is_symlink())

    async def current_binding(client):
        async with client.stream('GET', 'http://127.0.0.1:8000/api/v1/pets',
                headers={'X-API-Key': api_key, 'X-Mock-Session': owner_session,
                         'Accept': 'application/json', 'Accept-Encoding': 'identity'}) as response:
            require(response.status_code == 200)
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                require(len(raw) <= 131072)
        envelope = parse_json(bytes(raw), maximum=131072)
        require(envelope.get('success') is True and type(envelope.get('data')) is list)
        matches = [p for p in envelope['data'] if type(p) is dict and p.get('pet_id') == product_pet_id]
        require(len(matches) == 1 and matches[0].get('serial_number') == serial)
        value = {key: matches[0].get(key) for key in ('owner_user_id','pet_id','serial_number','bound_at')}
        _binding(value)
        return value

    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=4) as client:
        binding = await current_binding(client)
        device_id = 'dev_' + hashlib.sha256(serial.encode('ascii')).hexdigest()[:32]
        card = Card(device_id, secrets.token_hex(16), secrets.token_hex(32))
        state = root / card.card_id
        state.mkdir(mode=0o700)
        sessions = LocalPairingStore(state/'sessions.sqlite3', card=card, serial=serial, initialize=True)
        sessions.close()
        require(await current_binding(client) == binding)
        # This private operator invocation is itself the explicitly authorized
        # install action. Existing owner binding has just been verified twice.
        grant = DelegationStore(root/'delegation.sqlite3').issue(binding)
        config = dict(v=1, device_serial=serial, device_id=device_id, card_id=card.card_id,
            card_key=card.key_hex, api_key=api_key, local_delegation=grant,
            product_pet_id=product_pet_id, owner_user_id=binding['owner_user_id'],
            binding_epoch=binding['bound_at'], state_dir=str(state),
            enabled=dict(motion=True, interactions=True, dialog=True, preferences=True, feedback=True), discovery=True)
        # Publish config only after all private artifacts and the card exist.
        # A failed commissioning never overwrites/removes an existing setup.
        import qrcode
        from qrcode.image.svg import SvgPathImage
        image = qrcode.make(card.encode(), image_factory=SvgPathImage, border=4, box_size=10)
        _write_new(state/'device-card.svg', image.to_string())
        _write_new(state/'offline-first-setup.json', canonical({'v':1,'enabled':offline_setup}))
        _write_new(config_path, canonical(config))
        loaded = load_configuration(config_path)
        require(loaded.device_serial == serial and loaded.card == card)
    return dict(commissioned=True, deviceId=device_id, configFile=str(config_path),
                cardFile=str(state/'device-card.svg'), stateDirectory=str(state),
                network_changed=False, business_commands=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Private once-only local control commissioning; no radio/service actions', allow_abbrev=False)
    parser.add_argument('--confirm-install-local-control', required=True, action='store_true')
    parser.add_argument('--owner-authorization-stdin', required=True, action='store_true')
    parser.add_argument('--device-env-file', required=True, type=Path)
    parser.add_argument('--state-root', type=Path, default=Path('/var/lib/ai-cat-local'))
    parser.add_argument('--enable-offline-first-setup', action='store_true',
        help='Explicitly allow one short setup hotspot on a boot confirmed disconnected; never on ordinary runtime drops')
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0)
        raw = sys.stdin.buffer.read(8193)
        authorization = parse_json(raw, maximum=8192)
        require(set(authorization) == {'session_token','pet_id'})
        key = read_device_api_key(args.device_env_file)
        serial = Path('/sys/firmware/devicetree/base/serial-number').read_bytes().rstrip(b'\0').decode('ascii')
        result = asyncio.run(commission(state_root=args.state_root, api_key=key,
            owner_session=authorization['session_token'], product_pet_id=authorization['pet_id'], serial=serial,
            offline_setup=args.enable_offline_first_setup))
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps(dict(commissioned=False, private_details_printed=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
