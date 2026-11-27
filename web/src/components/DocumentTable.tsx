import { Trash2 } from 'lucide-react'
import type { DocumentInfo } from '../lib/types'
import Badge from './ui/Badge'
import type { Tone } from './ui/Badge'
import Button from './ui/Button'

export interface DocumentTableProps {
  documents: DocumentInfo[]
  onDelete: (docId: string) => void
  deletingId?: string | null
}

function statusTone(status: string): Tone {
  switch (status) {
    case 'ready':
      return 'ok'
    case 'failed':
    case 'error':
      return 'crit'
    case 'processing':
    case 'pending':
      return 'warn'
    default:
      return 'neutral'
  }
}

const STATUS_TEXT: Record<string, string> = {
  ready: '就绪',
  failed: '失败',
  error: '错误',
  processing: '处理中',
  pending: '待处理',
}

export default function DocumentTable({
  documents,
  onDelete,
  deletingId,
}: DocumentTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
      <table className="w-full min-w-[720px] border-collapse text-left">
        <thead>
          <tr className="border-b border-hairline">
            {['文档', '状态', '块数', '可见主体', ''].map((h) => (
              <th
                key={h}
                scope="col"
                className="px-3 py-2 text-[11.5px] font-medium text-ink-3"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {documents.map((d) => (
            <tr key={d.doc_id} className="border-b border-hairline last:border-b-0">
              <td className="px-3 py-2">
                <div className="text-[13px] font-medium text-ink">{d.filename}</div>
                {d.title && d.title !== d.filename ? (
                  <div className="text-[11.5px] text-ink-3">{d.title}</div>
                ) : null}
              </td>
              <td className="px-3 py-2">
                <Badge tone={statusTone(d.status)}>{STATUS_TEXT[d.status] ?? d.status}</Badge>
              </td>
              <td className="tnum px-3 py-2 text-right text-[12.5px] text-ink">
                {d.chunk_count}
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {d.acl_ids.length ? (
                    d.acl_ids.map((a) => (
                      <Badge key={a} tone="neutral" mono>
                        {a}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-[12px] text-ink-3">—</span>
                  )}
                </div>
              </td>
              <td className="px-3 py-2 text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onDelete(d.doc_id)}
                  disabled={deletingId === d.doc_id}
                  title={`删除 ${d.filename}`}
                >
                  <Trash2 className="size-3" aria-hidden />
                  <span className="sr-only">删除</span>
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
