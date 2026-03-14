import React from 'react';

const StopTaskButton = ({ 
  status, 
  websocket, 
  currentTaskId 
}) => {
  // Show ONLY when task is running
  if (status !== "running" && status !== "progress") return null;

  const handleStop = () => {
    if (websocket) {
      websocket.send({
        type: "stop_task",
        task_id: currentTaskId
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
