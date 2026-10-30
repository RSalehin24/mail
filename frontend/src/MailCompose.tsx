import { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, LinearProgress, Stack, TextField, Typography } from '@mui/material';
import { api, ApiError } from './api';
import { type Attachment, type ComposeStart, type MailProfile, type MessageRef, type SendOperation, submissionStates } from './webmailTypes';

interface Upload { id: string; filename: string; size: number }

export function MailCompose({ start, profile, onClose, onChanged }: { start: ComposeStart; profile: MailProfile; onClose: () => void; onChanged: () => void }) {
  const original = start.message;
  const draftMode = start.mode === 'draft';
  const reply = start.mode === 'reply' || start.mode === 'reply-all';
  const forward = start.mode === 'forward';
  const signature = profile.signature ? `\n\n${profile.signature}` : '';
  const emailList = (value: string) => [...value.matchAll(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi)].map(match => match[0]).filter(value => value.toLowerCase() !== profile.email.toLowerCase());
  const replyTo = original?.reply_to || original?.from || '';
  const [to, setTo] = useState(draftMode ? original?.to || '' : reply ? replyTo : '');
  const [cc, setCc] = useState(draftMode ? original?.cc || '' : start.mode === 'reply-all' ? [...new Set(emailList(`${original?.to},${original?.cc}`))].filter(value => !emailList(replyTo).includes(value)).join(', ') : '');
  const [bcc, setBcc] = useState(draftMode ? original?.bcc || '' : '');
  const [subject, setSubject] = useState(draftMode ? original?.subject || '' : original ? `${reply ? (/^re:/i.test(original.subject) ? '' : 'Re: ') : 'Fwd: '}${original.subject}` : '');
  const [text, setText] = useState(draftMode ? original?.text || '' : original ? `${signature}\n\n${forward ? '---------- Forwarded message ----------' : `On ${original.date}, ${original.from} wrote:`}\n${original.text.split('\n').map(line => reply ? `> ${line}` : line).join('\n')}` : signature);
  const [draft, setDraft] = useState<MessageRef | null>(draftMode ? original?.ref || null : null);
  const [attachments, setAttachments] = useState((forward || draftMode) ? original?.attachments || [] : []);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dirty, setDirty] = useState(false);
  const [autoSaveBlocked, setAutoSaveBlocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const [operation, setOperation] = useState<SendOperation | null>(null);
  const operationId = useRef(crypto.randomUUID().replaceAll('-', ''));
  const autoSave = useRef<() => void>(() => {});
  const [attachmentSource, setAttachmentSource] = useState<MessageRef | null>(original?.ref || null);
  const payload = () => ({ to, cc, bcc, subject, text, draft, uploads: uploads.map(file => file.id), attachments: attachments.map(file => file.part), attachment_source: attachmentSource,
    in_reply_to: reply ? original?.message_id || '' : draftMode ? original?.in_reply_to || '' : '', references: reply ? `${original?.references || ''} ${original?.message_id || ''}`.trim() : draftMode ? original?.references || '' : '' });
  async function save() {
    if (busy || uploading || operation) return;
    setBusy(true); setError('');
    try {
      const result = await api<{ ref: MessageRef; warning: string; attachments: Attachment[] }>('/webmail/drafts', payload());
      setDraft(result.ref); setAttachmentSource(result.ref); setAttachments(result.attachments);
      for (const file of uploads) void api(`/webmail/uploads/${file.id}`, {}, 'DELETE').catch(() => {});
      setUploads([]); setDirty(false); setAutoSaveBlocked(false); setSaved(`Saved on server at ${new Date().toLocaleTimeString()}`);
      if (result.warning) setError('The new draft was saved; the previous copy could not be removed.');
      onChanged();
    } catch (failure) { setError((failure as Error).message); setAutoSaveBlocked(true); }
    finally { setBusy(false); }
  }
  autoSave.current = () => { if (dirty && !autoSaveBlocked && !document.hidden) void save(); };
  useEffect(() => { const timer = setInterval(() => autoSave.current(), 30000); return () => clearInterval(timer); }, []);
  async function send() {
    setBusy(true); setError('');
    try {
      const result = await api<SendOperation>('/webmail/send', { ...payload(), operation_id: operationId.current });
      setOperation(result); setDirty(false); onChanged();
    } catch (failure) {
      setError((failure as Error).message);
      if (!(failure instanceof ApiError) || failure.code === 'operation_pending' || failure.status >= 500 && !['compose_busy', 'webmail_busy', 'work_overloaded', 'work_unavailable', 'attachment_capacity_busy'].includes(failure.code)) {
        // Preserve the operation ID and lock edits after transport uncertainty.
        setOperation({ id: operationId.current, state: 'submitting', error: '', created: Date.now() / 1000 });
      }
    } finally { setBusy(false); }
  }
  async function addFiles(files: FileList | null) {
    if (!files) return;
    setUploading(true); setError('');
    try {
      let size = uploads.reduce((sum, file) => sum + file.size, 0);
      for (const file of Array.from(files)) {
        size += file.size;
        if (size > 10 * 1024 * 1024) throw new Error('Combined attachments must be 10 MiB or less.');
        const response = await fetch(`/api/webmail/uploads?filename=${encodeURIComponent(file.name)}`, { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
        const result = await response.json();
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new Event('mail-admin-session-expired'));
          throw new Error(result.error?.replaceAll('_', ' ') || 'Upload failed');
        }
        setUploads(previous => [...previous, result as Upload]); setDirty(true);
      }
    } catch (failure) { setError((failure as Error).message); }
    finally { setUploading(false); }
  }
  function close() {
    if (busy || uploading) return;
    if (dirty && !window.confirm('Close this composer without saving your latest changes?')) return;
    for (const file of uploads) void api(`/webmail/uploads/${file.id}`, {}, 'DELETE').catch(() => {});
    onClose();
  }
  const edit = (setter: (value: string) => void) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => { setter(event.target.value); setDirty(true); };
  return <Dialog open fullWidth maxWidth="md" onClose={close} fullScreen={window.matchMedia('(max-width:599px)').matches}>
    <DialogTitle>{draftMode ? 'Edit draft' : reply ? 'Reply' : forward ? 'Forward' : 'New message'}</DialogTitle>
    {(busy || uploading) && <LinearProgress/>}
    <DialogContent dividers><Stack spacing={2}>
      <Typography variant="body2">From: {profile.display_name ? `${profile.display_name} <${profile.email}>` : profile.email}</Typography>
      {profile.reply_to && <Typography variant="caption">Reply-to: {profile.reply_to}</Typography>}
      {error && <Alert severity="error">{error}</Alert>}
      {operation && <Alert severity={operation.state === 'complete' ? 'success' : 'warning'}>{submissionStates[operation.state] || operation.state} {operation.error.replaceAll('_', ' ')}</Alert>}
      <Box component="fieldset" disabled={busy || uploading || !!operation} sx={{ border: 0, p: 0, m: 0, minWidth: 0 }}><Stack spacing={2}>
        <TextField label="To" value={to} onChange={edit(setTo)} placeholder="recipient@example.com"/>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}><TextField fullWidth label="Cc" value={cc} onChange={edit(setCc)}/><TextField fullWidth label="Bcc" value={bcc} onChange={edit(setBcc)}/></Stack>
        <TextField label="Subject" value={subject} onChange={edit(setSubject)}/>
        <TextField label="Message" multiline minRows={12} value={text} onChange={edit(setText)} slotProps={{ htmlInput: { maxLength: 131072 } }}/>
        <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
          {attachments.map(file => <Chip key={file.part} label={file.filename} onDelete={() => { setAttachments(values => values.filter(item => item.part !== file.part)); setDirty(true); }}/>) }
          {uploads.map(file => <Chip key={file.id} label={file.filename} onDelete={() => { void api(`/webmail/uploads/${file.id}`, {}, 'DELETE').catch(() => {}); setUploads(values => values.filter(item => item.id !== file.id)); setDirty(true); }}/>) }
        </Stack>
        <Button component="label" variant="outlined">Add attachments<input hidden type="file" multiple onChange={event => { void addFiles(event.target.files); event.target.value = ''; }}/></Button>
        <Typography variant="caption" color="text.secondary">Up to 10 MiB of attachments combined. Drafts save to the server every 30 seconds while this window is visible.</Typography>
      </Stack></Box>
      {saved && <Typography variant="caption" role="status">{saved}</Typography>}
    </Stack></DialogContent>
    <DialogActions sx={{ flexWrap: 'wrap', gap: 1 }}><Button disabled={busy || uploading} onClick={close}>Close</Button>
      {operation ? <><Button disabled={busy} onClick={async () => { setBusy(true); try { setOperation(await api<SendOperation>(`/webmail/sending/${operation.id}`)); } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); } }}>Check status</Button>
        {['accepted', 'sent_save_failed', 'saving_sent', 'sent_save_uncertain', 'sent_saved', 'cleanup_failed'].includes(operation.state) && <Button disabled={busy} onClick={async () => { setBusy(true); try { setOperation(await api<SendOperation>(`/webmail/sending/${operation.id}/save-sent`, {})); onChanged(); } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); } }}>Retry Sent save / cleanup</Button>}</>
        : <><Button disabled={busy || uploading} onClick={() => { void save(); }}>Save draft</Button><Button variant="contained" disabled={busy || uploading || !to.trim() && !cc.trim() && !bcc.trim()} onClick={() => { void send(); }}>Send</Button></>}
    </DialogActions>
  </Dialog>;
}
