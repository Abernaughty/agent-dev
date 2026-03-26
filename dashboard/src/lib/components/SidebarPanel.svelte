<script lang="ts">
	type PanelId = 'agents' | 'memory' | 'prs' | 'chat';

	interface Props {
		activePanel: PanelId | null;
	}

	let { activePanel }: Props = $props();

	const titles: Record<PanelId, string> = {
		agents: 'Agent Dashboard',
		memory: 'Memory Approvals',
		prs: 'Pull Requests',
		chat: 'Task Chat'
	};

	const stubs: Record<PanelId, { label: string; sublabel: string }[]> = {
		agents: [
			{ label: 'Task Timeline', sublabel: 'All agents' },
			{ label: 'Architect', sublabel: 'Gemini Flash' },
			{ label: 'Lead Dev', sublabel: 'Claude Sonnet' },
			{ label: 'QA Agent', sublabel: 'Claude Sonnet' }
		],
		memory: [
			{ label: 'Overview', sublabel: '0 pending' },
			{ label: 'L0-Core entries', sublabel: 'Human-managed' },
			{ label: 'L0-Discovered', sublabel: 'Agent-written' }
		],
		prs: [
			{ label: 'Overview', sublabel: '0 open' },
			{ label: 'Recent PRs', sublabel: 'GitHub REST' }
		],
		chat: [{ label: 'Full Conversation', sublabel: 'Current task' }]
	};
</script>

{#if activePanel}
	<div
		class="flex w-[260px] shrink-0 flex-col overflow-hidden border-r"
		style="background: var(--color-bg-sidebar); border-color: var(--color-border);"
	>
		<!-- Header -->
		<div class="border-b px-3 py-2" style="border-color: var(--color-border);">
			<span
				class="text-[10px] uppercase tracking-widest"
				style="color: var(--color-text-muted); font-family: var(--font-mono);"
			>
				{titles[activePanel]}
			</span>
		</div>

		<!-- Entries -->
		<div class="flex-1 overflow-y-auto py-1">
			{#each stubs[activePanel] as entry, i (i)}
				<button
					class="flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
					style="
						border-color: {i === 0 ? 'var(--color-accent-cyan)' : 'transparent'};
						background: {i === 0 ? 'var(--color-bg-surface)' : 'transparent'};
					"
				>
					<div class="min-w-0 flex-1">
						<div
							class="truncate text-[11px]"
							style="
								color: {i === 0 ? 'var(--color-text-bright)' : 'var(--color-text-muted)'};
								font-family: var(--font-mono);
							"
						>
							{entry.label}
						</div>
						<div
							class="mt-0.5 truncate text-[9px]"
							style="color: var(--color-text-dim); font-family: var(--font-mono);"
						>
							{entry.sublabel}
						</div>
					</div>
				</button>
			{/each}
		</div>
	</div>
{/if}
