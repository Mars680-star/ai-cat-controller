"""Local-root, private-terminal-only Android provisioning pilot.

No boot service, HTTP caller, imported module or --help opens a hotspot. Only an
explicit local operator command with network-change consent can create a window.
QR keys and temporary AP credentials are displayed only to that private TTY.
"""
import argparse
import asyncio
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
import signal
import sys

from .nm_backend import NetworkManagerBackend
from .protocol import ProvisioningError, require
from .server import ProvisioningTCPServer
from .window import ProvisioningWindow


# User-requested convenience for the local pilot only. This weak AP passphrase
# is not the random per-window QR encryption key or the target Wi-Fi credential.
LOCAL_TEST_AP_PASSWORD = '88888888'


def diagnostic_line(backend):
    """Only bounded, fixed-vocabulary operator metadata may leave memory."""
    try:
        value = backend.last_diagnostic
        if type(value) is not dict or set(value) != {'stage', 'cause', 'elapsed_ms', 'rollback'}:
            return ''
        if type(value['stage']) is not str or value['stage'] not in {
                'ACTIVATE', 'VERIFY_ASSOCIATION', 'PERSIST', 'COMMIT', 'AUTOCONNECT'}:
            return ''
        if type(value['cause']) is not str or value['cause'] not in {
                'TIMEOUT', 'PERMISSION', 'INVALID_ARGS', 'CANCELLED', 'OTHER', 'ASSOCIATION_TIMEOUT'}:
            return ''
        if type(value['rollback']) is not str or value['rollback'] not in {'SUCCESS', 'FAILED', 'NOT_AVAILABLE'}:
            return ''
        if type(value['elapsed_ms']) is not int or not 0 <= value['elapsed_ms'] <= 3_600_000:
            return ''
        return ('配网诊断：stage=' + value['stage'] + ' cause=' + value['cause'] +
                ' elapsed_ms=' + str(value['elapsed_ms']) + ' rollback=' + value['rollback'] + '\n')
    except BaseException:
        # Diagnostics must not alter result/receipt/cleanup, even for a broken
        # optional backend getter. Never stringify that exception.
        return ''


def parser():
    result = argparse.ArgumentParser(description='Local operator Android SoftAP provisioning; changes Wi-Fi only after explicit consent', allow_abbrev=False)
    result.add_argument('--confirm-network-change', action='store_true', help='Explicitly allow this temporary hotspot and one confirmed Android Wi-Fi change')
    result.add_argument('--window-seconds', type=int, default=180, help='90–300 seconds; failure/expiry rolls back through NetworkManager')
    return result


def device_id():
    serial = Path('/sys/firmware/devicetree/base/serial-number').read_bytes().rstrip(b'\0')
    require(6 <= len(serial) <= 64 and all(chr(c).isalnum() or chr(c) in '_-' for c in serial))
    return 'dev_' + hashlib.sha256(serial).hexdigest()[:32]


def render_qr(value, output):
    import qrcode
    matrix = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_L)
    matrix.add_data(value)
    matrix.make(fit=True)
    width = matrix.modules_count + 2 * matrix.border
    output.write(f'请加宽终端至至少 {width} 列，完整显示四周留白；手机对准完整二维码，不要裁边或折行。\n')
    # The operator already requires a private TTY. Fixed colors avoid turning
    # the code into white-on-white or inverted modules on a light terminal.
    matrix.print_ascii(out=output, tty=True)


def safe_console():
    """Reject ordinary Wi-Fi SSH: switching AP would lose the private receipt.

    Local/ADB terminals and loopback SSH over USB are operator workflows, not a
    production physical-presence attestation against a malicious root process.
    """
    connection = os.environ.get('SSH_CONNECTION', '')
    if not connection:
        return not os.environ.get('SSH_TTY')
    try:
        parts = connection.split()
        return len(parts) == 4 and all(ipaddress.ip_address(parts[index]).is_loopback for index in (0, 2))
    except ValueError:
        return False


