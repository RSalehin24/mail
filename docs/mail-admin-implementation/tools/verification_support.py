"""Shared, dependency-free validation primitives for this planning package."""

import hashlib
import json
import pathlib
import re
from datetime import datetime, timezone

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_RECORD_BYTES = 2 * 1024 * 1024


class ValidationError(ValueError):
    """A required contract or evidence condition did not hold."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON object key')
        result[key] = value
    return result


def read_json(file):
    with pathlib.Path(file).open('rb') as stream:
        payload = stream.read(MAX_RECORD_BYTES + 1)
    require(len(payload) <= MAX_RECORD_BYTES, 'JSON record exceeds size limit')
    return json.loads(payload, object_pairs_hook=unique_object)


def file_digest(file):
    digest = hashlib.sha256()
    with pathlib.Path(file).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def specification_digest(root=PACKAGE_ROOT):
    excluded = {'current-checks.json', 'observations.json', 'runtime-evidence.template.json'}
    files = sorted(file for file in root.rglob('*') if file.is_file())
    inputs = {str(file.relative_to(root)): file_digest(file) for file in files
              if file.suffix in {'.md', '.json', '.py'} and file.name not in excluded}
    return canonical_digest(inputs)


def inside_file(root, reference):
    require(isinstance(reference, str) and bool(reference), 'Missing bundle path')
    require(not pathlib.Path(reference).is_absolute(), 'Bundle path must be relative')
    target = (root / reference).resolve()
    require(target.is_relative_to(root.resolve()), 'Bundle path escapes its root')
    require(target.is_file(), 'Referenced bundle file is missing')
    return target


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def parse_timestamp(value):
    require(isinstance(value, str), 'Missing UTC timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'Timestamp requires a timezone')
    require(parsed.utcoffset().total_seconds() == 0, 'Timestamp must be UTC')
    return parsed.astimezone(timezone.utc)


def indexed(records):
    require(isinstance(records, list), 'Records must be an array')
    require(all(isinstance(record, dict) for record in records), 'Invalid record')
    identifiers = [record.get('id') for record in records]
    require(all(isinstance(value, str) for value in identifiers), 'Missing record ID')
    require(len(set(identifiers)) == len(identifiers), 'Duplicate record ID')
    return {record['id']: record for record in records}
