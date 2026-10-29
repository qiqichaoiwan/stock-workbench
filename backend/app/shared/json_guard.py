"""Bounded JSON validation shared by manual cards and public evaluation APIs.

Same limits everywhere so a payload accepted by one endpoint cannot bypass the
other: depth six, 100 object fields, 300 array items, 4096-character strings and
finite numbers only. Credential-looking keys are rejected before persistence.
"""
import math


def number(value, lower, upper, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('numeric field outside range')
    if integer and type(value) is not int:
        raise ValueError('integer required')


def bounded_json(value, depth=0):
    if depth > 6:
        raise ValueError('maximum JSON depth is six')
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError('too many object fields')
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise ValueError('invalid field name')
            normalized = key.lower().replace('-', '_')
            if (normalized != 'frozen_sessions' and any(s in normalized for s in ('password', 'secret', 'token', 'session', 'api_key', 'apikey', 'authorization', 'cookie'))) or normalized == 'key':
                raise ValueError('credential fields forbidden')
            bounded_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 300:
            raise ValueError('too many array items')
        for item in value:
            bounded_json(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ValueError('text exceeds 4096 characters')
    elif type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('non-finite number')
    elif value is not None and type(value) is not bool:
        raise ValueError('invalid JSON value')
