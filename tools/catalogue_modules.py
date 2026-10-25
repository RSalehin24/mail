"""Fresh bounded controllers prevent unrelated assertions inflating later child peaks."""

import json
import re
import subprocess
import uuid

from tools.maddy_modules import MODULE_TIMEOUT_SECONDS
from tools.run_tests import append_progress, timestamp


class GroupController:
    def __init__(self, selection, dependencies):
        self.group, self.directory = selection
        self.command, self.entrypoint, self.docker = dependencies
        self.name = 'mail-admin-catalogue-controller-' + str(uuid.uuid4())
        self.progress = self.directory / (self.group + '-controller.jsonl')
        self.progress.touch(mode=0o600, exist_ok=False)

    def create(self, output):
        if self.command[:2] != ('run', '--rm'):
            raise ValueError('The isolated catalogue requires the bounded fixture command')
        arguments = ('create', '--name', self.name, '--label',
                     'mail-admin.catalogue-controller=' + self.name) + self.command[2:]
        selector = '--assertion-module' if self.group.startswith('tests.') else '--group'
        identifier = self.docker(arguments + self.entrypoint + (
            selector, self.group, '--report-directory', output,
        ))
        if re.fullmatch(r'[0-9a-f]{64}', identifier) is None:
            raise RuntimeError('The group controller identity is invalid')
        self.identifier = identifier
        append_progress(self.progress, {'created_at': timestamp(), 'container_id': identifier,
                                        'container_name': self.name, 'group': self.group})

    def observation(self):
        record = json.loads(self.docker(('inspect', self.identifier)))[0]
        if (record['Id'] != self.identifier or record['Name'] != '/' + self.name
                or record['Config']['Labels'].get('mail-admin.catalogue-controller') != self.name
                or record['Config']['Image'] != self.command[-1]):
            raise RuntimeError('Refusing an untrusted catalogue controller')
        return {key: record['State'][key] for key in ('Running', 'ExitCode', 'OOMKilled')}

    def run(self, output):
        self.create(output)
        try:
            result = subprocess.run(  # noqa: S603, S607 -- the recorded private controller only.
                ('docker', 'start', '--attach', self.identifier),  # noqa: S607 -- fixed CLI.
                check=False,
                timeout=MODULE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            append_progress(self.progress, {'outcome': 'completion_unknown',
                                            'observed_at': timestamp()})
            raise
        self.complete(result.returncode)

    def complete(self, attached_code):
        observation = self.observation()
        append_progress(self.progress, {'observed_at': timestamp(), 'state': observation,
                                        'attached_exit_code': attached_code})
        if observation['Running']:
            raise RuntimeError('The catalogue controller outcome remains unknown')
        self.docker(('rm', self.identifier))
        if (observation['OOMKilled'] or observation['ExitCode'] not in (0, 1)
                or observation['ExitCode'] != attached_code):
            raise RuntimeError('A killed or failed controller cannot complete qualification')
        path = self.directory / (self.group + '.json')
        if not path.is_file() or json.loads(path.read_bytes()).get('test_id') != self.group:
            raise RuntimeError('A missing group report cannot complete qualification')


def catalogue_inventory(command, entrypoint, docker):
    inventory = json.loads(docker(command + entrypoint + ('--inventory',)))
    groups = inventory['groups']
    expected = {f'T{number:02d}' for number in range(1, 23)}
    if (not isinstance(groups, dict) or set(groups) != expected
            or any(not valid_cases(group, cases) for group, cases in groups.items())):
        raise ValueError('A complete validated catalogue inventory is required')
    return inventory


def valid_cases(group, cases):
    return (isinstance(cases, list) and bool(cases)
            and all(isinstance(case, str) and re.fullmatch(re.escape(group) + r'\.\d{2}', case)
                    for case in cases) and len(set(cases)) == len(cases))


def run_catalogue(command, context, docker):
    from tools.catalogue_assertions import validate_module_inventory
    from tools.catalogue_resume import (
        bind_directory,
        completed_module,
        execution_identity,
        preserve_assembly,
        require_stopped_controller,
        resume_directory,
    )
    from tools.qualification_bundle import QualificationBundle

    root, evidence, entrypoint, *resume = context
    bundle = QualificationBundle(evidence)
    bundle.verify_source()
    inventory = catalogue_inventory(command, entrypoint, docker)
    identity = execution_identity(inventory, command, json.loads(
        docker(('info', '--format', '{{json .}}')),
    ))
    if resume and resume[0] is not None:
        directory = resume_directory(evidence, resume[0])
    else:
        directory = evidence.resolve().parent / ('qualification-' + str(uuid.uuid4()))
        directory.mkdir(mode=0o700)
    bind_directory(directory, identity)
    inventory_path = directory / 'inventory.json'
    if not inventory_path.exists():
        with inventory_path.open('x') as writer:
            json.dump(inventory, writer, indent=2)
    output = '/app/build/' + str(directory.relative_to(root / 'build'))
    modules = validate_module_inventory(inventory)
    for module in modules:
        require_stopped_controller(directory, module, docker)
        if completed_module(directory, (module, modules[module]), (identity, bundle)):
            print('Verified completed catalogue assertions: ' + module, flush=True)
            continue
        print('Isolated catalogue assertions: ' + module, flush=True)
        GroupController((module, directory), (command, entrypoint, docker)).run(output)
    preserve_assembly(directory)
    result = subprocess.run(  # noqa: S603, S607 -- the same pinned controller and frozen directory.
        ('docker',) + command + entrypoint + ('--assemble-modules', '--report-directory', output),
        check=False, timeout=MODULE_TIMEOUT_SECONDS,
    )
    return result.returncode
