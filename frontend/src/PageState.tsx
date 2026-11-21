import { Box, Skeleton, Typography } from '@mui/material';

// Shared loading and empty states so every page reports them the same way.
export function LoadingState({ label }: { label: string }) {
  return <Box role="status" aria-label={label}>
    <Typography color="text.secondary" sx={{ mb: 1.5 }}>{label}</Typography>
    <Skeleton/><Skeleton width="80%"/>
  </Box>;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return <Box sx={{ py: 8, textAlign: 'center' }}>
    <Typography variant="h6">{title}</Typography>
    {hint && <Typography color="text.secondary" variant="body2" sx={{ mt: 1 }}>{hint}</Typography>}
  </Box>;
}
