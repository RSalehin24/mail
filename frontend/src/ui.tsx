import type { ReactNode } from 'react';
import { Box, Paper, Stack, Typography } from '@mui/material';

export type Tone = 'success' | 'warning' | 'error' | 'neutral';
const TONE_COLOR: Record<Tone, string> = {
  success: 'var(--mui-palette-success-main)', warning: 'var(--mui-palette-warning-main)',
  error: 'var(--mui-palette-error-main)', neutral: 'var(--mui-palette-text-secondary)',
};

// A coloured dot plus a text label: status without heavy chips.
export function StatusDot({ tone, label }: { tone: Tone; label: string }) {
  return <Stack direction="row" spacing={0.9} sx={{ alignItems: 'center', display: 'inline-flex' }}>
    <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: TONE_COLOR[tone], flexShrink: 0 }}/>
    <Typography variant="body2" component="span">{label}</Typography>
  </Stack>;
}

export function StatCard({ label, value, detail, tone = 'neutral' }: { label: string; value: ReactNode; detail?: ReactNode; tone?: Tone }) {
  return <Paper variant="outlined" sx={{ p: 2.25, minWidth: 0 }}>
    <Typography variant="body2" color="text.secondary">{label}</Typography>
    <Typography variant="h5" component="p" sx={{ mt: 0.5, color: tone === 'neutral' ? 'text.primary' : TONE_COLOR[tone] }}>{value}</Typography>
    {detail && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>{detail}</Typography>}
  </Paper>;
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' } }}>{children}</Box>;
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' }, mb: 3 }}>
    <Box><Typography variant="h4" component="h1">{title}</Typography>
      {subtitle && <Typography color="text.secondary" sx={{ mt: 0.5 }}>{subtitle}</Typography>}</Box>
    {action}
  </Stack>;
}
