<!--
	ChatView — conversational task planning with Planner agent.

	Phase B flow:
	1. User selects workspace via WorkspaceSelector
	2. User types objective → Planner session starts automatically
	3. Planner validates checklist, prompts for missing info
	4. When ready, user confirms → submit to Architect
	5. Task created, timeline begins

	The interface feels conversational, but builds a structured
	TaskSpec behind the scenes via the Planner agent.

	Chat rework:
	- Readiness checklist pinned as collapsible header (not inline in chat)
	- Warnings removed from chat stream (header communicates state)
	- SUBMIT button relocated to readiness header
	- Task ID in submission message links to Agent Dashboard
	- Dim dot → pulse → check state transitions (no red X)

	Issue #106 Phase B: ChatView planner UI
-->
<script lang="ts">
	import { plannerStore, type PlannerChatMessage } from '$lib/stores/planner.svelte.js';
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { getDashboardContext } from '$lib/stores/dashboard.svelte.js';
	import WorkspaceSelector from '$lib/components/WorkspaceSelector.svelte';
	import { tick } from 'svelte';

	const dashboardCtx = getDashboardContext();

	let input = $state('');
	let scrollContainer: HTMLDivElement | undefined = $state();
	let headerExpanded = $state(false);
	let specPreviewExpanded = $state(false);

	// -- Scroll to bottom when messages change --

	async function scrollToBottom() {
		await tick();
		if (scrollContainer) {
			scrollContainer.scrollTop = scrollContainer.scrollHeight;
		}
	}

	$effect(() => {
		void plannerStore.messages.length;
		scrollToBottom();
	});

	// -- Workspace readiness --

	const workspaceReady = $derived(
		!workspacesStore.loading &&
		!workspacesStore.error &&
		workspacesStore.canCreateTask
	);

	// -- Computed display state --

	const submitEnabled = $derived(plannerStore.canSubmit);
	const inputDisabled = $derived(
		plannerStore.phase === 'sending' ||
		plannerStore.phase === 'submitting' ||
		plannerStore.phase === 'submitted' ||
		plannerStore.phase === 'starting'
	);

	// -- Checklist item state logic --
	// Determines icon/color for each checklist field:
	// - Before Planner responds: dim dot (not yet filled)
	// - While Planner is thinking: pulsing circle
	// - Satisfied: green check
	// - Required + unsatisfied AFTER Planner responded: amber warning

	type ItemState = 'empty' | 'inferring' | 'satisfied' | 'warning';

	function getItemState(item: { satisfied: boolean; priority: string }): ItemState {
		if (item.satisfied) return 'satisfied';
		// Planner is actively thinking — show spinner
		if (plannerStore.phase === 'sending' || plannerStore.phase === 'starting') return 'inferring';
		// Planner has responded but field still unsatisfied
		if (plannerStore.checklist !== null && item.priority === 'required') return 'warning';
		// Not yet filled, no response yet
		return 'empty';
	}

	function itemIcon(state: ItemState): string {
		switch (state) {
			case 'satisfied': return '\u2713';
			case 'warning': return '!';
			case 'inferring': return '\u25CE';
			default: return '\u00b7';
		}
	}

	function itemColor(state: ItemState): string {
		switch (state) {
			case 'satisfied': return 'var(--color-accent-green)';
			case 'warning': return 'var(--color-accent-amber)';
			case 'inferring': return 'var(--color-accent-amber)';
			default: return 'var(--color-text-dim)';
		}
	}

	// -- Phase indicator (slim bar below header) --

	const phaseLabel = $derived.by(() => {
		switch (plannerStore.phase) {
			case 'idle': return '';
			case 'starting': return 'Starting session...';
			case 'chatting': return plannerStore.ready ? '' : 'Planning...';
			case 'sending': return 'Planner thinking...';
			case 'submitting': return 'Submitting to Architect...';
			case 'submitted': return 'Task submitted';
			default: return '';
		}
	});

	const phaseColor = $derived.by(() => {
		switch (plannerStore.phase) {
			case 'starting':
			case 'sending':
			case 'submitting': return 'var(--color-accent-amber)';
			case 'submitted': return 'var(--color-accent-green)';
			default: return plannerStore.ready ? 'var(--color-accent-green)' : 'var(--color-accent-cyan)';
		}
	});

	// -- Placeholder text --

	const placeholder = $derived.by(() => {
		if (!workspaceReady) return 'Select a workspace above to begin...';
		if (plannerStore.phase === 'idle') return "Describe what you'd like to build...";
		if (plannerStore.phase === 'submitted') return 'Start a new task...';
		if (plannerStore.phase === 'sending') return 'Waiting for Planner...';
		return 'Reply to the Planner...';
	});

	// -- Readiness header status --

	const headerStatus = $derived.by(() => {
		if (!plannerStore.checklist) return 'CHECKLIST';
		if (plannerStore.checklist.required_satisfied) return 'READY';
		return 'INCOMPLETE';
	});

	const headerStatusColor = $derived.by(() => {
		if (!plannerStore.checklist) return 'var(--color-text-dim)';
		if (plannerStore.checklist.required_satisfied) return 'var(--color-accent-green)';
		return 'var(--color-accent-amber)';
	});

	// -- Default checklist items (shown before session starts) --
	// Wrapped in $derived() so workspace field reactively updates
	// when workspaceReady or workspacesStore.selected changes.

	const defaultChecklistItems = $derived([
		{ field: 'workspace', priority: 'required', satisfied: workspaceReady, auto_inferred: false, value: workspaceReady ? workspacesStore.selected : null },
		{ field: 'objective', priority: 'required', satisfied: false, auto_inferred: false, value: null },
		{ field: 'languages', priority: 'required', satisfied: false, auto_inferred: false, value: null },
		{ field: 'frameworks', priority: 'recommended', satisfied: false, auto_inferred: false, value: null },
		{ field: 'output_type', priority: 'recommended', satisfied: false, auto_inferred: false, value: null },
		{ field: 'acceptance_criteria', priority: 'recommended', satisfied: false, auto_inferred: false, value: null },
		{ field: 'constraints', priority: 'optional', satisfied: false, auto_inferred: false, value: null },
		{ field: 'related_files', priority: 'optional', satisfied: false, auto_inferred: false, value: null },
	]);

	const checklistItems = $derived(
		plannerStore.checklist?.items ?? defaultChecklistItems
	);

	// -- Actions --

	async function handleSend() {
		const text = input.trim();
		if (!text) return;

		if (!workspaceReady) return;

		if (plannerStore.phase === 'idle' || plannerStore.phase === 'submitted') {
			const options = workspacesStore.isSelectedProtected && workspacesStore.verifiedPin
				? { pin: workspacesStore.verifiedPin }
				: undefined;

			if (plannerStore.phase === 'submitted') {
				plannerStore.reset();
			}

			const started = await plannerStore.startSession(
				workspacesStore.selected,
				options
			);
			if (!started) return;
			input = '';
			await plannerStore.sendMessage(text);
		} else if (plannerStore.canSend) {
			input = '';
			await plannerStore.sendMessage(text);
		}
	}

	async function handleSubmit() {
		if (!plannerStore.canSubmit) return;
		const taskId = await plannerStore.submit();
		if (taskId) {
			tasksStore.refresh();
		}
	}

	function handleNewTask() {
		plannerStore.reset();
		headerExpanded = false;
		specPreviewExpanded = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
	}

	/** Navigate to task timeline in Agent Dashboard. */
	function navigateToTask(taskId: string) {
		dashboardCtx.handlePanelSwitch('agents');
		dashboardCtx.handleSelect(`task-${taskId}`);
	}

	// -- Message styling helpers --

	function msgBg(msg: PlannerChatMessage): string {
		switch (msg.role) {
			case 'user': return 'var(--color-accent-cyan)10';
			case 'planner': return 'var(--color-accent-purple)08';
			case 'system': return 'var(--color-bg-sidebar)';
			case 'event': return 'var(--color-bg-surface)80';
			default: return 'var(--color-bg-sidebar)';
		}
	}

	function msgBorder(msg: PlannerChatMessage): string {
		switch (msg.role) {
			case 'user': return 'var(--color-accent-cyan)20';
			case 'planner': return 'var(--color-accent-purple)20';
			default: return 'var(--color-border)';
		}
	}

	function msgLabel(msg: PlannerChatMessage): string {
		switch (msg.role) {
			case 'user': return 'You';
			case 'planner': return 'Planner';
			case 'system': return 'System';
			default: return '';
		}
	}

	function msgLabelColor(msg: PlannerChatMessage): string {
		switch (msg.role) {
			case 'user': return 'var(--color-accent-cyan)';
			case 'planner': return 'var(--color-accent-purple)';
			case 'system': return 'var(--color-text-dim)';
			default: return 'var(--color-text-dim)';
		}
	}
