<script>
	import '../app.css';
	import { page } from '$app/stores';

	let { children } = $props();

	const navLinks = [
		{ href: '/', label: '⏱️ Focus & Windows', title: 'Podgląd okien i czasu w czasie rzeczywistym' }
	];
</script>

<svelte:head>
	<link rel="preconnect" href="https://fonts.googleapis.com" />
	<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
	<link
		rel="stylesheet"
		href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
	/>
</svelte:head>

<header class="top-header">
	<div class="nav-container">
		<div class="brand-group">
			<span class="brand-icon">⏱️</span>
			<div class="brand-titles">
				<span class="brand-name">WindowTracker</span>
				<span class="brand-sub">Hyprland Focus Telemetry</span>
			</div>
		</div>

		<nav class="nav-links">
			{#each navLinks as link}
				{#if link.external}
					<a href={link.href} target="_blank" rel="noopener noreferrer" class="nav-link nav-external" title={link.title}>
						{link.label} <span class="ext-arrow">↗</span>
					</a>
				{:else}
					<a href={link.href} class="nav-link" class:active={$page.url.pathname === link.href} title={link.title}>
						{link.label}
					</a>
				{/if}
			{/each}
		</nav>
	</div>
</header>

<main class="page-container">
	{@render children()}
</main>

<style>
	.top-header {
		position: sticky;
		top: 0;
		z-index: 50;
		background: rgba(9, 10, 16, 0.85);
		backdrop-filter: blur(14px);
		-webkit-backdrop-filter: blur(14px);
		border-bottom: 1px solid var(--border-subtle);
		padding: 0.75rem 2rem;
	}

	.nav-container {
		max-width: 1400px;
		margin: 0 auto;
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.brand-group {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.brand-icon {
		font-size: 1.5rem;
		filter: drop-shadow(0 0 8px rgba(99, 102, 241, 0.5));
	}

	.brand-titles {
		display: flex;
		flex-direction: column;
	}

	.brand-name {
		font-weight: 800;
		font-size: 1.15rem;
		letter-spacing: -0.02em;
		background: linear-gradient(135deg, #f8fafc 30%, #818cf8 100%);
		-webkit-background-clip: text;
		-webkit-text-fill-color: transparent;
		background-clip: text;
	}

	.brand-sub {
		font-size: 0.7rem;
		color: var(--text-muted);
		font-weight: 500;
		letter-spacing: 0.02em;
	}

	.nav-links {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.nav-link {
		color: var(--text-secondary);
		text-decoration: none;
		font-size: 0.85rem;
		font-weight: 500;
		padding: 0.45rem 0.9rem;
		border-radius: 8px;
		transition: all 0.15s ease-in-out;
		border: 1px solid transparent;
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}

	.nav-link:hover {
		color: var(--text-primary);
		background: rgba(255, 255, 255, 0.05);
	}

	.nav-link.active {
		color: #e0e7ff;
		background: rgba(99, 102, 241, 0.15);
		border-color: rgba(99, 102, 241, 0.35);
	}

	.nav-external {
		color: var(--text-muted);
	}

	.ext-arrow {
		font-size: 0.75rem;
		opacity: 0.7;
	}

	.page-container {
		max-width: 1400px;
		margin: 0 auto;
		padding: 1.5rem 2rem 4rem 2rem;
	}
</style>
