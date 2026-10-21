"""Enumerate finite specifications; this does not execute the application."""

import json
from collections import deque
from dataclasses import dataclass, replace

from verification_support import require

STATE_LIMIT = 10000


@dataclass(frozen=True)
class Authentication:
    revision: int = 0
    worker_revision: int = -1
    verified: bool = False
    session_revision: int = -1
    session_valid: bool = False


def authentication_steps(state):
    result = []
    if state.worker_revision == -1:
        result.append(('start_challenge', replace(state, worker_revision=state.revision)))
    if state.worker_revision >= 0 and not state.verified:
        result.append(('verify_factor', replace(state, verified=True)))
    if state.revision == 0:
        result.append(('change_password', replace(state, revision=1, session_valid=False)))
    if state.verified:
        result.append(('complete_checked', complete_authentication(state)))
    return result


def complete_authentication(state):
    if state.worker_revision != state.revision:
        return replace(state, worker_revision=-1, verified=False)
    return replace(state, worker_revision=-1, verified=False,
                   session_revision=state.revision, session_valid=True)


def authentication_invariant(state):
    return not state.session_valid or state.session_revision == state.revision


@dataclass(frozen=True)
class Creation:
    credentials: bool = False
    storage: bool = False
    journal_owned: bool = False
    completion_known: bool = True
    phase: str = 'idle'


def creation_progress(state):
    result = []
    if state.phase == 'idle':
        result.append(('persist_intent', replace(state, journal_owned=True, phase='credentials')))
    if state.phase == 'credentials':
        result.append(('create_credentials', replace(state, credentials=True, phase='storage')))
        result.append(('credential_error', replace(state, phase='failed')))
    if state.phase == 'storage':
        result.append(('insert_storage', replace(state, storage=True, phase='inserted')))
        result.append(('storage_error_before_insert', replace(state, phase='failed')))
    if state.phase == 'inserted':
        result.append(('later_error', replace(state, phase='failed')))
        result.append(('complete_create', replace(state, phase='done')))
    return result


def creation_recovery(state):
    result = []
    if state.phase in {'credentials', 'storage', 'inserted'}:
        result.append(('crash', replace(state, completion_known=False, phase='unknown')))
    if state.phase == 'unknown':
        result.append(('observe_completion', replace(state, completion_known=True, phase='failed')))
    can_rollback = (state.phase == 'failed' and state.journal_owned and
                    state.completion_known and state.credentials and not state.storage)
    if can_rollback:
        result.append(('rollback_owned_credentials', replace(state, credentials=False, phase='rolled_back')))
    return result


def creation_steps(state):
    return creation_progress(state) + creation_recovery(state)


def creation_invariant(state):
    return not state.storage or state.credentials


@dataclass(frozen=True)
class Coordination:
    renewal_owner: str = ''
    maddy_owner: str = ''
    acme_writing: bool = False
    cli_running: bool = False
    unknown: bool = False
    backup_capturing: bool = False


def helper_steps(state):
    result = []
    if not state.maddy_owner and not state.unknown:
        result.append(('helper_start', replace(state, maddy_owner='helper', cli_running=True)))
    if state.maddy_owner == 'helper':
        result.append(('helper_finish', replace(state, maddy_owner='', cli_running=False)))
        result.append(('helper_crash_child_survives', replace(state, maddy_owner='', unknown=True)))
    return result


def renewal_steps(state):
    result = []
    if not state.renewal_owner and not state.unknown:
        result.append(('renewal_acquire_run_lock', replace(state, renewal_owner='renewal', acme_writing=True)))
    if state.renewal_owner == 'renewal' and state.acme_writing:
        result.append(('acquisition_finish', replace(state, acme_writing=False)))
    can_activate = (state.renewal_owner == 'renewal' and not state.acme_writing and
                    not state.maddy_owner and not state.unknown)
    if can_activate:
        result.append(('activation_acquire_maddy_lock', replace(state, maddy_owner='renewal')))
    if state.maddy_owner == 'renewal':
        result.append(('activation_finish', replace(state, renewal_owner='', maddy_owner='')))
    if state.renewal_owner == 'renewal':
        owner = '' if state.maddy_owner == 'renewal' else state.maddy_owner
        result.append(('renewal_crash_child_may_survive', replace(state, renewal_owner='', maddy_owner=owner, unknown=True)))
    return result


def backup_steps(state):
    result = []
    available = not state.renewal_owner and not state.maddy_owner and not state.unknown
    if available and not state.acme_writing and not state.cli_running:
        result.append(('backup_acquire_both_locks', replace(state, renewal_owner='backup', maddy_owner='backup', backup_capturing=True)))
    if state.backup_capturing:
        result.append(('backup_finish', replace(state, renewal_owner='', maddy_owner='', backup_capturing=False)))
        result.append(('backup_crash', replace(state, renewal_owner='', maddy_owner='', backup_capturing=False, unknown=True)))
    return result


def reconciliation_steps(state):
    result = []
    if state.unknown and state.acme_writing:
        result.append(('observe_acme_exit', replace(state, acme_writing=False)))
    if state.unknown and state.cli_running:
        result.append(('observe_cli_exit', replace(state, cli_running=False)))
    if state.unknown and not state.acme_writing and not state.cli_running:
        result.append(('reconcile_unknown', replace(state, unknown=False)))
    return result


def coordination_steps(state):
    return (helper_steps(state) + renewal_steps(state) + backup_steps(state) +
            reconciliation_steps(state))


def coordination_invariant(state):
    if state.maddy_owner == 'renewal' and state.cli_running:
        return False
    if not state.backup_capturing:
        return True
    return (state.renewal_owner == 'backup' and state.maddy_owner == 'backup' and
            not state.acme_writing and not state.cli_running and not state.unknown)


def explore(initial, transitions, invariant):
    queue = deque([(initial, ())])
    seen = {initial}
    while queue:
        state, trail = queue.popleft()
        require(invariant(state), 'Model counterexample: ' + ' -> '.join(trail))
        unseen = [(candidate, trail + (action,)) for action, candidate in transitions(state)
                  if candidate not in seen]
        seen.update(candidate for candidate, _ in unseen)
        require(len(seen) <= STATE_LIMIT, 'Model exploration exceeded its declared bound')
        queue.extend(unseen)
    return frozenset(seen)


def run_models():
    authentication = explore(Authentication(), authentication_steps, authentication_invariant)
    creation = explore(Creation(), creation_steps, creation_invariant)
    coordination = explore(Coordination(), coordination_steps, coordination_invariant)
    require(any(state.acme_writing and state.cli_running for state in coordination),
            'Coordination model must allow CLI work during acquisition')
    return {'authentication_revision': len(authentication), 'creation_preservation': len(creation),
            'maintenance_exclusion': len(coordination)}


if __name__ == '__main__':
    print(json.dumps({'scope': 'finite specifications, not application execution',
                      'reachable_states_checked': run_models()}, indent=2))
