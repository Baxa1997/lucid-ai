'use client';

import React from 'react';
import manager from '@/lib/agentWSManager';

const StopTaskButton = ({ status }) => {
  // Show ONLY when task is running
  if (status !== "running" && status !== "progress") return null;

  const handleStop = () => {
    if (manager?.isOpen) {
      manager.send({
        type: "stop_task",
      });
    }
  };

  return (
    <button
      onClick={handleStop}
      className="bg-red-500 hover:bg-red-600 text-white font-medium py-1.5 px-4 rounded-md flex items-center gap-2 transition-colors duration-200 shadow-sm shadow-red-500/20 active:scale-95"
    >
      <span className="text-sm">⛔</span> 
      <span className="text-sm">Stop Task</span>
    </button>
  );
};

export default StopTaskButton;
