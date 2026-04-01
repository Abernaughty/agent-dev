/**
 * TypeScript interfaces for the Dev Suite API.
 *
 * These mirror the Pydantic models in dev-suite/src/api/models.py.
 * Shared between stores, proxy routes, and components.
 *
 * Issue #37: SvelteKit API Routes & Providers
 * Issue #19: Added confidence, sandbox, related_files to MemoryEntry; AuditLogEntry
 * Issue #85: Added tool_call SSE event type and ToolCallEvent interface
 */

// -- Envelope --

export interface ApiMeta {
	timestamp: string;
	version: string;
}

export interface ApiResponse<T = unknown> {
	data: T;
	meta: ApiMeta;
	errors: string[];
}

// -- Agents --

export type AgentStatus = 'idle' | 'planning' | 'coding' | 'reviewing' | 'waiting' | 'error';

export interface Agent {
	id: string;
	name: string;
	model: string;
	status: AgentStatus;
	current_task_id: string | null;
	color: string;
}

// -- Tasks --

export type TaskStatus =
	| 'queued'
	| 'planning'
	| 'building'
	| 'reviewing'
	| 'passed'
	| 'failed'
	| 'escalated'
	| 'cancelled';

export interface TimelineEvent {
	time: string;
	agent: string;
	action: string;
	type: string;
	sandbox: string;
}

export interface Blueprint {
	task_id: string;
	target_files: string[];
	instructions: string;
	constraints: string[];
	acceptance_criteria: string[];
}

export interface TaskBudget {
	tokens_used: number;
	token_budget: number;
	retries_used: number;
	max_retries: number;
	cost_used: number;
	cost_budget: number;
}

export interface TaskSummary {
	id: string;
	description: string;
	status: TaskStatus;
	created_at: string;
	completed_at: string | null;
	budget: TaskBudget;
	timeline: TimelineEvent[];
}

export interface TaskDetail extends TaskSummary {
	blueprint: Blueprint | null;
	generated_code: string;
	error_message: string;
}

export interface CreateTaskRequest {
	description: string;
}

export interface CreateTaskResponse {
	task_id: string;
	status: TaskStatus;
}

// -- Memory --

export type MemoryTier = 'l0-core' | 'l0-discovered' | 'l1' | 'l2';
export type MemoryStatus = 'pending' | 'approved' | 'rejected';

export interface MemoryEntry {
	id: string;
	content: string;
	tier: MemoryTier;
	module: string;
	source_agent: string;
	verified: boolean;
	status: MemoryStatus;
	created_at: number;
	expires_at: number | null;
	hours_remaining: number | null;
	confidence: number;
	sandbox: string;
	related_files: string[];
}

export interface MemoryActionRequest {
	action: 'approve' | 'reject';
}

// -- Audit Log --

export type AuditAction = 'approve' | 'reject';

export interface AuditLogEntry {
	id: string;
	entry_id: string;
	entry_content: string;
	entry_tier: string;
	entry_module: string;
	action: AuditAction;
	timestamp: string;
}

// -- Pull Requests --

export type PRStatus = 'open' | 'review' | 'merged' | 'closed';

export interface PRFileChange {
	name: string;
	additions: number;
	deletions: number;
	status: string;
}

export interface PRTestResults {
	passed: number;
	failed: number;
	total: number;
}

export interface PullRequest {
	id: string;
	title: string;
	author: string;
	status: PRStatus;
	branch: string;
	base: string;
	summary: string;
	additions: number;
	deletions: number;
	file_count: number;
	files: PRFileChange[];
	tests: PRTestResults;
}

// -- SSE --

export type SSEEventType =
	| 'agent_status'
	| 'task_progress'
	| 'task_complete'
	| 'memory_added'
	| 'log_line'
	| 'tool_call';

export interface ToolCallEvent {
	task_id: string;
	agent: string;
	tool: string;
	success: boolean;
	result_preview: string;
}

export interface SSEEventData {
	type: SSEEventType;
	timestamp: string;
	data: Record<string, unknown>;
}

// -- Connection --

export type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

// -- Health --

export interface HealthResponse {
	status: string;
	version: string;
	uptime_seconds: number;
	agents: number;
	active_tasks: number;
	sse_subscribers: number;
}
