<!--
	ChatView — task chat panel.

	Input field POSTs to tasksStore.create() to trigger new work.
	Displays a message history with user inputs, system confirmations,
	and SSE-driven progress events.

	Issue #38: Data Integration — PR4
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';

	interface ChatMessage {
		role: 'user' | 'system' | 'event';
		text: string;
		time: string;
	}

	let messages = $state<ChatMessage[]>([]);
	let input = $state('');
	let sending = $state(false);
	let scrollTarget: HTMLDivElement | undefined = $state();

	function timeNow(): string {
		return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
	}

	$effect(() => {
		if (messages.length > 0) {
			scrollTarget?.scrollIntoView({ behavior: 'smooth' });
		}
	});

	$effect(() => {
		function handleTaskProgress(e: Event) {
			const detail = (e as CustomEvent).detail;
			if (detail?.detail) {
				messages = [...messages, { role: 'event', text: detail.detail, time: timeNow() }];
			}
		}

		function handleTaskComplete(e: Event) {
			const detail = (e as CustomEvent).detail;
			if (detail?.detail) {
				messages = [...messages, { role: 'event', text: detail.detail, time: timeNow() }];
			}
		}

		window.addEventListener('sse:task_progress', handleTaskProgress);
		window.addEventListener('sse:task_complete', handleTaskComplete);

		return () => {
			window.removeEventListener('sse:task_progress', handleTaskProgress);
			window.removeEventListener('sse:task_complete', handleTaskComplete);
		};
	});

	async function send() {
		const text = input.trim();
		if (!text || sending) return;

		messages = [...messages, { role: 'user', text, time: timeNow() }];
		input = '';
		sending = true;

		const taskId = await tasksStore.create(text);
		if (taskId) {
			messages = [...messages, {
				role: 'system',
				text: `Task accepted (${taskId}). Routing to Architect...`,
				time: timeNow()
			}];
		} else {
			messages = [...messages, {
				role: 'system',
				text: `Failed to create task: ${tasksStore.error ?? 'Unknown error'}`,
				time: timeNow()
			}];
		}
		sending = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}
</script>

<div class="flex h-full flex-col" style="font-family: var(--font-mono);">
	<div class="flex-1 overflow-y-auto p-4 pl-6">
		{#if messages.length === 0}
			<div class="flex h-full flex-col items-center justify-center gap-3">
				<div class="text-[13px]" style="color: var(--color-text-muted);">Task Chat</div>
				<div class="max-w-md text-center text-[11px] leading-relaxed" style="color: var(--color-text-dim);">
					Describe a task for the agent team. The Architect will create a blueprint,
					the Lead Dev will build it, and QA will verify.
				</div>
			</div>
		{:else}
			{#each messages as msg, i (i)}
				<div
					class="mb-3"
					style="max-width: 560px; {msg.role === 'user' ? 'margin-left: auto;' : ''}"
				>
					<div
						class="rounded-lg border p-2.5 px-3.5 text-[12px] leading-relaxed"
						style="
							background: {msg.role === 'user' ? 'rgba(34, 211, 238, 0.06)' : msg.role === 'event' ? 'var(--color-bg-surface)80' : 'var(--color-bg-sidebar)'};
							border-color: {msg.role === 'user' ? 'rgba(34, 211, 238, 0.15)' : 'var(--color-border)'};
							color: var(--color-text-muted);
							{msg.role === 'event' ? 'font-style: italic;' : ''}
						"
					>
						{#if msg.role !== 'event'}
							<div
								class="mb-1 text-[9px] uppercase"
								style="color: {msg.role === 'user' ? 'var(--color-accent-cyan)' : 'var(--color-text-dim)'}; letter-spacing: 0.5px;"
							>
								{msg.role === 'user' ? 'You' : 'Orchestrator'} | {msg.time}
							</div>
						{/if}
						{msg.text}
					</div>
				</div>
			{/each}
			<div bind:this={scrollTarget}></div>
		{/if}
	</div>

	<div class="flex shrink-0 gap-2 border-t px-6 pb-3.5 pt-2" style="border-color: var(--color-border);">
		<input
			bind:value={input}
			onkeydown={handleKeydown}
			placeholder="Describe a task for the agents..."
			disabled={sending}
			class="flex-1 rounded-md border bg-transparent px-3 py-2.5 text-[12px] outline-none disabled:opacity-50"
			style="background: var(--color-bg-activity); border-color: var(--color-border); color: var(--color-text-bright); font-family: var(--font-mono);"
		/>
		<button
			onclick={send}
			disabled={sending || !input.trim()}
			class="shrink-0 cursor-pointer rounded-md border px-4 py-2.5 text-[11px] transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
			style="background: rgba(34, 211, 238, 0.1); border-color: rgba(34, 211, 238, 0.2); color: var(--color-accent-cyan); font-family: var(--font-mono);"
		>
			{sending ? 'SENDING...' : 'SEND'}
		</button>
	</div>
</div>
