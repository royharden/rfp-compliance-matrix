"""Development-only ports and admission fixtures, never live authorization.

All provider/model/terms/price strings below are test inputs. No private corpus,
credentials, custodian role or observed live model output is represented here.
"""
from dataclasses import replace
import platform
from types import SimpleNamespace

import pytest

from rfp_matrix.providers import CallLedger, ProviderConfig
import rfp_matrix.providers.dispatch as dispatch_module


@pytest.fixture
def policy():
    def make(method="F1", **changes):
        config = ProviderConfig(method=method, enabled=True,
            model="gpt-4.1-mini-2025-04-14" if method == "F2" else "jev-1.13.0",
            model_provenance="test-only snapshot fixture; not account admission",
            sdk_name="python-stdlib-urllib" if method == "F2" else "typesafe-sdk",
            sdk_version=platform.python_version() if method == "F2" else "0.7.5",
            sdk_provenance="test-only SDK provenance", license_reference="test-only license record",
            terms_reference="test-only terms", price_policy="test-only-price-v1",
            price_provenance="hand-authored test prices", input_per_million_usd="1",
            output_per_million_usd="2", budget_usd="10", document_id="test-document",
            source_sha256="a" * 64, corpus_sha256="b" * 64,
            token_bound_policy="utf8-byte-upper-bound-plus-1024-v1",
            admission_reference="test-fixture-only; never a real admission")
        config = replace(config, **changes)
        return replace(config, approved_config_sha256=config.config_sha256)
    return make


@pytest.fixture
def ledger_for(tmp_path):
    def make(config, *, name="accounting.sqlite", origin="mock"):
        return CallLedger(tmp_path / name, budget_usd=config.budget_usd,
            input_per_million_usd=config.input_per_million_usd,
            output_per_million_usd=config.output_per_million_usd,
            price_policy=config.price_policy, origin=origin)
    return make


@pytest.fixture
def inline_clock(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, delay):
            self.now += delay

    class InlineThread:
        def __init__(self, *, target, args, daemon, name):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    clock = Clock()
    monkeypatch.setattr(dispatch_module, "time", clock)
    monkeypatch.setattr(dispatch_module, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(dispatch_module, "random", SimpleNamespace(uniform=lambda low, high: 0.1))
    return clock


class FakeSDK:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def system_one(self, **request):
        self.calls.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeHTTP:
    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, body, *, timeout):
        self.calls.append({"body": body, "timeout": timeout})
        return self.response


@pytest.fixture
def sdk_factory():
    return FakeSDK


@pytest.fixture
def http_factory():
    return FakeHTTP


@pytest.fixture
def noul_response():
    def make(questions, model="jev-1.13.0"):
        return {"model": model,
            "answers": {key: {"type": "noul", "noul": 0.95} for key in questions},
            "usage": {"input_tokens": 20, "output_tokens": 10}}
    return make