</script>

<div class="flex h-full flex-col" style="font-family: var(--font-mono);">
	<!-- Workspace selector -->
	<WorkspaceSelector />

	<!-- ═══ Pinned Readiness Header ═══ -->
	<div
		class="shrink-0 border-b"
		style="
			border-color: {plannerStore.ready ? 'var(--color-accent-green)20' : 'var(--color-border)'};
			background: {plannerStore.ready ? 'var(--color-accent-green)05' : 'var(--color-bg-activity)'};
		"
	>
		<!-- Collapsed row: status chips + expand toggle + submit button -->
		<div class="flex items-center gap-1.5 px-4 py-2">
			<!-- Header label -->
			<span class="mr-1 text-[9px] uppercase" style="color: var(--color-text-dim); letter-spacing: 1px;">
				Task Readiness
			</span>

			<!-- Compact status chips -->
			<div class="flex flex-wrap items-center gap-1">
				{#each checklistItems as item (item.field)}
					{@const state = getItemState(item)}
					<span
						class="flex items-center gap-1 rounded px-1.5 py-px text-[9px]"
						style="
							color: {itemColor(state)};
							background: {itemColor(state)}08;
							opacity: {state === 'empty' ? 0.5 : 1};
						"
					>
						<span
							class="text-[8px]"
							style="{state === 'inferring' ? 'animation: pulse 1.5s ease-in-out infinite;' : ''}"
						>
							{itemIcon(state)}
						</span>
						{item.field}
					</span>
				{/each}
			</div>

			<div class="flex-1"></div>

			<!-- Status badge -->
			<span
				class="rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase"
				style="
					color: {headerStatusColor};
					background: {headerStatusColor}12;
					letter-spacing: 0.3px;
				"
			>
				{headerStatus}
			</span>

			<!-- Submit button (shown when ready, inside header) -->
			{#if submitEnabled}
				<button
					onclick={handleSubmit}
					disabled={plannerStore.phase === 'submitting'}
					class="cursor-pointer rounded-md border px-3 py-1 text-[10px] font-medium transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
					style="
						background: var(--color-accent-green)18;
						border-color: var(--color-accent-green)35;
						color: var(--color-accent-green);
					"
				>
					{plannerStore.phase === 'submitting' ? 'SUBMITTING...' : 'SUBMIT TO ARCHITECT'}
				</button>
			{/if}

			<!-- New Task button (shown when submitted) -->
			{#if plannerStore.phase === 'submitted'}
				<button
					onclick={handleNewTask}
					class="cursor-pointer rounded-md border px-2.5 py-1 text-[9px] uppercase transition-opacity hover:opacity-80"
					style="background: var(--color-accent-cyan)10; border-color: var(--color-accent-cyan)20; color: var(--color-accent-cyan); letter-spacing: 0.5px;"
				>
					New Task
				</button>
			{/if}

			<!-- Expand/collapse toggle -->
			<button
				onclick={() => headerExpanded = !headerExpanded}
				class="cursor-pointer rounded px-1 py-0.5 text-[10px] transition-opacity hover:opacity-80"
				style="color: var(--color-text-dim); background: transparent; border: none;"
			>
				{headerExpanded ? '\u25B2' : '\u25BC'}
			</button>
		</div>

		<!-- Expanded detail panel -->
		{#if headerExpanded}
			<div class="border-t px-4 py-2" style="border-color: var(--color-border);">
				{#each checklistItems as item (item.field)}
					{@const state = getItemState(item)}
					{@const isReq = item.priority === 'required'}
					{@const isRec = item.priority === 'recommended'}
					<div class="flex items-center gap-2 py-1">
						<!-- Status indicator -->
						<span
							class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm text-[8px]"
							style="
								background: {itemColor(state)}15;
								color: {itemColor(state)};
								{state === 'inferring' ? 'animation: pulse 1.5s ease-in-out infinite;' : ''}
							"
						>
							{itemIcon(state)}
						</span>

						<!-- Field name -->
						<span class="text-[10px]" style="color: var(--color-text-muted); min-width: 100px;">
							{item.field}
						</span>

						<!-- Priority badge -->
						<span
							class="rounded-sm px-1 py-px text-[7px] uppercase"
							style="
								color: {isReq ? 'var(--color-accent-amber)' : isRec ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'};
								background: {isReq ? 'var(--color-accent-amber)' : isRec ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'}10;
								letter-spacing: 0.3px;
							"
						>
							{item.priority}
						</span>

						<!-- Value preview -->
						{#if item.satisfied && item.value}
							<span class="flex-1 truncate text-[9px]" style="color: var(--color-text-dim);">
								{#if Array.isArray(item.value)}
									{item.value.join(', ')}
								{:else}
									{typeof item.value === 'string' && item.value.length > 60
										? item.value.slice(0, 60) + '...'
										: item.value}
								{/if}
							</span>
						{/if}

						<!-- Auto-inferred badge -->
						{#if item.auto_inferred}
							<span
								class="shrink-0 rounded-sm px-1 py-px text-[7px] uppercase"
								style="color: var(--color-accent-cyan); background: var(--color-accent-cyan)10; letter-spacing: 0.3px;"
							>
								auto
							</span>
						{/if}
					</div>
				{/each}

				<!-- TaskSpec JSON Preview (shown when ready) -->
				{#if plannerStore.ready && plannerStore.taskSpec}
					<div class="mt-2 border-t pt-2" style="border-color: var(--color-border);">
						<button
							onclick={() => specPreviewExpanded = !specPreviewExpanded}
							class="flex w-full cursor-pointer items-center gap-1.5 text-left"
							style="background: transparent; border: none;"
						>
							<span class="text-[9px] uppercase" style="color: var(--color-text-dim); letter-spacing: 1px;">TaskSpec Preview</span>
							<span class="text-[8px]" style="color: var(--color-text-dim);">{specPreviewExpanded ? '\u25B2' : '\u25BC'}</span>
						</button>
						{#if specPreviewExpanded}
							<div
								class="mt-1.5 overflow-x-auto rounded border p-2.5"
								style="background: var(--color-bg-primary); border-color: var(--color-border);"
							>
								<pre class="m-0 text-[10px] leading-relaxed" style="color: var(--color-text-muted); white-space: pre-wrap;">{JSON.stringify(plannerStore.taskSpec, null, 2)}</pre>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Phase indicator bar (only shown for transient states) -->
	{#if phaseLabel}
		<div
			class="flex items-center gap-2 border-b px-6 py-1.5"
			style="border-color: var(--color-border); background: {phaseColor}05;"
		>
			{#if plannerStore.phase === 'sending' || plannerStore.phase === 'starting' || plannerStore.phase === 'submitting'}
				<span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full" style="background: {phaseColor};"></span>
			{:else}
				<span class="inline-block h-1.5 w-1.5 rounded-full" style="background: {phaseColor};"></span>
			{/if}
			<span class="text-[10px]" style="color: {phaseColor};">{phaseLabel}</span>
		</div>
	{/if}

	<!-- Messages area -->
	<div bind:this={scrollContainer} class="flex-1 overflow-y-auto p-4 pl-6">
		<!-- Empty state -->
		{#if plannerStore.messages.length === 0}
			<div class="flex flex-col items-center justify-center py-16 text-center">
				<div class="mb-3 text-[24px]" style="color: var(--color-text-dim); opacity: 0.3;">
					&#9672;
				</div>
				<div class="mb-1 text-[13px]" style="color: var(--color-text-muted);">
					Plan a task for the agent team
				</div>
				<div class="max-w-[360px] text-[11px] leading-relaxed" style="color: var(--color-text-dim);">
					Describe what you want built. The checklist above shows
					what information helps the agents succeed.
				</div>
			</div>
		{/if}

		<!-- Chat messages -->
		{#each plannerStore.messages as msg, i (i)}
			<div
				class="mb-3 max-w-[600px]"
				style="{msg.role === 'user' ? 'margin-left: auto;' : ''}"
			>
				<div
					class="rounded-lg border p-2.5 px-3.5 text-[12px] leading-relaxed"
					style="
						background: {msgBg(msg)};
						border-color: {msgBorder(msg)};
						color: {msg.role === 'event' ? 'var(--color-text-muted)' : 'var(--color-text-bright)'};
						{msg.role === 'event' ? 'font-style: italic;' : ''}
					"
				>
					{#if msg.role !== 'event'}
						<div
							class="mb-1 text-[9px] uppercase"
							style="color: {msgLabelColor(msg)}; letter-spacing: 0.5px;"
						>
							{msgLabel(msg)} | {msg.time}
						</div>
					{/if}

					<!-- Render messages with task ID link -->
					{#if msg.taskId}
						{msg.text}
						<button
							onclick={() => navigateToTask(msg.taskId!)}
							class="ml-1 cursor-pointer border-none text-[12px] underline underline-offset-2 transition-opacity hover:opacity-80"
							style="background: transparent; color: var(--color-accent-cyan); font-family: var(--font-mono);"
						>
							{msg.taskId} &#8594;
						</button>
					{:else if msg.role === 'planner'}
						<!-- Render planner messages with line breaks preserved -->
						{#each msg.text.split('\n') as line, li (li)}
							{#if line.trim() === ''}
								<br />
							{:else}
								<p class="mb-1">{line}</p>
							{/if}
						{/each}
					{:else}
						{msg.text}
					{/if}
				</div>
			</div>
		{/each}
	</div>

	<!-- Input area -->
	<div class="flex gap-2 border-t p-2 px-6" style="border-color: var(--color-border);">
		<input
			bind:value={input}
			onkeydown={handleKeydown}
			{placeholder}
			disabled={inputDisabled || !workspaceReady}
			class="flex-1 rounded-md border px-3 py-2.5 text-[12px] outline-none disabled:opacity-50"
			style="
				background: var(--color-bg-input);
				border-color: var(--color-border);
				color: var(--color-text-bright);
				font-family: var(--font-mono);
			"
		/>
		<button
			onclick={handleSend}
			disabled={inputDisabled || !workspaceReady || !input.trim()}
			class="cursor-pointer rounded-md border px-4 py-2 text-[11px] transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
			style="
				background: var(--color-accent-cyan)15;
				border-color: var(--color-accent-cyan)25;
				color: var(--color-accent-cyan);
				font-family: var(--font-mono);
			"
		>
			{#if plannerStore.phase === 'sending'}
				THINKING...
			{:else if plannerStore.phase === 'starting'}
				STARTING...
			{:else}
				SEND
			{/if}
		</button>
	</div>
</div>
