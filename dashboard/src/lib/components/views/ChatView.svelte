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

	Issue #106 Phase B: ChatView planner UI
-->
<script lang="ts">
	import { plannerStore, type PlannerChatMessage } from '$lib/stores/planner.svelte.js';
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import WorkspaceSelector from '$lib/components/WorkspaceSelector.svelte';
	import { tick } from 'svelte';

	let input = $state('');
	let scrollContainer: HTMLDivElement | undefined = $state();

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

	const showChecklist = $derived(
		plannerStore.checklist !== null &&
		plannerStore.hasActiveSession
	);

	const submitEnabled = $derived(plannerStore.canSubmit);
	const inputDisabled = $derived(
		plannerStore.phase === 'sending' ||
		plannerStore.phase === 'submitting' ||
		plannerStore.phase === 'submitted' ||
		plannerStore.phase === 'starting'
	);

	// -- Phase labels for status indicator --

	const phaseLabel = $derived.by(() => {
		switch (plannerStore.phase) {
			case 'idle': return '';
			case 'starting': return 'Starting session...';
			case 'chatting': return plannerStore.ready ? 'Ready to submit' : 'Planning...';
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
		if (plannerStore.phase === 'idle') return 'Describe what you want the agents to do...';
		if (plannerStore.phase === 'submitted') return 'Start a new task...';
		if (plannerStore.phase === 'sending') return 'Waiting for Planner...';
		return 'Reply to the Planner...';
	});

	// -- Actions --

	async function handleSend() {
		const text = input.trim();
		if (!text) return;

		// Workspace validation
		if (!workspaceReady) {
			return;
		}

		if (plannerStore.phase === 'idle' || plannerStore.phase === 'submitted') {
			// Start new session + send first message
			const options = workspacesStore.isSelectedProtected && workspacesStore.verifiedPin
				? { pin: workspacesStore.verifiedPin }
				: undefined;

			// If coming from submitted state, reset first
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
			// Refresh tasks store to pick up the new task
			tasksStore.refresh();
		}
	}

	function handleNewTask() {
		plannerStore.reset();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
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

	<!-- Phase indicator bar -->
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

			{#if plannerStore.phase === 'submitted'}
				<button
					onclick={handleNewTask}
					class="ml-auto cursor-pointer rounded border px-2.5 py-0.5 text-[9px] uppercase transition-opacity hover:opacity-80"
					style="background: var(--color-accent-cyan)10; border-color: var(--color-accent-cyan)20; color: var(--color-accent-cyan); letter-spacing: 0.5px;"
				>
					New Task
				</button>
			{/if}
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
					Describe what you want built. The Planner will validate your request and ensure the
					task spec is complete before routing to the Architect.
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
					<!-- Render planner messages with line breaks preserved -->
					{#if msg.role === 'planner'}
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

		<!-- Inline checklist (shown below messages when active) -->
		{#if showChecklist && plannerStore.checklist}
			{@const cl = plannerStore.checklist}
			<div
				class="mb-3 max-w-[600px] rounded-lg border"
				style="background: var(--color-bg-activity); border-color: var(--color-border);"
			>
				<div
					class="flex items-center justify-between border-b px-3.5 py-2"
					style="border-color: var(--color-border);"
				>
					<span class="text-[9px] uppercase" style="color: var(--color-text-dim); letter-spacing: 1px;">
						Task Readiness
					</span>
					<span
						class="rounded-sm px-1.5 py-px text-[8px] font-semibold uppercase"
						style="
							color: {cl.required_satisfied ? 'var(--color-accent-green)' : 'var(--color-accent-amber)'};
							background: {cl.required_satisfied ? 'var(--color-accent-green)' : 'var(--color-accent-amber)'}12;
							letter-spacing: 0.3px;
						"
					>
						{cl.required_satisfied ? 'READY' : 'INCOMPLETE'}
					</span>
				</div>
				<div class="px-3.5 py-2">
					{#each cl.items as item (item.field)}
						{@const isReq = item.priority === 'required'}
						{@const isRec = item.priority === 'recommended'}
						<div class="flex items-center gap-2 py-1">
							<!-- Status indicator -->
							<span
								class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm text-[8px]"
								style="
									background: {item.satisfied
										? 'var(--color-accent-green)15'
										: isReq
											? 'var(--color-accent-red)15'
											: 'var(--color-text-dim)10'
									};
									color: {item.satisfied
										? 'var(--color-accent-green)'
										: isReq
											? 'var(--color-accent-red)'
											: 'var(--color-text-dim)'
									};
								"
							>
								{item.satisfied ? '\u2713' : isReq ? '!' : '\u00b7'}
							</span>

							<!-- Field name -->
							<span class="text-[10px]" style="color: var(--color-text-muted); min-width: 100px;">
								{item.field}
							</span>

							<!-- Priority badge -->
							<span
								class="rounded-sm px-1 py-px text-[7px] uppercase"
								style="
									color: {isReq ? 'var(--color-accent-red)' : isRec ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'};
									background: {isReq ? 'var(--color-accent-red)' : isRec ? 'var(--color-accent-amber)' : 'var(--color-text-dim)'}10;
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
				</div>
			</div>
		{/if}
	</div>

	<!-- Submit bar (shown when ready) -->
	{#if submitEnabled}
		<div
			class="flex items-center gap-3 border-t px-6 py-2.5"
			style="border-color: var(--color-accent-green)20; background: var(--color-accent-green)05;"
		>
			<span class="flex-1 text-[11px]" style="color: var(--color-accent-green);">
				Task spec complete — ready to submit to Architect
			</span>
			<button
				onclick={handleSubmit}
				disabled={plannerStore.phase === 'submitting'}
				class="cursor-pointer rounded-md border px-5 py-1.5 text-[11px] font-medium transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
				style="
					background: var(--color-accent-green)18;
					border-color: var(--color-accent-green)35;
					color: var(--color-accent-green);
				"
			>
				{plannerStore.phase === 'submitting' ? 'SUBMITTING...' : 'SUBMIT TO ARCHITECT'}
			</button>
		</div>
	{/if}

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
