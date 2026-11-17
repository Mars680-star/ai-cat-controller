"""Private-terminal CLI tests: fake backend/server, real encrypted window."""
import asyncio
import base64
import io
import re

import pytest

from ai_cat_controller.provisioning.cli import parser, render_qr, run_window
from ai_cat_controller.provisioning.protocol import (
    Invitation, ProvisioningError, encode_packet, parse_json, decode_packet,
)


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_terminal_qr_uses_four_module_quiet_zone_fixed_tty_colors_and_width_guidance(monkeypatch):
    import qrcode
    captured = {}
    class Matrix:
        modules_count = 45
        def __init__(self, **options):
            captured.update(options)
            self.border = options['border']
        def add_data(self, value):
            captured['value'] = value
        def make(self, *, fit):
            assert fit is True
        def print_ascii(self, **options):
            captured['print'] = options
    monkeypatch.setattr(qrcode, 'QRCode', Matrix)
    output = Terminal()
    render_qr('public-synthetic-only', output)
    assert captured['border'] >= 4
    assert captured['print']['out'] is output
    assert captured['print'].get('tty') is True
    assert str(45 + 2 * captured['border']) in output.getvalue()
    assert 'public-synthetic-only' not in output.getvalue()


def test_real_terminal_qr_has_full_white_quiet_zone_and_non_theme_dependent_contrast():
    import qrcode
    payload = 'AICAT-PROVISION:1:dev_' + 'a1' * 16 + ':' + 'b2' * 16 + ':' + 'c3' * 32
    output = Terminal()
    render_qr(payload, output)
    raw = output.getvalue()
    rows = [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in raw.splitlines() if '\x1b[' in line]
    matrix = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_L)
    matrix.add_data(payload); matrix.make(fit=True)
    width = matrix.modules_count + 8
    assert len(rows) == (width + 1) // 2
    assert all(len(row) == width for row in rows)
    assert rows[0] == rows[1] == '\u2588' * width
    assert all(row.startswith('\u2588' * 4) and row.endswith('\u2588' * 4) for row in rows[:-1])
    assert '\x1b[48;5;232m' in raw and '\x1b[38;5;255m' in raw
    assert payload not in raw


def harness(*, status='connected', stop_during_begin=False):
    calls, qr_values = [], []
    stopped = asyncio.Event()
    class Backend:
        async def begin(self, name, password, *, timeout):
            assert name.startswith('AI-CAT-') and password == '88888888'
            calls.append('begin')
            if stop_during_begin:
                stopped.set()
        async def __call__(self, credentials, request_id):
            assert credentials.ssid == '合成网络' and credentials.password == 'synthetic-password'
            calls.append('apply')
            return status
        async def close(self):
            calls.append('backend-close')
    class Server:
        def __init__(self, window):
            self.window = window
        async def start(self):
            calls.append('server-start')
            invite = Invitation.parse(qr_values[0])
            packet = encode_packet(invite, 'test-request', 'configure',
                {'ssid': '合成网络', 'password': 'synthetic-password', 'security': 'wpa2-personal'}, direction='request')
            await self.window.process_packet(packet)
            self.window.start_pending('test-request')
        async def close(self):
            calls.append('server-close')
            await self.window.close()
    return calls, qr_values, dict(output=Terminal(), uid=0, identity=lambda: 'dev_' + 'a' * 32,
        backend_factory=Backend, server_factory=Server, qr_renderer=lambda value, output: qr_values.append(value),
        stop_event=stopped, install_signals=False)


@pytest.mark.parametrize('changes', [{'confirmed': False}, {'uid': 1000}, {'output': io.StringIO()},
                                     {'seconds': 0}, {'seconds': 301}])
@pytest.mark.asyncio
async def test_cli_requires_root_private_terminal_and_explicit_network_consent(changes):
    calls, qr_values, options = harness()
    options = {'confirmed': True, **options, **changes}
    with pytest.raises(ProvisioningError):
        await run_window(**options)
    assert calls == [] and qr_values == []


