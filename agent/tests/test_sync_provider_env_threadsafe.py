"""_sync_provider_env runs on any LLM-calling thread; steady state must not mutate os.environ."""
from __future__ import annotations

import os
import threading

import src.providers.llm as llm


class _Spy(dict):
    writes = 0

    def __setitem__(self, k, v):
        type(self).writes += 1
        super().__setitem__(k, v)

    def __delitem__(self, k):
        type(self).writes += 1
        super().__delitem__(k)


def _spy_env(monkeypatch):
    env = _Spy(os.environ)
    _Spy.writes = 0
    monkeypatch.setattr(os, "environ", env)
    return env


def test_unchanged_values_cause_no_writes(monkeypatch):
    _spy_env(monkeypatch)
    llm._mirror_env({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "u"})
    first = _Spy.writes
    assert first == 2
    for _ in range(5):
        llm._mirror_env({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "u"})
    assert _Spy.writes == first


def test_unsetting_absent_key_is_noop_and_present_key_is_removed(monkeypatch):
    env = _spy_env(monkeypatch)
    dict.pop(env, "OPENAI_API_KEY", None)
    _Spy.writes = 0
    llm._mirror_env({"OPENAI_API_KEY": None})
    assert _Spy.writes == 0
    llm._mirror_env({"OPENAI_API_KEY": "x"})
    llm._mirror_env({"OPENAI_API_KEY": None})
    assert "OPENAI_API_KEY" not in env


def test_value_change_is_written(monkeypatch):
    env = _spy_env(monkeypatch)
    llm._mirror_env({"OPENAI_BASE_URL": "a"})
    llm._mirror_env({"OPENAI_BASE_URL": "b"})
    assert env["OPENAI_BASE_URL"] == "b"


def test_concurrent_mirror_while_iterating_environ(monkeypatch):
    _spy_env(monkeypatch)
    llm._mirror_env({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "u"})
    errors: list[Exception] = []
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            llm._mirror_env({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "u"})

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    try:
        for _ in range(3000):
            try:
                dict(os.environ)
            except RuntimeError as e:  # dictionary changed size during iteration
                errors.append(e)
    finally:
        stop.set()
        for t in ts:
            t.join()
    assert not errors
