import { Skeleton } from '@/components/ui/skeleton';

export default function EngineerLoading() {
  return (
    <div className="flex-1 p-8 space-y-6 animate-in fade-in duration-300">
      {/* Header skeleton */}
      <div className="text-center space-y-4 pt-8">
        <Skeleton className="h-10 w-80 mx-auto" />
        <Skeleton className="h-5 w-96 mx-auto" />
      </div>

      {/* Prompt box skeleton */}
      <div className="max-w-2xl mx-auto">
        <Skeleton className="h-32 w-full rounded-2xl" />
      </div>

      {/* Idea chips */}
      <div className="max-w-3xl mx-auto grid grid-cols-3 sm:grid-cols-6 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>

      {/* Projects grid */}
      <div className="max-w-6xl mx-auto space-y-4 pt-4">
        <Skeleton className="h-6 w-32" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-2xl" />
          ))}
        </div>
      </div>
    </div>
  );
}
