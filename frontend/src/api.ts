export let adminEmail = '';
export let domain = '';
export let relayHost = '';
export interface Installation { kind: 'legacy' | 'management' | 'webmail'; version: string; configured: boolean; initialized: boolean; domain: string; hostname: string; mail_hostname: string; admin_email: string; relay_host: string }
export let installationKind: Installation['kind'] = 'legacy';
export async function loadInstallation(): Promise<Installation> {
  const value = await api<Installation>('/installation');
  adminEmail = value.admin_email; domain = value.domain; relayHost = value.relay_host; installationKind = value.kind;
  return value;
}

const errorMessages: Record<string, string> = {
  mail_management_helper_upgrade_required: 'Upgrade the local Mail Management helper before using Webmail.',
  disable_mailbox_access_before_pairing: 'Disable mailbox access before pairing with a different installation.',
  management_connection_unavailable: 'Mail Management is unreachable. Check its hostname, firewall, TLS certificate and pairing status.',
  integration_upgrade_required: 'The applications use incompatible integration versions. Upgrade the older application and check pairing again.',
  integration_credential_revoked_or_expired: 'The pairing credential was revoked or expired. Ask the Webmail administrator to pair again.',
  mail_management_pairing_required: 'Pair Webmail with Mail Management in administration settings.',
  two_server_qualification_required: 'The operator must complete two-server capacity qualification before enabling mailboxes.',
  pilot_mailbox_required: 'Select one mailbox for the initial pilot.',
  mailbox_identity_reconciliation_required: 'Your mailbox changed. Ask your administrator to reconnect webmail.',
  email_delivery_confirmation_required: 'Confirm receipt of an administrator delivery test before enabling email codes.',
  invalid_master_password: 'Invalid master password.',
  authentication_failed: 'Authentication failed. Check your details and try again.',
  invalid_verification_code: 'Invalid verification code.',
  authentication_required: 'Please sign in to continue.',
  recent_authentication_required: 'Please verify your identity to continue.',
  rate_limited: 'Too many attempts. Please try again later.',
  work_overloaded: 'The server is busy. Please try again shortly.',
  work_unavailable: 'The service is temporarily unavailable. Please try again shortly.',
  service_temporarily_unavailable: 'The service is temporarily unavailable. Please try again shortly.',
  invalid_server_response: 'The server could not complete your request. Please try again.',
  invalid_request: 'Check the details you entered and try again.',
  request_failed: 'Your request could not be completed. Please try again.',
  password_confirmation_mismatch: 'The passwords do not match.',
  account_not_found: 'This mailbox could not be found.',
  account_already_exists: 'This mailbox already exists.',
  recovery_operation_in_progress: 'A recovery operation is already in progress.',
  operation_pending: 'This operation is still in progress. Please wait.',
  email_delivery_failed: 'The email could not be sent. Please try again.',
  relay_not_configured: 'Set up your outgoing mail provider first.',
  helper_unavailable: 'The mail service is temporarily unavailable. Please try again.',
};

export function errorMessage(code: string): string {
  if (errorMessages[code]) return errorMessages[code];
  const words = code.replaceAll('_', ' ').trim().replace(/[.]+$/, '');
  return words ? words.charAt(0).toUpperCase() + words.slice(1) + '.' : errorMessages.request_failed;
}

export class ApiError extends Error {
  constructor(public code: string, public status: number, public requestId = '', message?: string, public revoked = false) {
    super(message ?? errorMessage(code));
  }
}

const cache = new Map<string, unknown>();
const pending = new Map<string, Promise<unknown>>();
let epoch = 0;
const lanes = { primary: { active: 0, waiting: [] as (() => void)[] }, telemetry: { active: 0, waiting: [] as (() => void)[] } };
const controllers = new Set<AbortController>();

export function cached<T>(path: string): T | undefined { return cache.get(path) as T | undefined; }
export function clearSessionData() {
  epoch++; cache.clear(); pending.clear();
  for (const controller of controllers) controller.abort();
}
window.addEventListener('mail-admin-session-expired', clearSessionData);

export async function api<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  if (body !== undefined) {
    const result = await request<T>(path, body, method);
    if (/^\/(logout|login|auth|security|setup)/.test(path)) clearSessionData();
    else { epoch++; cache.clear(); pending.clear(); }
    return result;
  }
  const existing = pending.get(path);
  if (existing) return existing as Promise<T>;
  const generation = epoch;
  const read = (async () => {
    const lane = path === '/telemetry' ? lanes.telemetry : lanes.primary;
    const limit = path === '/telemetry' ? 1 : 2;
    if (lane.active >= limit) await new Promise<void>(resolve => lane.waiting.push(resolve));
    else lane.active++;
    const controller = new AbortController(); controllers.add(controller);
    try {
      if (generation !== epoch) throw new DOMException('Session changed', 'AbortError');
      const result = await request<T>(path, undefined, 'GET', controller.signal);
      if (generation === epoch) cache.set(path, result);
      return result;
    } finally {
      controllers.delete(controller);
      const next = lane.waiting.shift(); if (next) next(); else lane.active--;
    }
  })();
  pending.set(path, read);
  try { return await read; }
  finally { if (pending.get(path) === read) pending.delete(path); }
}

