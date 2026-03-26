/**
 * Mock data for demo / offline development.
 *
 * Activated by PUBLIC_USE_MOCK_DATA=true in .env.
 * Data matches the TypeScript types in $lib/types/api.ts and mirrors
 * the sample data from the React mockup (streamlit-v4-vertical-tabs.jsx).
 *
 * Issue #38: Data Integration — Bootstrap
 */

import type { Agent, TaskSummary, MemoryEntry, PullRequest } from '$lib/types/api.js';

export const MOCK_AGENTS: Agent[] = [
	{
		id: 'arch',
		name: 'Architect',
		model: 'gemini-2.5-flash-preview-05-20',
		status: 'idle',
		current_task_id: null,
		color: '#22d3ee'
	},
	{
		id: 'dev',
		name: 'Lead Dev',
		model: 'claude-sonnet-4-20250514',
		status: 'coding',
		current_task_id: 'task-001',
		color: '#a78bfa'
	},
	{
		id: 'qa',
		name: 'QA Agent',
		model: 'claude-sonnet-4-20250514',
		status: 'waiting',
		current_task_id: null,
		color: '#34d399'
	}
];

export const MOCK_TASKS: TaskSummary[] = [
	{
		id: 'task-001',
		description: 'Set up Supabase auth with RLS for the user_profiles table',
		status: 'passed',
		created_at: '2026-03-26T14:32:00Z',
		completed_at: '2026-03-26T14:37:00Z',
		budget: {
			tokens_used: 38200,
			token_budget: 50000,
			retries_used: 1,
			max_retries: 3,
			cost_used: 0.47,
			cost_budget: 1.0
		},
		timeline: [
			{ time: '14:32', agent: 'arch', action: 'Blueprint created for auth-middleware module', type: 'plan', sandbox: 'locked' },
			{ time: '14:33', agent: 'dev', action: 'Picked up blueprint. Writing auth.js...', type: 'code', sandbox: 'locked' },
			{ time: '14:35', agent: 'dev', action: 'E2B sandbox spun up. Running npm test...', type: 'exec', sandbox: 'locked' },
			{ time: '14:36', agent: 'qa', action: '2 tests failed: session cookie not set on redirect', type: 'fail', sandbox: 'locked' },
			{ time: '14:36', agent: 'dev', action: 'Retry 1/3 - applying fix from QA failure report', type: 'retry', sandbox: 'locked' },
			{ time: '14:37', agent: 'dev', action: 'All 14 tests passing. PR #142 opened.', type: 'success', sandbox: 'locked' }
		]
	}
];

export const MOCK_MEMORY: MemoryEntry[] = [
	{
		id: 'mem-1',
		content: 'Supabase auth requires RLS policies on all public tables',
		tier: 'l0-discovered',
		module: 'auth-middleware',
		source_agent: 'Architect',
		verified: false,
		status: 'pending',
		created_at: 1711454280000,
		expires_at: 1711626960000,
		hours_remaining: 47.97
	},
	{
		id: 'mem-2',
		content: 'auth.js depends on supabase-ssr v0.5+ for cookie-based sessions',
		tier: 'l1',
		module: 'auth-middleware',
		source_agent: 'Lead Dev',
		verified: false,
		status: 'pending',
		created_at: 1711453920000,
		expires_at: null,
		hours_remaining: null
	},
	{
		id: 'mem-3',
		content: 'Rate limiter middleware must wrap all /api/* routes',
		tier: 'l0-discovered',
		module: 'rate-limiter',
		source_agent: 'QA Agent',
		verified: false,
		status: 'pending',
		created_at: 1711453560000,
		expires_at: 1711626240000,
		hours_remaining: 47.77
	}
];

export const MOCK_PRS: PullRequest[] = [
	{
		id: '#142',
		title: 'feat: add Supabase auth middleware',
		author: 'Lead Dev',
		status: 'review',
		branch: 'feature/supabase-auth',
		base: 'main',
		summary: 'Adds cookie-based auth middleware with automatic session refresh. Includes RLS migration for user_profiles table.',
		additions: 187,
		deletions: 23,
		file_count: 4,
		files: [
			{ name: 'src/middleware/auth.js', additions: 94, deletions: 0, status: 'added' },
			{ name: 'src/lib/supabase.js', additions: 52, deletions: 12, status: 'modified' },
			{ name: 'supabase/migrations/003_rls.sql', additions: 28, deletions: 0, status: 'added' },
			{ name: 'tests/auth.test.js', additions: 13, deletions: 11, status: 'modified' }
		],
		tests: { passed: 14, failed: 0, total: 14 }
	},
	{
		id: '#141',
		title: 'fix: RLS policy for user_profiles',
		author: 'Lead Dev',
		status: 'merged',
		branch: 'fix/rls-profiles',
		base: 'main',
		summary: 'Fixes permissive RLS policy that allowed unauthenticated reads on user_profiles.',
		additions: 34,
		deletions: 8,
		file_count: 2,
		files: [
			{ name: 'supabase/migrations/002_rls.sql', additions: 34, deletions: 8, status: 'modified' }
		],
		tests: { passed: 9, failed: 0, total: 9 }
	}
];

/** Terminal log lines for demo mode. */
export const MOCK_LOG_LINES = [
	{ type: 'cmd', text: "$ langgraph run --task 'supabase-auth-rls'" },
	{ type: 'info', text: '[orchestrator] Task accepted. Spinning up agent team...' },
	{ type: 'info', text: '[orchestrator] Architect assigned -> blueprint generation' },
	{ type: 'info', text: '[sandbox:locked] E2B micro-VM started (dev-sandbox-a3f2)' },
	{ type: 'warn', text: '[qa] 2/14 tests failed - session cookie not set on redirect' },
	{ type: 'info', text: '[orchestrator] Retry 1/3 dispatched to Lead Dev' },
	{ type: 'success', text: '[qa] 14/14 tests passing' },
	{ type: 'success', text: '[github] PR #142 opened -> feat: add Supabase auth middleware' },
	{ type: 'info', text: '[memory] 3 new entries pending approval' }
];
