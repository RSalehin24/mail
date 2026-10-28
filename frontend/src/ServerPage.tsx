import { formatDate, formatDateTime } from './format';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, Divider, LinearProgress, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { api, cached, type Diagnostics } from './api';
import { formatBytes, HistoryChart, CpuRing, MemoryRing, type ResourceSample } from './ResourceCharts';

type Host = Diagnostics['host'];
function label(value?: string) { return value ? value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase()) : 'Not recorded'; }
function date(value?: number) { return value ? formatDateTime(value) : 'Not recorded'; }
function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <Paper component="section" variant="outlined" sx={{ p: { xs: 2.5, sm: 3 }, borderRadius: '16px', minWidth: 0 }}><Typography component="h2" variant="h6" sx={{ fontWeight: 650, mb: 2.5 }}>{title}</Typography>{children}</Paper>; }
function Observation({ title, value, at, now, maxAge, children }: {
  title: string; value: string; at?: number; now: number; maxAge?: number; children?: React.ReactNode;
}) {
  const age = at ? Math.max(0, now / 1000 - at) : undefined;
  const unavailable = age === undefined || (at ?? 0) > now / 1000 + 5;
  const stale = !unavailable && maxAge !== undefined && age! > maxAge;
  const ageLabel = age === undefined ? '' : age < 5 ? 'Just now' : age < 60 ? `${Math.floor(age)}s ago` : age < 3600 ? `${Math.floor(age / 60)}m ago` : age < 86400 ? `${Math.floor(age / 3600)}h ago` : `${Math.floor(age / 86400)}d ago`;
  return <Box sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}>
    <Stack direction="row" spacing={1} sx={{ alignItems: 'start', justifyContent: 'space-between', mb: 1 }}><Typography variant="body2" color="text.secondary">{title}</Typography><Chip size="small" variant="outlined" color={stale ? 'warning' : 'default'} label={unavailable ? 'Unavailable' : stale ? 'Stale' : maxAge === undefined ? 'Recorded' : 'Fresh'}/></Stack>
    <Typography variant="body1" sx={{ fontWeight: 650 }}>{value}</Typography>
    {at && !unavailable ? <Stack spacing={.25} sx={{ mt: 1 }}><Typography variant="caption" color={stale ? 'warning.main' : 'text.secondary'}>Checked {ageLabel}</Typography><Typography component="time" dateTime={new Date(at * 1000).toISOString()} variant="caption" color="text.secondary">{formatDateTime(at)}</Typography></Stack> : <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>No observation recorded</Typography>}
    {children}
  </Box>;
}

