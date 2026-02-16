'use client';

import { 
  Bot, User, Paperclip, Send, Terminal, Play, 
  MoreVertical, Github, ChevronDown, ArrowUp, 
  Code, Globe, HardHat, FileText, Check, Loader2, Sparkles,
  GitPullRequest, GitMerge, RefreshCw, LayoutList, GripVertical, Plus, MessageSquare, Wrench, Clock
} from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';

export default function WorkspacePage() {
  const params = useParams();
  const router = useRouter();
  const projectId = decodeURIComponent(params.projectId || 'unknown');
  const [input, setInput] = useState('');
  const scrollRef = useRef(null);

  // Mock messages based on the image structure
  const [messages] = useState([
    {
      id: 1,
      role: 'user',
      content: 'dasde3qewwqdas'
    },
    {
      id: 2,
      role: 'assistant',
      content: 'I\'m not sure what you meant by "dasde3qewwqdas". Could you please clarify what you\'d like help with?\n\nI can assist you with tasks like:\n\n• Code development - writing, debugging, or refactoring code\n• Repository exploration - understanding codebases and finding files\n• Git operations - commits, branches, and pull requests\n• File editing - creating or modifying files\n• Running commands - executing scripts, tests, or build commands\n• Web browsing - researching documentation or APIs\n\nJust let me know what you need!'
    }
  ]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="flex h-screen bg-[#ffffff] text-slate-900 overflow-hidden font-sans">
      
      {/* ── SIDEBAR (Added) ── */}
      <aside className="w-[240px] h-full bg-[#FAFAFA] border-r border-slate-200 flex flex-col shrink-0 z-50">
        
        {/* Logo area */}
        <div className="h-14 flex items-center px-4 border-b border-transparent">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-sm shadow-blue-600/20">
              <Terminal className="w-4 h-4 text-white fill-current" />
            </div>
            <span className="font-bold text-slate-900 text-sm tracking-tight">Lucid AI</span>
          </div>
        </div>

        {/* Navigation */}
        <div className="p-3 space-y-1">
          <button 
            onClick={() => router.push('/dashboard/engineer')}
            className="w-full flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium shadow-sm shadow-blue-600/20 hover:bg-blue-700 transition-all"
          >
            <Plus className="w-4 h-4" />
            New Project
          </button>
        </div>

        <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto">
          <SidebarItem 
            icon={MessageSquare} 
            label="Conversations" 
            active 
            onClick={() => router.push('/dashboard/engineer/conversations')}
          />
          <SidebarItem 
            icon={LayoutList} 
            label="Integrations" 
            onClick={() => router.push('/dashboard/engineer/integrations')}
          />
          <SidebarItem 
            icon={FileText} 
            label="Documentation" 
            onClick={() => router.push('/dashboard/engineer/docs')}
          />
          <SidebarItem 
            icon={MoreVertical} 
            label="Settings" 
            onClick={() => router.push('/dashboard/engineer/settings')}
          />
        </nav>

        {/* User Profile */}
        <div className="p-3 border-t border-slate-100">
          <div className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-slate-100 transition-colors cursor-pointer">
            <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-xs font-bold text-slate-600">
              AR
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900 truncate">Alex Rivard</p>
              <p className="text-xs text-slate-500 truncate">Pro Developer</p>
            </div>
          </div>
        </div>
      </aside>

      {/* ── Main Content Wrapper ── */}
      <div className="flex-1 flex flex-col min-w-0 bg-white">

        {/* ── Header ── */}
        <header className="h-14 min-h-[56px] border-b border-slate-200 bg-white flex items-center justify-between px-4 z-40">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 bg-emerald-50 text-emerald-700 px-2 py-1 rounded-full border border-emerald-100">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              <span className="text-[10px] font-bold uppercase tracking-wider">Running</span>
            </div>
            <div className="h-4 w-px bg-slate-200" />
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-semibold text-slate-900">Unclear Request or Random Input</h1>
              <span className="text-[10px] font-mono bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded border border-slate-200">v1</span>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <ActionButton icon={LayoutList} tooltip="Plan" />
            <ActionButton icon={Code} tooltip="Code" active />
            <ActionButton icon={Terminal} tooltip="Terminal" />
            <ActionButton icon={Globe} tooltip="Browser" />
            <div className="w-px h-4 bg-slate-200 mx-2" />
            <ActionButton icon={HardHat} tooltip="Memory" />
            <ActionButton icon={MoreVertical} tooltip="More" />
          </div>
        </header>

        {/* ── Main Chat Area ── */}
        <main className="flex-1 overflow-hidden relative">
          <div 
            ref={scrollRef}
            className="h-full overflow-y-auto pt-8 pb-40 px-4 scroll-smooth"
          >
            <div className="max-w-3xl mx-auto space-y-8">
              {messages.map((msg) => (
                <div key={msg.id} className={cn(
                  "flex gap-4 group animate-in fade-in slide-in-from-bottom-2 duration-300",
                  msg.role === 'user' ? "flex-row-reverse" : "flex-row"
                )}>
                  {/* Avatar */}
                  <div className={cn(
                    "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 shadow-sm border",
                    msg.role === 'user' 
                      ? "bg-white border-slate-200 text-slate-400" 
                      : "bg-blue-600 border-blue-600 text-white"
                  )}>
                    {msg.role === 'user' ? (
                      <User className="w-4 h-4" />
                    ) : (
                      <Bot className="w-4 h-4" />
                    )}
                  </div>

                  {/* Message Content */}
                  <div className={cn(
                    "max-w-[85%] text-sm leading-relaxed whitespace-pre-wrap",
                    msg.role === 'user' 
                      ? "bg-slate-50 text-slate-900 px-4 py-3 rounded-2xl rounded-tr-sm border border-slate-200 shadow-sm"
                      : "text-slate-700 pt-1"
                  )}>
                    {msg.content}
                  </div>
                </div>
              ))}
            </div>
          </div>

        {/* ── Floating Input Area ── */}
        <div className="absolute bottom-0 left-0 right-0 px-4 pb-6 pt-10 bg-gradient-to-t from-white via-white to-transparent">
          <div className="max-w-3xl mx-auto">
            <div className="bg-white border border-slate-200 rounded-2xl shadow-xl shadow-slate-200/50 p-4 relative transition-all duration-300">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="What do you want to build?"
                spellCheck={false}
                className="w-full bg-transparent border-none focus:ring-0 outline-none text-slate-900 placeholder:text-slate-400 text-base resize-none min-h-[60px] max-h-[200px] py-1 px-0"
                rows={1}
              />
              
              <div className="flex items-center justify-between mt-3 pt-2">
                <div className="flex items-center gap-2">
                  <button className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors">
                    <Paperclip className="w-4 h-4" />
                  </button>
                  <button className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-900 px-2 py-1.5 rounded-lg hover:bg-slate-100 transition-colors">
                    <Wrench className="w-3.5 h-3.5" />
                    Tools
                  </button>
                </div>

                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2 text-[11px] text-slate-400 font-medium">
                    <Clock className="w-3 h-3" />
                    Waiting for task
                  </div>
                  <button 
                    disabled={!input.trim()}
                    className={cn(
                      "p-2 rounded-lg transition-all duration-200 flex items-center justify-center",
                      input.trim() 
                        ? "bg-blue-600 text-white shadow-md hover:bg-blue-700" 
                        : "bg-slate-100 text-slate-300"
                    )}
                  >
                    <ArrowUp className="w-4 h-4" strokeWidth={3} />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

        {/* ── Footer ── */}
        <footer className="h-9 border-t border-slate-200 bg-white flex items-center justify-between px-4 z-40 text-xs select-none">
          <div className="flex items-center gap-4">
            <button className="flex items-center gap-2 px-2 py-1 hover:bg-slate-50 rounded transition-colors text-slate-600 font-medium">
              <div className="w-4 h-4 rounded bg-slate-900 text-white flex items-center justify-center text-[9px] font-bold">N</div>
              Lucid AI
              <ChevronDown className="w-3 h-3 opacity-50" />
            </button>
            
            <div className="flex items-center gap-2 text-slate-500">
              <Github className="w-3.5 h-3.5" />
              <span className="font-mono text-[11px]">lucid-ai/workspace</span>
             </div>

            <div className="flex items-center gap-1.5 px-2 py-0.5 bg-slate-100 rounded text-slate-600 font-mono text-[10px] font-medium">
              <GitMerge className="w-3 h-3" />
              master
            </div>
          </div>

          <div className="flex items-center gap-2">
            <FooterButton icon={RefreshCw} label="Pull" />
            <FooterButton icon={ArrowUp} label="Push" />
            <FooterButton icon={GitPullRequest} label="Pull Request" />
          </div>
        </footer>
      </div>
    </div>
  );
}

function ActionButton({ icon: Icon, tooltip, active }) {
  return (
    <button className={cn(
      "p-2 rounded-lg transition-all relative group",
      active ? "bg-slate-100 text-blue-600" : "text-slate-400 hover:text-slate-600 hover:bg-slate-50"
    )}>
      <Icon className="w-4 h-4" />
      <span className="absolute top-full right-0 mt-2 px-2 py-1 bg-slate-800 text-white text-[10px] rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 pointer-events-none">
        {tooltip}
      </span>
    </button>
  );
}

function SidebarItem({ icon: Icon, label, active, onClick }) {
  return (
    <button 
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
        active ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
      )}
    >
      <Icon className="w-4 h-4" />
      {label}
    </button>
  );
}

function FooterButton({ icon: Icon, label }) {
  return (
    <button className="flex items-center gap-1.5 px-2.5 py-1 text-slate-500 hover:text-slate-800 hover:bg-slate-200/50 rounded transition-colors font-medium border border-transparent hover:border-slate-200">
      <Icon className="w-3 h-3" />
      {label}
    </button>
  );
}
