import asyncio
import socket
import struct

import pytest

from ai_cat_controller.local_gateway.discovery import DiscoveryPublisher,PythonZeroconfPublisher,SERVICE_TYPE,wlan_ipv4


class MissingFallback:
    def __init__(self,**kwargs): pass
    async def start(self): return False
    async def aclose(self): pass


def test_discovery_constructor_is_dormant_and_metadata_has_no_key():
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,enabled=True)
    assert value._process is None and SERVICE_TYPE=='_aicat-local._tcp.'


@pytest.mark.asyncio
async def test_disabled_discovery_never_constructs_process():
    async def forbidden(*args,**kwargs): raise AssertionError('No discovery side effects')
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,spawn=forbidden)
    assert await value.start() is False
    await value.aclose()


@pytest.mark.asyncio
async def test_missing_optional_avahi_is_fixed_warning_and_not_gateway_failure(monkeypatch,caplog):
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.Path.is_file',lambda _:False)
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,enabled=True,fallback_factory=MissingFallback)
    assert await value.start() is False
    assert 'use the device LAN IP manually' in caplog.text


@pytest.mark.asyncio
async def test_existing_publisher_uses_fixed_type_port_and_only_public_metadata(monkeypatch):
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.Path.is_file',lambda _:True)
    calls=[]
    class Process:
        returncode=None
        def __init__(self,activation=False): self.activation=activation; self.done=asyncio.Event()
        async def wait(self):
            if self.activation: self.returncode=0; return 0
            await self.done.wait(); self.returncode=0; return 0
        def terminate(self): self.done.set()
        def kill(self): self.done.set()
    async def spawn(*args,**kwargs):
        calls.append(args)
        return Process(activation=args[0].endswith('busctl'))
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,enabled=True,spawn=spawn)
    assert await value.start() is True
    assert len(calls)==2 and calls[0][-2:]==('org.freedesktop.Avahi','0')
    assert calls[1][2:5]==('AI-CAT-aaaaaaaa','_aicat-local._tcp','8789')
    assert set(calls[1][5:])=={'v=1','deviceId=dev_'+'a'*32,'cardId='+'b'*32}
    await value.aclose(); assert value._process is None


def fake_library(events,*,register_error=None):
    class Info:
        def __init__(self,*args,**kwargs):
            self.args,self.kwargs=args,kwargs
    class Client:
        def __init__(self,**kwargs):
            events.append(('client',kwargs))
        async def async_register_service(self,info,**kwargs):
            events.append(('register',info,kwargs))
            if register_error is not None: raise register_error
            async def announcement(): events.append(('announce',))
            return asyncio.create_task(announcement())
        async def async_unregister_service(self,info): events.append(('unregister',info))
        async def async_close(self): events.append(('close',))
    return Client,Info,'v4-only'


def python_publisher(events,address_reader,*,register_error=None):
    value=PythonZeroconfPublisher(device_id='dev_'+'a'*32,card_id='b'*32,address_reader=address_reader)
    value._factory,value._info_factory,value._ip_version=fake_library(events,register_error=register_error)
    return value


def test_python_constructor_never_loads_library_or_reads_interface():
    def forbidden(): raise AssertionError('Dormant constructor')
    value=PythonZeroconfPublisher(device_id='dev_'+'a'*32,card_id='b'*32,address_reader=forbidden,loader=forbidden)
    assert value._runner is None and value._zeroconf is None


@pytest.mark.asyncio
async def test_python_publisher_uses_one_ipv4_and_only_public_metadata():
    events=[]
    value=python_publisher(events,lambda:'192.168.1.152')
    await value._step()
    assert events[0]==('client',{'interfaces':['192.168.1.152'],'ip_version':'v4-only'})
    _,info,kwargs=events[1]
    assert info.args==('_aicat-local._tcp.local.','AI-CAT-aaaaaaaa._aicat-local._tcp.local.')
    assert kwargs=={'allow_name_change':False}
    assert info.kwargs=={'addresses':[socket.inet_aton('192.168.1.152')],'port':8789,
        'properties':{b'v':b'1',b'deviceId':b'dev_'+b'a'*32,b'cardId':b'b'*32},
        'server':'aicat-aaaaaaaa.local.','host_ttl':30,'other_ttl':30}
    assert events[-1]==('announce',)
    await value._withdraw()
    assert [event[0] for event in events][-2:]==['unregister','close']


