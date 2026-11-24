// How a mailbox's outgoing verification is shown, shared by the Outgoing page and the verify dialog so both always agree.
export interface SendingState { color: 'success' | 'warning' | 'error'; label: string; action: string }
const STATES: Record<string, SendingState> = {
  confirmed: { color: 'success', label: 'Verified', action: '' },
  'awaiting receipt': { color: 'warning', label: 'Awaiting receipt', action: 'Confirm receipt' },
  failed: { color: 'error', label: 'Test failed', action: 'Verify delivery' },
};
const NOT_TESTED: SendingState = { color: 'warning', label: 'Verification required', action: 'Verify delivery' };
export function sendingState(readiness: string): SendingState { return STATES[readiness] ?? NOT_TESTED; }
