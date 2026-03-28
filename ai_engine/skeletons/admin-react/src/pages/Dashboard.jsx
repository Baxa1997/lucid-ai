import { LayoutDashboard } from 'lucide-react';

export default function Dashboard() {
  return (
    <div className="page-dashboard">
      <div className="page-header">
        <h1><LayoutDashboard size={24} /> Dashboard</h1>
        <p>Overview of your application.</p>
      </div>
      <div className="stat-grid">
        {/* Stats will be added by Claude based on project spec */}
      </div>
    </div>
  );
}
