'use client';

import { 
  Book, ChevronRight, ChevronDown, Plus, Sparkles, 
  Globe, FileText, Code, CheckCircle, X, 
  Zap, Settings, Share2, LayoutTemplate, MoreHorizontal 
} from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';

export default function DocDashboardPage() {
  const [showPublishPanel, setShowPublishPanel] = useState(false);
  const [activePage, setActivePage] = useState('getting-started');

  return (
    <div className="h-screen flex flex-col bg-white overflow-hidden font-sans text-slate-900">
      
      {/* ── TOP NAVIGATION ── */}
      <header className="h-16 border-b border-slate-100 flex items-center px-6 justify-between bg-white shrink-0 z-30 relative">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center text-white shadow-md shadow-indigo-500/20">
            <Book className="w-4 h-4 text-white" />
          </div>
          <div className="flex flex-col">
            <h1 className="font-bold text-sm tracking-tight text-slate-900">Lucid AI Documentation</h1>
            <span className="text-[10px] text-slate-400 font-medium flex items-center gap-1.5">
              <CheckCircle className="w-3 h-3 text-emerald-500" />
              Last auto-synced 2m ago
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4">
           {/* Pro Button - Shiny Effect */}
           <button 
             onClick={() => setShowPublishPanel(!showPublishPanel)}
             className="relative overflow-hidden group bg-gradient-to-r from-violet-600 to-indigo-600 text-white font-semibold pl-4 pr-5 py-2 rounded-full shadow-lg shadow-indigo-500/30 hover:shadow-indigo-600/40 hover:-translate-y-0.5 transition-all duration-300 text-xs flex items-center gap-2"
           >
             <div className="absolute inset-0 bg-white/20 group-hover:translate-x-full transition-transform duration-700 -skew-x-12 -translate-x-[150%]"></div>
             <Zap className="w-3.5 h-3.5 text-yellow-300 fill-current" />
             Publish (Pro)
           </button>
           
           <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center border border-slate-200 cursor-pointer">
             <div className="w-full h-full rounded-full overflow-hidden bg-gradient-to-br from-slate-200 to-slate-300"></div>
           </div>
        </div>
      </header>


      {/* ── MAIN LAYOUT ── */}
      <div className="flex-1 flex overflow-hidden relative">
        
        {/* ── LEFT SIDEBAR (Structure) ── */}
        <div className="w-[280px] flex flex-col border-r border-slate-100 bg-slate-50/30 shrink-0">
          
          <div className="p-4 border-b border-slate-100">
            <button className="w-full bg-white border border-slate-200 text-slate-600 hover:border-slate-300 hover:text-slate-900 font-medium py-2 px-3 rounded-lg text-xs flex items-center justify-center gap-2 shadow-sm transition-all">
              <Sparkles className="w-3.5 h-3.5 text-indigo-500" />
              Auto-Generate Structure
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
             <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3 pl-2">Table of Contents</div>
             
             <ul className="space-y-0.5">
               <NavItem active={activePage === 'introduction'} onClick={() => setActivePage('introduction')} label="Introduction" />
               <NavItem active={activePage === 'quick-start'} onClick={() => setActivePage('quick-start')} label="Quick Start" />
               
               <div className="pt-2 pb-1">
                 <div className="flex items-center justify-between px-2 py-1 text-slate-500 hover:bg-slate-100 rounded cursor-pointer group">
                    <span className="text-xs font-semibold flex items-center gap-1">
                      <ChevronDown className="w-3 h-3" /> Core Concepts
                    </span>
                    <Plus className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
                 </div>
                 <div className="pl-4 border-l border-slate-200 ml-3 mt-1 space-y-0.5">
                    <NavItem active={activePage === 'auth'} onClick={() => setActivePage('auth')} label="Authentication" />
                    <NavItem active={activePage === 'db'} onClick={() => setActivePage('db')} label="Database Schema" />
                    <NavItem active={activePage === 'api'} onClick={() => setActivePage('api')} label="API Reference" />
                 </div>
               </div>

               <div className="pt-2 pb-1">
                 <div className="flex items-center justify-between px-2 py-1 text-slate-500 hover:bg-slate-100 rounded cursor-pointer group">
                    <span className="text-xs font-semibold flex items-center gap-1">
                      <ChevronRight className="w-3 h-3" /> Integrations
                    </span>
                    <Plus className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
                 </div>
               </div>
             </ul>
          </div>
          
          <div className="p-4 border-t border-slate-100">
             <div className="text-xs text-slate-400 flex items-center gap-2 px-2">
                <LayoutTemplate className="w-3.5 h-3.5" />
                <span>Theme: </span>
                <span className="font-medium text-slate-600">GitBook Light</span>
             </div>
          </div>
        </div>


        {/* ── MAIN EDITOR CONTENT ── */}
        <div className="flex-1 overflow-y-auto bg-white relative">
          
          <div className="max-w-4xl mx-auto px-12 py-12">
            
            {/* Magic Input */}
            <div className="mb-12 relative group">
               <div className="absolute inset-0 bg-gradient-to-r from-indigo-500/20 to-purple-500/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500"></div>
               <div className="relative bg-white border border-slate-200 shadow-sm rounded-xl p-1 flex items-center gap-2 focus-within:ring-2 focus-within:ring-indigo-500/20 focus-within:border-indigo-500 transition-all">
                  <div className="w-10 h-10 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                     <Sparkles className="w-5 h-5 text-indigo-600" />
                  </div>
                  <input 
                    type="text" 
                    placeholder="Paste a URL or code snippet to auto-write this page..." 
                    className="flex-1 bg-transparent border-none outline-none text-sm text-slate-700 placeholder:text-slate-400 h-10 px-2"
                  />
                  <button className="bg-indigo-600 text-white text-xs font-medium px-4 py-2 rounded-lg hover:bg-indigo-700 transition-colors">
                    Generate
                  </button>
               </div>
            </div>

            {/* Document Content (Mocked Markdown Rendering) */}
            <div className="prose prose-slate prose-lg max-w-none">
              <h1 className="text-4xl font-extrabold text-slate-900 tracking-tight mb-6">Authentication</h1>
              
              <p className="text-lg text-slate-600 leading-relaxed mb-6">
                Lucid AI uses a secure <span className="font-medium text-indigo-600 bg-indigo-50 px-1 rounded">JWT-based authentication</span> system 
                to ensure that your API requests are authorized. Before you can make calls to the Core API, 
                you need to obtain an access token.
              </p>

              <div className="bg-slate-50 rounded-xl border border-slate-200 p-5 mb-8 font-mono text-xs overflow-x-auto relative group/code">
                <div className="absolute right-4 top-4 opacity-0 group-hover/code:opacity-100 transition-opacity">
                  <button className="text-slate-400 hover:text-slate-600"><Share2 className="w-4 h-4" /></button>
                </div>
                <div className="flex gap-1.5 mb-3">
                   <div className="w-2.5 h-2.5 rounded-full bg-slate-300"></div>
                   <div className="w-2.5 h-2.5 rounded-full bg-slate-300"></div>
                </div>
                <span className="text-purple-600">const</span> <span className="text-blue-600">authenticate</span> = <span className="text-purple-600">async</span> (apiKey) ={'>'} {'{'}
                <br/>
                &nbsp;&nbsp;<span className="text-purple-600">const</span> response = <span className="text-purple-600">await</span> fetch(<span className="text-green-600">'https://api.lucid.ai/v1/auth'</span>, {'{'}
                <br/>
                &nbsp;&nbsp;&nbsp;&nbsp;method: <span className="text-green-600">'POST'</span>,
                <br/>
                &nbsp;&nbsp;&nbsp;&nbsp;headers: {'{'} <span className="text-green-600">'Authorization'</span>: <span className="text-green-600">`Bearer {'$'}{'{'}apiKey{'}'}`</span> {'}'}
                <br/>
                &nbsp;&nbsp;{'}'});
                <br/>
                &nbsp;&nbsp;<span className="text-purple-600">return</span> response.json();
                <br/>
                {'}'};
              </div>

              <h2 className="text-2xl font-bold text-slate-900 mt-10 mb-4">Handling Errors</h2>
              <p className="text-slate-600 leading-relaxed">
                If the token is invalid or expired, the API will return a <code>401 Unauthorized</code> response. 
                You should implement a refresh token flow to handle this gracefully without forcing the user to log in again.
              </p>
            </div>

          </div>
        </div>

        {/* ── PUBLISH SETTINGS PANEL (Pro) ── */}
        <div 
          className={cn(
            "absolute top-0 right-0 h-full w-[320px] bg-white border-l border-slate-100 shadow-2xl transform transition-transform duration-300 ease-in-out z-40",
            showPublishPanel ? "translate-x-0" : "translate-x-full"
          )}
        >
          <div className="p-6 h-full flex flex-col">
             <div className="flex items-center justify-between mb-8">
                <h3 className="font-bold text-slate-900 text-lg">Publish Settings</h3>
                <button onClick={() => setShowPublishPanel(false)} className="text-slate-400 hover:text-slate-600">
                   <X className="w-5 h-5" />
                </button>
             </div>

             <div className="space-y-8 flex-1">
                
                {/* Setting Group 1 */}
                <div className="space-y-4">
                   <div className="flex items-start justify-between">
                      <div className="flex gap-3">
                         <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center text-indigo-600 mt-0.5">
                            <Globe className="w-4 h-4" />
                         </div>
                         <div>
                            <div className="font-semibold text-sm text-slate-900">Custom Domain</div>
                            <div className="text-[11px] text-slate-500 leading-tight mt-1 max-w-[150px]">Host docs on your own domain (e.g. docs.yours.com)</div>
                         </div>
                      </div>
                      <ToggleSwitch checked={true} />
                   </div>
                   
                   <input className="w-full text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1.5 focus:outline-none focus:border-indigo-500 transition-colors" value="docs.lucid.ai" readOnly />
                </div>

                {/* Setting Group 2 */}
                <div className="flex items-start justify-between">
                  <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center text-purple-600 mt-0.5">
                        <FileText className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="font-semibold text-sm text-slate-900">Export as PDF</div>
                        <div className="text-[11px] text-slate-500 leading-tight mt-1">Allow users to download docs</div>
                      </div>
                  </div>
                  <ToggleSwitch checked={false} />
                </div>

                {/* Setting Group 3 */}
                <div className="flex items-start justify-between">
                  <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center text-amber-600 mt-0.5">
                        <Code className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="font-semibold text-sm text-slate-900">Generate SDKs</div>
                        <div className="text-[11px] text-slate-500 leading-tight mt-1">Auto-build client libraries</div>
                      </div>
                  </div>
                  <ToggleSwitch checked={true} />
                </div>

             </div>

             {/* Footer Action */}
             <div className="mt-auto pt-6 border-t border-slate-100">
                <button className="w-full bg-slate-900 text-white font-bold py-3 rounded-xl hover:bg-slate-800 transition-all shadow-lg flex items-center justify-center gap-2 text-sm">
                   <Zap className="w-4 h-4 text-yellow-400 fill-current" />
                   Publish Changes now
                </button>
             </div>
          </div>
        </div>

      </div>
    </div>
  );
}

/* ── Helper Components ── */

function NavItem({ active, label, onClick }) {
  return (
    <div 
      onClick={onClick}
      className={cn(
        "px-2 py-1.5 rounded-md cursor-pointer flex items-center justify-between group transition-colors",
        active ? "bg-indigo-50 text-indigo-700 font-medium" : "text-slate-500 hover:bg-slate-100/80 hover:text-slate-900"
      )}
    >
      <span className="text-xs">{label}</span>
      {active && <div className="w-1.5 h-1.5 rounded-full bg-indigo-500"></div>}
    </div>
  );
}

function ToggleSwitch({ checked }) {
  return (
    <div className={cn(
      "w-9 h-5 rounded-full relative cursor-pointer transition-colors duration-300",
      checked ? "bg-indigo-600" : "bg-slate-300"
    )}>
      <div className={cn(
        "absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow-sm transition-transform duration-300",
        checked ? "translate-x-4" : "translate-x-0"
      )}></div>
    </div>
  );
}
