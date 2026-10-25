"""Observe real staging, publication and activation before terminating the writer."""

import os
from contextlib import ExitStack
from unittest.mock import patch

from shared.files import atomic_json
from tests.mutation_crash_support import CrashObserver
from tests.process_fault_support import kill_at_observed_boundary


def interrupt_generation(host, source, boundary):
    kill_at_observed_boundary(_generation_child, (host, source, boundary), boundary)


def _generation_child(channel, operation):
    host, source, boundary = operation
    observer = GenerationObserver(channel, (host, boundary))
    with observer.scope(), host.coordination.consistent():
        generation = host.generations.stage(source)
        host.generations.publish(generation)
        host.generations.activate(host.renewal.consumers)
    channel.send(('unexpected_completion', None))


class GenerationObserver:
    def __init__(self, channel, selection):
        self.host, boundary = selection
        self.observer = CrashObserver(channel, boundary)
        self.stage_file, self.rename = self.host.generations._stage_file, os.rename
        self.published = False

    def staged_file(self, path, value):
        self.stage_file(path, value)
        self.observer.reached(('staged', path.name))

    def renamed(self, source, target):
        self.observer.reached(('before_rename', None))
        self.rename(source, target)
        self.observer.reached(('after_rename', None))

    def written(self, path, record):
        publication = not self.published and 'desired_generation' in record
        if publication:
            self.observer.reached(('before_publication', None))
        atomic_json(path, record)
        if publication:
            self.published = True
            self.observer.reached(('after_publication', None))

    def activation(self, role, activate):
        def observed(generation, certificate):
            activate(generation, certificate)
            self.observer.reached(('consumer', role))
        return observed

    def scope(self):
        scope = ExitStack()
        scope.enter_context(patch.object(self.host.generations, '_stage_file', self.staged_file))
        scope.enter_context(patch('host.certificates.os.rename', side_effect=self.renamed))
        scope.enter_context(patch('host.certificates.atomic_json', side_effect=self.written))
        for role, consumer in self.host.renewal.consumers.items():
            scope.enter_context(patch.object(consumer, 'activate',
                side_effect=self.activation(role, consumer.activate)))
        return scope
