"""Strict private per-device configuration. No client-selected origin or secrets in repr."""
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import quote

from .protocol import Card

UPSTREAM = 'http://127.0.0.1:8000'
ENABLED_FIELDS = frozenset({'motion','interactions','dialog','preferences','feedback'})
CONFIG_FIELDS = frozenset({'v','device_serial','device_id','card_id','card_key','api_key',
    'local_delegation','product_pet_id','owner_user_id','binding_epoch','state_dir','enabled'})
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')


def require(value):
    if not value:
        raise ValueError('Private local gateway configuration is invalid')


def normalized_path(raw):
    require(type(raw) is str and raw.startswith('/') and
        not any(p in {'','.','..'} for p in raw.split('/')[1:]) and
        not any(ord(c)<32 or ord(c)==127 for c in raw))
    path=Path(raw)
    require(not any(p.is_symlink() for p in (path,*path.parents)))
    require(not any(p.lower() in {'miniprogram','wechat-miniprogram'} for p in path.parts))
    return path


def private_identity(path, *, directory=False):
    normalized_path(str(path))
    info=path.stat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
            info.st_uid==os.geteuid() and not info.st_mode & 0o077 and (directory or info.st_nlink==1))
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns


@dataclass(frozen=True)
class LocalGatewayConfiguration:
    device_serial:str
    device_id:str
    card_id:str
    product_pet_id:str
    owner_user_id:str
    binding_epoch:str
    state_dir:Path
    enabled:dict
    card_key:str=field(repr=False)
    api_key:str=field(repr=False)
    local_delegation:str=field(repr=False)
    discovery:bool=False

    @property
    def card(self):
        return Card(self.device_id,self.card_id,self.card_key)

    @property
    def sessions_path(self):
        return self.state_dir/'sessions.sqlite3'


def load_configuration(path):
    path=normalized_path(os.fspath(path))
    before=private_identity(path)
    require(0 < before[2] <= 8192)
    def unique(pairs):
        value={}
        for k,v in pairs:
            require(k not in value); value[k]=v
        return value
    try:
        with path.open('rb') as stream:
            raw=stream.read(8193)
        require(len(raw)<=8192)
        value=json.loads(raw,object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        require(private_identity(path)==before)
        require(type(value) is dict and CONFIG_FIELDS <= set(value) <= CONFIG_FIELDS|{'discovery'})
        require(type(value['v']) is int and value['v']==1)
        for name in ('product_pet_id','owner_user_id'):
            require(type(value[name]) is str and _ID.fullmatch(value[name]))
        require(type(value['device_serial']) is str and re.fullmatch(r'[A-Za-z0-9_-]{6,64}',value['device_serial']))
        Card(value['device_id'],value['card_id'],value['card_key'])
        require(type(value['api_key']) is str and 1 <= len(value['api_key']) <= 4096
            and all(33<=ord(c)<=126 for c in value['api_key']))
        require(type(value['local_delegation']) is str and re.fullmatch(r'lgd_[A-Za-z0-9_-]{43}',value['local_delegation']))
        epoch=value['binding_epoch']
        require(type(epoch) is str and 1 <= len(epoch) <= 64 and
            datetime.fromisoformat(epoch.replace('Z','+00:00')).tzinfo is not None)
        enabled=value['enabled']
        require(type(enabled) is dict and set(enabled)==ENABLED_FIELDS and all(type(v) is bool for v in enabled.values()))
        require(type(value.get('discovery',False)) is bool)
        state=normalized_path(value['state_dir']); private_identity(state,directory=True)
        return LocalGatewayConfiguration(**{k:v for k,v in value.items() if k not in {'v','state_dir'}},state_dir=state)
    except Exception:
        raise ValueError('Private local gateway configuration is invalid') from None


@contextmanager
def _readonly(path):
    before=private_identity(path)
    require(not any(Path(str(path)+s).exists() for s in ('-wal','-shm')))
    with closing(sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro&immutable=1',uri=True,timeout=2)) as connection:
        connection.execute('PRAGMA query_only=ON')
        require(connection.execute('PRAGMA integrity_check').fetchone()==('ok',))
        require(connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete')
        require(not connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone())
        require(private_identity(path)==before)
        yield connection
        require(private_identity(path)==before)


def check_existing_state(config):
    """Read only: never create lock files/ledgers, repair counters or mark requests unknown."""
    private_identity(config.state_dir,directory=True)
    pins={'schema':'local_pairing_v1','device':config.device_id,'card':config.card_id,
        'serial':hashlib.sha256(config.device_serial.encode()).hexdigest(),
        'key':hashlib.sha256(config.card_key.encode()).hexdigest()}
    with _readonly(config.sessions_path) as c:
        require({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}=={'lg_meta','lg_sessions','lg_counter'})
        require(dict(c.execute('SELECT key,value FROM lg_meta'))==pins)
        counters=c.execute('SELECT id,value FROM lg_counter').fetchall()
        require(len(counters)==1 and counters[0][0]==1 and type(counters[0][1]) is int and 0<=counters[0][1]<2**63-1)
    device=hashlib.sha256(config.device_serial.encode()).hexdigest()
    product=hashlib.sha256(json.dumps([config.device_serial,config.product_pet_id],separators=(',',':')).encode()).hexdigest()
    business=hashlib.sha256(json.dumps([UPSTREAM,config.device_serial,config.product_pet_id],separators=(',',':')).encode()).hexdigest()
    owner=hashlib.sha256(config.owner_user_id.encode()).hexdigest()
    specs={
        'motion.sqlite3':({'mm_meta','mm_intents'},'mm_meta',{'schema':'mini_motion_v1','device':device}),
        'product.sqlite3':({'product_meta','product_receipts'},'product_meta',{'schema':'mini_product_v1','device':product}),
        'dialog.sqlite3':({'dc_meta','dc_requests'},'dc_meta',{'schema':'dialog_control_v1','pin':business}),
        'preferences.sqlite3':({'pf_meta','pf_requests','pf_steps'},'pf_meta',{'schema':'preferences_v1','pin':business}),
        'provisioning.sqlite3':({'lp_meta','lp_requests'},'lp_meta',{'schema':'local_provisioning_v1','device':config.device_id}),
    }
    checked=1
    for filename,(tables,meta,expected) in specs.items():
        path=config.state_dir/filename
        if not path.exists():
            require(not path.is_symlink())
            if filename == 'provisioning.sqlite3':
                require(not (config.state_dir/'provision-reviews').exists() and not (config.state_dir/'provision-reviews').is_symlink())
            continue
        with _readonly(path) as c:
            require({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}==tables)
            actual=dict(c.execute('SELECT key,value FROM '+meta))
            require(all(actual.get(k)==v for k,v in expected.items()))
            require('owner' not in actual or actual['owner']==owner)
            if filename == 'provisioning.sqlite3':
                from .provision_reviews import ProvisionReviewLedger
                c.row_factory=sqlite3.Row
                rows={row['request_id']:dict(row) for row in c.execute('SELECT * FROM lp_requests')}
                ProvisionReviewLedger(config.state_dir/'provision-reviews',device_id=config.device_id,
                    review_identity={'cardId':config.card_id,'userId':config.owner_user_id,
                                     'bindingEpoch':config.binding_epoch}).read(rows)
        checked+=1
    return checked