@pytest.mark.asyncio
async def test_python_publisher_withdraws_before_changed_interface_and_when_offline():
    events=[]; address=['192.168.1.152']
    value=python_publisher(events,lambda:address[0])
    await value._step(); await value._step()
    assert [event[0] for event in events].count('client')==1
    address[0]='10.47.0.1'; await value._step()
    assert [event[0] for event in events]==['client','register','announce','unregister','close','client','register','announce']
    address[0]=None; await value._step()
    assert [event[0] for event in events][-2:]==['unregister','close']
    assert value._zeroconf is None


@pytest.mark.asyncio
@pytest.mark.parametrize('address',['8.8.8.8','127.0.0.1','169.254.1.2','::1','not-an-address'])
async def test_python_publisher_does_not_publish_invalid_or_non_lan_addresses(address):
    events=[]; value=python_publisher(events,lambda:address)
    await value._step()
    assert not events


@pytest.mark.asyncio
async def test_python_registration_failure_closes_and_does_not_loop_retry_same_ip(caplog):
    events=[]; value=python_publisher(events,lambda:'192.168.1.152',register_error=ValueError('private-detail'))
    await value._step(); await value._step()
    assert [event[0] for event in events]==['client','register','unregister','close']
    assert 'use the device LAN IP manually' in caplog.text and 'private-detail' not in caplog.text


@pytest.mark.asyncio
async def test_python_start_and_stop_while_polling_are_bounded():
    events=[]
    value=PythonZeroconfPublisher(device_id='dev_'+'a'*32,card_id='b'*32,
        address_reader=lambda:'192.168.1.152',loader=lambda:fake_library(events),interval=300)
    assert await value.start()
    for _ in range(10):
        if value._info is not None: break
        await asyncio.sleep(0)
    await asyncio.wait_for(value.aclose(),1)
    assert value._runner is None and value._zeroconf is None
    assert events[-1]==('close',)


@pytest.mark.asyncio
async def test_missing_avahi_uses_python_without_starting_daemon(monkeypatch):
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.Path.is_file',lambda _:False)
    events=[]
    class Fallback:
        def __init__(self,**kwargs): events.append(kwargs)
        async def start(self): events.append('start'); return True
        async def aclose(self): events.append('close')
    async def forbidden(*args,**kwargs): raise AssertionError('No system changes')
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,enabled=True,spawn=forbidden,fallback_factory=Fallback)
    assert await value.start()
    await value.aclose()
    assert events==[{'device_id':'dev_'+'a'*32,'card_id':'b'*32},'start','close']


@pytest.mark.asyncio
async def test_missing_python_dependency_and_constructor_error_do_not_fail_gateway(monkeypatch,caplog):
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.Path.is_file',lambda _:False)
    def broken(**kwargs): raise ImportError('do-not-log-this')
    value=DiscoveryPublisher(device_id='dev_'+'a'*32,card_id='b'*32,enabled=True,fallback_factory=broken)
    assert not await value.start()
    await value.aclose()
    assert 'use the device LAN IP manually' in caplog.text and 'do-not-log-this' not in caplog.text


@pytest.mark.parametrize('up',[True,False])
def test_wlan_ipv4_uses_only_local_fixed_wlan0_ioctl(monkeypatch,up):
    events=[]
    class LocalSocket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fileno(self): return 9
    def fake_socket(*args):
        assert args==(socket.AF_INET,socket.SOCK_DGRAM)
        return LocalSocket()
    def ioctl(fd,operation,request):
        assert fd==9 and request==struct.pack('256s',b'wlan0')
        events.append(operation)
        response=bytearray(256)
        if operation==0x8913: struct.pack_into('H',response,16,int(up))
        elif operation==0x8915: response[20:24]=socket.inet_aton('10.47.0.1')
        else: raise AssertionError('Unexpected ioctl')
        return bytes(response)
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.socket.socket',fake_socket)
    monkeypatch.setattr('ai_cat_controller.local_gateway.discovery.fcntl.ioctl',ioctl)
    assert wlan_ipv4()==('10.47.0.1' if up else None)
    assert events==([0x8913,0x8915] if up else [0x8913])
