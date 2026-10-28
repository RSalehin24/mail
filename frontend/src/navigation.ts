import { useEffect, useState } from 'react';

export function navigate(path: string) {
  if (window.location.pathname !== path) { window.history.pushState(null, '', path); window.scrollTo(0, 0); }
  window.dispatchEvent(new PopStateEvent('popstate'));
}
export function mailboxPath(email: string, tab = 'details') {
  return `/mailboxes/${encodeURIComponent(email)}/${tab}`;
}
export function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  return path;
}
