<!--
	BottomPanel — resizable terminal panel with SSE log streaming.

	Listens for SSE `log_line` events via window CustomEvent.
	Command input triggers task creation via tasksStore.

	Issue #38: Data Integration — PR4
	Issue #51: Removed mock mode — SSE-only log streaming
	Issue #92: Fixed log_line field mismatch (message/level vs text/type)
	Issue #106: Pass workspace to tasksStore.create()
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';

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

	interface LogLine {
		type: string;
		text: string;
	}

	let lines = $state<LogLine[]>([]);
	let input = $state('');
	let scrollTarget: HTMLDivElement | undefined = $state();

	const typeColors: Record<string, string> = {
		cmd: 'var(--color-text-bright)',
		info: 'var(--color-text-dim)',
		warn: 'var(--color-accent-amber)',
		success: 'var(--color-accent-green)',
		error: 'var(--color-accent-red)'
	};

	/**
	 * Map runner log levels to terminal color types.
	 * The runner emits `level: "info"` but the terminal uses `type` for color lookup.
	 * Also map warning/error levels appropriately.
	 */
	function resolveLogType(detail: Record<string, unknown>): string {
		if (typeof detail.type === 'string' && detail.type in typeColors) return detail.type;
		if (typeof detail.level === 'string') {
			const level = detail.level as string;
			if (level === 'warning' || level === 'warn') return 'warn';
			if (level === 'error') return 'error';
			if (level === 'success') return 'success';
			return 'info';
		}
		return 'info';
	}

	/**
	 * Extract display text from SSE log_line payload.
	 * The runner sends { message, level } but earlier code expected { text, type }.
	 * Support both formats for backwards compatibility.
	 */
	function resolveLogText(detail: Record<string, unknown>): string | null {
		if (typeof detail.text === 'string') return detail.text;
		if (typeof detail.message === 'string') return detail.message;
		if (typeof detail.detail === 'string') return detail.detail;
		return null;
	}

	onMount(() => {
		function handleLogLine(e: Event) {
			const detail = (e as CustomEvent).detail;
			if (!detail) return;
			const text = resolveLogText(detail);
			if (text) {
				lines = [...lines, { type: resolveLogType(detail), text }];
			}
		}

		window.addEventListener('sse:log_line', handleLogLine);
		return () => window.removeEventListener('sse:log_line', handleLogLine);
	});

	$effect(() => {
		if (lines.length > 0) {
			scrollTarget?.scrollIntoView({ behavior: 'smooth' });
		}
	});

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

	async function handleCmd() {
		const text = input.trim();
		if (!text) return;

		lines = [...lines, { type: 'cmd', text: `$ ${text}` }];
		input = '';

		if (text.startsWith('run ') || text.startsWith('task ')) {
			const desc = text.replace(/^(run|task)\s+/, '');
			if (!workspacesStore.canCreateTask) {
				lines = [...lines, { type: 'error', text: '[orchestrator] No workspace selected or workspace requires PIN. Use the Chat panel to select a workspace.' }];
				return;
			}
			lines = [...lines, { type: 'info', text: `[orchestrator] Processing: "${desc}"...` }];
			const options = workspacesStore.isSelectedProtected && workspacesStore.verifiedPin
				? { pin: workspacesStore.verifiedPin }
				: undefined;
			const taskId = await tasksStore.create(desc, workspacesStore.selected, options);
			if (taskId) {
				lines = [...lines, { type: 'success', text: `[orchestrator] Task ${taskId} created` }];
			} else {
				lines = [...lines, { type: 'error', text: `[orchestrator] Failed: ${tasksStore.error ?? 'Unknown error'}` }];
			}
		} else {
			lines = [...lines, { type: 'info', text: `[shell] Command not recognized. Use "run <description>" to create a task.` }];
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			handleCmd();
		}
	}
</script>

<svelte:window onmousemove={handleMouseMove} onmouseup={handleMouseUp} />

<div
	class="flex shrink-0 flex-col border-t"
	style="height: {height}px; background: var(--color-bg-activity); border-color: var(--color-border);"
>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="flex h-1 shrink-0 cursor-ns-resize items-center justify-center"
		style="background: {isDragging ? 'var(--color-accent-cyan)' : 'transparent'};"
		onmousedown={handleMouseDown}
	>
		<div class="h-0.5 w-10 rounded-sm" style="background: var(--color-bg-surface);"></div>
	</div>

	<div class="flex h-7 shrink-0 items-center gap-4 border-b px-3" style="border-color: var(--color-border);">
		{#each tabs as tab (tab)}
			<button
				onclick={() => (activeTab = tab)}
				class="pb-1.5 pt-1.5 text-[10px]"
				style="font-family: var(--font-mono); color: {activeTab === tab ? 'var(--color-text-bright)' : 'var(--color-text-dim)'}; border-bottom: {activeTab === tab ? '1px solid var(--color-accent-cyan)' : '1px solid transparent'};"
			>
				{tab}
			</button>
		{/each}
	</div>

	<div class="flex-1 overflow-y-auto px-3.5 py-1">
		{#if lines.length === 0}
			<div class="py-3 text-center text-[10px]" style="color: var(--color-text-faint); font-family: var(--font-mono);">
				Waiting for log events...
			</div>
		{:else}
			{#each lines as line, i (i)}
				<div class="text-[11px] leading-7" style="color: {typeColors[line.type] || 'var(--color-text-dim)'}; font-family: var(--font-mono);">
					{line.text}
				</div>
			{/each}
		{/if}
		<div bind:this={scrollTarget}></div>
	</div>

	<div class="flex shrink-0 items-center gap-1.5 px-3.5 pb-1.5 pt-1">
		<span class="text-[11px]" style="color: var(--color-accent-cyan); font-family: var(--font-mono);">$</span>
		<input
			bind:value={input}
			onkeydown={handleKeydown}
			type="text"
			placeholder="run command..."
			class="flex-1 border-none bg-transparent text-[11px] outline-none"
			style="color: var(--color-text-bright); font-family: var(--font-mono);"
		/>
	</div>
</div>
