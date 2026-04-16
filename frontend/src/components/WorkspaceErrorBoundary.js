"use client";

// ─────────────────────────────────────────────────────────
//  WorkspaceErrorBoundary — catches JS errors in the
//  workspace so the user sees a recovery UI instead of a
//  blank white page.
// ─────────────────────────────────────────────────────────

import React from "react";
import {AlertTriangle, RotateCcw, ArrowLeft} from "lucide-react";

export class WorkspaceErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {hasError: false, error: null};
  }

  static getDerivedStateFromError(error) {
    return {hasError: true, error};
  }

  componentDidCatch(error, errorInfo) {
    console.error("[WorkspaceErrorBoundary]", error, errorInfo);
  }

  handleReload = () => {
    this.setState({hasError: false, error: null});
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-full flex items-center justify-center bg-[#f5f7fa] dark:bg-[#0d1117]">
          <div className="max-w-md mx-auto text-center px-6">
            {/* Icon */}
            <div className="mx-auto w-16 h-16 rounded-2xl bg-red-100 dark:bg-red-900/20 flex items-center justify-center mb-6 border border-red-200 dark:border-red-800/50">
              <AlertTriangle className="w-8 h-8 text-red-600 dark:text-red-400" />
            </div>

            <h2 className="text-xl font-bold text-slate-900 dark:text-slate-100 mb-2">
              Workspace Error
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-6 leading-relaxed">
              Something went wrong in the workspace. Your session data is safe —
              you can reload to reconnect, or go back to the dashboard.
            </p>

            {/* Error detail (collapsed) */}
            {this.state.error && (
              <details className="mb-6 text-left">
                <summary className="text-xs font-mono text-slate-400 cursor-pointer hover:text-slate-600 transition-colors">
                  Error details
                </summary>
                <pre className="mt-2 p-3 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-[11px] font-mono text-red-600 dark:text-red-400 overflow-x-auto max-h-32 overflow-y-auto">
                  {this.state.error.toString()}
                </pre>
              </details>
            )}

            {/* Actions */}
            <div className="flex items-center gap-3 justify-center">
              <a
                href="/dashboard/engineer"
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium text-slate-600 dark:text-slate-300 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 transition-all shadow-sm">
                <ArrowLeft className="w-4 h-4" />
                Dashboard
              </a>
              <button
                onClick={this.handleReload}
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 transition-all shadow-sm">
                <RotateCcw className="w-4 h-4" />
                Reload Workspace
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
