<!--
	ChatView — task submission and orchestrator message history.

	Provides a chat-style interface for submitting tasks to the
	agent team and viewing system/event messages as they stream in.
	Includes workspace selector for targeting agent work.

	Relates to #17 — Dashboard v1
	Issue #106: Workspace selector + workspace-aware create()
-->
<script lang="ts">
	import { tasksStore } from '$lib/stores/tasks.svelte.js';
	import { workspacesStore } from '$lib/stores/workspaces.svelte.js';
	import WorkspaceSelector from '$lib/components/WorkspaceSelector.svelte';
	import { tick } from 'svelte';

	interface ChatMessage {
		role: 'user' | 'system' | 'event';
		text: string;
		time: string;
	}

	let messages = $state<ChatMessage[]>([
		{
			role: 'system',
			text: 'Agent team ready. Describe a task to begin.',
			time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
		}
	]);
	let input = $state('');
	let sending = $state(false);
	let scrollContainer: HTMLDivElement | undefined = $state();

	async function scrollToBottom() {
		await tick();
		if (scrollContainer) {
			scrollContainer.scrollTop = scrollContainer.scrollHeight;
		}
	}

	$effect(() => {
		// Scroll when messages change
		void messages.length;
		scrollToBottom();
	});

	async function send() {
		const text = input.trim();
		if (!text || sending) return;

		// Check workspace is selected and ready
		if (!workspacesStore.canCreateTask) {
			const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			if (!workspacesStore.selected) {
				messages = [...messages, { role: 'event', text: 'No workspace selected. Choose a workspace above.', time: now }];
			} else if (workspacesStore.isSelectedProtected && !workspacesStore.pinVerified) {
				messages = [...messages, { role: 'event', text: 'Protected workspace — enter PIN above before submitting.', time: now }];
			}
			return;
		}

		const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
		messages = [...messages, { role: 'user', text, time: now }];
		input = '';
		sending = true;

		const taskId = await tasksStore.create(text, workspacesStore.selected);

		if (taskId) {
			messages = [
				...messages,
				{
					role: 'system',
					text: `Task accepted (${taskId}). Routing to Architect for blueprint generation...`,
					time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
				}
			];
		} else {
			messages = [
				...messages,
				{
					role: 'event',
					text: tasksStore.error ?? 'Failed to create task. Is the backend running?',
					time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
				}
			];
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
	<!-- Workspace selector -->
	<WorkspaceSelector />

	<!-- Messages -->
	<div bind:this={scrollContainer} class="flex-1 overflow-y-auto p-4 pl-6">
		{#each messages as msg (msg)}
			<div
				class="mb-3 max-w-[560px]"
				style="{msg.role === 'user' ? 'margin-left: auto;' : ''}"
			>
				<div
					class="rounded-lg border p-2.5 px-3.5 text-[12px] leading-relaxed"
					style="
						background: {msg.role === 'user'
							? 'var(--color-accent-cyan)10'
							: msg.role === 'event'
								? 'var(--color-bg-surface)80'
								: 'var(--color-bg-sidebar)'};
						border-color: {msg.role === 'user'
							? 'var(--color-accent-cyan)20'
							: 'var(--color-border)'};
						color: {msg.role === 'event'
							? 'var(--color-text-muted)'
							: 'var(--color-text-bright)'};
						{msg.role === 'event' ? 'font-style: italic;' : ''}
					"
				>
					{#if msg.role !== 'event'}
						<div
							class="mb-1 text-[9px] uppercase"
							style="color: {msg.role === 'user'
								? 'var(--color-accent-cyan)'
								: 'var(--color-text-dim)'}; letter-spacing: 0.5px;"
						>
							{msg.role === 'user' ? 'You' : 'Orchestrator'} | {msg.time}
						</div>
					{/if}
					{msg.text}
				</div>
			</div>
		{/each}
	</div>

	<!-- Input -->
	<div class="flex gap-2 border-t p-2 px-6" style="border-color: var(--color-border);">
		<input
			bind:value={input}
			onkeydown={handleKeydown}
			placeholder="Describe a task for the agents..."
			disabled={sending}
			class="flex-1 rounded-md border px-3 py-2.5 text-[12px] outline-none"
			style="
				background: var(--color-bg-input);
				border-color: var(--color-border);
				color: var(--color-text-bright);
				font-family: var(--font-mono);
			"
		/>
		<button
			onclick={send}
			disabled={sending || !input.trim()}
			class="cursor-pointer rounded-md border px-4 py-2 text-[11px] transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
			style="
				background: var(--color-accent-cyan)15;
				border-color: var(--color-accent-cyan)25;
				color: var(--color-accent-cyan);
				font-family: var(--font-mono);
			"
		>
			{sending ? 'SENDING...' : 'SEND'}
		</button>
	</div>
</div>
