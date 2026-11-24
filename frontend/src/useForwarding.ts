import { useCallback, useEffect, useState } from 'react';
import { api, type Authorize, type Mailbox, type Notify } from './api';

export interface Rule { email: string; destination: string; enabled: boolean; keep_local: true; pending: number; attention: number; destination_verified?: boolean; local_delivery_verified?: boolean; synchronized?: boolean; verification_state?: string; last_result: {state: string; error: string; updated: number} | null }
export interface Listing { connection?: {configured: boolean; state: string; observed_at?: number; reason?: string; mailboxes?: Record<string, {local_delivery_verified: boolean}>; connected_mailboxes?: string[]}; rules: Rule[]; revision: number; mode: string; keep_local: true }

const REFRESH_MILLISECONDS = 15000;

// Forwarding rules, the Cloudflare connection and the mailbox list, refreshed together; shared by the Forwarding and Incoming pages.
export function useForwarding(authorize: Authorize, notify: Notify) {
  const [result, setResult] = useState<Listing | null>(null);
  const [accounts, setAccounts] = useState<Mailbox[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const [rules, mailboxes] = await Promise.all([api<Listing>('/forwarding'), api<Mailbox[]>('/accounts')]);
      setResult(rules); setAccounts(mailboxes); setError('');
    } catch (failure) { setError((failure as Error).message); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => { void load(); }, REFRESH_MILLISECONDS); return () => window.clearInterval(timer); }, [load]);
  function perform(operation: () => Promise<Listing>, message: string, onDone?: () => void) {
    authorize(async () => {
      setBusy(true); setError('');
      try { setResult(await operation()); onDone?.(); notify(message); }
      catch (failure) { setError((failure as Error).message); throw failure; }
      finally { setBusy(false); }
    });
  }
  return { result, accounts, busy, error, load, perform };
}
