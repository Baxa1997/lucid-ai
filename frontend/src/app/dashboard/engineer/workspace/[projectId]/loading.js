import { Skeleton } from '@/components/ui/skeleton';

export default function WorkspaceLoading() {
  return (
    <div className="flex h-screen bg-[#f8f9fb] dark:bg-[#0d1117] overflow-hidden animate-in fade-in duration-200">
      {/* Chat panel skeleton */}
      <div className="w-[360px] shrink-0 border-r border-[#e3e5eb] dark:border-[#1c2128] flex flex-col">
        {/* Chat messages area */}
        <div className="flex-1 p-4 space-y-4">
          <div className="flex items-start gap-3">
            <Skeleton className="w-7 h-7 rounded-full shrink-0" />
            <div className="space-y-2 flex-1">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          </div>
          <div className="flex items-start gap-3">
            <Skeleton className="w-7 h-7 rounded-full shrink-0" />
            <div className="space-y-2 flex-1">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          </div>
        </div>
        {/* Input area */}
        <div className="p-3">
          <Skeleton className="h-20 w-full rounded-2xl" />
        </div>
      </div>

      {/* Right panel skeleton */}
      <div className="flex-1 m-3 rounded-xl overflow-hidden">
        <Skeleton className="h-[42px] w-full rounded-none" />
        <div className="flex items-center justify-center h-[calc(100%-42px)]">
          <div className="text-center space-y-4">
            <Skeleton className="w-20 h-20 rounded-full mx-auto" />
            <Skeleton className="h-5 w-40 mx-auto" />
            <Skeleton className="h-4 w-56 mx-auto" />
          </div>
        </div>
      </div>
    </div>
  );
}
