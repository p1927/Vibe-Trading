import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import types
import pytest
@pytest.mark.parametrize('broken', ['corrupt', 'list', 'io'])
def test_job_read_failure_is_not_absence(tmp_path, monkeypatch, broken):
    from src.trade import job_runner
    path = tmp_path / 'job.json'
    path.write_text('{' if broken == 'corrupt' else '[]')
    if broken == 'io':
        monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: (_ for _ in ()).throw(OSError('EIO')))
    with pytest.raises((ValueError, OSError)):
        job_runner.read_job_json_unsafe(path)

def test_missing_job_is_absent(tmp_path):
    from src.trade import job_runner
    assert job_runner.read_job_json_unsafe(tmp_path / 'absent') is None

def test_job_claim_stat_io_does_not_skip_active_recording(tmp_path, monkeypatch):
    from src.trade.job_store import JobStore
    root = tmp_path / 'jobs'
    target = root / ('a' * 32) / 'job.json'
    target.parent.mkdir(parents=True)
    target.write_text('{"status":"waiting_for_open"}')
    (jobs, active) = ({}, {})
    store = JobStore(jobs_root=lambda : root, serialize=lambda j: j, scope_for=lambda j: 's', wall_clock_seconds=lambda j: 1, queued_no_pid_seconds=lambda : 1, result_key='result', jobs_ref=lambda : jobs, active_ref=lambda : active)
    original = Path.stat

    def broken(path, *a, **kw):
        if path == target:
            raise OSError('EIO')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'stat', broken)
    with pytest.raises(OSError, match='EIO'):
        store._find_live_on_disk('s')

def test_failed_wake_retained_then_success_clears_entry(tmp_path, monkeypatch):
    from src.scheduled_research.store import ScheduledResearchJobStore
    from src.trade import recording_wait_scheduler as scheduler
    store = ScheduledResearchJobStore(path=tmp_path / 'schedule.json')
    schedule_id = scheduler.schedule_recording_wake(recording_job_id='a' * 32, next_open_at=datetime(2000, 1, 1, tzinfo=timezone.utc), store=store)
    attempts = []

    def wake(job):
        attempts.append(job)
        if len(attempts) == 1:
            raise OSError('spawn failed')
        return True
    monkeypatch.setitem(sys.modules, 'src.trade.recording_jobs', types.SimpleNamespace(wake_recording_job=wake))
    asyncio.run(scheduler._recording_wake_poll_tick(store))
    assert schedule_id in store.load()
    asyncio.run(scheduler._recording_wake_poll_tick(store))
    assert schedule_id not in store.load() and len(attempts) == 2

@pytest.mark.parametrize('kind', ['job', 'auto'])
def test_owned_writers_use_shared_durable_atomic_writer(tmp_path, monkeypatch, kind):
    from src.trade import job_runner, recording_auto
    import os
    path = tmp_path / (kind + '.json')
    fsyncs = []
    real = os.fsync

    def sync(fd):
        fsyncs.append(fd)
        real(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    if kind == 'job':
        job_runner.write_job_json_unsafe(path, {'status': 'running'})
        assert json.loads(path.read_text())['schema_version'] == 1
    else:
        monkeypatch.setattr(recording_auto, '_store_path', lambda : path)
        recording_auto.save_auto_record(enabled=True, config={'underlyings': ['NIFTY']})
        assert recording_auto.load_auto_record()['enabled'] is True
    assert len(fsyncs) == 2
    assert not list(tmp_path.glob('*.tmp'))

@pytest.mark.parametrize('body', ['{', '[]'])
def test_auto_record_corruption_is_not_disabled(tmp_path, monkeypatch, body):
    from src.trade import recording_auto
    path = tmp_path / 'auto.json'
    path.write_text(body)
    monkeypatch.setattr(recording_auto, '_store_path', lambda : path)
    with pytest.raises(ValueError):
        recording_auto.load_auto_record()

@pytest.mark.parametrize('kind', ['job', 'auto'])
def test_kill_before_replace_keeps_previous_owned_record(tmp_path, kind):
    import subprocess
    path = tmp_path / (kind + '.json')
    original = {'schema_version': 1, 'status': 'running'} if kind == 'job' else {'enabled': True}
    path.write_text(json.dumps(original))
    script = "\nimport json, os, runpy, signal, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport os\nfrom src.trade import job_runner, recording_auto\ndef stop(*args): os.kill(os.getpid(), signal.SIGKILL)\nos.replace = stop\npath = Path(sys.argv[2])\nif sys.argv[3] == 'job':\n    job_runner.write_job_json_unsafe(path, {'status': 'done'})\nelse:\n    recording_auto._store_path = lambda: path\n    recording_auto.save_auto_record(enabled=False, config=None)\n"
    process = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]), str(path), kind], capture_output=True, text=True)
    assert process.returncode == -9, process.stderr
    assert json.loads(path.read_text()) == original

def test_native_jobs_do_not_require_trade(tmp_path):
    import subprocess
    script = "\nimport builtins, runpy, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\noriginal = builtins.__import__\ndef guarded(name, *args, **kwargs):\n    if name.startswith('trade_integrations'):\n        raise AssertionError('standalone job attempted a Trade import: ' + name)\n    return original(name, *args, **kwargs)\nbuiltins.__import__ = guarded\nfrom src.trade import job_runner, recording_auto\nroot = Path(sys.argv[2])\njob_runner.write_job_json_unsafe(root / 'job.json', {'status': 'running'})\nrecording_auto._store_path = lambda: root / 'auto.json'\nrecording_auto.save_auto_record(enabled=True, config={})\nassert recording_auto.load_auto_record()['enabled']\nprint('PASS standalone jobs: no Trade import')\n"
    process = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]), str(tmp_path)], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert 'PASS standalone' in process.stdout
