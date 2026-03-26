<script lang="ts">
	interface Props {
		height: number;
		onResize: (height: number) => void;
	}

	let { height, onResize }: Props = $props();

	let isDragging = $state(false);
	let startY = $state(0);
	let startH = $state(0);

	const tabs = ['TERMINAL', 'PROBLEMS', 'OUTPUT'];
	let activeTab = $state('TERMINAL');

	const terminalLines = [
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

	const typeColors: Record<string, string> = {
		cmd: 'var(--color-text-bright)',
		info: 'var(--color-text-dim)',
		warn: 'var(--color-accent-amber)',
		success: 'var(--color-accent-green)',
		error: 'var(--color-accent-red)'
	};

	function handleMouseDown(e: MouseEvent) {
		isDragging = true;
		startY = e.clientY;
		startH = height;
	}

	function handleMouseMove(e: MouseEvent) {
		if (!isDragging) return;
		const newHeight = Math.max(60, Math.min(400, startH + (startY - e.clientY)));
		onResize(newHeight);
	}

	function handleMouseUp() {
		isDragging = false;
	}
</script>

<svelte:window onmousemove={handleMouseMove} onmouseup={handleMouseUp} />

<div
	class="flex shrink-0 flex-col border-t"
	style="height: {height}px; background: var(--color-bg-activity); border-color: var(--color-border);"
>
	<!-- Drag handle -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="flex h-1 shrink-0 cursor-ns-resize items-center justify-center"
		style="background: {isDragging ? 'var(--color-accent-cyan)' : 'transparent'};"
		onmousedown={handleMouseDown}
	>
		<div
			class="h-0.5 w-10 rounded-sm"
			style="background: var(--color-bg-surface);"
		></div>
	</div>

	<!-- Tab bar -->
	<div
		class="flex h-7 shrink-0 items-center gap-4 border-b px-3"
		style="border-color: var(--color-border);"
	>
		{#each tabs as tab (tab)}
			<button
				onclick={() => (activeTab = tab)}
				class="pb-1.5 pt-1.5 text-[10px]"
				style="
					font-family: var(--font-mono);
					color: {activeTab === tab ? 'var(--color-text-bright)' : 'var(--color-text-dim)'};
					border-bottom: {activeTab === tab ? '1px solid var(--color-accent-cyan)' : '1px solid transparent'};
				"
			>
				{tab}
			</button>
		{/each}
	</div>

	<!-- Terminal content -->
	<div class="flex-1 overflow-y-auto px-3.5 py-1">
		{#each terminalLines as line (line.text)}
			<div
				class="text-[11px] leading-7"
				style="color: {typeColors[line.type] || 'var(--color-text-dim)'}; font-family: var(--font-mono);"
			>
				{line.text}
			</div>
		{/each}
	</div>

	<!-- Input -->
	<div
		class="flex shrink-0 items-center gap-1.5 px-3.5 pb-1.5 pt-1"
	>
		<span
			class="text-[11px]"
			style="color: var(--color-accent-cyan); font-family: var(--font-mono);"
		>$</span>
		<input
			type="text"
			placeholder="run command..."
			class="flex-1 border-none bg-transparent text-[11px] outline-none"
			style="color: var(--color-text-bright); font-family: var(--font-mono);"
		/>
	</div>
</div>
