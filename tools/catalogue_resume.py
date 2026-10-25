"""Resume only completed passing modules from the same frozen execution."""

import json
import re
import uuid
from pathlib import Path

from tools.catalogue_assertions import validate_controller, validate_module_report


def execution_identity(inventory, command, engine):
    options = ('--memory', '--memory-swap', '--cpus', '--pids-limit', '--network')
    return {
        'inventory': inventory,
        'controller': {option: command[command.index(option) + 1] for option in options},
        'images': {key: next(value.split('=', 1)[1] for value in command
                             if value.startswith(variable + '=')) for key, variable in (
            ('runtime_image', 'MAIL_ADMIN_RUNTIME_IMAGE'),
            ('qualification_image', 'MAIL_ADMIN_QUALIFICATION_IMAGE'),
        )},
        'engine': {key: engine[key] for key in ('ID', 'OSType', 'Architecture')},
    }


def bind_directory(directory, identity):
    path = directory / 'execution.json'
    if path.is_symlink():
        raise ValueError('A symlink cannot bind a resumed execution')
    if path.exists():
        if json.loads(path.read_bytes()) != identity:
            raise ValueError('Frozen catalogue execution changed; start a fresh run')
        return
    if any(directory.iterdir()):
        raise ValueError('An older unbound catalogue cannot be resumed')
    with path.open('x') as writer:
        json.dump(identity, writer, indent=2)
        writer.write('\n')


def completed_module(directory, selection, execution):
    identity, bundle = execution
    module, tests = selection
    report = directory / (module + '.json')
    progress = directory / (module + '-controller.jsonl')
    if report.is_symlink() or progress.is_symlink():
        raise ValueError('Symlinked module observations cannot be resumed')
    if not report.exists() and not progress.exists():
        return False
    try:
        observed = json.loads(report.read_bytes())
        records = [json.loads(line) for line in progress.read_text().splitlines()]
        validate_module_report(bundle, (module, tests, identity['images']), observed)
        validate_controller(module, records, observed)
    except (OSError, KeyError, TypeError, ValueError):
        preserve_attempt(directory, module)
        return False
    if observed['result'] != 'pass':
        preserve_attempt(directory, module)
        return False
    return True


def require_stopped_controller(directory, module, docker):
    path = directory / (module + '-controller.jsonl')
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError('Symlinked controller observations cannot be resumed')
    records = path.read_text().splitlines()
    first = json.loads(records[0]) if records else {}
    identifier = first.get('container_id', '')
    if re.fullmatch(r'[0-9a-f]{64}', identifier) is None:
        raise ValueError('Interrupted controller identity is unavailable')
    present = docker(('ps', '--all', '--no-trunc', '--filter', 'id=' + identifier,
                      '--format', '{{.ID}}'))
    if present and (present != identifier or json.loads(
            docker(('inspect', identifier)))[0]['State']['Running']):
        raise ValueError('A surviving controller blocks catalogue resume')


def preserve_attempt(directory, module):
    retained = directory / 'attempts' / module / str(uuid.uuid4())
    retained.mkdir(mode=0o700, parents=True)
    for suffix in ('.json', '-controller.jsonl'):
        path = directory / (module + suffix)
        if path.exists():
            path.rename(retained / path.name)


def preserve_assembly(directory):
    paths = [directory / f'T{number:02d}.json' for number in range(1, 23)]
    paths.append(directory.parent / (directory.name + '-evidence.json'))
    existing = [path for path in paths if path.exists() or path.is_symlink()]
    if not existing:
        return
    if any(path.is_symlink() for path in existing):
        raise ValueError('Symlinked assembly output cannot be resumed')
    retained = directory / 'attempts' / ('assembly-' + str(uuid.uuid4()))
    retained.mkdir(mode=0o700, parents=True)
    for path in existing:
        path.rename(retained / path.name)


def resume_directory(evidence: Path, selected: Path) -> Path:
    directory = selected.resolve()
    if (selected.is_symlink() or directory.parent != evidence.resolve().parent
            or not directory.name.startswith('qualification-') or not directory.is_dir()):
        raise ValueError('Resume requires the original catalogue directory beside its bundle')
    if (directory / 'result.json').exists():
        raise ValueError('A completed catalogue remains immutable; start a fresh run')
    return directory
