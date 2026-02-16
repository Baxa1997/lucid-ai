import { create } from 'zustand';

const useFlowStore = create((set, get) => ({
  // ── User Profile ──
  user: {
    name: 'John Doe',
    email: 'john@u-code.dev',
    avatar: 'JD',
    plan: 'Pro',
  },

  // ── Integrations ──
  integrations: {
    github: false,
    gitlab: false,
  },
  setIntegration: (provider, connected) =>
    set((state) => ({
      integrations: { ...state.integrations, [provider]: connected },
    })),
  hasAnyIntegration: () => {
    const { integrations } = get();
    return integrations.github || integrations.gitlab;
  },

  // ── Selected Goal (Step 1) ──
  selectedGoal: null, // 'engineer' | 'docs'
  setSelectedGoal: (goal) => set({ selectedGoal: goal }),

  // ── Repository Selection (Step 2) ──
  activeProvider: 'github', // 'github' | 'gitlab' | 'demo'
  setActiveProvider: (provider) => set({ activeProvider: provider }),
  
  selectedRepo: null,
  setSelectedRepo: (repo) => set({ selectedRepo: repo }),

  // ── Branch Config (Step 3) ──
  sourceBranch: 'main',
  setSourceBranch: (branch) => set({ sourceBranch: branch }),
  
  createFeatureBranch: true,
  setCreateFeatureBranch: (val) => set({ createFeatureBranch: val }),
  
  targetBranch: `ai-feat-${new Date().toISOString().slice(5, 10).replace('-', '')}`,
  setTargetBranch: (branch) => set({ targetBranch: branch }),

  // ── Workspace (Step 4) ──
  sessionActive: false,
  setSessionActive: (val) => set({ sessionActive: val }),
  
  chatHistory: [],
  addMessage: (message) =>
    set((state) => ({
      chatHistory: [...state.chatHistory, message],
    })),
  clearChat: () => set({ chatHistory: [] }),

  // ── Mock Data ──
  mockRepos: {
    github: [
      { name: 'u-code/dashboard', updated: '2026-02-14', lang: 'TypeScript', stars: 142 },
      { name: 'u-code/api-gateway', updated: '2026-02-13', lang: 'Go', stars: 89 },
      { name: 'u-code/auth-service', updated: '2026-02-12', lang: 'Python', stars: 67 },
      { name: 'u-code/mobile-app', updated: '2026-02-10', lang: 'React Native', stars: 234 },
      { name: 'u-code/ml-pipeline', updated: '2026-02-08', lang: 'Python', stars: 56 },
      { name: 'u-code/docs-engine', updated: '2026-02-06', lang: 'JavaScript', stars: 31 },
    ],
    gitlab: [
      { name: 'platform/core-service', updated: '2026-02-14', lang: 'Java', stars: 45 },
      { name: 'platform/infra-config', updated: '2026-02-11', lang: 'Terraform', stars: 12 },
      { name: 'platform/auth-module', updated: '2026-02-09', lang: 'Go', stars: 28 },
    ],
    demo: [
      { name: 'demo/todo-fullstack', updated: '2026-02-15', lang: 'JavaScript', stars: 0, isDemo: true },
      { name: 'demo/e-commerce-api', updated: '2026-02-15', lang: 'Python', stars: 0, isDemo: true },
      { name: 'demo/chat-realtime', updated: '2026-02-15', lang: 'TypeScript', stars: 0, isDemo: true },
    ],
  },

  // ── Reset ──
  resetFlow: () =>
    set({
      selectedGoal: null,
      selectedRepo: null,
      sourceBranch: 'main',
      createFeatureBranch: true,
      targetBranch: `ai-feat-${new Date().toISOString().slice(5, 10).replace('-', '')}`,
      sessionActive: false,
      chatHistory: [],
    }),
}));

export default useFlowStore;
