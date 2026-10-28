"""Test-wide fixtures."""

import pytest
from respx.models import AllMockedAssertionError
from respx.router import Router as RespxRouter




# respx raises AllMockedAssertionError for a request no route matches *before*
# recording it, so `respx.calls` (and helpers like non_get_calls) would miss an
# unmocked *arr write that production code attempted and then swallowed. Record
# every unmatched request too, so write assertions see all attempted traffic.
def _record_unmatched(resolve):
    def wrapper(self, request):
        try:
            return resolve(self, request)
        except AllMockedAssertionError:
            self.calls.record(request, None)
            raise

    return wrapper


def _arecord_unmatched(aresolve):
    async def wrapper(self, request):
        try:
            return await aresolve(self, request)
        except AllMockedAssertionError:
            self.calls.record(request, None)
            raise

    return wrapper


@pytest.fixture(autouse=True)
def _respx_records_unmatched(monkeypatch):
    monkeypatch.setattr(RespxRouter, "resolve", _record_unmatched(RespxRouter.resolve))
    monkeypatch.setattr(RespxRouter, "aresolve", _arecord_unmatched(RespxRouter.aresolve))
