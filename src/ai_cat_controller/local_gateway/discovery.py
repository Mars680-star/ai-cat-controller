"""Optional fixed WLAN publisher; no service browsing or address-range scan.

The publisher maintains one persistent D-Bus connection for its entry group.
Using separate busctl EntryGroupNew/AddService calls would lose the group when
the first short-lived connection exits, so only activation uses busctl. When
system Avahi tools are absent, optional pure-Python zeroconf publishes only on
the current wlan0 IPv4, withdrawing and reopening on address changes.
"""
import asyncio
import fcntl
import logging
from pathlib import Path
import socket
import struct

from .protocol import validate_address

LOGGER=logging.getLogger(__name__)
SERVICE_TYPE='_aicat-local._tcp.'
_PUBLISHER='/usr/bin/avahi-publish-service'
_BUSCTL='/usr/bin/busctl'


def wlan_ipv4():
    """Local kernel ioctl only. Never enumerate remote hosts or change Wi-Fi."""
    try:
        request=struct.pack('256s',b'wlan0')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as connection:
            flags=fcntl.ioctl(connection.fileno(),0x8913,request)
            if not struct.unpack_from('H',flags,16)[0] & 1:
                return None
            value=fcntl.ioctl(connection.fileno(),0x8915,request)
        return validate_address(socket.inet_ntoa(value[20:24]))
    except (OSError,ValueError):
        return None


def _zeroconf_library():
    from zeroconf import IPVersion,ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf
    return AsyncZeroconf,ServiceInfo,IPVersion.V4Only


class PythonZeroconfPublisher:
    def __init__(self,*,device_id,card_id,address_reader=wlan_ipv4,loader=_zeroconf_library,interval=2):
        self.device_id,self.card_id=device_id,card_id
        self._address_reader,self._loader,self._interval=address_reader,loader,interval
        self._runner,self._zeroconf,self._info=None,None,None
        self._stop=asyncio.Event();self._attempted=None

    async def start(self):
        if self._runner is not None:
            return not self._runner.done()
        try:
            self._factory,self._info_factory,self._ip_version=self._loader()
        except Exception:
            return False
        self._runner=asyncio.create_task(self._run())
        return True

    async def _invoke(self,operation,*,timeout=2):
        result=await asyncio.wait_for(operation,timeout)
        # Current zeroconf async registration APIs return the announcement
        # task after registration; await that too without an unbounded stop.
        if hasattr(result,'__await__'):
            await asyncio.wait_for(result,timeout)

    async def _withdraw(self):
        current,info=self._zeroconf,self._info
        self._zeroconf,self._info=None,None
        if current is None:
            return
        try:
            if info is not None:
                await self._invoke(current.async_unregister_service(info),timeout=1)
        except Exception:
            pass
        try:
            await asyncio.wait_for(current.async_close(),2)
        except Exception:
            LOGGER.warning('Local discovery cleanup incomplete; its short-lived WLAN records will expire.')

    async def _step(self):
        try:
            address=self._address_reader()
            if address is not None:
                address=validate_address(address)
        except Exception:
            address=None
        if address==self._attempted:
            return
        await self._withdraw()
        self._attempted=address
        if address is None or self._stop.is_set():
            return
        try:
            wire_type=SERVICE_TYPE+'local.'
            self._zeroconf=self._factory(interfaces=[address],ip_version=self._ip_version)
            self._info=self._info_factory(wire_type,'AI-CAT-'+self.device_id[-8:]+'.'+wire_type,
                addresses=[socket.inet_aton(address)],port=8789,
                properties={b'v':b'1',b'deviceId':self.device_id.encode('ascii'),b'cardId':self.card_id.encode('ascii')},
                server='aicat-'+self.device_id[-8:]+'.local.',host_ttl=30,other_ttl=30)
            await self._invoke(self._zeroconf.async_register_service(self._info,allow_name_change=False))
        except Exception:
            await self._withdraw()
            LOGGER.warning('Local discovery registration unavailable; use the device LAN IP manually.')

    async def _run(self):
        try:
            while not self._stop.is_set():
                await self._step()
                try:
                    await asyncio.wait_for(self._stop.wait(),self._interval)
                except TimeoutError:
                    pass
        finally:
            await self._withdraw()

    async def aclose(self):
        self._stop.set()
        task,self._runner=self._runner,None
        if task is None:
            return
        done,pending=await asyncio.wait({task},timeout=8)
        if pending:
            task.cancel()
            await asyncio.wait({task},timeout=4)
            LOGGER.warning('Local discovery stop reached its deadline; gateway state was not modified.')
        elif not task.cancelled():
            # Retrieve any unexpected task exception so an optional publisher
            # cannot emit an unhandled-task traceback with runtime metadata.
            task.exception()


class DiscoveryPublisher:
    def __init__(self,*,device_id,card_id,enabled=False,spawn=None,fallback_factory=None):
        self.device_id,self.card_id,self.enabled=device_id,card_id,enabled
        self._spawn=spawn or asyncio.create_subprocess_exec
        self._process=None
        self._python=None
        self._fallback_factory=fallback_factory or PythonZeroconfPublisher

    async def start(self):
        if not self.enabled or self._process is not None:
            return False
        if self._python is not None:
            try:
                return await self._python.start()
            except Exception:
                LOGGER.warning('Local discovery unavailable; use the device LAN IP manually.')
                return False
        try:
            if not Path(_PUBLISHER).is_file():
                raise ValueError('Optional publisher unavailable')
            if Path(_BUSCTL).is_file():
                process=await self._spawn(_BUSCTL,'--system','--timeout=2','call',
                    'org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                    'StartServiceByName','su','org.freedesktop.Avahi','0',
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                try:
                    code=await asyncio.wait_for(process.wait(),3)
                    if code!=0:
                        raise ValueError('Optional discovery activation unavailable')
                except TimeoutError:
                    try:
                        process.kill()
                        await asyncio.wait_for(process.wait(),1)
                    except (ProcessLookupError,TimeoutError):
                        pass
                    raise ValueError('Optional discovery activation unavailable') from None
            self._process=await self._spawn(_PUBLISHER,'--no-fail',
                'AI-CAT-'+self.device_id[-8:],SERVICE_TYPE.rstrip('.'),'8789',
                'v=1','deviceId='+self.device_id,'cardId='+self.card_id,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(asyncio.shield(self._process.wait()),.15)
                self._process=None
                raise ValueError('Optional discovery publication unavailable')
            except TimeoutError:
                return True
        except Exception:
            await self._close_avahi()
            try:
                self._python=self._fallback_factory(device_id=self.device_id,card_id=self.card_id)
                if await self._python.start():
                    return True
            except Exception:
                self._python=None
            LOGGER.warning('Local discovery unavailable; use the device LAN IP manually.')
            return False

    async def _close_avahi(self):
        process,self._process=self._process,None
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(),2)
        except TimeoutError:
            try:
                process.kill()
                await asyncio.wait_for(process.wait(),1)
            except (ProcessLookupError,TimeoutError):
                LOGGER.warning('Optional discovery process cleanup reached its deadline.')
        except ProcessLookupError:
            pass

    async def aclose(self):
        if self._python is not None:
            try:
                await self._python.aclose()
            except Exception:
                LOGGER.warning('Optional discovery cleanup unavailable; gateway state was not modified.')
            self._python=None
        await self._close_avahi()