export function ServerPage() {
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(() => cached<Diagnostics>('/diagnostics') ?? null);
  const [live, setLive] = useState<Host | null>(() => cached<Host>('/telemetry') ?? null);
  const [samples, setSamples] = useState<ResourceSample[]>([]);
  const [error, setError] = useState('');
  const [checkError, setCheckError] = useState('');
  const [checking, setChecking] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [seconds, setSeconds] = useState(30);
  const running = useRef(false);
  const checks = useCallback(async () => {
    if (running.current) return; running.current = true; setChecking(true);
    try { setDiagnostics(await api<Diagnostics>('/diagnostics')); setCheckError(''); }
    catch (failure) { setCheckError((failure as Error).message); }
    finally { running.current = false; setChecking(false); }
  }, []);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    let first = true;
    async function poll() {
      const started = performance.now();
      try {
        const result = await api<Host>('/telemetry');
        if (!active) return;
        if (!result.summary) throw new Error(`Host observations unavailable: ${result.reason || 'check the helper service'}`);
        setLive(result); setError('');
        const host = result.summary;
        const cpu = host.cpu_utilization_percent;
        if (host.memory_total_bytes && host.memory_available_bytes !== undefined && host.disk_free_bytes !== undefined) {
          const sample = { time: host.observed_at, memory: 100 * (host.memory_total_bytes - host.memory_available_bytes) / host.memory_total_bytes, cpu };
          setSamples(previous => [...previous.filter(value => value.time > sample.time - 300 && value.time !== sample.time), sample].slice(-301));
        }
        if (first) { first = false; void checks(); }
      } catch (failure) { if (active) setError((failure as Error).message); }
      if (active) timer = setTimeout(() => { void poll(); }, Math.max(100, 1000 - (performance.now() - started)));
    }
    void poll(); const clock = setInterval(() => setNow(Date.now()), 1000);
    const serviceChecks = setInterval(() => { void checks(); }, 60000);
    return () => { active = false; clearTimeout(timer); clearInterval(clock); clearInterval(serviceChecks); };
  }, [checks]);
  const host = live?.summary ?? diagnostics?.host.summary;
  const stale = !host || now / 1000 - host.observed_at > 3 || host.observed_at > now / 1000 + 5;
  const backup = host?.backup, certificate = host?.certificates;
  const capacity = host?.disk_total_bytes;
  const cpu = !stale && host?.cpu_sampled_at && now / 1000 - host.cpu_sampled_at <= 3 ? host.cpu_utilization_percent : undefined;
  const memoryUsed = host?.memory_total_bytes !== undefined && host.memory_available_bytes !== undefined ? host.memory_total_bytes - host.memory_available_bytes : undefined;
  const diskLow = host?.disk_free_bytes !== undefined && host.thresholds?.disk_free_minimum_bytes !== undefined && host.disk_free_bytes < host.thresholds.disk_free_minimum_bytes;
  return <Stack spacing={3} data-testid="server-page" data-sample-count={samples.length}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}>
      <Box><Typography variant="h4" component="h1">Server</Typography></Box>
      <Chip variant="outlined" color={stale ? 'warning' : 'success'} label={!host ? 'Connecting…' : stale ? 'Updates delayed' : 'Live · every second'}/>
    </Stack>
    {error && <Alert severity="warning">{error}. Retrying automatically; previous readings remain visible with their timestamps.</Alert>}
    {host && <Typography variant="caption" color="text.secondary">Last resource reading · {date(host.observed_at)}</Typography>}
    <Stack direction="row" sx={{ justifyContent: 'flex-end', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}><Typography variant="body2" color="text.secondary">History</Typography><ToggleButtonGroup exclusive size="small" value={seconds} onChange={(_, value: number | null) => { if (value) setSeconds(value); }} aria-label="CPU and memory history time range"><ToggleButton value={30}>30 seconds</ToggleButton><ToggleButton value={60}>1 minute</ToggleButton><ToggleButton value={300}>5 minutes</ToggleButton></ToggleButtonGroup></Stack>
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 3 }}>
      <Panel title="CPU Usage"><CpuRing percent={cpu} cores={host?.cpu_logical_count}/><Divider sx={{ my: 3 }}/><HistoryChart samples={samples} metric="cpu" seconds={seconds} now={now}/></Panel>
      <Panel title="Memory Usage">{host?.memory_total_bytes && host.memory_available_bytes !== undefined && memoryUsed !== undefined ? <MemoryRing used={memoryUsed} available={host.memory_available_bytes} total={host.memory_total_bytes}/> : <Typography color="text.secondary">Waiting for memory readings…</Typography>}<Divider sx={{ my: 3 }}/><HistoryChart samples={samples} metric="memory" seconds={seconds} now={now}/></Panel>
    </Box>
    <Panel title="Disk Space"><Stack spacing={2.5}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' }, gap: 2.5 }}>
        <Box><Typography variant="caption" color="text.secondary">Available</Typography><Typography variant="h5" sx={{ fontWeight: 700, mt: .5 }}>{formatBytes(host?.disk_free_bytes)}</Typography></Box>
        <Box><Typography variant="caption" color="text.secondary">Used</Typography><Typography variant="h5" sx={{ fontWeight: 650, mt: .5 }}>{formatBytes(host?.disk_used_bytes)}</Typography></Box>
        <Box><Typography variant="caption" color="text.secondary">Capacity</Typography><Typography variant="h5" sx={{ fontWeight: 650, mt: .5 }}>{formatBytes(capacity)}</Typography></Box>
        <Box><Typography variant="caption" color="text.secondary">Warning Below</Typography><Typography variant="h5" sx={{ fontWeight: 650, mt: .5 }}>{formatBytes(host?.thresholds?.disk_free_minimum_bytes)}</Typography></Box>
      </Box>
      {capacity && host?.disk_used_bytes !== undefined && <LinearProgress aria-label="Used disk capacity" variant="determinate" value={100 * Math.min(1, Math.max(0, host.disk_used_bytes / capacity))} sx={{ height: 12, borderRadius: 3, bgcolor: 'action.hover', '& .MuiLinearProgress-bar': { borderRadius: 3, bgcolor: diskLow ? 'warning.main' : '#788d86' } }}/>}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>{host?.inodes_free !== undefined && <Typography variant="caption" color="text.secondary">{host.inodes_free.toLocaleString()} free inodes</Typography>}{!!host?.disk_reserved_bytes && <Typography variant="caption" color="text.secondary">{formatBytes(host.disk_reserved_bytes)} reserved by the filesystem</Typography>}</Stack>
      {diskLow && <Alert severity="warning">Free space is below the configured threshold. Review disk usage and backup staging.</Alert>}
    </Stack></Panel>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { xs: 'start', sm: 'center' } }}><Typography variant="h6" component="h2" sx={{ fontWeight: 650 }}>Service observations</Typography><Button variant="outlined" disabled={checking} onClick={() => { void checks(); }}>{checking ? 'Checking…' : 'Refresh checks'}</Button></Stack>
    {checkError && <Alert severity="warning">{checkError}. Use Refresh checks to retry the service probe.</Alert>}
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 3 }}>
      <Panel title="Services and Certificates"><Stack spacing={2}>
        <Observation title="Dashboard API" value={host && !stale ? 'Responding' : 'Awaiting response'} at={host?.observed_at} now={now} maxAge={3}/>
        <Observation title="Mailbox Inventory" value={diagnostics?.host.maddy?.state === 'available' ? 'Responded' : 'Not available'} at={diagnostics?.host.maddy?.observed_at} now={now} maxAge={300}/>
        {(['maddy', 'mail-admin'] as const).map(name => { const consumer = certificate?.consumers?.[name]; const expiry = consumer?.served_expiry ?? (consumer?.served_sha256 && consumer.served_sha256 === certificate?.served_sha256 ? certificate.expiry : undefined); return <Observation key={name} title={name === 'maddy' ? 'IMAP Certificate' : 'HTTPS Certificate'} value={expiry ? `Expires ${formatDate(expiry)}` : 'Certificate unavailable'} at={certificate?.observed_at} now={now} maxAge={600}>
          {consumer?.reason && <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1 }}>Verify the endpoint and certificate chain: {consumer.reason.replaceAll('_', ' ')}.</Typography>}
        </Observation>; })}
        <Observation title="Renewal Record" value={label(certificate?.outcome)} at={certificate?.renewal_observed_at} now={now}/>
        {!certificate?.consumers && <Alert severity="info">Run the certificate monitor to record served certificates.</Alert>}
      </Stack></Panel>
      <Panel title="Backup and Recovery"><Stack spacing={2}>
        <Observation title="Backup Integrity" value={backup?.verified ? 'Verified' : 'Not verified'} at={backup?.availability_observed_at} now={now}>
          {backup?.cutoff && <Box sx={{ borderTop: '1px solid', borderColor: 'divider', pt: 1.5, mt: 1.5 }}><Typography variant="caption" color="text.secondary">Recovery cutoff</Typography><Typography variant="body2" sx={{ mt: .5 }}>{formatDateTime(backup.cutoff)}</Typography></Box>}
        </Observation>
        <Observation title="Archive Availability" value={backup?.available ? 'Last check passed' : 'Not available'} at={backup?.availability_observed_at} now={now} maxAge={host?.thresholds?.recovery_point_seconds ?? 300}/>
        <Observation title="Full-Server Recovery Test" value={backup?.restore_passed ? 'Passed for this backup' : 'Not recorded for this backup'} now={now}/>
        {!backup?.verified && <Alert severity="info">Run backup verification to record a verified backup.</Alert>}
        {backup?.cutoff && host?.thresholds?.recovery_point_seconds !== undefined && now / 1000 - Date.parse(backup.cutoff) / 1000 > host.thresholds.recovery_point_seconds && <Alert severity="warning">The backup cutoff exceeds the configured limit. Run and verify a fresh backup.</Alert>}
      </Stack></Panel>
    </Box>
    <Panel title="Monitoring"><Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
      <Observation title="Host Checks" value={host?.monitoring?.outcome ? label(host.monitoring.outcome) : 'No run recorded'} at={host?.monitoring?.observed_at} now={now} maxAge={600}/>
      <Observation title="Independent Availability Alerts" value={host?.external_monitoring?.enabled ? 'Enabled' : 'Activation not recorded'} at={host?.external_monitoring?.observed_at} now={now} maxAge={86400}/>
    </Box></Panel>
  </Stack>;
}
