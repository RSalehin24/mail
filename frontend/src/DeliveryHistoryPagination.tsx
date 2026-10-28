import { useEffect, useState } from 'react';
import { api, type RelaySettings } from './api';
import { PaginationControls } from './PaginationControls';

type DeliveryTest = RelaySettings['tests'][number];
interface HistoryPage { items: DeliveryTest[]; total: number; page: number }
export function useDeliveryHistory(email: string, refreshKey: string) {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const [result, setResult] = useState<HistoryPage | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void api<HistoryPage>(`/relay-tests?sender=${encodeURIComponent(email)}&page=${page + 1}&page_size=${size}`).then(value => { if (active) { setResult(value); if (page !== value.page - 1) setPage(value.page - 1); setError(''); } }).catch(failure => { if (active) setError((failure as Error).message); });
    return () => { active = false; };
  }, [email, page, size, refreshKey]);
  return { rows: result?.items ?? [], error, controls: result && <PaginationControls count={result.total} page={result.page - 1} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/> };
}
