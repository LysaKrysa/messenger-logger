"""Real Docker lifecycle with offline fixture workers; no Messenger login or sends."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from cryptography.fernet import Fernet

from dashboard.manager import Manager
from dashboard.store import Store


FIXTURE = '''import json, os, signal, sys, time
from pathlib import Path
root = Path('/data')
if '--cleanup' in sys.argv:
    (root / 'cleanup-ran').write_text('offline cleanup')
    raise SystemExit(0)
assert 'FB_SESSION_JSON' not in os.environ
assert 'TELEGRAM_BOT_TOKEN' not in os.environ
assert 'TELEGRAM_ACCOUNT_KEY' not in os.environ
values = json.loads(Path(os.environ['LOGGER_CREDENTIALS_FILE']).read_text())
cookies = json.loads(values['FB_SESSION_JSON'])
assert cookies['xs'] == 'SYNTHETIC_OFFLINE'
(root / 'identity').write_text(cookies['c_user'])
(root / 'messenger-modern-status.log').write_text('Messenger listener ready for offline fixture.\\n')
def stop(*args):
    (root / 'graceful-stop').write_text('SIGTERM received')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
while True: time.sleep(.1)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run the container smoke test with sudo on an isolated CI host.')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        code = root / 'fixture-code'
        code.mkdir(mode=0o755)
        (code / 'logger.py').write_text(FIXTURE)
        store = Store(root / 'state', Fernet.generate_key())
        store.configure_bot('123456:' + 'SYNTHETIC_OFFLINE_TOKEN_NOT_REAL' * 2,
                            {'id': 123456, 'username': 'synthetic_offline_bot', 'has_topics_enabled': True})
        database_uid = int(os.environ.get('SUDO_UID', '65534'))
        database_gid = int(os.environ.get('SUDO_GID', '65534'))
        root.chmod(0o755)
        os.chown(store.root, database_uid, database_gid)
        os.chown(store.path, database_uid, database_gid)
        # Real production topology: root controller + unprivileged database
        # owner. WAL/SHM files must remain accessible to that owner.
        with store.db() as db:
            db.execute('SELECT * FROM settings').fetchall()
            assert os.geteuid() == database_uid
            for suffix in ('-wal', '-shm'):
                assert Path(str(store.path) + suffix).stat().st_uid == database_uid
        assert os.geteuid() == 0
        manager = Manager(store, data_root=root/'accounts', status_root=root/'status', runtime_root=root/'run', code_root=code, image=args.image)
        ids = []
        try:
            for index in range(2):
                ident = store.add_account('Offline ' + str(index), json.dumps({'c_user': str(111222333+index), 'xs': 'SYNTHETIC_OFFLINE', 'datr': 'SYNTHETIC_OFFLINE'}))
                ids.append(ident)
                store.claim_invite(store.invite(ident).split('start=')[1], 87654321+index, 'Offline fixture')
                store.action(ident, 'approve')
                store.action(ident, 'start')
            manager.run_once()
            # The web service can read the safe snapshot but not the root ledger.
            public_usage = root / "status/usage.json"
            private_usage = root / "status/usage-state.json"
            assert public_usage.stat().st_mode & 0o777 == 0o644
            assert private_usage.stat().st_mode & 0o777 == 0o600
            os.seteuid(database_uid)
            try:
                usage = json.loads(public_usage.read_text())
                assert usage["version"] == 1
                assert "SYNTHETIC_OFFLINE" not in json.dumps(usage)
                try:
                    private_usage.read_text()
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Web identity could read the root usage ledger")
            finally:
                os.seteuid(0)
            until = time.monotonic() + 20
            while time.monotonic() < until and not all((manager.path(ident)/'identity').is_file() for ident in ids):
                time.sleep(.2)
            for index, ident in enumerate(ids):
                assert (manager.path(ident)/'identity').read_text() == str(111222333+index)
                info = manager.docker.inspect('messenger-account-' + ident)
                assert info['State']['Running']
                assert info['HostConfig']['ReadonlyRootfs']
                assert info['HostConfig']['Memory'] == 512*1024*1024
                assert not any('SYNTHETIC_OFFLINE' in value for value in info['Config']['Env'])
            store.action(ids[0], 'stop')
            manager.run_once()
            assert (manager.path(ids[0])/'graceful-stop').is_file()
            assert manager.docker.inspect('messenger-account-' + ids[0]) is None
            assert manager.docker.inspect('messenger-account-' + ids[1])['State']['Running']
            manager.cleanup()
            assert (manager.path(ids[0])/'cleanup-ran').is_file()
            store.action(ids[0], 'remove')
            manager.run_once()
            assert not (manager.data_root/ids[0]).exists()
            assert (manager.data_root/ids[1]).exists()
            print('Real container smoke passed: isolated identity/data, mounted secrets, resource limits, graceful stop, offline cleanup, scoped removal.')
        finally:
            for ident in ids:
                name = 'messenger-account-' + ident
                manager.stop(name, manager.docker.inspect(name))


if __name__ == '__main__':
    main()
