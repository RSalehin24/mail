export const adminEmail = 'rsalehin24@gmail.com';
export const domain = 'rsalehin24.me';

export class ApiError extends Error {
  constructor(public code: string, public status: number, public requestId = '') {
    super(`${code.replaceAll('_', ' ')}${requestId ? ` · Request ${requestId}` : ''}`);
  }
}

export async function api<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : method,
    credentials: 'same-origin', cache: 'no-store',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('mail-admin-session-expired'));
    if (result.error === 'recent_authentication_required') window.dispatchEvent(new Event('mail-admin-reauth-expired'));
    throw new ApiError(result.error ?? 'request_failed', response.status, result.operation_id ?? result.request_id);
  }
  return result as T;
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
  email: string; display_name: string; notes: string; status: string;
  credentials_present: boolean; storage_present: boolean; outbound_readiness: string;
}
export interface Outcome { state: string; warning: string; request_id: string }
export interface Diagnostics {
  host: { state?: string; reason?: string; summary?: {
    observed_at: number; scope: string; memory_available_bytes?: number; disk_free_bytes?: number;
    backup?: { cutoff?: string; verified?: boolean; available?: boolean; state?: string };
    certificates?: { expiry?: number; outcome?: string; state?: string };
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
