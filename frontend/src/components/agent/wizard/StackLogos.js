'use client';

// ─────────────────────────────────────────────────────────
//  StackLogos — SVG logo icons for each tech stack
//  Used by WizardStep0Stack
// ─────────────────────────────────────────────────────────

export const StackLogos = {
  'html-css': () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><polygon fill="#E44D26" points="5.902 27.201 3.655 2 28.345 2 26.095 27.197 15.985 30"/><polygon fill="#F16529" points="16 27.858 24.17 25.593 26.092 4.061 16 4.061"/><path fill="#EBEBEB" d="M16 13.407H11.91l-.282-3.165H16V7.151H8.383l.074.83.759 8.517H16zm0 8.027l-.014.004-3.442-.929-.22-2.465H9.221l.433 4.852 6.332 1.758.014-.004z"/><path fill="#fff" d="M15.989 13.407v3.091h3.806l-.358 4.009-3.448.93v3.216l6.337-1.757.046-.522.726-8.137.076-.83h-.834zm0-6.256v3.091h7.466l.062-.694.141-1.567.074-.83z"/></svg>
  ),
  nextjs: () => (
    <svg viewBox="0 0 180 180" className="w-6 h-6"><mask id="nj" maskUnits="userSpaceOnUse" x="0" y="0" width="180" height="180"><circle cx="90" cy="90" r="90" fill="white"/></mask><g mask="url(#nj)"><circle cx="90" cy="90" r="90" fill="black" className="dark:fill-white"/><path d="M149.508 157.52L69.142 54H54v71.97h12.114V69.384l73.885 95.461A90.304 90.304 0 01149.508 157.52z" fill="url(#ng)" className="dark:[fill:url(#ng-dark)]"/><rect x="115" y="54" width="12" height="72" fill="url(#nr)"/></g><defs><linearGradient id="ng" x1="109" y1="116.5" x2="144.5" y2="160.5" gradientUnits="userSpaceOnUse"><stop stopColor="white"/><stop offset="1" stopColor="white" stopOpacity="0"/></linearGradient><linearGradient id="ng-dark" x1="109" y1="116.5" x2="144.5" y2="160.5" gradientUnits="userSpaceOnUse"><stop stopColor="black"/><stop offset="1" stopColor="black" stopOpacity="0"/></linearGradient><linearGradient id="nr" x1="121" y1="54" x2="120.799" y2="106.875" gradientUnits="userSpaceOnUse"><stop stopColor="white" className="dark:[stop-color:black]"/><stop offset="1" stopColor="white" stopOpacity="0" className="dark:[stop-color:black]"/></linearGradient></defs></svg>
  ),
  react: () => <img src="/icons/reactjs.svg" alt="React" className="w-6 h-6" />,
  fastapi: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path fill="#009688" d="M16 2C8.268 2 2 8.268 2 16s6.268 14 14 14 14-6.268 14-14S23.732 2 16 2zm-.6 25.2V18h-4l5.2-13.2V14h4l-5.2 13.2z"/></svg>
  ),
  express: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path d="M2 16c0-7.732 6.268-14 14-14s14 6.268 14 14-6.268 14-14 14S2 23.732 2 16z" fill="#333" className="dark:fill-slate-300"/><path d="M9.5 12h2v8h-2zm4 0l3.5 4-3.5 4h2.5l2.25-2.67L20.5 20H23l-3.5-4 3.5-4h-2.5l-2.25 2.67L16 12z" fill="white" className="dark:fill-slate-900"/></svg>
  ),
  django: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><rect x="2" y="2" width="28" height="28" rx="4" fill="#092E20"/><path d="M18.4 6h3.2v13.6c-1.648.312-2.856.424-4.176.424-3.928 0-5.976-1.776-5.976-5.176 0-3.256 2.16-5.36 5.504-5.36.568 0 1 .048 1.448.168V6zm0 6.96a2.856 2.856 0 00-1.08-.192c-1.624 0-2.568 1-2.568 2.728 0 1.68.904 2.608 2.536 2.608.36 0 .656-.024 1.112-.104V12.96zM22.56 6h3.2v4.072h-3.2V6zm0 5.504h3.2v9.296h-3.2v-9.296z" fill="white"/></svg>
  ),
  nestjs: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path d="M18.744 2.641a2.985 2.985 0 00-1.019.218 2.215 2.215 0 01.855.764c.152.254.256.533.308.824.019.135.026.27.022.406a5.832 5.832 0 01.131 1.3c.053.712-.032 1.5-.616 2.013a2.135 2.135 0 01-.313.224 2.244 2.244 0 01.2-.907A2.4 2.4 0 0118.744 2.641zm-2.453 4.035c-.249.5-.187 1.089-.218 1.632a4.619 4.619 0 01-.153.95 1.837 1.837 0 01-.428.7 3.485 3.485 0 01-.692.553l-.163.109A8.577 8.577 0 0110.7 13.61a7.908 7.908 0 00-1.319 2.2 6.6 6.6 0 00-.434 2.656 9.023 9.023 0 004.074 7.276c-.009-.06-.024-.117-.03-.177a5.08 5.08 0 01.045-1.308 6.081 6.081 0 01.322-1.261 8.147 8.147 0 011.3-2.191c.368-.435.773-.837 1.152-1.262a14.233 14.233 0 001.663-2.2 6.148 6.148 0 00.836-3.2c-.024.081-.044.163-.07.244a3.975 3.975 0 01-1.258 1.827A3.449 3.449 0 0115.67 17a2.672 2.672 0 01-1.028-.4 2.656 2.656 0 01-.865-.911 3.141 3.141 0 01-.382-1.343 3.739 3.739 0 01.4-2.06 4.063 4.063 0 011.066-1.271c.163-.127.334-.242.51-.349a6.289 6.289 0 00-.006-.636 3.014 3.014 0 00-.26-1.06l-.035-.075a1.64 1.64 0 00-1.357-1.213z" fill="#E0234E"/><path d="M21.6 10.9a.591.591 0 00-.268.036c.181.128.288.321.389.506a3.6 3.6 0 01.348 2.112 4.244 4.244 0 01-.675 1.883A12.06 12.06 0 0119.9 17.4a13.6 13.6 0 00-1.538 2.194 7.27 7.27 0 00-.809 2.524 6.476 6.476 0 00.2 2.618A9 9 0 0024.2 18.51a8.892 8.892 0 00.715-4.4 5.389 5.389 0 00-1.247-3.017 2.77 2.77 0 00-1.571-.97 1.159 1.159 0 00-.496-.223z" fill="#E0234E"/></svg>
  ),
  vue: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path fill="#41B883" d="M24.4 3.925H30l-14 24.15L2 3.925h10.71l3.29 5.6 3.22-5.6z"/><path fill="#41B883" d="M2 3.925l14 24.15 14-24.15h-5.6L16 18.415 7.53 3.925z"/><path fill="#35495E" d="M7.53 3.925L16 18.485l8.4-14.56h-5.18L16 9.525l-3.29-5.6z"/></svg>
  ),
};

export const STACKS = [
  { id: 'nextjs',   name: 'Next.js',          description: 'Full-stack React with SSR',      tag: 'Popular' },
  { id: 'react',    name: 'React',             description: 'Component-based admin panels',   tag: 'Popular' },
  { id: 'vue',      name: 'Vue.js',            description: 'Progressive admin framework',    tag: 'Popular' },
  { id: 'html-css', name: 'HTML & CSS',        description: 'Pure static site — no framework', tag: 'Simple' },
  { id: 'fastapi',  name: 'FastAPI',            description: 'Modern Python backend',          tag: 'Backend' },
  { id: 'express',  name: 'Node.js + Express', description: 'Lightweight Node.js server',     tag: 'Backend' },
  { id: 'django',   name: 'Django',            description: 'Batteries-included Python',      tag: 'Backend' },
  { id: 'nestjs',   name: 'NestJS',            description: 'TypeScript Node.js framework',   tag: 'Backend' },
];
