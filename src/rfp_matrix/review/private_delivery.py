"""Owner-only local bootstrap delivery source; no I/O or ACL work at import.

Windows objects receive a protected current-user-only DACL at creation, followed
by handle-based owner/DACL verification. chmod is never used as Windows proof.
Linux uses exclusive no-follow creation and checks owner/mode/ACL metadata.
Other platforms fail closed until an equivalent policy is implemented.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Iterator
import uuid

from .store import ReviewError


def _failure() -> ReviewError:
    return ReviewError("private_delivery_unavailable", "Owner-only login delivery could not be established; no terminal/log/URL fallback is allowed.")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _linux_metadata(info: Any, owner_uid: int, attributes: list[str], *, directory: bool = False) -> None:
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise _failure()
    if not directory and info.st_nlink != 1:
        raise _failure()
    # Reject named ACL metadata rather than assuming mode bits explain every
    # filesystem extension. Unsupported xattr inspection fails closed as well.
    if any("acl" in name.casefold() for name in attributes):
        raise _failure()


def _linux_private(fd: int, *, directory: bool = False) -> None:
    _linux_metadata(os.fstat(fd), os.getuid(), os.listxattr(fd), directory=directory)


def _platform_kind() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux") and hasattr(os, "O_NOFOLLOW"):
        return "linux"
    return "unsupported"


class _WindowsPrivacy:
    """Lazy stdlib Win32 bindings; no process, PowerShell or permission repair."""

    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.advapi = c.WinDLL("advapi32", use_last_error=True)
        pointer = c.c_void_p
        pp = c.POINTER(pointer)

        class SecurityAttributes(c.Structure):
            _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", pointer), ("bInheritHandle", w.BOOL)]

        class SidAndAttributes(c.Structure):
            _fields_ = [("Sid", pointer), ("Attributes", w.DWORD)]

        self.SA, self.SidAndAttributes = SecurityAttributes, SidAndAttributes
        declarations = [
            (self.kernel, "GetCurrentProcess", w.HANDLE, []),
            (self.kernel, "CloseHandle", w.BOOL, [w.HANDLE]),
            (self.kernel, "LocalFree", pointer, [pointer]),
            (self.kernel, "CreateDirectoryW", w.BOOL, [w.LPCWSTR, c.POINTER(SecurityAttributes)]),
            (self.kernel, "GetFileAttributesW", w.DWORD, [w.LPCWSTR]),
            (self.kernel, "CreateFileW", w.HANDLE, [w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(SecurityAttributes), w.DWORD, w.DWORD, w.HANDLE]),
            (self.advapi, "OpenProcessToken", w.BOOL, [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]),
            (self.advapi, "GetTokenInformation", w.BOOL, [w.HANDLE, c.c_int, pointer, w.DWORD, c.POINTER(w.DWORD)]),
            (self.advapi, "ConvertSidToStringSidW", w.BOOL, [pointer, pp]),
            (self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, [w.LPCWSTR, w.DWORD, pp, c.POINTER(w.DWORD)]),
            (self.advapi, "ConvertSecurityDescriptorToStringSecurityDescriptorW", w.BOOL, [pointer, w.DWORD, w.DWORD, pp, c.POINTER(w.DWORD)]),
            (self.advapi, "GetSecurityInfo", w.DWORD, [w.HANDLE, c.c_int, w.DWORD, pp, pp, pp, pp, pp]),
        ]
        for library, name, result, arguments in declarations:
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        self.descriptor = pointer()
        self.directory_handle = None
        owner = self._owner_sid()
        sddl = "O:" + owner + "D:P(A;;FA;;;" + owner + ")"
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, c.byref(self.descriptor), None):
            raise _failure()
        try:
            self.expected = self._sddl(self.descriptor)
        except BaseException:
            self.close()
            raise
        self.attributes = SecurityAttributes(c.sizeof(SecurityAttributes), self.descriptor, False)

    def _owner_sid(self) -> str:
        c, w = self.c, self.w
        token = w.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, c.byref(token)):
            raise _failure()
        converted = c.c_void_p()
        try:
            size = w.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, c.byref(size))
            if not 0 < size.value <= 65536:
                raise _failure()
            buffer = c.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
                raise _failure()
            sid = c.cast(buffer, c.POINTER(self.SidAndAttributes)).contents.Sid
            if not self.advapi.ConvertSidToStringSidW(sid, c.byref(converted)):
                raise _failure()
            value = c.cast(converted, w.LPWSTR).value
            if not isinstance(value, str) or not re.fullmatch(r"S-1-\d+(?:-\d+)+", value):
                raise _failure()
            return value
        finally:
            if converted:
                self.kernel.LocalFree(converted)
            self.kernel.CloseHandle(token)

    def _sddl(self, descriptor: Any) -> str:
        c = self.c
        result = c.c_void_p()
        if not self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 0x00000005, c.byref(result), None):
            raise _failure()
        try:
            value = c.cast(result, self.w.LPWSTR).value
            if not isinstance(value, str) or not value or len(value) > 4096:
                raise _failure()
            return value
        finally:
            self.kernel.LocalFree(result)

    def verify(self, handle: Any) -> None:
        c = self.c
        owner, dacl, descriptor = c.c_void_p(), c.c_void_p(), c.c_void_p()
        error = self.advapi.GetSecurityInfo(handle, 1, 0x00000005, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor))
        if error:
            raise _failure()
        try:
            # Canonical conversion normalizes SID aliases for BOTH expected and
            # actual descriptors. Any extra trustee, absent/protected DACL flag,
            # inherited ACE or owner change differs and is rejected.
            if not owner or not dacl or self._sddl(descriptor) != self.expected:
                raise _failure()
        finally:
            if descriptor:
                self.kernel.LocalFree(descriptor)

    def create(self, directory: Path, filename: str) -> tuple[int, Callable[[], None]]:
        import msvcrt
        c = self.c
        if not self.kernel.CreateDirectoryW(str(directory), c.byref(self.attributes)):
            raise _failure()
        invalid = c.c_void_p(-1).value
        # Keep the directory handle without FILE_SHARE_DELETE until cleanup so
        # its path cannot be swapped after handle-based privacy verification.
        self.directory_handle = self.kernel.CreateFileW(str(directory), 0x00020000, 3, None, 3, 0x02200000, None)
        if self.directory_handle in (None, invalid):
            self.directory_handle = None
            raise _failure()
        flags = self.kernel.GetFileAttributesW(str(directory))
        if flags == 0xFFFFFFFF or flags & 0x400:
            raise _failure()
        self.verify(self.directory_handle)
        handle = self.kernel.CreateFileW(str(directory / filename), 0x40020000, 0, c.byref(self.attributes), 1, 0x80, None)
        if handle in (None, invalid):
            raise _failure()
        try:
            self.verify(handle)  # Before even an empty file becomes a secret file.
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        # fd now owns the native handle; no double CloseHandle.
        return fd, lambda: (self.verify(self.directory_handle), self.verify(msvcrt.get_osfhandle(fd)))

    def close(self) -> None:
        if self.directory_handle is not None:
            self.kernel.CloseHandle(self.directory_handle)
            self.directory_handle = None
        if self.descriptor:
            self.kernel.LocalFree(self.descriptor)
            self.descriptor = self.c.c_void_p()


class PrivateDelivery:
    def __init__(self, directory: Path, fd: int, verify: Callable[[], Any], close_platform: Callable[[], None]):
        self.directory, self.path = directory, directory / "login.json"
        self.fd, self.verify, self.close_platform = fd, verify, close_platform
        self.file_identity = _identity(os.fstat(fd))
        self.directory_identity = _identity(directory.lstat())
        self.written = False

    def write(self, credential: str, *, port: int, lifetime: int) -> Path:
        if self.written or self.fd is None or not isinstance(credential, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", credential):
            raise _failure()
        self.verify()  # Fail closed again immediately before writing any secret.
        data = (json.dumps({"schema_version": 1, "credential": credential, "login_url": f"http://127.0.0.1:{port}/login",
                            "expires_in_seconds": lifetime}, sort_keys=True) + "\n").encode("utf-8")
        remaining = memoryview(data)
        while remaining:
            count = os.write(self.fd, remaining)
            if count <= 0:
                raise _failure()
            remaining = remaining[count:]
        os.fsync(self.fd)
        os.close(self.fd)
        self.fd, self.written = None, True
        return self.path

    def close(self) -> None:
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if not stat.S_ISREG(info.st_mode) or _identity(info) != self.file_identity:
                    raise _failure()  # Never delete an object this delivery did not create.
                self.path.unlink()
        finally:
            self.close_platform()
        if self.directory.exists():
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or _identity(info) != self.directory_identity:
                raise _failure()
            self.directory.rmdir()  # Exact own empty directory only; never recursive.


@contextmanager
def private_delivery(state_root: str | Path) -> Iterator[PrivateDelivery]:
    from .operations import _path
    root = _path(state_root)
    if not root.is_dir() or str(root).startswith(("\\\\", "//")) or any("onedrive" in part.casefold() for part in root.parts):
        raise _failure()
    directory = root / ("local-login-" + uuid.uuid4().hex)
    delivery = None
    platform = None
    fd = None
    try:
        kind = _platform_kind()
        if kind == "windows":
            platform = _WindowsPrivacy()
            fd, verify = platform.create(directory, "login.json")
            delivery = PrivateDelivery(directory, fd, verify, platform.close)
        elif kind == "linux":
            directory.mkdir(mode=0o700)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            platform = directory_fd
            _linux_private(directory_fd, directory=True)
            fd = os.open("login.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            _linux_private(fd)
            delivery = PrivateDelivery(directory, fd, lambda: (_linux_private(directory_fd, directory=True), _linux_private(fd)), lambda: os.close(directory_fd))
        else:
            raise _failure()
        yield delivery
    except (OSError, ValueError, AttributeError, ImportError) as exc:
        if isinstance(exc, ReviewError) or delivery is not None:
            raise
        raise _failure() from None
    finally:
        if delivery is not None:
            delivery.close()
        else:
            if fd is not None:
                os.close(fd)
            if isinstance(platform, _WindowsPrivacy):
                platform.close()
            elif isinstance(platform, int):
                os.close(platform)
            # Failed setup may leave empty, unverified objects; preserve them.
            # No credential was issued/written before setup succeeded.