@pytest.mark.parametrize('status', ['connected', 'failed', 'unknown', 'not_sent'])
@pytest.mark.asyncio
async def test_cli_renders_only_original_authenticated_receipt_after_actual_backend_settles(status):
    calls, qr_values, options = harness(status=status)
    assert await run_window(confirmed=True, **options) == status
    assert calls == ['begin', 'server-start', 'apply', 'server-close', 'backend-close']
    assert len(qr_values) == 2
    invitation = Invitation.parse(qr_values[0])
    prefix = 'AICAT-PROVISION-RECEIPT:1:'
    assert qr_values[1].startswith(prefix)
    encoded = qr_values[1][len(prefix):]
    packet = parse_json(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    _, body = decode_packet(invitation, packet, direction='response')
    assert packet['requestId'] == 'test-request' and packet['kind'] == 'configure'
    assert body == {'status': status}
    text = options['output'].getvalue()
    assert 'synthetic-password' not in text and '合成网络' not in text
    assert invitation.key_hex not in text and '本次网络结果：' + status in text


@pytest.mark.asyncio
async def test_requested_fixed_hotspot_password_keeps_qr_keys_random_and_target_credentials_separate():
    invitations = []
    for _ in range(2):
        calls, qr_values, options = harness()
        base = options['backend_factory']
        class ObservedBackend(base):
            async def begin(self, name, password, *, timeout):
                assert name.startswith('AI-CAT-')
                assert password == '88888888'
                calls.append('begin')
        options['backend_factory'] = ObservedBackend
        assert await run_window(confirmed=True, **options) == 'connected'
        assert calls == ['begin', 'server-start', 'apply', 'server-close', 'backend-close']
        invitations.append(Invitation.parse(qr_values[0]))
    assert len(invitations[0].key_hex) == 64
    assert invitations[0].key_hex != invitations[1].key_hex
    assert invitations[0].session_id != invitations[1].session_id


@pytest.mark.asyncio
async def test_operator_prompt_warns_about_fixed_test_password_and_connects_hotspot_before_page_scan():
    _, _, options = harness(stop_during_begin=True)
    await run_window(confirmed=True, **options)
    text = options['output'].getvalue()
    assert '固定弱口令仅供本地测试' in text
    assert '不要用于正式部署' in text
    assert '88888888' in text
    connect = text.find('先手动连接上述热点')
    scan = text.find('再回到安卓微信配网页扫描')
    assert 0 <= connect < scan


@pytest.mark.asyncio
async def test_operator_interrupt_during_ap_preparation_never_starts_tcp_listener():
    calls, qr_values, options = harness(stop_during_begin=True)
    assert await run_window(confirmed=True, **options) == 'not_sent'
    assert calls == ['begin', 'server-close', 'backend-close']
    assert len(qr_values) == 1


def test_cli_does_not_accept_credentials_or_arbitrary_hosts_from_arguments():
    options = {action.dest for action in parser()._actions}
    assert options == {'help', 'confirm_network_change', 'window_seconds'}
    with pytest.raises(SystemExit):
        parser().parse_args(['--password', 'synthetic'])


@pytest.mark.parametrize('connection,expected', [
    ('127.0.0.1 4567 127.0.0.1 22', True), ('::1 4567 ::1 22', True),
    ('192.168.1.10 4567 192.168.1.152 22', False), ('malformed', False),
])
def test_wifi_ssh_cannot_open_a_window_that_would_lose_its_operator_receipt(monkeypatch, connection, expected):
    from ai_cat_controller.provisioning.cli import safe_console
    monkeypatch.setenv('SSH_CONNECTION', connection)
    assert safe_console() is expected


@pytest.mark.asyncio
async def test_failed_backend_close_cannot_skip_restoring_original_signal_handlers(monkeypatch):
    from ai_cat_controller.provisioning import cli
    calls, qr_values, options = harness()
    signals = []
    class Loop:
        def add_signal_handler(self, item, callback):
            signals.append(('add', item))
        def remove_signal_handler(self, item):
            signals.append(('remove', item))
    monkeypatch.setattr(cli.asyncio, 'get_running_loop', lambda: Loop())
    monkeypatch.setattr(cli.signal, 'getsignal', lambda item: 'original')
    monkeypatch.setattr(cli.signal, 'signal', lambda item, handler: signals.append(('restore', item, handler)))
    base = options['backend_factory']
    class BrokenClose(base):
        async def close(self):
            raise RuntimeError('synthetic close error')
    options['backend_factory'] = BrokenClose
    options['stop_event'].set()
    options['install_signals'] = True
    with pytest.raises(RuntimeError, match='synthetic close error'):
        await run_window(confirmed=True, **options)
    assert len([value for value in signals if value[0] == 'add']) == 2
    assert len([value for value in signals if value[0] == 'remove']) == 2
    assert len([value for value in signals if value[0] == 'restore' and value[2] == 'original']) == 2


@pytest.mark.parametrize('status', ['unknown', 'failed'])
@pytest.mark.asyncio
async def test_failed_result_prints_only_validated_fixed_diagnostic_before_unchanged_authenticated_receipt(status):
    calls, qr_values, options = harness(status=status)
    base = options['backend_factory']
    class DiagnosedBackend(base):
        @property
        def last_diagnostic(self):
            return {'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 3000, 'rollback': 'SUCCESS'}
    options['backend_factory'] = DiagnosedBackend
    assert await run_window(confirmed=True, **options) == status
    line = '配网诊断：stage=PERSIST cause=TIMEOUT elapsed_ms=3000 rollback=SUCCESS'
    text = options['output'].getvalue()
    assert text.count(line) == 1
    assert text.index(line) < text.index('本次网络结果：' + status)
    assert calls == ['begin', 'server-start', 'apply', 'server-close', 'backend-close']
    assert len(qr_values) == 2
    invitation = Invitation.parse(qr_values[0])
    encoded = qr_values[1].split(':', 2)[2]
    packet = parse_json(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    _, body = decode_packet(invitation, packet, direction='response')
    assert body == {'status': status}
    assert set(packet) == {'v', 'deviceId', 'sessionId', 'requestId', 'kind', 'nonce', 'ciphertext'}


@pytest.mark.parametrize('patch', [
    {'stage': 'PRIVATE_STAGE\nsynthetic-password'}, {'stage': 1},
    {'cause': 'PRIVATE_CAUSE'}, {'cause': []},
    {'elapsed_ms': True}, {'elapsed_ms': 1.5}, {'elapsed_ms': -1},
    {'elapsed_ms': 3600001}, {'elapsed_ms': 'PRIVATE_TIME'},
    {'rollback': 'PRIVATE_ROLLBACK'}, {'rollback': None},
    {'password': 'PRIVATE_PASSWORD'},
])
@pytest.mark.asyncio
async def test_invalid_diagnostic_fields_never_print_or_change_unknown_receipt_and_cleanup(patch):
    calls, qr_values, options = harness(status='unknown')
    base = options['backend_factory']
    class UntrustedBackend(base):
        @property
        def last_diagnostic(self):
            return {'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 3000, 'rollback': 'SUCCESS', **patch}
    options['backend_factory'] = UntrustedBackend
    assert await run_window(confirmed=True, **options) == 'unknown'
    text = options['output'].getvalue()
    assert '配网诊断：' not in text and 'PRIVATE_' not in text and 'synthetic-password' not in text
    assert calls[-2:] == ['server-close', 'backend-close']
    assert len(qr_values) == 2 and qr_values[1].startswith('AICAT-PROVISION-RECEIPT:1:')


@pytest.mark.parametrize('variant', ['getter', 'not-dict', 'dict-subclass', 'missing-key', 'unprintable-field'])
@pytest.mark.asyncio
async def test_hostile_diagnostic_getter_or_container_cannot_break_return_receipt_or_cleanup(variant):
    calls, qr_values, options = harness(status='unknown')
    class Hostile:
        def __str__(self):
            raise RuntimeError('PRIVATE_STRINGIFY')
        def __repr__(self):
            raise RuntimeError('PRIVATE_REPR')
    class HostileDict(dict):
        def __iter__(self):
            raise RuntimeError('PRIVATE_ITERATE')
    base = options['backend_factory']
    class UntrustedBackend(base):
        @property
        def last_diagnostic(self):
            if variant == 'getter':
                raise RuntimeError('PRIVATE_GETTER')
            if variant == 'not-dict':
                return Hostile()
            value = {'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 3000, 'rollback': 'SUCCESS'}
            if variant == 'dict-subclass':
                return HostileDict(value)
            if variant == 'missing-key':
                del value['cause']
            else:
                value['cause'] = Hostile()
            return value
    options['backend_factory'] = UntrustedBackend
    assert await run_window(confirmed=True, **options) == 'unknown'
    text = options['output'].getvalue()
    assert '配网诊断：' not in text and 'PRIVATE_' not in text
    assert calls == ['begin', 'server-start', 'apply', 'server-close', 'backend-close']
    assert len(qr_values) == 2


@pytest.mark.parametrize('status', ['connected', 'not_sent'])
@pytest.mark.asyncio
async def test_non_failure_results_never_even_read_optional_backend_diagnostics(status):
    calls, qr_values, options = harness(status=status)
    base = options['backend_factory']
    class NoDiagnosticAccess(base):
        @property
        def last_diagnostic(self):
            calls.append('unexpected-diagnostic-read')
            raise AssertionError('diagnostics are only for failed or unknown results')
    options['backend_factory'] = NoDiagnosticAccess
    assert await run_window(confirmed=True, **options) == status
    assert 'unexpected-diagnostic-read' not in calls
    assert '配网诊断：' not in options['output'].getvalue()
    assert len(qr_values) == 2


@pytest.mark.parametrize('stage,cause,elapsed_ms,rollback', [
    ('ACTIVATE', 'OTHER', 0, 'NOT_AVAILABLE'),
    ('VERIFY_ASSOCIATION', 'ASSOCIATION_TIMEOUT', 60000, 'SUCCESS'),
    ('PERSIST', 'PERMISSION', 3600000, 'FAILED'),
    ('COMMIT', 'INVALID_ARGS', 1, 'SUCCESS'),
    ('AUTOCONNECT', 'CANCELLED', 3000, 'NOT_AVAILABLE'),
])
def test_diagnostic_formatter_accepts_only_bounded_fixed_vocabulary_and_formats_exactly(stage, cause, elapsed_ms, rollback):
    from types import SimpleNamespace
    from ai_cat_controller.provisioning.cli import diagnostic_line
    backend = SimpleNamespace(last_diagnostic={'stage': stage, 'cause': cause, 'elapsed_ms': elapsed_ms, 'rollback': rollback})
    assert diagnostic_line(backend) == f'配网诊断：stage={stage} cause={cause} elapsed_ms={elapsed_ms} rollback={rollback}\n'
