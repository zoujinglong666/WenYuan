import { useMutation, useQuery } from '@tanstack/react-query'
import { FileText } from 'lucide-react'
import { api } from '../lib/api'
import DocumentTable from '../components/DocumentTable'
import UploadPanel from '../components/UploadPanel'
import EmptyState from '../components/ui/EmptyState'
import Spinner from '../components/ui/Spinner'

export default function DocumentsPage() {
  const docs = useQuery({
    queryKey: ['documents'],
    queryFn: api.documents,
  })

  const remove = useMutation({
    mutationFn: (docId: string) => api.deleteDocument(docId),
    onSuccess: () => docs.refetch(),
  })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-8 lg:px-10">
        <div className="mt-2">
          <UploadPanel onUploaded={() => docs.refetch()} />
        </div>

        <div className="mt-6 flex items-baseline justify-between gap-3">
          <h2 className="text-[13px] font-medium text-ink">已入库文档</h2>
          <span className="tnum text-[12px] text-ink-3">{docs.data?.length ?? 0} 份</span>
        </div>

        <div className="mt-2.5">
          {docs.isLoading ? (
            <div className="flex justify-center py-10">
              <Spinner label="加载中" />
            </div>
          ) : docs.isError ? (
            <p className="rounded-lg bg-critical-soft px-3 py-2 text-[13px] text-critical">
              {(docs.error as Error).message}
            </p>
          ) : docs.data && docs.data.length > 0 ? (
            <DocumentTable
              documents={docs.data}
              onDelete={(id) => remove.mutate(id)}
              deletingId={remove.isPending ? remove.variables : null}
            />
          ) : (
            <EmptyState
              icon={<FileText className="size-5" aria-hidden />}
              title="还没有文档"
              description="上传第一份文档后，它会被解析、切块并写入双路索引。"
            />
          )}
        </div>
      </div>
    </div>
  )
}