async def run_window(*, confirmed, seconds=180, output=None, uid=None,
                     identity=device_id, backend_factory=NetworkManagerBackend,
                     window_factory=ProvisioningWindow, server_factory=ProvisioningTCPServer,
                     qr_renderer=render_qr, stop_event=None, install_signals=True):
    output = sys.stdout if output is None else output
    uid = os.geteuid() if uid is None else uid
    # Test doubles can supply a private fake terminal, never a public endpoint.
    require(confirmed is True and uid == 0 and output.isatty() and safe_console())
    require(type(seconds) is int and 90 <= seconds <= 300)
    import qrcode  # dependency check before opening/changing any network
    del qrcode
    backend = backend_factory()
    complete = asyncio.Event()
    stopped = asyncio.Event() if stop_event is None else stop_event
    result = {'status': None}
    async def apply(credentials, request_id):
        result['status'] = 'not_sent' if stopped.is_set() else await backend(credentials, request_id)
        complete.set()
        return result['status']
    window = window_factory(identity(), apply, ttl_seconds=seconds)
    server = server_factory(window)
    ap_name, ap_password = 'AI-CAT-' + secrets.token_hex(3).upper(), LOCAL_TEST_AP_PASSWORD
    qr = window.open()
    loop = asyncio.get_running_loop()
    installed_signals = {}
    try:
        for item in (signal.SIGINT, signal.SIGTERM) if install_signals else ():
            try:
                previous = signal.getsignal(item)
                loop.add_signal_handler(item, stopped.set)
                installed_signals[item] = previous
            except (NotImplementedError, RuntimeError):
                pass
        output.write('本次将暂时断开当前 Wi-Fi；超时由 NetworkManager 恢复原网络。\n')
        output.write('固定弱口令仅供本地测试；不要用于正式部署，二维码仍为随机短时密钥。\n')
        output.write('临时热点：' + ap_name + '\n临时热点密码（仅供本地测试）：' + ap_password + '\n')
        output.write('请先手动连接上述热点，再回到安卓微信配网页扫描下方私有二维码。不要分享二维码。\n')
        qr_renderer(qr, output)
        output.flush()
        if stopped.is_set():
            return 'not_sent'
        await backend.begin(ap_name, ap_password, timeout=seconds)
        if stopped.is_set():
            return 'not_sent'
        await server.start()
        if stopped.is_set():
            await window.settle()
            return result['status'] or 'unknown'
        output.write('配网窗口已打开；请在安卓页面确认一次。未开放新设备归属认领。\n')
        output.flush()
        waiting, interrupted = asyncio.create_task(complete.wait()), asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait([waiting, interrupted], timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            if waiting in done:
                await window.settle()
                # The old hotspot can disappear when the station connects.
                # This authenticated offline receipt, not a guess from TCP
                # disconnection, lets the Android client reconcile its marker.
                if result['status'] in {'unknown', 'failed'}:
                    output.write(diagnostic_line(backend))
                output.write('本次网络结果：' + str(result['status']) + '\n请扫描收尾回执（非新配网码）：\n')
                qr_renderer(window.receipt_qr(), output)
                output.flush()
                return result['status']
            # An accepted request may still be draining/applying when the
            # operator interrupts. Let actual work settle; never label this
            # path not_sent merely because the wait timed out.
            await window.settle()
            if result['status'] is not None:
                if result['status'] in {'unknown', 'failed'}:
                    output.write(diagnostic_line(backend))
                output.write('本次网络结果：' + str(result['status']) + '\n请扫描收尾回执（非新配网码）：\n')
                qr_renderer(window.receipt_qr(), output)
                output.flush()
            return result['status'] or 'unknown'
        finally:
            for task in (waiting, interrupted):
                if not task.done():
                    task.cancel()
            await asyncio.gather(waiting, interrupted, return_exceptions=True)
    finally:
        try:
            try:
                await server.close()
            finally:
                await backend.close()
        finally:
            for item in installed_signals:
                try:
                    loop.remove_signal_handler(item)
                finally:
                    signal.signal(item, installed_signals[item])


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run_window(confirmed=args.confirm_network_change, seconds=args.window_seconds))
        return 0 if result == 'connected' else 2
    except (Exception, KeyboardInterrupt):
        # Do not include an exception or QR/password in journal/pipe output.
        print('配网未完成或结果未核实；需USB/本机私有终端，不能用普通Wi-Fi SSH。请保留原编号核对网络。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
