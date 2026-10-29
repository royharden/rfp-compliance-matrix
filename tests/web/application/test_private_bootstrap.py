"""Privacy-policy regression SOURCE ONLY; fake metadata/APIs, no host ACL work."""
import ctypes
import stat
import sys
from types import SimpleNamespace

import pytest

from rfp_matrix.review import ReviewError
from rfp_matrix.review import private_delivery as privacy
from rfp_matrix.review.server import serve_local


@pytest.mark.parametrize("owner,dacl,actual,accepted", [
    (True, True, "O:SYNTHETICD:P(A;;FA;;;SYNTHETIC)", True),
    (False, True, "O:SYNTHETICD:P(A;;FA;;;SYNTHETIC)", False),
    (True, False, "O:SYNTHETICD:P(A;;FA;;;SYNTHETIC)", False),
    (True, True, "O:SYNTHETICD:(A;;FA;;;SYNTHETIC)", False),
    (True, True, "O:OTHERD:P(A;;FA;;;SYNTHETIC)", False),
    (True, True, "O:SYNTHETICD:P(A;;FA;;;SYNTHETIC)(A;;FR;;;EVERYONE)", False),
])
def test_windows_handle_verifier_requires_exact_owner_protected_dacl(owner, dacl, actual, accepted):
    """what_bug_this_catches: chmod-only Windows privacy or trusting null/inherited/additional-trustee ACLs."""
    # Fake GetSecurityInfo fills ctypes output containers; no WinDLL/native ACL
    # query is constructed or called, even when this source is run on Windows.
    def get_security(*args):
        args[3]._obj.value = 11 if owner else None
        args[5]._obj.value = 22 if dacl else None
        args[7]._obj.value = 33
        return 0

    api = object.__new__(privacy._WindowsPrivacy)
    freed = []
    api.c = ctypes
    api.advapi = SimpleNamespace(GetSecurityInfo=get_security)
    api.kernel = SimpleNamespace(LocalFree=lambda pointer: freed.append(pointer.value))
    api.expected = "O:SYNTHETICD:P(A;;FA;;;SYNTHETIC)"
    api._sddl = lambda _: actual
    if accepted:
        api.verify("fake-handle")
    else:
        with pytest.raises(ReviewError) as error:
            api.verify("fake-handle")
        assert error.value.code == "private_delivery_unavailable"
    assert freed == [33]


@pytest.mark.parametrize("mode,owner,links,attributes,accepted", [
    (0o600, 1000, 1, [], True), (0o644, 1000, 1, [], False),
    (0o600, 999, 1, [], False), (0o600, 1000, 2, [], False),
    (0o600, 1000, 1, ["system.posix_acl_access"], False),
])
def test_linux_policy_rejects_nonprivate_metadata_without_chmod_assumption(mode, owner, links, attributes, accepted):
    """what_bug_this_catches: inherited ACLs, hardlinks, wrong owner or world/group-readable bootstrap secrets."""
    metadata = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner, st_nlink=links)
    if accepted:
        privacy._linux_metadata(metadata, 1000, attributes)
    else:
        with pytest.raises(ReviewError):
            privacy._linux_metadata(metadata, 1000, attributes)


@pytest.mark.parametrize("kind", ["unsupported", "windows"])
def test_failed_platform_privacy_precedes_challenge_and_listener(application, monkeypatch, capsys, kind):
    """what_bug_this_catches: issuing/delivering a secret or falling back to terminal when private ACL setup fails."""
    import rfp_matrix.web as web
    import rfp_matrix.web.security as security
    issued, served = [], []

    class FakeAuthority:
        challenge_seconds = 120

        def issue_login_challenge(self, _):
            issued.append(True)
            return "SyntheticCapability_" + "A" * 32

    class RefusedWindows:
        def create(self, *_):
            raise privacy._failure()

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *a, **k: served.append(True)))
    monkeypatch.setattr(web, "create_app", lambda *a, **k: object())
    monkeypatch.setattr(security, "LocalSessionAuthority", FakeAuthority)
    monkeypatch.setattr(privacy, "_platform_kind", lambda: kind)
    monkeypatch.setattr(privacy, "_WindowsPrivacy", RefusedWindows)
    with pytest.raises(ReviewError) as error:
        serve_local(application.store, port=22555, port_claim_reference="synthetic-not-a-real-claim",
                    run_ids=["R1"], private_login_delivery=True)
    assert error.value.code == "private_delivery_unavailable"
    assert issued == [] and served == []
    output = capsys.readouterr()
    assert "SyntheticCapability" not in output.out + output.err


def test_privacy_recheck_failure_writes_no_secret():
    """what_bug_this_catches: writing a capability after the private-file verification changed or failed."""
    delivery = object.__new__(privacy.PrivateDelivery)
    delivery.written = False
    delivery.fd = object()  # Deliberately not a real OS descriptor: no accidental host write.

    def refuse():
        raise privacy._failure()

    delivery.verify = refuse
    with pytest.raises(ReviewError) as error:
        delivery.write("SyntheticCapability_" + "A" * 32, port=22555, lifetime=120)
    assert error.value.code == "private_delivery_unavailable"
