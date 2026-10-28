import { useEffect, useState } from 'react';
import { Box, Pagination, TablePagination } from '@mui/material';

export function usePagination<T>(items: T[], resetKey = '') {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const current = Math.min(page, Math.max(0, Math.ceil(items.length / size) - 1));
  useEffect(() => { setPage(0); }, [resetKey]);
  useEffect(() => { if (page !== current) setPage(current); }, [page, current]);
  return { rows: items.slice(current * size, (current + 1) * size), controls: <PaginationControls count={items.length} page={current} size={size} onPage={setPage} onSize={value => { setSize(value); setPage(0); }}/> };
}

export function PaginationControls({ count, page, size, onPage, onSize }: { count: number; page: number; size: number; onPage: (page: number) => void; onSize: (size: number) => void }) {
  return <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, alignItems: 'center', justifyContent: 'space-between', px: 1, py: 1 }}>
    <TablePagination slotProps={{ select: { native: true } }} component="div" count={count} page={page} rowsPerPage={size} rowsPerPageOptions={[5, 10, 20, 50]} onPageChange={(_, value) => onPage(value)} onRowsPerPageChange={event => onSize(Number(event.target.value))} sx={{ '.MuiTablePagination-actions': { display: 'none' }, '.MuiTablePagination-toolbar': { flexWrap: 'wrap', px: 1 }, '.MuiTablePagination-spacer': { display: { xs: 'none', sm: 'block' } } }}/>
    <Pagination color="primary" shape="rounded" aria-label="Pages" count={Math.max(1, Math.ceil(count / size))} page={page + 1} onChange={(_, value) => onPage(value - 1)} showFirstButton showLastButton boundaryCount={2} siblingCount={1} sx={{ '& ul': { justifyContent: 'center', flexWrap: 'wrap' } }}/>
  </Box>;
}
