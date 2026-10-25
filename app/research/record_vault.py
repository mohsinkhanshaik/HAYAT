"""Explicitly authorized local records; ciphertext bound to the Windows account.

No network adapter or model tool can read this store. Authorization is a revocable
local grant, not a claim that third-party records have been legally verified.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]


def dpapi(data, *, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('This record vault requires Windows account encryption (DPAPI).')
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(incoming), ctypes.c_wchar_p('HAYAT authorized records'), None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


class RecordVault:
    _locks = {}
    _locks_guard = threading.Lock()

    def __init__(self, directory, *, principal='local_owner', codec=dpapi):
        if principal != 'local_owner':
            raise PermissionError('Only the local owner can open the authorized record vault.')
        self.directory = Path(directory)
        self.codec = codec
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.directory.resolve()).casefold(), threading.RLock())

    def _read(self):
        path = self.directory / 'records.dpapi'
        return json.loads(self.codec(path.read_bytes(), decrypt=True)) if path.exists() else {'records': {}, 'grants': {}, 'audit': []}

    def _write(self, state):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / 'records.dpapi'
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(self.codec(json.dumps(state, ensure_ascii=False).encode()))
        temporary.replace(path)

    def _audit(self, state, action, record_id=''):
        state['audit'].append({'at': datetime.now(UTC).isoformat(), 'action': action, 'record_id': record_id})

    def grant(self, authorization, *, consent=False, ttl=3600):
        if consent is not True or not isinstance(authorization, str) or not 10 <= len(authorization.strip()) <= 500:
            raise PermissionError('Explicit consent and a description of ownership or authorization are required.')
        if type(ttl) is not int or not 1 <= ttl <= 86400:
            raise ValueError('Authorization must expire within 24 hours.')
        with self._lock:
            state = self._read()
            token = uuid.uuid4().hex
            state['grants'][token] = {'authorization': authorization.strip(), 'expires': time.time() + ttl, 'revoked': False}
            self._audit(state, 'authorization_granted')
            self._write(state)
            return token

    def _authorized(self, state, token):
        grant = state['grants'].get(token, {})
        if grant.get('revoked', True) or grant.get('expires', 0) <= time.time():
            raise PermissionError('Record authorization is missing, expired or revoked.')

    def put(self, token, title, content, *, kind='authorized_document'):
        if not isinstance(content, str) or len(content.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('Authorized text must be at most 2 MiB.')
        if not isinstance(title, str) or not 1 <= len(title) <= 200:
            raise ValueError('Give the record a title of 1–200 characters.')
        if kind not in {'authorized_document', 'public_investigation'}:
            raise ValueError('Unsupported record kind.')
        with self._lock:
            state = self._read()
            self._authorized(state, token)
            rid = uuid.uuid4().hex
            state['records'][rid] = {'title': title, 'content': content, 'kind': kind, 'grant': token,
                                     'created_at': datetime.now(UTC).isoformat()}
            self._audit(state, 'record_saved', rid)
            self._write(state)
            return rid

    def list(self):
        with self._lock:
            state = self._read()
            return [{'id': rid, 'title': row['title'], 'kind': row['kind'], 'created_at': row['created_at'],
                     'accessible': not state['grants'].get(row['grant'], {}).get('revoked', True)
                         and state['grants'].get(row['grant'], {}).get('expires', 0) > time.time()}
                    for rid, row in state['records'].items()]

    def get(self, token, rid):
        with self._lock:
            state = self._read()
            self._authorized(state, token)
            record = state['records'].get(rid)
            if not record or record['grant'] != token:
                raise PermissionError('This authorization does not cover that record.')
            self._audit(state, 'record_read', rid)
            self._write(state)
            return dict(record)

    def revoke(self, token):
        with self._lock:
            state = self._read()
            if token in state['grants']:
                state['grants'][token]['revoked'] = True
            self._audit(state, 'authorization_revoked')
            self._write(state)

    def reauthorize(self, token, rid):
        """A new, explicit owner grant may replace an expired grant for one record."""
        with self._lock:
            state = self._read()
            self._authorized(state, token)
            if rid not in state['records']:
                raise ValueError('Record not found.')
            state['records'][rid]['grant'] = token
            self._audit(state, 'record_reauthorized', rid)
            self._write(state)

    def revoke_record(self, rid):
        with self._lock:
            state = self._read()
            record = state['records'].get(rid)
            if not record:
                raise ValueError('Record not found.')
            state['grants'][record['grant']]['revoked'] = True
            self._audit(state, 'record_access_revoked', rid)
            self._write(state)

    def delete(self, rid):
        if not re.fullmatch('[0-9a-f]{32}', rid):
            raise ValueError('Invalid record id.')
        with self._lock:
            state = self._read()
            state['records'].pop(rid, None)
            self._audit(state, 'record_deleted', rid)
            self._write(state)

    def audit(self):
        return self._read()['audit']
