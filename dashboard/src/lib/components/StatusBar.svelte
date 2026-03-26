<script lang="ts">
	const agents = [
		{ name: 'Architect', status: 'idle' },
		{ name: 'Lead Dev', status: 'coding' },
		{ name: 'QA Agent', status: 'waiting' }
	];

	const statusColors: Record<string, string> = {
		idle: 'var(--color-text-dim)',
		coding: 'var(--color-accent-purple)',
		waiting: 'var(--color-accent-yellow)',
		testing: 'var(--color-accent-green)',
		error: 'var(--color-accent-red)'
	};

	const aliveStatuses = ['coding', 'testing'];
</script>

<div
	class="flex h-6 shrink-0 items-center justify-between px-3"
	style="background: var(--color-status-bar);"
>
	<!-- Left side -->
	<div class="flex items-center gap-3.5 text-[10px] text-white" style="font-family: var(--font-mono);">
		<span>LangGraph v0.4</span>
		<span class="opacity-70">|</span>
		{#each agents as agent (agent.name)}
			<span class="flex items-center gap-1">
				<span
					class="inline-block h-[7px] w-[7px] rounded-full"
					style="
						background: {statusColors[agent.status] || 'var(--color-text-dim)'};
						animation: {aliveStatuses.includes(agent.status) ? 'pulse 1.5s ease-in-out infinite' : 'none'};
						box-shadow: {aliveStatuses.includes(agent.status) ? '0 0 6px ' + statusColors[agent.status] : 'none'};
					"
				></span>
				<span class="opacity-90">{agent.name}</span>
			</span>
		{/each}
	</div>

	<!-- Right side -->
	<div
		class="flex items-center gap-2.5 text-[10px]"
		style="color: rgba(255,255,255,0.8); font-family: var(--font-mono);"
	>
		<span>Sandbox: locked-down</span>
		<span class="opacity-50">|</span>
		<span class="flex items-center gap-1.5">
			<span>Tokens:</span>
			<span
				class="inline-block h-1 w-[50px] overflow-hidden rounded-sm"
				style="background: rgba(255,255,255,0.2);"
			>
				<span
					class="block h-full rounded-sm"
					style="width: 76%; background: var(--color-accent-amber);"
				></span>
			</span>
			<span style="color: var(--color-accent-amber);">76%</span>
		</span>
		<span class="opacity-50">|</span>
		<span>$0.47</span>
		<span class="opacity-50">|</span>
		<span>Retries: 1/3</span>
	</div>
</div>
