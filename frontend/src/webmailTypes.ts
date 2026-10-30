export interface MessageRef { folder: string; uidvalidity: number; uid: number }
export interface MailLabel { id: string; name: string; color: string }
export interface MailFolder { name: string; special: string; unread: number; total: number }
export interface MailRow {
  ref: MessageRef; from: string; to: string; cc: string; bcc: string; reply_to: string;
  subject: string; date: string; message_id: string; in_reply_to: string; references: string;
  unread: boolean; labels: string[]; size: number;
}
export interface Attachment { part: string; filename: string; size: number; content_type: string }
export interface MailMessage extends MailRow { text: string; html: string; blocked_images: number; truncated: boolean; attachments: Attachment[] }
export interface MailPage { messages: MailRow[]; total: number; page: number; size: 25 | 50 }
export interface MailProfile {
  id: string; email: string; display_name: string; reply_to: string; signature: string;
  theme: 'light' | 'dark' | 'system'; two_step_enabled: boolean; recovery_codes_remaining: number;
  connections: { host: string; imap_port: number; smtp_port: number };
}
export interface MailSearch {
  sender: string; recipient: string; subject: string; body: string; after: string; before: string;
  unread: boolean; include_trash: boolean; include_junk: boolean;
}
export const emptySearch: MailSearch = { sender: '', recipient: '', subject: '', body: '', after: '', before: '', unread: false, include_trash: false, include_junk: false };
export const refKey = (ref: MessageRef) => JSON.stringify(ref);
export type ComposeMode = 'new' | 'reply' | 'reply-all' | 'forward' | 'draft';
export interface ComposeStart { mode: ComposeMode; message?: MailMessage }
export interface SendOperation { id: string; state: string; error: string; created: number }
export const submissionStates: Record<string, string> = {
  complete: 'Submitted to Maddy and saved to Sent. Recipient delivery is not confirmed.',
  accepted: 'Submitted to Maddy. The Sent copy still needs to be saved.',
  rejected: 'Submission failed. This operation will not send again. Review the error before creating another message.',
  submitting: 'Submission is in progress. Do not send another copy.',
  submission_uncertain: 'The submission result is uncertain. Check Sent and recipient receipt before creating another message. This operation will not resend.',
  preparing: 'Preparing the saved message. No automatic retry will send it.',
  preparation_uncertain: 'The draft save result is uncertain. No message was submitted. Check Drafts before composing again.',
  saving_sent: 'Submission succeeded; saving the Sent copy is in progress.',
  sent_save_failed: 'Submission succeeded, but saving Sent failed. Retry saving Sent only.',
  sent_save_uncertain: 'Submission succeeded; the Sent save result needs reconciliation. Retry checks for the existing copy and will not send again.',
  sent_saved: 'Submission succeeded and Sent is saved; draft cleanup is pending.',
  cleanup_failed: 'Submission succeeded and Sent is saved; draft cleanup needs a retry.',
};

export function attachmentUrl(ref: MessageRef, part: string) {
  return `/api/webmail/attachments/${encodeURIComponent(part)}?${new URLSearchParams({ folder: ref.folder, uidvalidity: String(ref.uidvalidity), uid: String(ref.uid) })}`;
}
