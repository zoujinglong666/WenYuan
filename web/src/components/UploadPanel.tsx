import { useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, Upload } from 'lucide-react'
import { api } from '../lib/api'
import type { IngestResponse } from '../lib/types'
import { cn, formatMs } from '../lib/utils'
import Button from './ui/Button'
import Input from './ui/Input'
import Spinner from './ui/Spinner'

export interface UploadPanelProps {
  onUploaded: () => void
}

/**
 * 摄入成功 / 失败 / 跳过的判定。
 *
 * 后端在**解析失败**时也会回 HTTP 200（文档已登记为 failed，
 * 前端要能看到「这份文档入库了，但解析炸了」），所以不能只看
 * HTTP 状态码 —— 必须看 body 里的 `status` 与 `error`。
 */
function outcomeOf(result: IngestResponse | null): {
  kind: 'none' | 'ok' | 'skipped' | 'failed'
  message: string
} {
  if (!result) return { kind: 'none', message: '' }

  const failed = result.status === 'failed' || result.status === 'error' || !!result.error
  if (failed) {
    return {
      kind: 'failed',
      message: result.error
        ? `「${result.filename}」解析失败：${result.error}`
        : `「${result.filename}」解析失败（${result.status}）`,
    }
  }

  if (result.skipped) {
    return { kind: 'skipped', message: `「${result.filename}」内容未变，已跳过` }
  }

  return {
    kind: 'ok',
    message: `「${result.filename}」已建立索引 · ${result.chunk_count} 块 · ${formatMs(
      result.timings.index,
    )}`,
  }
}

export default function UploadPanel({ onUploaded }: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null)
  const [aclIds, setAclIds] = useState('public')
  const [parser, setParser] = useState('')
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请先选择文件')
      const ids = aclIds
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      return api.ingest(file, ids.length ? ids : ['public'], parser || undefined)
    },
    onSuccess: (data) => {
      setResult(data)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      // 失败不入库，不该去刷新文档列表（刷新了也还是 failed 状态，但会误导）
      if (data.status !== 'failed' && !data.error) onUploaded()
    },
  })

  const outcome = outcomeOf(result)

  /** 拖拽真的能用 —— 文案写了「拖拽到此处」，就得能拖。 */
  const acceptFile = (f: File | null | undefined) => {
    if (!f) return
    setFile(f)
    setResult(null)
  }

  return (
    <section className="rounded-lg border border-hairline bg-surface p-4">
      <div className="space-y-3">
        <label
          className={cn(
            'flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-6 text-center transition-colors',
            dragging
              ? 'border-accent bg-accent-soft/60'
              : 'border-hairline-strong bg-paper hover:border-accent-line hover:bg-accent-soft/40',
          )}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={(e) => {
            e.preventDefault()
            setDragging(false)
          }}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            acceptFile(e.dataTransfer.files?.[0])
          }}
        >
          <Upload className="size-4 text-ink-3" aria-hidden />
          <span className="text-[13px] text-ink">
            {file ? file.name : '选择文件，或拖拽到此处'}
          </span>
          {file ? (
            <span className="tnum text-[11.5px] text-ink-3">
              {(file.size / 1024).toFixed(1)} KB
            </span>
          ) : (
            <span className="text-[11.5px] text-ink-3">支持 PDF / Word / PPT / Excel / Markdown</span>
          )}
          <input
            ref={inputRef}
            type="file"
            className="sr-only"
            onChange={(e) => {
              acceptFile(e.target.files?.[0])
            }}
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label
              htmlFor="acl-ids"
              className="mb-2 block text-[12px] text-ink-2"
            >
              可见主体（逗号分隔）
            </label>
            <Input
              id="acl-ids"
              value={aclIds}
              onChange={(e) => setAclIds(e.target.value)}
              placeholder="public, dept:finance"
            />
          </div>
          <div>
            <label
              htmlFor="parser"
              className="mb-2 block text-[12px] text-ink-2"
            >
              解析器
            </label>
            <select
              id="parser"
              value={parser}
              onChange={(e) => setParser(e.target.value)}
              className="h-10 w-full rounded-lg border border-hairline bg-surface px-3 text-sm text-ink focus:border-accent"
            >
              <option value="">自动（按扩展名分派）</option>
              <option value="mineru">MinerU 版面还原</option>
              <option value="office">Office 原生解析</option>
              <option value="scanned">扫描件 OCR（本地）</option>
            </select>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          {outcome.kind === 'ok' || outcome.kind === 'skipped' ? (
            <p className="mr-auto text-[12px] text-ink-2">{outcome.message}</p>
          ) : null}
          <Button
            variant="primary"
            onClick={() => upload.mutate()}
            disabled={!file || upload.isPending}
          >
            {upload.isPending ? <Spinner /> : null}
            {upload.isPending ? '解析中' : '上传并索引'}
          </Button>
        </div>

        {outcome.kind === 'failed' ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg bg-critical-soft px-3 py-2 text-[13px] text-critical"
          >
            <AlertTriangle className="mt-px size-4 shrink-0" aria-hidden />
            <span>{outcome.message}</span>
          </p>
        ) : null}

        {upload.isError ? (
          <p
            role="alert"
            className="rounded-lg bg-critical-soft px-3 py-2 text-[13px] text-critical"
          >
            {(upload.error as Error).message}
          </p>
        ) : null}
      </div>
    </section>
  )
}
