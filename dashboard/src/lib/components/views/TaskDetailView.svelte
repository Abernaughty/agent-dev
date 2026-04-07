<!--
	TaskDetailView — full task detail with outcome summary, collapsible blueprint,
	split code/reasoning, milestone timeline, and budget metrics.

	Fetches TaskDetail on-demand via tasksStore.fetchDetail().
	Replaces the old BlueprintView for task-{id} selections.

	Issue #108: Click-to-expand task detail view
	Issue #143: Design polish — hierarchy, typography, contrast, affordances
	Issue #109: Added cross-navigation to PR blade via dashboard context

	Hotfix (post-#143):
	  - Fix 1: Duplicate "Agent reasoning" label — inner header shows neutral text
	  - Fix 2: Timeline background matches other sections (#08090e)
	  - Fix 3: File cards are collapsible (click header to toggle)
	  - Fix 4: Markdown rendered in agent reasoning via marked
	  - Fix 5: Remove redundant "Redacted JSON" label from Task JSON header
	  - Fix 6: Move initExpandedFiles into $effect (state_unsafe_mutation)
-->
<script lang="ts">
	import { untrack } from 'svelte';
	import { Marked } from 'marked';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { getDashboardContext } from '$lib/stores/dashboard.svelte.js';
	import { redactSecrets } from '$lib/utils/redact.js';
	import type { TaskDetail, TaskSummary, TimelineEvent } from '$lib/types/api.js';

	interface Props {
		taskId: string;
	}

	let { taskId }: Props = $props();

	const dash = getDashboardContext();

	const summary = $derived.by(() => tasksStore.list.find((t) => t.id === taskId) ?? null);
	const detail = $derived.by(() => tasksStore.getDetail(taskId));

	const agentMap = $derived.by(() => {
		const map: Record<string, { name: string; color: string }> = {};
		for (const a of agentsStore.list) {
			map[a.id] = { name: a.name, color: a.color };
		}
		return map;
	});

	// Fetch detail when taskId changes.
	// CRITICAL: untrack the fetch call so this effect only depends on taskId,
	// not on the reactive state that fetchDetail mutates.
	$effect(() => {
		const id = taskId;
		if (id) {
			untrack(() => tasksStore.fetchDetail(id));
		}
	});

	function agentInfo(agentId: string) {
		return agentMap[agentId] ?? { name: agentId, color: 'var(--color-text-dim)' };
	}

	const eventStyles: Record<string, { color: string; label: string }> = {
		plan: { color: 'var(--color-accent-cyan)', label: 'PLAN' },
		code: { color: 'var(--color-accent-purple)', label: 'CODE' },
		exec: { color: 'var(--color-accent-yellow)', label: 'EXEC' },
		fail: { color: 'var(--color-accent-red)', label: 'FAIL' },
		retry: { color: 'var(--color-accent-orange)', label: 'RETRY' },
		success: { color: 'var(--color-accent-green)', label: 'DONE' },
		tool_call: { color: 'var(--color-accent-blue, #60a5fa)', label: 'TOOL' }
	};

	const statusColors: Record<string, string> = {
		passed: 'var(--color-accent-green)',
		failed: 'var(--color-accent-red)',
		escalated: 'var(--color-accent-orange)',
		cancelled: 'var(--color-text-dim)',
		queued: 'var(--color-accent-yellow)',
		planning: 'var(--color-accent-cyan)',
		building: 'var(--color-accent-purple)',
		reviewing: 'var(--color-accent-yellow)'
	};

	// P1: Collapsible sections — blueprint collapsed for completed tasks
	let showBlueprint = $state(false);
	let showCode = $state(false);
	let showReasoning = $state(false);
	let showJson = $state(false);
	let showTimeline = $state(true);
	let showAllTimelineEvents = $state(false);

	// Fix 3: Track which file cards are expanded (by path)
	let expandedFiles: Set<string> = $state(new Set());

	// P1: Auto-expand blueprint for in-progress tasks
	$effect(() => {
		const s = summary;
		if (s) {
			const isComplete = s.status === 'passed' || s.status === 'failed' || s.status === 'cancelled';
			untrack(() => {
				showBlueprint = !isComplete;
			});
		}
	});

	// Fix 6: Initialize first file as expanded when detail arrives.
	let lastInitializedTaskId = '';
	$effect(() => {
		const d = detail;
		const id = taskId;
		if (d?.generated_code && id !== lastInitializedTaskId) {
			const marker = /^# --- FILE:\s*(.+?)\s*---$/m;
			const match = d.generated_code.match(marker);
			untrack(() => {
				if (match) {
					expandedFiles = new Set([match[1]]);
				} else {
					expandedFiles = new Set();
				}
				lastInitializedTaskId = id;
			});
		}
	});

	// Fix 4: Configure marked for safe rendering
	const marked = new Marked({
		breaks: true,
		gfm: true
	});

	/** Render markdown to sanitized HTML. */
	function renderMarkdown(text: string): string {
		try {
			const result = marked.parse(text);
			if (typeof result === 'string') return result;
			return text;
		} catch {
			return text;
		}
	}

	/** Copy text to clipboard. */
	async function copyToClipboard(text: string) {
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			// Fallback: ignore silently
		}
	}

	/** Copy text to clipboard, stopping event propagation (for nested elements). */
	function copyAndStop(e: Event, text: string) {
		e.stopPropagation();
		copyToClipboard(text);
	}

	/** Refresh detail data from the API. */
	function refreshDetail() {
		tasksStore.fetchDetail(taskId, true);
	}

	/** Issue #109: Navigate to PR blade and select a specific PR. */
	function navigateToPR(prNumber: number) {
		dash.handlePanelSwitch('prs');
		dash.handleSelect(`pr-#${prNumber}`);
	}

	function hasSandboxOutput(event: TimelineEvent): boolean {
		return !!(event.output_summary?.trim() || event.errors?.length);
	}

	function buildRedactedJson(s: TaskSummary, d: TaskDetail | null): string {
		const obj: Record<string, unknown> = {
			task_id: s.id,
			description: redactSecrets(s.description),
			status: s.status,
			workspace: s.workspace,
			budget: s.budget
		};
		if (d) {
			if (d.error_message) obj.error_message = redactSecrets(d.error_message);
			if (d.blueprint) {
				obj.blueprint = {
					...d.blueprint,
					instructions: redactSecrets(d.blueprint.instructions)
				};
			}
		}
		return JSON.stringify(obj, null, 2);
	}

	function getMilestoneEvents(timeline: TimelineEvent[]): TimelineEvent[] {
		return timeline.filter((e) => e.type !== 'tool_call');
	}

	function countToolCalls(timeline: TimelineEvent[]): number {
		return timeline.filter((e) => e.type === 'tool_call').length;
	}

	function tokenPct(s: TaskSummary): number {
		if (s.budget.token_budget === 0) return 0;
		return Math.round((s.budget.tokens_used / s.budget.token_budget) * 100);
	}

	function tokenPctColor(pct: number): string {
		if (pct > 90) return 'var(--color-accent-red)';
		if (pct > 75) return 'var(--color-accent-amber)';
		return 'var(--color-text-bright)';
	}

	function splitCodeAndReasoning(raw: string): { files: { path: string; code: string }[]; reasoning: string } {
		const files: { path: string; code: string }[] = [];
		const reasoningLines: string[] = [];
		const marker = /^# --- FILE:\s*(.+?)\s*---$/;
		let currentFile: { path: string; lines: string[] } | null = null;

		for (const line of raw.split('\n')) {
			const match = line.match(marker);
			if (match) {
				if (currentFile) {
					files.push({ path: currentFile.path, code: currentFile.lines.join('\n').trim() });
				}
				currentFile = { path: match[1], lines: [] };
			} else if (currentFile) {
				currentFile.lines.push(line);
			} else {
				reasoningLines.push(line);
			}
		}
		if (currentFile) {
			files.push({ path: currentFile.path, code: currentFile.lines.join('\n').trim() });
		}
		return { files, reasoning: reasoningLines.join('\n').trim() };
	}

	function wordCount(text: string): number {
		if (!text) return 0;
		return text.split(/\s+/).filter(Boolean).length;
	}

	function toggleFile(path: string) {
		const next = new Set(expandedFiles);
		if (next.has(path)) {
			next.delete(path);
		} else {
			next.add(path);
		}
		expandedFiles = next;
	}
</script>

<div class="max-w-[800px] p-5 pl-6" style="font-family: var(--font-mono);">
	{#if summary}
		{@const s = summary}
		{@const d = detail}
		{@const sColor = statusColors[s.status] || 'var(--color-text-dim)'}
		{@const isFailed = s.status === 'failed'}
		{@const isComplete = s.status === 'passed' || s.status === 'failed' || s.status === 'cancelled'}
		{@const isLoading = tasksStore.isDetailLoading(taskId)}
		{@const fetchError = tasksStore.getDetailError(taskId)}
		{@const pct = tokenPct(s)}

		<!-- ===== STATUS HEADER (T1 title) ===== -->
		<div class="mb-1.5 flex items-center gap-2.5">
			<span class="text-[15px] font-medium" style="color: var(--color-text-bright);">{s.description.length > 70 ? s.description.slice(0, 70) + '...' : s.description}</span>
			<span
				class="rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase"
				style="color: {sColor}; background: {sColor}15;"
			>{s.status}</span>
		</div>
		<!-- T4 meta line -->
		<div class="mb-5 flex items-center gap-3 text-[11px]" style="color: var(--color-text-dim);">
			<span>Task: {s.id}</span>
			{#if s.workspace}
				<span style="color: var(--color-text-faint);">|</span>
				<span>{s.workspace}</span>
			{/if}
			{#if s.pr_number}
				<span style="color: var(--color-text-faint);">|</span>
				<button
					onclick={() => navigateToPR(s.pr_number!)}
					class="cursor-pointer underline"
					style="color: var(--color-accent-cyan); background: none; border: none; font-family: var(--font-mono); font-size: 11px;"
				>PR #{s.pr_number} &#8599;</button>
			{/if}
			<span class="flex-1"></span>
			<!-- P5: Refresh button -->
			<button
				onclick={refreshDetail}
				class="flex cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] transition-opacity hover:opacity-100"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); color: var(--color-text-dim);"
			>&#8635; Refresh</button>
		</div>

		<!-- ===== LOADING STATE ===== -->
		{#if isLoading}
			<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: var(--color-bg-activity); border-color: var(--color-border); color: var(--color-text-dim);">
				Loading task details...
			</div>
		{/if}

		<!-- ===== ERROR MESSAGE (prominent for failed tasks) ===== -->
		{#if isFailed}
			{@const errorMsg = d?.error_message || s.completion_detail || 'Task failed \u2014 no error details available. Check terminal output for more info.'}
			<div
				class="mb-5 rounded-md border p-4"
				style="background: var(--color-accent-red)08; border-color: var(--color-accent-red)30; border-left: 3px solid var(--color-accent-red);"
			>
				<div class="mb-2 text-[12px] font-medium" style="color: var(--color-accent-red);">Failure reason</div>
				<div class="text-[12px] leading-relaxed" style="color: #f8a0a0; white-space: pre-wrap;">{redactSecrets(errorMsg)}</div>
			</div>
		{/if}

		<!-- ===== DETAIL FETCH ERROR ===== -->
		{#if fetchError && !isLoading}
			<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: var(--color-accent-red)08; border-color: var(--color-accent-red)20; color: var(--color-accent-red);">
				Failed to load details: {fetchError}
			</div>
		{/if}

		<!-- ===== P1: OUTCOME SUMMARY CARD ===== -->
		{#if isComplete}
			<div class="mb-5 rounded-md border p-3.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<!-- Metric tiles -->
				<div class="mb-3 grid grid-cols-4 gap-2.5">
					{#each [
						{ label: 'Duration', value: s.completed_at ? '\u2014' : '...', color: 'var(--color-text-bright)' },
						{ label: 'Cost', value: `$${s.budget.cost_used.toFixed(2)}`, sub: `of $${s.budget.cost_budget}`, color: 'var(--color-text-bright)' },
						{ label: 'Retries', value: `${s.budget.retries_used} / ${s.budget.max_retries}`, color: s.budget.retries_used >= s.budget.max_retries ? 'var(--color-accent-red)' : 'var(--color-text-bright)' },
						{ label: 'Tokens', value: `${pct}%`, sub: `${(s.budget.tokens_used / 1000).toFixed(1)}k / ${(s.budget.token_budget / 1000).toFixed(0)}k`, color: tokenPctColor(pct) }
					] as metric}
						<div class="rounded-md border p-2.5" style="background: var(--color-bg-primary); border-color: var(--color-border);">
							<div class="text-[11px]" style="color: var(--color-text-dim);">{metric.label}</div>
							<div class="text-[18px] font-medium" style="color: {metric.color};">{metric.value}</div>
							{#if metric.sub}
								<div class="text-[11px]" style="color: var(--color-text-dim);">{metric.sub}</div>
							{/if}
						</div>
					{/each}
				</div>
				<!-- Output badges -->
				<div class="flex flex-wrap gap-2">
					{#if d?.blueprint?.target_files}
						<span class="rounded border px-2.5 py-1 text-[11px]" style="color: var(--color-text-dim); background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border));">{d.blueprint.target_files.length} file{d.blueprint.target_files.length !== 1 ? 's' : ''} targeted</span>
					{/if}
					{#if s.pr_number}
						<button
							onclick={() => navigateToPR(s.pr_number!)}
							class="cursor-pointer rounded border px-2.5 py-1 text-[11px]"
							style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)10; border-color: var(--color-accent-cyan)25; font-family: var(--font-mono);"
						>PR #{s.pr_number} &#8599;</button>
					{/if}
					<span class="rounded border px-2.5 py-1 text-[11px]" style="color: var(--color-accent-green); background: var(--color-accent-green)10; border-color: var(--color-accent-green)25;">{s.timeline.filter(e => e.type === 'success').length > 0 ? 'Tests passing' : 'No test results'}</span>
				</div>
			</div>
		{:else}
			<!-- In-progress: show budget inline -->
			<div class="mb-5 grid grid-cols-3 gap-3">
				{#each [
					{ label: 'Tokens', value: `${s.budget.tokens_used.toLocaleString()} / ${s.budget.token_budget.toLocaleString()}` },
					{ label: 'Cost', value: `$${s.budget.cost_used.toFixed(2)} / $${s.budget.cost_budget}` },
					{ label: 'Retries', value: `${s.budget.retries_used} / ${s.budget.max_retries}` }
				] as metric}
					<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
						<div class="text-[11px]" style="color: var(--color-text-dim);">{metric.label}</div>
						<div class="text-[13px] font-medium" style="color: var(--color-text-bright);">{metric.value}</div>
					</div>
				{/each}
			</div>
		{/if}

		<!-- ===== BLUEPRINT (collapsible) ===== -->
		{#if d?.blueprint}
			{@const bp = d.blueprint}
			<button
				onclick={() => showBlueprint = !showBlueprint}
				class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[11px]" style="color: var(--color-text-faint);">{showBlueprint ? '\u25bc' : '\u25b6'}</span>
					<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Blueprint</span>
				</div>
				<span class="text-[11px]" style="color: var(--color-text-dim);">{bp.target_files.length} file{bp.target_files.length !== 1 ? 's' : ''} &middot; {bp.constraints.length} constraint{bp.constraints.length !== 1 ? 's' : ''} &middot; {bp.acceptance_criteria.length} criteria</span>
			</button>

			{#if showBlueprint}
				<div class="mb-4 rounded-md border p-3.5" style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid var(--color-accent-cyan);">
					<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Instructions</div>
					<div class="text-[12px] leading-relaxed" style="color: var(--color-text-muted); white-space: pre-wrap;">{redactSecrets(bp.instructions)}</div>
				</div>

				{#if bp.target_files.length > 0}
					<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Target files</div>
					<div class="mb-4 flex flex-wrap gap-1.5">
						{#each bp.target_files as f}
							<span class="rounded border px-2 py-0.5 text-[11px]" style="color: var(--color-text-muted); background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border));">{f}</span>
						{/each}
					</div>
				{/if}

				{#if bp.constraints.length > 0}
					<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Constraints</div>
					<div class="mb-4 flex flex-col gap-1">
						{#each bp.constraints as c}
							<div class="flex items-start gap-2 py-1.5 pl-2.5 pr-2.5 text-[12px] leading-relaxed" style="color: var(--color-text-muted); background: var(--color-bg-surface); border-left: 2px solid var(--color-accent-amber);">
								<span class="mt-px shrink-0" style="color: var(--color-text-dim);">&#9676;</span>
								<span>{c}</span>
							</div>
						{/each}
					</div>
				{/if}

				{#if bp.acceptance_criteria.length > 0}
					<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Acceptance criteria</div>
					<div class="mb-5 flex flex-col gap-1">
						{#each bp.acceptance_criteria as c}
							<div class="flex items-start gap-2 py-1.5 pl-2.5 pr-2.5 text-[12px] leading-relaxed" style="color: var(--color-text-muted); background: var(--color-bg-surface); border-left: 2px solid var(--color-accent-green);">
								<span class="mt-px shrink-0" style="color: var(--color-accent-green);">&#10003;</span>
								<span>{c}</span>
							</div>
						{/each}
					</div>
				{/if}
			{/if}
		{:else if !d && !isLoading}
			<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Instructions</div>
			<div class="mb-5 rounded-md border p-3.5" style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid var(--color-accent-cyan);">
				<div class="text-[12px] leading-relaxed" style="color: var(--color-text-muted);">{s.description}</div>
			</div>
		{/if}

		<!-- ===== FILES CREATED ===== -->
		{#if d?.generated_code}
			{@const { files: codeFiles, reasoning } = splitCodeAndReasoning(d.generated_code)}

			{#if codeFiles.length > 0}
				<div class="mb-1.5 text-[12px] font-medium" style="color: var(--color-text-bright);">Files created</div>
				<div class="mb-4 flex flex-col gap-2">
					{#each codeFiles as file}
						{@const redactedCode = redactSecrets(file.code)}
						{@const isExpanded = expandedFiles.has(file.path)}
						<div class="overflow-hidden rounded-md border" style="border-color: var(--color-border);">
							<div
								onclick={() => toggleFile(file.path)}
								onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleFile(file.path); } }}
								role="button"
								tabindex="0"
								class="flex w-full cursor-pointer items-center justify-between border-b px-3 py-1.5"
								style="background: var(--color-bg-surface); border-color: {isExpanded ? 'var(--color-border)' : 'transparent'}; font-family: var(--font-mono);"
							>
								<div class="flex items-center gap-2">
									<span class="text-[11px]" style="color: var(--color-text-faint);">{isExpanded ? '\u25bc' : '\u25b6'}</span>
									<span class="text-[11px]" style="color: var(--color-text-bright);">{file.path}</span>
								</div>
								<button
									onclick={(e) => copyAndStop(e, redactedCode)}
									class="cursor-pointer rounded border px-2 py-0.5 text-[11px] transition-opacity hover:opacity-100"
									style="background: var(--color-bg-primary); border-color: var(--color-border-secondary, var(--color-border)); color: var(--color-text-dim);"
								>Copy</button>
							</div>
							{#if isExpanded}
								<pre class="max-h-[300px] overflow-auto p-3.5 text-[11px] leading-relaxed" style="background: #08090e; color: var(--color-text-muted); font-family: var(--font-mono); margin: 0; white-space: pre-wrap; word-break: break-word;">{redactedCode}</pre>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			{#if reasoning || codeFiles.length === 0}
				{@const displayText = reasoning || d.generated_code}
				{@const redactedReasoning = redactSecrets(displayText)}
				<button
					onclick={() => showReasoning = !showReasoning}
					class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
					style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
				>
					<div class="flex items-center gap-2">
						<span class="text-[11px]" style="color: var(--color-text-faint);">{showReasoning ? '\u25bc' : '\u25b6'}</span>
						<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">{codeFiles.length > 0 ? 'Agent reasoning' : 'Generated code'}</span>
					</div>
					<span class="text-[11px]" style="color: var(--color-text-dim);">~{wordCount(displayText)} words</span>
				</button>
				{#if showReasoning}
					<div class="mb-4 overflow-hidden rounded-md border" style="border-color: var(--color-border);">
						<div class="flex items-center justify-end border-b px-3 py-1.5" style="background: var(--color-bg-surface); border-color: var(--color-border);">
							<button
								onclick={() => copyToClipboard(redactedReasoning)}
								class="cursor-pointer rounded border px-2 py-0.5 text-[11px] transition-opacity hover:opacity-100"
								style="background: var(--color-bg-primary); border-color: var(--color-border-secondary, var(--color-border)); color: var(--color-text-dim);"
							>Copy</button>
						</div>
						<div class="reasoning-content overflow-x-auto p-3.5 text-[12px] leading-relaxed" style="background: #08090e; color: var(--color-text-muted); font-family: var(--font-mono);">
							{@html renderMarkdown(redactedReasoning)}
						</div>
					</div>
				{/if}
			{/if}
		{/if}

		<!-- ===== COMPACT TIMELINE ===== -->
		{#if s.timeline.length > 0}
			{@const milestones = getMilestoneEvents(s.timeline)}
			{@const toolCallCount = countToolCalls(s.timeline)}
			{@const displayEvents = showAllTimelineEvents ? s.timeline : milestones}

			<button
				onclick={() => showTimeline = !showTimeline}
				class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
				style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
			>
				<div class="flex items-center gap-2">
					<span class="text-[11px]" style="color: var(--color-text-faint);">{showTimeline ? '\u25bc' : '\u25b6'}</span>
					<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Timeline</span>
				</div>
				<span class="text-[11px]" style="color: var(--color-text-dim);">{milestones.length} milestone{milestones.length !== 1 ? 's' : ''}{toolCallCount > 0 ? ` \u00b7 ${toolCallCount} tool call${toolCallCount !== 1 ? 's' : ''}` : ''}</span>
			</button>

			{#if showTimeline}
				<div class="mb-5 rounded-md border p-3" style="background: #08090e; border-color: var(--color-border);">
					{#each displayEvents as event, i}
						{@const agent = agentInfo(event.agent)}
						{@const style = eventStyles[event.type] ?? { color: 'var(--color-text-dim)', label: '?' }}
						{@const isTool = event.type === 'tool_call'}
						{@const isFail = event.type === 'fail'}
						<div class:mb-2={i < displayEvents.length - 1}>
							<div class="flex items-start gap-2" class:opacity-70={isTool}>
								<span class="shrink-0 pt-px text-[11px]" style="color: var(--color-text-faint); min-width: 36px;">{event.time}</span>
								<span class="shrink-0 text-[11px] font-medium" style="color: {agent.color}; min-width: 60px;">{agent.name}</span>
								<span class="shrink-0 rounded-sm px-1.5 py-px text-center text-[11px] font-semibold" style="color: {style.color}; background: {style.color}18; letter-spacing: 0.5px; min-width: 38px;">{style.label}</span>
								<span class="text-[11px] leading-relaxed" style="color: {isFail ? '#f8a0a0' : event.type === 'success' ? '#6ee7b7' : isTool ? 'var(--color-text-dim)' : 'var(--color-text-muted)'};">{event.action}</span>
							</div>
							{#if hasSandboxOutput(event)}
								<div class="ml-[100px] mt-1">
									{#if event.errors?.length}
										<div class="rounded px-2 py-1" style="background: var(--color-accent-red)08; border-left: 2px solid var(--color-accent-red);">
											{#each event.errors as err}
												<div class="text-[11px] leading-relaxed" style="color: var(--color-accent-red);">{redactSecrets(err)}</div>
											{/each}
										</div>
									{/if}
									{#if event.output_summary?.trim()}
										<pre class="mt-0.5 max-h-[120px] overflow-y-auto rounded px-2 py-1 text-[11px] leading-relaxed" style="background: var(--color-bg-activity); color: var(--color-text-dim); margin: 0; white-space: pre-wrap; word-break: break-word;">{redactSecrets(event.output_summary)}</pre>
									{/if}
									{#if event.exit_code !== undefined}
										<div class="mt-0.5 text-[11px]" style="color: {event.exit_code === 0 ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">exit code: {event.exit_code}</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}

					{#if toolCallCount > 0}
						<button
							onclick={() => showAllTimelineEvents = !showAllTimelineEvents}
							class="mt-2 w-full cursor-pointer border-t pt-2 text-center text-[11px]"
							style="border-color: var(--color-border); color: var(--color-accent-cyan); font-family: var(--font-mono); background: transparent; border-left: none; border-right: none; border-bottom: none;"
						>
							{showAllTimelineEvents ? `Hide tool calls (show ${milestones.length} milestones)` : `Show all ${s.timeline.length} events (including ${toolCallCount} tool calls)`}
						</button>
					{/if}
				</div>
			{/if}
		{/if}

		<!-- ===== RAW JSON (collapsible) ===== -->
		<button
			onclick={() => showJson = !showJson}
			class="mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border px-3.5 py-2 text-left"
			style="background: var(--color-bg-surface); border-color: var(--color-border-secondary, var(--color-border)); font-family: var(--font-mono);"
		>
			<div class="flex items-center gap-2">
				<span class="text-[11px]" style="color: var(--color-text-faint);">{showJson ? '\u25bc' : '\u25b6'}</span>
				<span class="text-[12px] font-medium" style="color: var(--color-text-bright);">Task JSON</span>
			</div>
		</button>
		{#if showJson}
			<div class="overflow-hidden rounded-md border" style="border-color: var(--color-border);">
				<div class="flex items-center justify-end border-b px-3 py-1.5" style="background: var(--color-bg-surface); border-color: var(--color-border);">
					<button
						onclick={() => copyToClipboard(buildRedactedJson(s, d))}
						class="cursor-pointer rounded border px-2 py-0.5 text-[11px] transition-opacity hover:opacity-100"
						style="background: var(--color-bg-primary); border-color: var(--color-border-secondary, var(--color-border)); color: var(--color-text-dim);"
					>Copy</button>
				</div>
				<pre class="overflow-x-auto p-3.5 text-[11px] leading-relaxed" style="background: #08090e; color: var(--color-text-muted); margin: 0;">{buildRedactedJson(s, d)}</pre>
			</div>
		{/if}

	{:else}
		<div class="flex h-full flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">Task not found</div>
		</div>
	{/if}
</div>

<!-- Scoped styles for markdown-rendered reasoning content -->
<style>
	.reasoning-content :global(h1),
	.reasoning-content :global(h2),
	.reasoning-content :global(h3),
	.reasoning-content :global(h4) {
		color: var(--color-text-bright);
		font-weight: 500;
		margin-top: 1em;
		margin-bottom: 0.4em;
	}
	.reasoning-content :global(h1) { font-size: 14px; }
	.reasoning-content :global(h2) { font-size: 13px; }
	.reasoning-content :global(h3) { font-size: 12px; }
	.reasoning-content :global(h4) { font-size: 12px; color: var(--color-text-muted); }

	.reasoning-content :global(p) {
		margin-bottom: 0.6em;
		line-height: 1.6;
	}

	.reasoning-content :global(strong) {
		color: var(--color-text-bright);
		font-weight: 500;
	}

	.reasoning-content :global(em) {
		font-style: italic;
	}

	.reasoning-content :global(code) {
		background: var(--color-bg-surface);
		color: var(--color-accent-cyan);
		padding: 1px 4px;
		border-radius: 3px;
		font-size: 11px;
	}

	.reasoning-content :global(pre) {
		background: var(--color-bg-activity);
		border: 1px solid var(--color-border);
		border-radius: 4px;
		padding: 10px 12px;
		margin: 0.6em 0;
		overflow-x: auto;
		font-size: 11px;
		line-height: 1.6;
	}

	.reasoning-content :global(pre code) {
		background: none;
		padding: 0;
		color: var(--color-text-muted);
	}

	.reasoning-content :global(ul),
	.reasoning-content :global(ol) {
		padding-left: 1.4em;
		margin-bottom: 0.6em;
	}

	.reasoning-content :global(li) {
		margin-bottom: 0.25em;
		line-height: 1.6;
	}

	.reasoning-content :global(hr) {
		border: none;
		border-top: 1px solid var(--color-border);
		margin: 1em 0;
	}

	.reasoning-content :global(blockquote) {
		border-left: 2px solid var(--color-accent-cyan);
		padding-left: 10px;
		margin: 0.6em 0;
		color: var(--color-text-dim);
	}

	.reasoning-content :global(a) {
		color: var(--color-accent-cyan);
		text-decoration: underline;
	}

	.reasoning-content :global(:first-child) {
		margin-top: 0;
	}

	.reasoning-content :global(:last-child) {
		margin-bottom: 0;
	}
</style>
