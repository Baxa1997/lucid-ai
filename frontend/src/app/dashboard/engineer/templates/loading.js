import { Skeleton } from '@/components/ui/skeleton';

export default function TemplatesLoading() {
  return (
    <div className="flex-1 p-6 space-y-4 animate-in fade-in duration-300">
      <div className="text-center space-y-3 pt-4">
        <Skeleton className="h-8 w-64 mx-auto" />
        <Skeleton className="h-5 w-96 mx-auto" />
      </div>
      <div className="max-w-md mx-auto">
        <Skeleton className="h-10 w-full rounded-xl" />
      </div>
      <div className="flex gap-2 justify-center pt-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-20 rounded-full" />
        ))}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-52 rounded-2xl" />
        ))}
      </div>
    </div>
  );
}
