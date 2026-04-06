<!--
	TimelineView — task timeline showing events, budget bar, and session debrief.

	Reads from tasksStore + agentsStore to render the timeline for
	the most recent (or active) task.

	Issue #38: Data Integration — PR3
	Issue #85: Added tool_call event rendering
	Issue #107: Sandbox stdout/stderr collapsible output blocks
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { agentsStore } from '$lib/stores/agents.svelte.js';
	import { redactSecrets } from '$lib/utils/redact.js';
	import type { TimelineEvent, TaskBudget } from '$lib/types/api.js';

	const currentTask = $derived(() => {
		const active = tasksStore.activeTasks;
		if (active.length > 0) return active[active.length - 1];
		const list = tasksStore.list;
		return list.length > 0 ? list[list.length - 1] : null;
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

	function budgetPct(b: TaskBudget): number {
		return b.token_budget > 0 ? Math.round((b.tokens_used / b.token_budget) * 100) : 0;
	}

	function budgetColor(pct: number): string {
		if (pct > 90) return 'var(--color-accent-red)';
		if (pct > 75) return 'var(--color-accent-amber)';
		return 'var(--color-accent-cyan)';
	}

	/** Check if an event is a tool_call for compact rendering. */
	function isToolCall(event: TimelineEvent): boolean {
		return event.type === 'tool_call';
	}

	/**
	 * Issue #107: Track sandbox output visibility and full-text state independently.
	 * visibleOutputs: whether the output block is open/visible
	 * fullOutputs: whether the block shows full text vs truncated
	 */
	let visibleOutputs = $state<Set<number>>(new Set());
	let fullOutputs = $state<Set<number>>(new Set());

	/** Check if a timeline event has sandbox output to display. */
	function hasSandboxOutput(event: TimelineEvent): boolean {
		return !!(event.output_summary?.trim() || (event.errors && event.errors.length > 0));
	}

	/** Count lines in output text. */
	function lineCount(text: string): number {
		return text.split('\n').length;
	}

	/** Truncate output to N lines. */
	function truncateOutput(text: string, maxLines: number): string {
		const lines = text.split('\n');
		if (lines.length <= maxLines) return text;
		return lines.slice(0, maxLines).join('\n');
	}

	/** Toggle visibility of a sandbox output block. */
	function toggleOutput(index: number) {
		const next = new Set(visibleOutputs);
		if (next.has(index)) {
			next.delete(index);
		} else {
			next.add(index);
		}
		visibleOutputs = next;
	}

	/** Toggle between truncated and full output display. */
	function toggleFullOutput(index: number) {
		const next = new Set(fullOutputs);
		if (next.has(index)) {
			next.delete(index);
		} else {
			next.add(index);
		}
		fullOutputs = next;
	}

	/** Copy text to clipboard. */
	async function copyToClipboard(text: string) {
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			// Fallback: ignore silently
		}
	}

	/** Check if a sandbox output block should be visible (open). */
	function isOutputVisible(event: TimelineEvent, index: number): boolean {
		if (visibleOutputs.has(index)) return true;
		// Auto-open on failure so users see errors immediately
		return event.exit_code !== undefined && event.exit_code !== 0;
	}

	const MAX_COLLAPSED_LINES = 50;
</script>

<div class="p-4 pl-6" style="font-family: var(--font-mono);">
	{#if currentTask()}
		{@const task = currentTask()!}
		{@const pct = budgetPct(task.budget)}
		{@const bColor = budgetColor(pct)}

		<!-- Header -->
		<div class="mb-3.5 flex items-center gap-2 text-[10px]" style="color: var(--color-text-faint); letter-spacing: 1px;">
			<span>TASK TIMELINE</span>
			<span class="flex-1" style="height: 1px; background: var(--color-border);"></span>
			<span style="color: var(--color-text-dim); letter-spacing: 0;">{task.description}</span>
		</div>

		<!-- Budget bar -->
		<div class="mb-4 rounded-md border p-2.5 px-3.5" style="background: var(--color-bg-activity); border-color: var(--color-border);">
			<div class="mb-2 flex items-center justify-between">
				<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">TASK BUDGET</span>
				<span class="text-[10px]" style="color: {pct > 90 ? 'var(--color-accent-red)' : pct > 75 ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'};">
					{pct > 90 ? 'CRITICAL' : pct > 75 ? 'WARNING' : 'HEALTHY'}
				</span>
			</div>
			<div class="flex items-center gap-5">
				<div class="flex-1">
					<div class="mb-1 flex justify-between">
						<span class="text-[10px]" style="color: var(--color-text-muted);">Tokens</span>
						<span class="text-[10px]" style="color: {bColor};">{(task.budget.tokens_used / 1000).toFixed(0)}k / {(task.budget.token_budget / 1000).toFixed(0)}k ({pct}%)</span>
					</div>
					<div class="h-1.5 overflow-hidden rounded-sm" style="background: var(--color-border);">
						<div class="h-full rounded-sm transition-all duration-300" style="width: {pct}%; background: {bColor};"></div>
					</div>
				</div>
				<div class="flex gap-4">
					<div class="text-center">
						<div class="text-[14px] font-semibold" style="color: {task.budget.retries_used >= task.budget.max_retries ? 'var(--color-accent-red)' : 'var(--color-text-bright)'};">{task.budget.retries_used}/{task.budget.max_retries}</div>
						<div class="text-[8px]" style="color: var(--color-text-dim);">RETRIES</div>
					</div>
					<div class="text-center">
						<div class="text-[14px] font-semibold" style="color: var(--color-text-bright);">${task.budget.cost_used.toFixed(2)}</div>
						<div class="text-[8px]" style="color: var(--color-text-dim);">COST (${task.budget.cost_budget})</div>
					</div>
				</div>
			</div>
		</div>

		<!-- Timeline events -->
		{#each task.timeline as event, i (i)}
			{@const agent = agentInfo(event.agent)}
			{@const style = eventStyles[event.type] ?? { color: 'var(--color-text-dim)', label: '?' }}
			{@const isFail = event.type === 'fail'}
			{@const isSuccess = event.type === 'success'}
			{@const isTool = isToolCall(event)}
			{@const isLast = i === task.timeline.length - 1}

			<div class="relative flex gap-3 pb-0.5">
				{#if !isLast}
					<div class="absolute bottom-0 left-[13px] top-7 w-px" style="background: var(--color-border);"></div>
				{/if}

				<!-- Agent avatar — smaller for tool calls -->
				<div
					class="z-1 flex shrink-0 items-center justify-center rounded-md font-semibold"
					class:h-7={!isTool}
					class:w-7={!isTool}
					class:text-[10px]={!isTool}
					class:h-5={isTool}
					class:w-5={isTool}
					class:text-[8px]={isTool}
					class:mt-0.5={isTool}
					class:ml-1={isTool}
					style="background: {agent.color}18; border: 1px solid {agent.color}25; color: {agent.color};"
				>
					{agent.name.charAt(0)}
				</div>

				<!-- Event card — compact for tool calls -->
				<div
					class="mb-1.5 flex-1 rounded-md border"
					class:p-1.5={!isTool}
					class:px-3={!isTool}
					class:py-1={isTool}
					class:px-2.5={isTool}
					style="
						background: {style.color}08;
						border-color: {isFail ? style.color + '40' : 'var(--color-border)'};
						border-left: {isFail ? '3px solid ' + style.color : isSuccess ? '3px solid ' + style.color : isTool ? '2px solid ' + style.color + '40' : '1px solid var(--color-border)'};
						{isTool ? 'opacity: 0.85;' : ''}
					"
				>
					<div class="flex items-center justify-between" class:mb-0.5={!isTool}>
						<div class="flex items-center gap-1.5">
							{#if !isTool}
								<span class="text-[10px]" style="color: {agent.color};">{agent.name}</span>
							{/if}
							<span
								class="rounded-sm px-1.5 py-px font-semibold"
								class:text-[8px]={!isTool}
								class:text-[7px]={isTool}
								style="color: {style.color}; background: {style.color}18; letter-spacing: 0.5px;"
							>{style.label}</span>
						</div>
						<span class="text-[9px]" style="color: var(--color-text-faint);">{event.time}</span>
					</div>
					<div
						class="leading-relaxed"
						class:text-[11px]={!isTool}
						class:text-[10px]={isTool}
						style="color: {isFail ? '#f8a0a0' : isSuccess ? '#6ee7b7' : isTool ? 'var(--color-text-dim)' : 'var(--color-text-muted)'};"
					>
						{event.action}
					</div>

					<!-- Issue #107: Sandbox output block -->
					{#if hasSandboxOutput(event)}
						{@const visible = isOutputVisible(event, i)}
						{@const rawOutput = redactSecrets(event.output_summary)}
						{@const totalLines = rawOutput ? lineCount(rawOutput) : 0}
						{@const showFull = fullOutputs.has(i)}
						{@const needsTruncation = totalLines > MAX_COLLAPSED_LINES && !showFull}
						{@const displayOutput = needsTruncation ? truncateOutput(rawOutput, MAX_COLLAPSED_LINES) : rawOutput}

						<!-- Errors block (above stdout, red-tinted) -->
						{#if event.errors && event.errors.length > 0}
							<div class="mt-2 rounded border-l-2 px-2.5 py-1.5" style="background: var(--color-accent-red, #ef4444)10; border-color: var(--color-accent-red, #ef4444);">
								{#each event.errors as err}
									<div class="text-[10px] leading-relaxed" style="color: var(--color-accent-red, #ef4444); font-family: var(--font-mono);">{redactSecrets(err)}</div>
								{/each}
							</div>
						{/if}

						<!-- Collapsible stdout block -->
						{#if rawOutput}
							<div class="mt-1.5">
								<button
									onclick={() => toggleOutput(i)}
									class="mb-1 flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-[9px]"
									style="color: var(--color-text-dim); font-family: var(--font-mono);"
								>
									<span style="font-size: 8px;">{visible ? '▼' : '▶'}</span>
									{visible ? 'Hide output' : `Show output (${totalLines} lines)`}
								</button>

								{#if visible}
									<div class="relative rounded border" style="background: var(--color-bg-activity, #0a0c10); border-color: var(--color-border);">
										<!-- Copy button -->
										<button
											onclick={() => copyToClipboard(rawOutput)}
											class="absolute right-1.5 top-1.5 cursor-pointer rounded border px-1.5 py-0.5 text-[8px] opacity-60 transition-opacity hover:opacity-100"
											style="background: var(--color-bg-surface); border-color: var(--color-border); color: var(--color-text-dim); font-family: var(--font-mono);"
										>
											Copy
										</button>

										<pre class="overflow-x-auto p-2.5 pr-14 text-[10px] leading-relaxed" style="color: var(--color-text-muted); font-family: var(--font-mono); margin: 0; white-space: pre-wrap; word-break: break-word;">{displayOutput}</pre>

										{#if needsTruncation}
											<button
												onclick={() => toggleFullOutput(i)}
												class="w-full cursor-pointer border-t border-none py-1.5 text-center text-[9px]"
												style="background: var(--color-bg-activity, #0a0c10); border-color: var(--color-border); color: var(--color-accent-cyan, #22d3ee); font-family: var(--font-mono);"
											>
												Show all ({totalLines} lines)
											</button>
										{/if}
									</div>
								{/if}
							</div>
						{/if}
					{/if}
				</div>
			</div>
		{/each}

		<!-- Session debrief (if task is complete) -->
		{#if task.completed_at}
			<div class="mt-5 overflow-hidden rounded-lg border" style="background: var(--color-bg-activity); border-color: var(--color-border);">
				<div class="flex items-center justify-between border-b px-3.5 py-2.5" style="background: var(--color-bg-surface); border-color: var(--color-border);">
					<div class="flex items-center gap-2">
						<span class="text-[10px]" style="color: var(--color-text-dim); letter-spacing: 1px;">SESSION DEBRIEF</span>
						<span class="rounded-sm px-1.5 py-px text-[8px]" style="color: var(--color-accent-green); background: var(--color-accent-green)12;">COMPLETE</span>
					</div>
				</div>
				<div class="grid grid-cols-4 gap-2.5 p-3.5">
					{#each [
						{ label: 'Tokens Used', value: (task.budget.tokens_used).toLocaleString(), sub: `of ${task.budget.token_budget.toLocaleString()} budget`, icon: 'Tk' },
						{ label: 'Cost', value: `$${task.budget.cost_used.toFixed(2)}`, sub: `of $${task.budget.cost_budget} budget`, icon: '$' },
						{ label: 'Retries', value: String(task.budget.retries_used), sub: `of ${task.budget.max_retries} max`, icon: 'Rt' },
						{ label: 'Events', value: String(task.timeline.length), sub: `${task.timeline.filter(e => e.type === 'tool_call').length} tool calls`, icon: 'Ev' }
					] as metric}
						<div class="rounded-md border p-2.5" style="background: var(--color-bg-primary); border-color: var(--color-border);">
							<div class="mb-1 flex items-center justify-between">
								<span class="text-[8px]" style="color: var(--color-text-dim); letter-spacing: 0.5px;">{metric.label}</span>
								<span class="text-[8px] font-semibold" style="color: var(--color-text-faint);">{metric.icon}</span>
							</div>
							<div class="text-[18px] font-semibold" style="color: var(--color-text-bright);">{metric.value}</div>
							<div class="mt-0.5 text-[8px]" style="color: var(--color-text-dim);">{metric.sub}</div>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{:else}
		<div class="flex h-full flex-col items-center justify-center gap-3">
			<div class="text-[13px]" style="color: var(--color-text-muted);">No tasks yet</div>
			<div class="text-[11px]" style="color: var(--color-text-dim);">Use the chat panel to submit a task to the agent team.</div>
		</div>
	{/if}
</div>
