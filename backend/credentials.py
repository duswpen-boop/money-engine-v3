"""Per-user Windows DPAPI storage for future provider credentials."""

import ctypes
import os
from ctypes import wintypes

from .paths import data_dir

KEY_FILE = "openai-key.dpapi"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("API 키의 로컬 암호화 저장은 Windows에서만 사용할 수 있습니다.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = (ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(DataBlob))
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output_blob = DataBlob()
    if not operation(ctypes.byref(input_blob), None, None, None, None, 0,
                     ctypes.byref(output_blob)):
        raise OSError(ctypes.get_last_error(), "Windows 암호화 저장에 실패했습니다.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def secret_path():
    return data_dir() / KEY_FILE


def has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) or secret_path().is_file()


def get_openai_key() -> str | None:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    if secret_path().is_file():
        return _crypt(secret_path().read_bytes(), decrypt=True).decode("utf-8")
    return None


def save_openai_key(value: str):
    if not value.strip():
        raise ValueError("API 키를 입력해 주세요.")
    path = secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt(value.strip().encode("utf-8"), decrypt=False)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    os.replace(temp, path)


def delete_openai_key():
    secret_path().unlink(missing_ok=True)
