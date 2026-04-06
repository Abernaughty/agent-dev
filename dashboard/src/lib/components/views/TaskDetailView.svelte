<!--
	TaskDetailView — full task detail with error messages, blueprint,
	generated code, budget, and compact timeline.

	Fetches TaskDetail on-demand via tasksStore.fetchDetail().
	Replaces the old BlueprintView for task-{id} selections.

	Issue #108: Click-to-expand task detail view
	CR fixes: neutral acceptance criteria indicators, sandbox output in timeline,
	          per-task loading/error checks
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { redactSecrets } from '$lib/utils/redact.js';
	import type { TaskDetail, TaskSummary, TimelineEvent } from '$lib/types/api.js';

	interface Props {
		taskId: string;
	}

	let { taskId }: Props = $props();

	// Summary from the list store (always available)
	const summary = $derived(() => tasksStore.list.find((t) => t.id === taskId) ?? null);

	// Full detail (fetched on-demand)
	const detail = $derived(() => tasksStore.getDetail(taskId));

	// Fetch detail when taskId changes
	$effect(() => {
		if (taskId) {
			tasksStore.fetchDetail(taskId);
		}
	});

	const agentMap = $derived(() => {
		const map: Record<string, { name: string; color: string }> = {};
		for (const a of agentsStore.list) {
			map[a.id] = { name: a.name, color: a.color };
		}
		return map;
	});

	function agentInfo(agentId: string) {
		return agentMap()[agentId] ?? { name: agentId, color: 'var(--color-text-dim)' };
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

	// Collapsible sections
	let showCode = $state(false);
	let showJson = $state(false);
	let showTimeline = $state(true);

	/** Copy text to clipboard. */
	async function copyToClipboard(text: string) {
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			// Fallback: ignore silently
		}
	}

	/** Refresh detail data from the API. */
	function refreshDetail() {
		tasksStore.fetchDetail(taskId, true);
	}

	/** Check if a timeline event has sandbox output to display. */
	function hasSandboxOutput(event: TimelineEvent): boolean {
		return !!(event.output_summary?.trim() || (event.errors && event.errors.length > 0));
	}
</script>

