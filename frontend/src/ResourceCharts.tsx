import { useId } from 'react';
import { Box, Stack, Typography, useMediaQuery, useTheme } from '@mui/material';

export function formatBytes(value?: number) { return value === undefined ? 'Unavailable' : `${(value / 1024 ** 3).toFixed(2)} GiB`; }
export interface ResourceSample { time: number; memory: number; cpu?: number }
export function MemoryRing({ used, available, total }: { used: number; available: number; total: number }) {
  const theme = useTheme(); const ratio = Math.min(1, Math.max(0, used / total));
  return <Stack direction={{ xs: 'column', md: 'row' }} spacing={3} sx={{ alignItems: 'center' }}>
    <Box sx={{ position: 'relative', width: 150, height: 150, flexShrink: 0 }}>
      <svg viewBox="0 0 150 150" width="150" height="150" role="img" aria-label={`Memory: ${formatBytes(used)} used and ${formatBytes(available)} available out of ${formatBytes(total)}`}>
        <circle cx="75" cy="75" r="60" fill="none" stroke={theme.palette.action.hover} strokeWidth="14"/>
        <circle cx="75" cy="75" r="60" fill="none" stroke={theme.palette.primary.main} strokeWidth="14" strokeDasharray={`${ratio * 377} 377`} strokeLinecap="round" transform="rotate(-90 75 75)"/>
      </svg>
      <Box sx={{ position: 'absolute', inset: 0, display: 'grid', placeContent: 'center', textAlign: 'center' }}><Typography variant="h4" sx={{ fontWeight: 700 }}>{Math.round(ratio * 100)}%</Typography><Typography variant="caption" color="text.secondary">used</Typography></Box>
    </Box>
    <Stack spacing={1.5} sx={{ flex: 1, width: '100%' }}><Metric label="Used" value={formatBytes(used)} color="primary.main"/><Metric label="Available" value={formatBytes(available)}/><Metric label="Capacity" value={formatBytes(total)}/></Stack>
  </Stack>;
}
export function CpuRing({ percent, cores }: { percent?: number; cores?: number }) {
  const theme = useTheme(); const color = theme.palette.mode === 'dark' ? '#87c8e0' : '#267894';
  return <Stack direction={{ xs: 'column', md: 'row' }} spacing={3} sx={{ alignItems: 'center' }}>
    <Box sx={{ position: 'relative', width: 150, height: 150, flexShrink: 0 }}>
      <svg viewBox="0 0 150 150" width="150" height="150" role="img" aria-label={percent === undefined ? 'CPU: waiting for readings' : `CPU: ${percent.toFixed(1)}% used`}>
        <circle cx="75" cy="75" r="60" fill="none" stroke={theme.palette.action.hover} strokeWidth="14"/>
        <circle cx="75" cy="75" r="60" fill="none" stroke={color} strokeWidth="14" strokeDasharray={`${(percent ?? 0) / 100 * 377} 377`} strokeLinecap="round" transform="rotate(-90 75 75)"/>
      </svg>
      <Box sx={{ position: 'absolute', inset: 0, display: 'grid', placeContent: 'center', textAlign: 'center' }}><Typography variant="h4" sx={{ fontWeight: 700 }}>{percent === undefined ? '—' : `${percent.toFixed(1)}%`}</Typography><Typography variant="caption" color="text.secondary">used</Typography></Box>
    </Box>
    <Stack spacing={1.5} sx={{ flex: 1, width: '100%' }}><Metric label="Used" value={percent === undefined ? 'Waiting…' : `${percent.toFixed(1)}%`} color={color}/><Metric label="Available" value={percent === undefined ? 'Waiting…' : `${(100 - percent).toFixed(1)}%`}/><Metric label="Logical CPUs" value={cores?.toString() ?? 'Unavailable'}/></Stack>
  </Stack>;
}
export function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return <Stack direction="row" spacing={2} sx={{ alignItems: 'center', justifyContent: 'space-between' }}><Typography variant="body2" color="text.secondary">{label}</Typography><Typography variant="body2" sx={{ fontWeight: 650, color: color || 'text.primary', fontVariantNumeric: 'tabular-nums' }}>{value}</Typography></Stack>;
}
export function HistoryChart({ samples, metric, seconds, now }: { samples: ResourceSample[]; metric: 'memory' | 'cpu'; seconds: number; now: number }) {
  const theme = useTheme(); const small = useMediaQuery(theme.breakpoints.down('md')); const id = useId(); const memory = metric === 'memory';
  const values = samples.filter(sample => sample.time >= now / 1000 - seconds && sample[metric] !== undefined);
  const max = 100;
  const start = now / 1000 - seconds; const canvas = small ? 320 : 630; const width = canvas - 70, height = 150;
  const x = (time: number) => 48 + Math.min(1, Math.max(0, (time - start) / seconds)) * width;
  const y = (value: number) => 20 + height - value / max * height;
  let path = '';
  values.forEach((sample, index) => { path += `${index === 0 || sample.time - values[index - 1].time > 3 ? 'M' : 'L'}${x(sample.time).toFixed(2)},${y(sample[metric]!).toFixed(2)} `; });
  const color = memory ? theme.palette.primary.main : theme.palette.mode === 'dark' ? '#87c8e0' : '#267894';
  const title = `${memory ? 'Memory used' : 'CPU usage'} over the last ${seconds} seconds. ${values.length} measured samples.`;
  return <Box sx={{ minWidth: 0 }}>
    <svg viewBox={`0 0 ${canvas} 210`} width="100%" role="img" aria-labelledby={id} style={{ display: 'block', overflow: 'visible' }}>
      <title id={id}>{title}</title>
      {[0, .25, .5, .75, 1].map(tick => <g key={tick}><line x1="48" x2={48 + width} y1={20 + height * tick} y2={20 + height * tick} stroke={theme.palette.divider} strokeDasharray="3 5"/><text x="39" y={24 + height * tick} fill={theme.palette.text.secondary} fontSize="12" textAnchor="end">{`${Math.round(max * (1 - tick))}%`}</text></g>)}
      {[0, .5, 1].map(tick => <text key={tick} x={48 + width * tick} y="196" fill={theme.palette.text.secondary} fontSize="12" textAnchor={tick === 0 ? 'start' : tick === 1 ? 'end' : 'middle'}>{new Date((start + seconds * tick) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</text>)}
      <path d={path} stroke={color} strokeWidth="2.5" fill="none" strokeLinejoin="round" strokeLinecap="round"/>
      {values.length > 0 && <circle cx={x(values[values.length - 1].time)} cy={y(values[values.length - 1][metric]!)} r="4" fill={color}><title>{values[values.length - 1][metric]!.toFixed(2)} %</title></circle>}
      {values.length < 2 && <text x={48 + width / 2} y="90" fill={theme.palette.text.secondary} fontSize="13" textAnchor="middle">Collecting live samples…</text>}
    </svg>
    <Typography variant="caption" color="text.secondary">{values.length} samples</Typography>
  </Box>;
}
