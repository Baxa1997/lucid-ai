export default function Footer() {
  return (
    <footer className="border-t border-slate-100 dark:border-slate-800 py-8 px-6 mt-auto bg-white dark:bg-slate-950 transition-colors duration-200">
      <div className="max-w-[1400px] mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="text-[13px] text-slate-500 dark:text-slate-400 font-medium flex items-center gap-2">
          <span className="text-slate-900 dark:text-slate-100 font-bold">Lucid AI</span>
          <span className="text-slate-300 dark:text-slate-600">|</span>
          <span>© 2024. Engineering the future.</span>
        </div>
        
        <div className="flex items-center gap-8 text-[13px] text-slate-500 dark:text-slate-400 font-bold">
          <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Documentation</a>
          <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Privacy</a>
          <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Terms</a>
          <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Contact</a>
        </div>
      </div>
    </footer>
  );
}