async function request<T>(path: string, body?: unknown, method = 'POST', signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await fetch(`/api${path}`, {
      signal, method: body === undefined ? 'GET' : method,
      credentials: 'same-origin', cache: 'no-store',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    let result;
    try { result = await response.json(); }
    catch {
      if (body === undefined && response.status === 503 && attempt === 0) {
        await new Promise(resolve => setTimeout(resolve, 1000)); continue;
      }
      throw new ApiError(response.status === 503 ? 'service_temporarily_unavailable' : 'invalid_server_response', response.status);
    }
    if (!response.ok) {
      if (body === undefined && response.status === 503 && attempt === 0 && ['work_overloaded', 'work_unavailable'].includes(result.error)) {
        await new Promise(resolve => setTimeout(resolve, 1000)); continue;
      }
      if (response.status === 401 && result.error === 'authentication_required') window.dispatchEvent(new Event('mail-admin-session-expired'));
      if (result.error === 'recent_authentication_required') window.dispatchEvent(new Event('mail-admin-reauth-expired'));
      const code = result.error ?? 'request_failed';
      const authenticationMessage = code === 'authentication_failed'
        ? errorMessage(path === '/login' ? 'authentication_failed' : path === '/security/reauth' ? 'invalid_master_password' : 'invalid_verification_code')
        : undefined;
      throw new ApiError(code, response.status, result.operation_id ?? result.request_id, authenticationMessage, result.revoked === true);
    }
    return result as T;
  }
  throw new ApiError('service_temporarily_unavailable', 503);
}

export function generatePassword(): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  return Array.from(crypto.getRandomValues(new Uint8Array(24)), value => alphabet[value & 63]).join('');
}

export function validMailboxPassword(value: string): boolean {
  return [...value].length >= 16 && new TextEncoder().encode(value).length <= 72 && !/[\r\n\0]/.test(value);
}

export type Authorize = (operation: () => Promise<void>) => void;
export type Notify = (message: string) => void;
export interface Enrollment { secret: string; uri: string; qr: string }
export interface Mailbox {
  created?: number; updated?: number; email: string; display_name: string; notes: string; status: string;
  credentials_present: boolean; storage_present: boolean; outbound_readiness: string;
  current_relay_revision?: number;
  sender_history?: { id: string; revision: number; connection_ok: number; accepted: number; confirmed: number; created: number }[];
  sender_tests?: { id: string; accepted: number; confirmed: number; created: number }[];
}
export interface Outcome { state: string; warning: string; request_id: string }
export interface Diagnostics {
  host: { maddy?: { state: string; observed_at?: number; reason?: string; credential_count?: number; storage_count?: number }; state?: string; reason?: string; summary?: {
    observed_at: number; scope: string; cpu_utilization_percent?: number; cpu_sample_interval_seconds?: number; cpu_sampled_at?: number; cpu_total_ticks?: number; cpu_idle_ticks?: number; cpu_logical_count?: number; memory_available_bytes?: number; memory_total_bytes?: number; disk_free_bytes?: number; disk_total_bytes?: number; disk_used_bytes?: number; disk_reserved_bytes?: number; inodes_free?: number; thresholds?: { disk_free_minimum_bytes?: number; inode_free_minimum?: number; recovery_point_seconds?: number };
    backup?: { cutoff?: string; verified?: boolean; available?: boolean; state?: string; observed_at?: number; availability_observed_at?: number; restore_passed?: boolean; reason?: string };
    monitoring?: { state?: string; outcome?: string; observed_at?: number; reason?: string };
    external_monitoring?: { state?: string; outcome?: string; enabled?: boolean | null; provider?: string; configured?: boolean; last_verified_at?: number; observed_at?: number; reason?: string };
    certificates?: { served_sha256?: string; renewal_observed_at?: number; reason?: string; expiry?: number; outcome?: string; state?: string; observed_at?: number; consumers?: Record<string, { state?: string; reason?: string; served_sha256?: string; served_expiry?: number }> };
  }; unresolved?: unknown[] };
  dns_mx: { state: string; configured_mode: string; records?: string[] };
  relay: { state: string };
  capabilities: { permanent_deletion: boolean; email_otp: boolean };
}
export interface RelayTest {
  id: string; sender: string; recipient: string; revision: number; created: number;
  accepted: number; confirmed: number; connection_ok: number;
}
export interface RelaySettings {
  configured: boolean; host: string; port: number; username: string; revision: number;
  otp_ready: boolean; tests: RelayTest[];
}