<div class="max-w-[800px] p-5 pl-6" style="font-family: var(--font-mono);">
	{#if summary()}
		{@const s = summary()!}
		{@const d = detail()}
		{@const sColor = statusColors[s.status] || 'var(--color-text-dim)'}
		{@const isFailed = s.status === 'failed'}
		{@const isLoading = tasksStore.isDetailLoading(taskId)}
		{@const fetchError = tasksStore.getDetailError(taskId)}

		<!-- ===== STATUS HEADER ===== -->
		<div class="mb-1.5 flex items-center gap-2.5">
			<span class="text-[16px] font-semibold" style="color: var(--color-text-bright);">{s.description.length > 70 ? s.description.slice(0, 70) + '...' : s.description}</span>
			<span
				class="rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase"
				style="color: {sColor}; background: {sColor}15;"
			>{s.status}</span>
		</div>
		<div class="mb-5 flex items-center gap-3 text-[10px]" style="color: var(--color-text-dim);">
			<span>Task: {s.id}</span>
			{#if s.workspace}
				<span style="color: var(--color-text-faint);">|</span>
				<span>{s.workspace}</span>
			{/if}
			{#if s.pr_url}
				<span style="color: var(--color-text-faint);">|</span>
				<a href={s.pr_url} target="_blank" rel="noopener" class="underline" style="color: var(--color-accent-cyan);">PR #{s.pr_number}</a>
			{/if}
			<span class="flex-1"></span>
			<button
				onclick={refreshDetail}
				class="cursor-pointer rounded border px-2 py-0.5 text-[9px] transition-opacity hover:opacity-100"
				style="background: transparent; border-color: var(--color-border); color: var(--color-text-dim); opacity: 0.7;"
			>Refresh</button>
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
				style="background: var(--color-accent-red, #ef4444)08; border-color: var(--color-accent-red, #ef4444)30; border-left: 3px solid var(--color-accent-red, #ef4444);"
			>
				<div class="mb-2 flex items-center gap-2">
					<span class="text-[10px] font-semibold uppercase" style="color: var(--color-accent-red); letter-spacing: 1px;">Failure Reason</span>
				</div>
				<div class="text-[12px] leading-relaxed" style="color: #f8a0a0; white-space: pre-wrap;">{redactSecrets(errorMsg)}</div>
			</div>
		{/if}

		<!-- ===== DETAIL FETCH ERROR ===== -->
		{#if fetchError && !isLoading}
			<div class="mb-4 rounded-md border px-3 py-2 text-[11px]" style="background: var(--color-accent-red)08; border-color: var(--color-accent-red)20; color: var(--color-accent-red);">
				Failed to load details: {fetchError}
			</div>
		{/if}

		<!-- ===== BLUEPRINT ===== -->
		{#if d?.blueprint}
			{@const bp = d.blueprint}
			<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">BLUEPRINT</div>

			<!-- Instructions -->
			<div
				class="mb-4 rounded-md border p-3.5"
				style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid var(--color-accent-cyan);"
			>
				<div class="mb-1 text-[9px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">INSTRUCTIONS</div>
				<div class="text-[12px] leading-relaxed" style="color: var(--color-text-muted); white-space: pre-wrap;">{redactSecrets(bp.instructions)}</div>
			</div>

			<!-- Target Files -->
			{#if bp.target_files.length > 0}
				<div class="mb-1 text-[9px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">TARGET FILES</div>
				<div class="mb-4 flex flex-wrap gap-1.5">
					{#each bp.target_files as f}
						<span class="rounded px-2 py-0.5 text-[10px]" style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)08;">{f}</span>
					{/each}
				</div>
			{/if}

			<!-- Constraints -->
			{#if bp.constraints.length > 0}
				<div class="mb-1 text-[9px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">CONSTRAINTS</div>
				<div class="mb-4 flex flex-col gap-1">
					{#each bp.constraints as c}
						<div class="rounded border px-2.5 py-1.5 text-[11px] leading-relaxed" style="color: var(--color-accent-amber); background: var(--color-accent-amber)08; border-color: var(--color-accent-amber)15;">{c}</div>
					{/each}
				</div>
			{/if}

			<!-- Acceptance Criteria — CR fix #1: neutral indicator, no fabricated per-criterion status -->
			{#if bp.acceptance_criteria.length > 0}
				<div class="mb-1 text-[9px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">ACCEPTANCE CRITERIA</div>
				<div class="mb-5 flex flex-col gap-1">
					{#each bp.acceptance_criteria as c}
						<div class="flex items-start gap-2 rounded border px-2.5 py-1.5 text-[11px] leading-relaxed" style="color: var(--color-text-muted); background: var(--color-bg-activity); border-color: var(--color-border);">
							<span class="mt-px shrink-0" style="color: var(--color-text-dim);">\u2022</span>
							<span>{c}</span>
						</div>
					{/each}
				</div>
			{/if}
		{:else if !d && !isLoading}
			<!-- Fallback: show description as instructions when no detail loaded -->
			<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">INSTRUCTIONS</div>
			<div
				class="mb-5 rounded-md border p-3.5"
				style="background: var(--color-bg-activity); border-color: var(--color-border); border-left: 3px solid var(--color-accent-cyan);"
			>
				<div class="text-[12px] leading-relaxed" style="color: var(--color-text-muted);">{s.description}</div>
			</div>
		{/if}

		<!-- ===== GENERATED CODE (collapsible) ===== -->
		{#if d?.generated_code}
			<div class="mb-4">
				<button
					onclick={() => showCode = !showCode}
					class="mb-1 flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-[10px]"
					style="color: var(--color-text-dim); font-family: var(--font-mono); letter-spacing: 1px;"
				>
					<span style="font-size: 8px;">{showCode ? '\u25bc' : '\u25b6'}</span>
					GENERATED CODE ({d.generated_code.split('\n').length} lines)
				</button>
				{#if showCode}
					<div class="relative rounded-md border" style="background: #08090e; border-color: var(--color-border);">
						<button
							onclick={() => copyToClipboard(d.generated_code)}
							class="absolute right-2 top-2 cursor-pointer rounded border px-2 py-0.5 text-[8px] opacity-60 transition-opacity hover:opacity-100"
							style="background: var(--color-bg-surface); border-color: var(--color-border); color: var(--color-text-dim);"
						>Copy</button>
						<pre class="overflow-x-auto p-3.5 pr-16 text-[10px] leading-relaxed" style="color: var(--color-text-muted); font-family: var(--font-mono); margin: 0; white-space: pre-wrap; word-break: break-word;">{redactSecrets(d.generated_code)}</pre>
					</div>
				{/if}
			</div>
		{/if}

		<!-- ===== BUDGET SUMMARY ===== -->
		<div class="mb-2 text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">BUDGET</div>
		<div class="mb-5 grid grid-cols-3 gap-3">
			{#each [
				{ label: 'Tokens', value: `${s.budget.tokens_used.toLocaleString()} / ${s.budget.token_budget.toLocaleString()}` },
				{ label: 'Cost', value: `$${s.budget.cost_used.toFixed(2)} / $${s.budget.cost_budget}` },
				{ label: 'Retries', value: `${s.budget.retries_used} / ${s.budget.max_retries}` }
			] as metric}
				<div class="rounded-md border p-2.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
					<div class="text-[8px]" style="color: var(--color-text-dim);">{metric.label}</div>
					<div class="text-[13px] font-semibold" style="color: var(--color-text-bright);">{metric.value}</div>
				</div>
			{/each}
		</div>

		<!-- ===== COMPACT TIMELINE (collapsible) ===== -->
		{#if s.timeline.length > 0}
			<button
				onclick={() => showTimeline = !showTimeline}
				class="mb-1 flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-[10px]"
				style="color: var(--color-text-dim); font-family: var(--font-mono); letter-spacing: 1px;"
			>
				<span style="font-size: 8px;">{showTimeline ? '\u25bc' : '\u25b6'}</span>
				TIMELINE ({s.timeline.length} events)
			</button>
			{#if showTimeline}
				<div class="mb-5 rounded-md border p-3" style="background: var(--color-bg-activity); border-color: var(--color-border);">
					{#each s.timeline as event, i}
						{@const agent = agentInfo(event.agent)}
						{@const style = eventStyles[event.type] ?? { color: 'var(--color-text-dim)', label: '?' }}
						{@const isTool = event.type === 'tool_call'}
						<div class:mb-1.5={i < s.timeline.length - 1}>
							<div class="flex items-start gap-2" class:opacity-70={isTool}>
								<span class="shrink-0 text-[9px]" style="color: var(--color-text-faint); min-width: 34px; padding-top: 1px;">{event.time}</span>
								<span class="shrink-0 text-[10px] font-semibold" style="color: {agent.color}; min-width: 20px;">{agent.name.charAt(0)}</span>
								<span
									class="shrink-0 rounded-sm px-1 py-px text-center font-semibold"
									class:text-[7px]={isTool}
									class:text-[8px]={!isTool}
									style="color: {style.color}; background: {style.color}18; letter-spacing: 0.5px; min-width: 32px;"
								>{style.label}</span>
								<span class="text-[10px] leading-relaxed" style="color: var(--color-text-muted);">{event.action}</span>
							</div>
							<!-- CR fix #2: Surface sandbox validation payload -->
							{#if hasSandboxOutput(event)}
								<div class="ml-[88px] mt-1">
									{#if event.errors && event.errors.length > 0}
										<div class="rounded border-l-2 px-2 py-1" style="background: var(--color-accent-red)08; border-color: var(--color-accent-red);">
											{#each event.errors as err}
												<div class="text-[9px] leading-relaxed" style="color: var(--color-accent-red);">{redactSecrets(err)}</div>
											{/each}
										</div>
									{/if}
									{#if event.output_summary?.trim()}
										<pre class="mt-0.5 max-h-[120px] overflow-y-auto rounded px-2 py-1 text-[9px] leading-relaxed" style="background: var(--color-bg-activity); color: var(--color-text-dim); margin: 0; white-space: pre-wrap; word-break: break-word;">{redactSecrets(event.output_summary)}</pre>
									{/if}
									{#if event.exit_code !== undefined}
										<div class="mt-0.5 text-[8px]" style="color: {event.exit_code === 0 ? 'var(--color-accent-green)' : 'var(--color-accent-red)'};">exit code: {event.exit_code}</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		{/if}

		<!-- ===== RAW JSON (collapsible) ===== -->
		<button
			onclick={() => showJson = !showJson}
			class="mb-1 flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-[10px]"
			style="color: var(--color-text-dim); font-family: var(--font-mono); letter-spacing: 1px;"
		>
			<span style="font-size: 8px;">{showJson ? '\u25bc' : '\u25b6'}</span>
			TASK JSON
		</button>
		{#if showJson}
			<div class="overflow-x-auto rounded-md border p-3.5" style="background: #08090e; border-color: var(--color-border);">
				<pre class="m-0 text-[11px] leading-relaxed" style="color: var(--color-text-muted);">{JSON.stringify({
					task_id: s.id,
					description: s.description,
					status: s.status,
					workspace: s.workspace,
					budget: s.budget,
					...(d ? {
						error_message: d.error_message || undefined,
						blueprint: d.blueprint || undefined
					} : {})
				}, null, 2)}</pre>
			</div>
		{/if}

	{:else}
		<div class="flex h-full flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">Task not found</div>
		</div>
	{/if}
</div>
