export default function Footer() {
  return (
    <footer className="border-t border-slate-100 py-8 px-6 mt-auto bg-white">
      <div className="max-w-[1400px] mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="text-[13px] text-slate-500 font-medium flex items-center gap-2">
          <span className="text-slate-900 font-bold">Lucid AI</span>
          <span className="text-slate-300">|</span>
          <span>© 2024. Engineering the future.</span>
        </div>
        
        <div className="flex items-center gap-8 text-[13px] text-slate-500 font-bold">
          <a href="#" className="hover:text-violet-600 transition-colors">Documentation</a>
          <a href="#" className="hover:text-violet-600 transition-colors">Privacy</a>
          <a href="#" className="hover:text-violet-600 transition-colors">Terms</a>
          <a href="#" className="hover:text-violet-600 transition-colors">Contact</a>
        </div>
      </div>
    </footer>
  );
}
