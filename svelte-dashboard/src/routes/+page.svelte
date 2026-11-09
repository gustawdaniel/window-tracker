<script>
	import { onMount, onDestroy } from 'svelte';
	import { base } from '$app/paths';

	// State
	let range = $state('today');
	let stats = $state({
		totalDurationMs: 0,
		wallDurationMs: 0,
		activeDurationMs: 0,
		switchCount: 0,
		uniqueApps: 0,
		uniqueWindows: 0,
		topApps: [],
		hourlyActivity: [],
		topEntities: [],
		topProjects: [],
		backend: 'sqlite'
	});

	let history = $state([]);
	let searchQuery = $state('');
	let selectedAppFilter = $state('');
	let selectedHourFilter = $state(null); // null | 0..23
	let sessions = $state([]);
	let activeTab = $state('feed'); // 'feed' | 'entities' | 'timeline'

	let liveEvent = $state(null);
	let liveDurationSec = $state(0);
	let liveTimer = null;
	let sseSource = null;
	let isConnected = $state(false);

	// Fetch statistics
	async function fetchStats() {
		try {
			const res = await fetch(`${base}/api/stats?range=${range}`);
			if (res.ok) {
				stats = await res.json();
			}
		} catch (e) {
			console.error('Error fetching stats:', e);
		}
	}

	// Fetch history
	async function fetchHistory() {
		try {
			const params = new URLSearchParams({ limit: '80' });
			if (searchQuery) params.set('search', searchQuery);
			if (selectedAppFilter) params.set('app', selectedAppFilter);

			const res = await fetch(`${base}/api/history?${params.toString()}`);
			if (res.ok) {
				history = await res.json();
				if (history.length > 0 && !liveEvent) {
					liveEvent = history[0];
				}
			}
		} catch (e) {
			console.error('Error fetching history:', e);
		}
	}

	// Fetch timeline sessions
	async function fetchTimeline() {
		try {
			const res = await fetch(`${base}/api/timeline?limit=150`);
			if (res.ok) {
				sessions = await res.json();
			}
		} catch (e) {
			console.error('Error fetching timeline:', e);
		}
	}

	function formatDuration(ms) {
		if (!ms || ms < 1000) return '< 1s';
		const totalSec = Math.floor(ms / 1000);
		const hours = Math.floor(totalSec / 3600);
		const minutes = Math.floor((totalSec % 3600) / 60);
		const seconds = totalSec % 60;

		if (hours > 0) return `${hours}h ${minutes}m`;
		if (minutes > 0) return `${minutes}m ${seconds}s`;
		return `${seconds}s`;
	}

	function formatTimeOnly(ts) {
		if (!ts) return '--:--';
		return new Date(ts).toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	}

	function getAppColor(appClass) {
		const c = (appClass || '').toLowerCase();
		if (c.includes('code') || c.includes('antigravity') || c.includes('nvim') || c.includes('vim') || c.includes('ide') || c.includes('datagrip')) {
			return '#818cf8'; // indigo
		}
		if (c.includes('zen') || c.includes('chrome') || c.includes('firefox') || c.includes('brave') || c.includes('browser') || c.includes('epiphany')) {
			return '#38bdf8'; // cyan
		}
		if (c.includes('beeper') || c.includes('slack') || c.includes('discord') || c.includes('telegram')) {
			return '#34d399'; // emerald
		}
		if (c.includes('term') || c.includes('kitty') || c.includes('alacritty') || c.includes('wezterm')) {
			return '#fbbf24'; // amber
		}
		if (c.includes('thunar') || c.includes('nautilus') || c.includes('finder')) {
			return '#fb7185'; // rose
		}
		return '#94a3b8'; // slate
	}

	function getAppIcon(appClass) {
		const c = (appClass || '').toLowerCase();
		if (c.includes('antigravity') || c.includes('code')) return '⚡';
		if (c.includes('datagrip')) return '🗄️';
		if (c.includes('zen') || c.includes('chrome') || c.includes('firefox') || c.includes('epiphany')) return '🌐';
		if (c.includes('beeper') || c.includes('slack') || c.includes('telegram')) return '💬';
		if (c.includes('kitty') || c.includes('term')) return '💻';
		if (c.includes('thunar')) return '📁';
		return '🪟';
	}

	function getCategoryIcon(cat) {
		switch (cat) {
			case 'code': return '💻';
			case 'browser': return '🌐';
			case 'terminal': return '⌨️';
			case 'chat': return '💬';
			case 'docs': return '📝';
			default: return '🪟';
		}
	}

	async function fetchActive() {
		try {
			const res = await fetch(`${base}/api/active`);
			if (res.ok) {
				const active = await res.json();
				if (active) {
					liveEvent = active;
					const elapsed = Math.max(0, Math.floor((Date.now() - active.ts_start) / 1000));
					liveDurationSec = elapsed;
				}
			}
		} catch (e) {
			console.error('Error fetching active window:', e);
		}
	}

	// SSE Real-Time Stream
	let pollTimer = null;

	// SSE Real-Time Stream
	function startSSE() {
		if (sseSource) sseSource.close();

		sseSource = new EventSource(`${base}/api/stream`);

		sseSource.onopen = () => {
			isConnected = true;
		};

		sseSource.onmessage = (event) => {
			try {
				const item = JSON.parse(event.data);
				if (!item) return;

				liveEvent = item;
				if (item.duration_ms === 0) {
					liveDurationSec = 0;
				} else {
					liveDurationSec = Math.floor(item.duration_ms / 1000);
				}

				if (item.duration_ms > 0) {
					const exists = history.some(h => (h.id && item.id && h.id === item.id) || (h.ts_end === item.ts_end && h.app_class === item.app_class && h.duration_ms === item.duration_ms));
					if (!exists) {
						history = [item, ...history.filter(h => !(h.id && item.id && h.id === item.id) && !(h.ts_end === item.ts_end && h.app_class === item.app_class && h.duration_ms === item.duration_ms)).slice(0, 79)];
					}
				}
			} catch (e) {
				console.error('SSE parsing error:', e);
			}
		};

		sseSource.onerror = () => {
			isConnected = false;
		};
	}

	function handleVisibilityChange() {
		if (typeof document !== 'undefined' && !document.hidden) {
			fetchActive();
			fetchHistory();
			fetchStats();
		}
	}

	onMount(() => {
		fetchActive();
		fetchStats();
		fetchHistory();
		fetchTimeline();
		startSSE();

		liveTimer = setInterval(() => {
			liveDurationSec += 1;
		}, 1000);

		// Dynamiczny reconciliation loop co 30 sekund (z pominięciem uśpionych kart w tle)
		pollTimer = setInterval(() => {
			if (typeof document !== 'undefined' && document.hidden) return;
			fetchHistory();
			fetchStats();
			fetchActive();
		}, 30000);

		if (typeof document !== 'undefined') {
			document.addEventListener('visibilitychange', handleVisibilityChange);
		}
	});

	onDestroy(() => {
		if (sseSource) sseSource.close();
		if (liveTimer) clearInterval(liveTimer);
		if (pollTimer) clearInterval(pollTimer);
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
		}
	});

	function handleRangeChange(newRange) {
		range = newRange;
		fetchStats();
	}

	function toggleAppFilter(appName) {
		selectedAppFilter = (selectedAppFilter === appName) ? '' : appName;
		fetchHistory();
	}

	function toggleHourFilter(hour) {
		selectedHourFilter = (selectedHourFilter === hour) ? null : hour;
	}

	// Oblicz dynamiczny rozkład 24h z uwzględnieniem wybranej aplikacji
	let currentHourlyData = $derived(
		(stats.hourlyActivity || []).map(h => {
			if (!selectedAppFilter) {
				return {
					hour: h.hour,
					durationMs: h.activeDurationMs ?? h.durationMs,
					activeDurationMs: h.activeDurationMs ?? h.durationMs,
					wallDurationMs: h.wallDurationMs ?? h.durationMs,
					switches: h.switches,
					appBreakdown: h.apps || {}
				};
			}
			const appMs = (h.apps && h.apps[selectedAppFilter]) || 0;
			const appWallMs = (h.wallApps && h.wallApps[selectedAppFilter]) || appMs;
			return {
				hour: h.hour,
				durationMs: appMs,
				activeDurationMs: appMs,
				wallDurationMs: appWallMs,
				switches: appMs > 0 ? 1 : 0,
				appBreakdown: { [selectedAppFilter]: appMs }
			};
		})
	);

	let maxHourlyMs = $derived(
		Math.max(1, ...currentHourlyData.map(h => Math.max(h.durationMs, h.wallDurationMs || 0)))
	);

	// Filtrowanie historii
	let filteredHistory = $derived(
		history.filter(item => {
			if (selectedAppFilter && item.app_class !== selectedAppFilter) return false;
			if (selectedHourFilter !== null) {
				const itemHour = new Date(item.ts_start).getHours();
				if (itemHour !== selectedHourFilter) return false;
			}
			if (!searchQuery) return true;
			const q = searchQuery.toLowerCase();
			return (
				(item.window_title || '').toLowerCase().includes(q) ||
				(item.app_class || '').toLowerCase().includes(q) ||
				(item.entity || '').toLowerCase().includes(q) ||
				(item.project || '').toLowerCase().includes(q)
			);
		})
	);
</script>

<div class="dashboard-wrapper">
	<!-- Top Bar / Controls -->
	<section class="controls-bar glass-panel">
		<div class="status-left">
			<div class="badge badge-live">
				<span class="badge-pulse"></span>
				{isConnected ? 'LIVE STREAM ACTIVE' : 'CONNECTING...'}
			</div>
			<div class="backend-badge">
				BACKEND: <span class="backend-name">{stats.backend.toUpperCase()}</span>
			</div>
			{#if selectedAppFilter}
				<div class="filter-pill">
					Filtr aplikacji: <strong>{selectedAppFilter}</strong>
					<button class="pill-close" onclick={() => toggleAppFilter(selectedAppFilter)}>✕</button>
				</div>
			{/if}
			{#if selectedHourFilter !== null}
				<div class="filter-pill hour-pill">
					Godzina: <strong>{selectedHourFilter}:00</strong>
					<button class="pill-close" onclick={() => selectedHourFilter = null}>✕</button>
				</div>
			{/if}
		</div>

		<div class="range-selector">
			{#each ['today', '7d', '30d'] as r}
				<button
					class="range-btn"
					class:active={range === r}
					onclick={() => handleRangeChange(r)}
				>
					{r === 'today' ? 'Dzisiaj' : r === '7d' ? '7 Dni' : '30 Dni'}
				</button>
			{/each}
		</div>
	</section>

	<!-- Hero Section: Currently Active Window -->
	{#if liveEvent}
		<section class="hero-live glass-panel glow-indigo">
			<div class="hero-header">
				<div class="hero-left-tags">
					<span class="live-tag">🔴 AKTUALNE OKNO W FOCUSIE</span>
					{#if liveEvent.category}
						<span class="category-tag">{getCategoryIcon(liveEvent.category)} {liveEvent.category.toUpperCase()}</span>
					{/if}
					{#if liveEvent.project}
						<span class="project-tag">📦 {liveEvent.project}</span>
					{/if}
				</div>
				<div class="hero-meta">
					<span class="meta-tag">🖥️ Monitor {liveEvent.monitor ?? 0}</span>
					<span class="meta-tag">🏷️ Obszar #{liveEvent.workspace ?? '1'}</span>
					<span class="meta-tag">💻 Host {liveEvent.host ?? 'rog'}</span>
				</div>
			</div>

			<div class="hero-body">
				<div class="app-icon-circle" style="background: {getAppColor(liveEvent.app_class)}20; border-color: {getAppColor(liveEvent.app_class)}60">
					<span>{getAppIcon(liveEvent.app_class)}</span>
				</div>
				<div class="app-info">
					<div class="app-title-line">
						<h2 class="app-class" style="color: {getAppColor(liveEvent.app_class)}">{liveEvent.app_class}</h2>
						{#if liveEvent.entity}
							<div class="entity-badge">
								<span class="entity-icon">
									{liveEvent.category === 'code' ? '📄' : liveEvent.category === 'browser' ? '🌐' : liveEvent.category === 'terminal' ? '📁' : '📍'}
								</span>
								<span class="entity-text">{liveEvent.entity}</span>
							</div>
						{/if}
					</div>
					<p class="window-title" title={liveEvent.window_title}>{liveEvent.window_title || 'Workspace'}</p>
				</div>
				<div class="time-block">
					<div class="timer-label">CZAS W TYM OKNIE</div>
					<div class="timer-val">{formatDuration(liveDurationSec * 1000)}</div>
				</div>
			</div>
		</section>
	{/if}

	<!-- KPI Metric Cards -->
	<section class="kpi-grid">
		<div class="kpi-card glass-panel">
			<div class="kpi-icon">⏱️</div>
			<div class="kpi-content">
				<div class="kpi-label">Łączny Czas Skupienia</div>
				<div class="kpi-value">{formatDuration(stats.totalDurationMs)}</div>
				<div class="kpi-sub">
					{#if stats.wallDurationMs && stats.wallDurationMs > stats.totalDurationMs}
						aktywny czas (włączony: {formatDuration(stats.wallDurationMs)})
					{:else}
						faktycznie zarejestrowana aktywność
					{/if}
				</div>
			</div>
		</div>

		<div class="kpi-card glass-panel">
			<div class="kpi-icon">🔄</div>
			<div class="kpi-content">
				<div class="kpi-label">Przełączenia Okien</div>
				<div class="kpi-value">{stats.switchCount}</div>
				<div class="kpi-sub">{stats.uniqueWindows} unikalnych okien</div>
			</div>
		</div>

		<div class="kpi-card glass-panel">
			<div class="kpi-icon">📦</div>
			<div class="kpi-content">
				<div class="kpi-label">Główny Program</div>
				<div class="kpi-value">{stats.topApps[0]?.appClass ?? 'Brak'}</div>
				<div class="kpi-sub">{stats.uniqueApps} aktywnych programów</div>
			</div>
		</div>

		<div class="kpi-card glass-panel">
			<div class="kpi-icon">🎯</div>
			<div class="kpi-content">
				<div class="kpi-label">Deep Work Ratio</div>
				<div class="kpi-value">
					{stats.totalDurationMs > 0 ? Math.min(100, Math.round((stats.topApps[0]?.durationMs || 0) * 100 / stats.totalDurationMs)) : 0}%
				</div>
				<div class="kpi-sub">udział lidera w czasie pracy</div>
			</div>
		</div>
	</section>

	<!-- Main Analytics Grid -->
	<div class="main-layout">
		<!-- Left: Interactive Applications Breakdown & 24h Diurnal Chart -->
		<div class="analytics-col">
			<!-- Application Breakdown Card -->
			<div class="glass-panel card-box">
				<div class="card-header">
					<div>
						<h3 class="card-title">📊 Podział Czasu wg Aplikacji</h3>
						<p class="card-hint">Kliknij aplikację, aby wyizolować jej rozkład dobowy</p>
					</div>
					{#if selectedAppFilter}
						<button class="clear-selection-btn" onclick={() => toggleAppFilter(selectedAppFilter)}>
							Pokaż wszystkie ✕
						</button>
					{:else}
						<span class="card-badge">{stats.topApps.length} aplikacji</span>
					{/if}
				</div>

				<div class="apps-list">
					{#each stats.topApps as app}
						<div
							class="app-row"
							class:selected={selectedAppFilter === app.appClass}
							onclick={() => toggleAppFilter(app.appClass)}
							role="button"
							tabindex="0"
							onkeydown={(e) => e.key === 'Enter' && toggleAppFilter(app.appClass)}
						>
							<div class="app-row-top">
								<div class="app-row-name">
									<span class="app-row-icon">{getAppIcon(app.appClass)}</span>
									<span class="app-name-txt" style="color: {getAppColor(app.appClass)}">{app.appClass}</span>
									<span class="app-switches-count">({app.switches} zm.)</span>
								</div>
								<div class="app-row-time">
									<span class="app-time-txt">{formatDuration(app.durationMs)}</span>
									<span class="app-pct-txt">{app.pct}%</span>
								</div>
							</div>
							<div class="progress-bar-bg">
								<div
									class="progress-bar-fill"
									style="width: {app.pct}%; background: {getAppColor(app.appClass)}"
								></div>
							</div>
						</div>
					{/each}
				</div>
			</div>

			<!-- Diurnal 24h Activity Distribution Chart (Interactive!) -->
			<div class="glass-panel card-box">
				<div class="card-header">
					<div>
						<h3 class="card-title">
							⏰ Rozkład Dobowy (24h)
							{#if selectedAppFilter}
								<span class="chart-filter-label" style="color: {getAppColor(selectedAppFilter)}">
									• {selectedAppFilter}
								</span>
							{/if}
						</h3>
						<span class="card-sub-info">Kliknij słupek, aby przefiltrować zdarzenia z danej godziny</span>
					</div>
					{#if selectedHourFilter !== null}
						<button class="clear-selection-btn" onclick={() => selectedHourFilter = null}>
							Wyczyść godzinę ✕
						</button>
					{/if}
				</div>

				<div class="diurnal-chart">
					{#each currentHourlyData as h}
						<div
							class="diurnal-col"
							class:selected-hour={selectedHourFilter === h.hour}
							onclick={() => toggleHourFilter(h.hour)}
							role="button"
							tabindex="0"
							onkeydown={(e) => e.key === 'Enter' && toggleHourFilter(h.hour)}
							title="Godzina {h.hour}:00 — {formatDuration(h.durationMs)} aktywnej pracy ({formatDuration(h.wallDurationMs)} włączony, {h.switches} zmian)"
						>
							<div class="bar-track">
								{#if h.wallDurationMs > h.durationMs}
									<div
										class="bar-ghost"
										style="height: {maxHourlyMs > 0 ? (h.wallDurationMs / maxHourlyMs) * 100 : 0}%;"
										title="Całkowity czas z oknem: {formatDuration(h.wallDurationMs)}"
									></div>
								{/if}
								<div
									class="bar-fill"
									style="height: {maxHourlyMs > 0 ? (h.durationMs / maxHourlyMs) * 100 : 0}%; background: {selectedAppFilter ? getAppColor(selectedAppFilter) : (h.durationMs > 0 ? 'linear-gradient(to top, #6366f1, #38bdf8)' : 'transparent')}"
								></div>
							</div>
							<span class="hour-lbl" class:highlight={h.durationMs > 0} class:active-hour={selectedHourFilter === h.hour}>
								{h.hour}
							</span>
						</div>
					{/each}
				</div>
			</div>
		</div>

		<!-- Right: Tabs (Feed, Specific Activities & Entities, Timeline) -->
		<div class="stream-col">
			<div class="glass-panel card-box">
				<div class="feed-tabs-header">
					<div class="tabs-group">
						<button
							class="tab-btn"
							class:active={activeTab === 'feed'}
							onclick={() => activeTab = 'feed'}
						>
							⚡ Strumień Okien ({filteredHistory.length})
						</button>
						<button
							class="tab-btn"
							class:active={activeTab === 'entities'}
							onclick={() => activeTab = 'entities'}
						>
							🎯 Pliki, Strony & Katalogi ({(stats.topEntities || []).length})
						</button>
						<button
							class="tab-btn"
							class:active={activeTab === 'timeline'}
							onclick={() => activeTab = 'timeline'}
						>
							🕒 Bloki Skupienia ({sessions.length})
						</button>
					</div>

					<div class="search-wrap">
						<input
							type="text"
							placeholder="Szukaj pliku, strony, tytułu..."
							bind:value={searchQuery}
							oninput={fetchHistory}
							class="search-input"
						/>
					</div>
				</div>

				{#if activeTab === 'feed'}
					<!-- Strumień okien na żywo -->
					<div class="history-list">
						{#if filteredHistory.length === 0}
							<div class="empty-state">
								<span>Brak zarejestrowanych okien spełniających kryteria.</span>
							</div>
						{/if}

						{#each filteredHistory as item (item.id || item.ts_end)}
							<div class="feed-item">
								<div class="feed-item-left">
									<div class="feed-icon" style="background: {getAppColor(item.app_class)}18; border-color: {getAppColor(item.app_class)}40">
										{getAppIcon(item.app_class)}
									</div>
									<div class="feed-content">
										<div class="feed-title-line">
											<span class="feed-app" style="color: {getAppColor(item.app_class)}">{item.app_class}</span>
											{#if item.project}
												<span class="feed-project-tag">📦 {item.project}</span>
											{/if}
											<span class="feed-time-span">
												{formatTimeOnly(item.ts_start)} – {formatTimeOnly(item.ts_end)}
											</span>
											<span class="feed-ws">Obszar #{item.workspace}</span>
										</div>
										
										{#if item.entity}
											<div class="feed-entity-line">
												<span class="entity-bullet">
													{item.category === 'code' ? '📄' : item.category === 'browser' ? '🌐' : item.category === 'terminal' ? '📁' : '📍'}
												</span>
												<strong class="entity-name">{item.entity}</strong>
											</div>
										{/if}
										
										<p class="feed-win-title" title={item.window_title}>{item.window_title || 'Workspace'}</p>
									</div>
								</div>
								<div class="feed-item-right">
									{#if item.mouse_moves || item.mouse_clicks || item.mouse_scrolls}
										<span class="feed-mouse-badge" title="{item.mouse_moves} ruchów, {item.mouse_clicks} kliknięć, {item.mouse_scrolls} scrolli">
											🖱️ {item.mouse_clicks > 0 ? `${item.mouse_clicks} klik` : ''} {item.mouse_moves > 0 ? `${item.mouse_moves} ruch` : ''}
										</span>
									{/if}
									<span class="feed-duration-badge">{formatDuration(item.duration_ms)}</span>
								</div>
							</div>
						{/each}
					</div>

				{:else if activeTab === 'entities'}
					<!-- Widok szczegółowych plików / stron / katalogów -->
					<div class="entities-list">
						{#if !stats.topEntities || stats.topEntities.length === 0}
							<div class="empty-state">
								<span>Brak zgromadzonych danych o plikach i stronach.</span>
							</div>
						{/if}

						{#each stats.topEntities as ent}
							<div class="entity-item">
								<div class="entity-item-left">
									<span class="entity-type-icon">
										{ent.category === 'code' ? '📄' : ent.category === 'browser' ? '🌐' : ent.category === 'terminal' ? '📁' : '📍'}
									</span>
									<div class="entity-details">
										<div class="entity-heading">
											<span class="entity-main-title">{ent.entity}</span>
											{#if ent.project}
												<span class="entity-project-badge">📦 {ent.project}</span>
											{/if}
										</div>
										<div class="entity-sub">
											<span style="color: {getAppColor(ent.appClass)}">{ent.appClass}</span>
											<span class="bullet">•</span>
											<span>{ent.switches} wizyt / otwarć</span>
										</div>
									</div>
								</div>
								<div class="entity-item-right">
									<span class="entity-duration">{formatDuration(ent.durationMs)}</span>
								</div>
							</div>
						{/each}
					</div>

				{:else}
					<!-- Sessions & Timeline View -->
					<div class="sessions-list">
						{#if sessions.length === 0}
							<div class="empty-state">
								<span>Brak wystarczającej liczby danych do utworzenia sesji.</span>
							</div>
						{/if}

						{#each sessions as session (session.id)}
							<div class="session-card" class:deep-work={session.isDeepWork}>
								<div class="session-header">
									<div class="session-title-group">
										<span class="session-icon">{session.isDeepWork ? '🔥' : '💼'}</span>
										<div>
											<span class="session-label">
												{session.isDeepWork ? 'Głęboki Blok Skupienia (Deep Work)' : 'Sesja Robocza'}
											</span>
											<div class="session-time-sub">
												{session.date} • {session.startTime} – {session.endTime} ({session.durationMin} min)
											</div>
										</div>
									</div>
									<div class="session-stats">
										<span class="session-app-badge" style="color: {getAppColor(session.dominantApp)}">
											Lider: {session.dominantApp}
										</span>
										<span class="session-switches-badge">{session.switches} zmian</span>
									</div>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

<style>
	.dashboard-wrapper {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
	}

	/* Top Controls Bar */
	.controls-bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.75rem 1.25rem;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.status-left {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		flex-wrap: wrap;
	}

	.backend-badge {
		font-size: 0.75rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
		letter-spacing: 0.05em;
	}

	.backend-name {
		color: #38bdf8;
		font-weight: 700;
	}

	.filter-pill {
		font-size: 0.75rem;
		background: rgba(99, 102, 241, 0.15);
		border: 1px solid rgba(99, 102, 241, 0.35);
		color: #c7d2fe;
		padding: 0.2rem 0.6rem;
		border-radius: 6px;
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}

	.hour-pill {
		background: rgba(6, 182, 212, 0.15);
		border-color: rgba(6, 182, 212, 0.35);
		color: #67e8f9;
	}

	.pill-close {
		background: transparent;
		border: none;
		color: inherit;
		cursor: pointer;
		font-size: 0.75rem;
		padding: 0;
	}

	.range-selector {
		display: flex;
		background: rgba(0, 0, 0, 0.3);
		padding: 3px;
		border-radius: 8px;
		border: 1px solid var(--border-subtle);
	}

	.range-btn {
		background: transparent;
		border: none;
		color: var(--text-secondary);
		padding: 0.35rem 0.85rem;
		font-size: 0.8rem;
		font-weight: 600;
		border-radius: 6px;
		cursor: pointer;
		transition: all 0.15s;
	}

	.range-btn:hover {
		color: var(--text-primary);
	}

	.range-btn.active {
		background: var(--accent-primary);
		color: #ffffff;
		box-shadow: 0 0 12px rgba(99, 102, 241, 0.4);
	}

	/* Hero Live Window Card */
	.hero-live {
		padding: 1.5rem;
		border-color: rgba(99, 102, 241, 0.35);
		background: linear-gradient(135deg, rgba(24, 27, 38, 0.85) 0%, rgba(16, 18, 26, 0.95) 100%);
	}

	.hero-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1rem;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.hero-left-tags {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}

	.live-tag {
		font-size: 0.75rem;
		font-weight: 800;
		color: #f43f5e;
		letter-spacing: 0.06em;
	}

	.category-tag {
		font-size: 0.7rem;
		padding: 0.15rem 0.5rem;
		background: rgba(99, 102, 241, 0.15);
		border: 1px solid rgba(99, 102, 241, 0.3);
		border-radius: 5px;
		color: #c7d2fe;
		font-weight: 600;
	}

	.project-tag {
		font-size: 0.7rem;
		padding: 0.15rem 0.5rem;
		background: rgba(56, 189, 248, 0.15);
		border: 1px solid rgba(56, 189, 248, 0.3);
		border-radius: 5px;
		color: #38bdf8;
		font-weight: 600;
	}

	.hero-meta {
		display: flex;
		gap: 0.5rem;
	}

	.meta-tag {
		font-size: 0.75rem;
		padding: 0.2rem 0.55rem;
		background: rgba(255, 255, 255, 0.06);
		border-radius: 6px;
		color: var(--text-secondary);
		font-family: var(--font-mono);
	}

	.hero-body {
		display: flex;
		align-items: center;
		gap: 1.25rem;
	}

	.app-icon-circle {
		width: 54px;
		height: 54px;
		border-radius: 14px;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 1.8rem;
		border: 1px solid;
		flex-shrink: 0;
	}

	.app-info {
		flex: 1;
		min-width: 0;
	}

	.app-title-line {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		margin-bottom: 0.25rem;
		flex-wrap: wrap;
	}

	.app-class {
		font-size: 1.35rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}

	.entity-badge {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		background: rgba(255, 255, 255, 0.06);
		border: 1px solid rgba(255, 255, 255, 0.12);
		padding: 0.2rem 0.6rem;
		border-radius: 6px;
		font-size: 0.85rem;
		font-weight: 600;
		color: #f8fafc;
	}

	.entity-icon {
		font-size: 0.9rem;
	}

	.entity-text {
		font-family: var(--font-mono);
		font-size: 0.8rem;
	}

	.window-title {
		font-size: 0.85rem;
		color: var(--text-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.time-block {
		text-align: right;
		padding-left: 1.5rem;
		border-left: 1px solid var(--border-subtle);
		flex-shrink: 0;
	}

	.timer-label {
		font-size: 0.65rem;
		font-weight: 700;
		color: var(--text-muted);
		letter-spacing: 0.08em;
	}

	.timer-val {
		font-size: 1.6rem;
		font-weight: 800;
		font-family: var(--font-mono);
		color: #38bdf8;
		letter-spacing: -0.02em;
	}

	/* KPI Grid */
	.kpi-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 1rem;
	}

	.kpi-card {
		padding: 1.25rem;
		display: flex;
		align-items: center;
		gap: 1rem;
	}

	.kpi-icon {
		font-size: 2rem;
		background: rgba(255, 255, 255, 0.04);
		width: 50px;
		height: 50px;
		border-radius: 12px;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}

	.kpi-content {
		min-width: 0;
	}

	.kpi-label {
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.kpi-value {
		font-size: 1.35rem;
		font-weight: 800;
		color: var(--text-primary);
		letter-spacing: -0.02em;
		margin: 0.15rem 0;
	}

	.kpi-sub {
		font-size: 0.75rem;
		color: var(--text-secondary);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* Main Layout */
	.main-layout {
		display: grid;
		grid-template-columns: 460px 1fr;
		gap: 1.5rem;
		align-items: start;
	}

	@media (max-width: 1080px) {
		.main-layout {
			grid-template-columns: 1fr;
		}
	}

	.analytics-col, .stream-col {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
	}

	.card-box {
		padding: 1.25rem;
	}

	.card-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1.25rem;
	}

	.card-title {
		font-size: 1rem;
		font-weight: 700;
		color: var(--text-primary);
	}

	.card-hint {
		font-size: 0.7rem;
		color: var(--text-muted);
		margin-top: 0.15rem;
	}

	.card-badge {
		font-size: 0.75rem;
		padding: 0.15rem 0.55rem;
		background: rgba(255, 255, 255, 0.06);
		border-radius: 6px;
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	.card-sub-info {
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.clear-selection-btn {
		background: rgba(255, 255, 255, 0.08);
		border: 1px solid rgba(255, 255, 255, 0.15);
		color: var(--text-primary);
		font-size: 0.75rem;
		padding: 0.25rem 0.55rem;
		border-radius: 6px;
		cursor: pointer;
		transition: all 0.15s;
	}

	.clear-selection-btn:hover {
		background: rgba(244, 63, 94, 0.2);
		border-color: rgba(244, 63, 94, 0.4);
		color: #f43f5e;
	}

	.chart-filter-label {
		font-size: 0.85rem;
		font-weight: 700;
	}

	/* Apps List */
	.apps-list {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.app-row {
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid rgba(255, 255, 255, 0.04);
		border-radius: 10px;
		padding: 0.75rem 0.9rem;
		cursor: pointer;
		transition: all 0.15s;
	}

	.app-row:hover {
		background: rgba(255, 255, 255, 0.05);
		border-color: rgba(255, 255, 255, 0.1);
	}

	.app-row.selected {
		border-color: var(--accent-primary);
		background: rgba(99, 102, 241, 0.15);
		box-shadow: 0 0 12px rgba(99, 102, 241, 0.2);
	}

	.app-row-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.45rem;
	}

	.app-row-name {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.app-row-icon {
		font-size: 1.1rem;
	}

	.app-name-txt {
		font-weight: 700;
		font-size: 0.9rem;
	}

	.app-switches-count {
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.app-row-time {
		display: flex;
		align-items: center;
		gap: 0.65rem;
	}

	.app-time-txt {
		font-weight: 700;
		font-size: 0.85rem;
		font-family: var(--font-mono);
		color: var(--text-primary);
	}

	.app-pct-txt {
		font-size: 0.8rem;
		color: var(--text-muted);
		min-width: 40px;
		text-align: right;
	}

	.progress-bar-bg {
		width: 100%;
		height: 5px;
		background: rgba(255, 255, 255, 0.06);
		border-radius: 3px;
		overflow: hidden;
	}

	.progress-bar-fill {
		height: 100%;
		border-radius: 3px;
		transition: width 0.4s ease-out;
	}

	/* Diurnal 24h Chart */
	.diurnal-chart {
		display: flex;
		align-items: flex-end;
		height: 130px;
		gap: 4px;
		padding-top: 1rem;
		border-bottom: 1px solid var(--border-subtle);
		padding-bottom: 0.5rem;
	}

	.diurnal-col {
		flex: 1;
		height: 100%;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: flex-end;
		gap: 0.35rem;
		cursor: pointer;
		padding: 2px 0;
		border-radius: 4px;
		transition: background 0.15s;
	}

	.diurnal-col:hover {
		background: rgba(255, 255, 255, 0.05);
	}

	.diurnal-col.selected-hour {
		background: rgba(6, 182, 212, 0.15);
	}

	.bar-track {
		width: 100%;
		height: 95px;
		background: rgba(255, 255, 255, 0.03);
		border-radius: 3px;
		display: flex;
		align-items: flex-end;
		overflow: hidden;
		position: relative;
	}

	.bar-ghost {
		position: absolute;
		bottom: 0;
		left: 0;
		width: 100%;
		background: rgba(255, 255, 255, 0.08);
		border-radius: 3px 3px 0 0;
		pointer-events: none;
		transition: height 0.3s ease;
	}

	.bar-fill {
		position: relative;
		z-index: 1;
		width: 100%;
		border-radius: 3px 3px 0 0;
		transition: height 0.3s ease;
	}

	.hour-lbl {
		font-size: 0.65rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	.hour-lbl.highlight {
		color: #38bdf8;
		font-weight: 700;
	}

	.hour-lbl.active-hour {
		color: #67e8f9;
		font-weight: 800;
		text-decoration: underline;
	}

	/* Feed & Timeline Tabs */
	.feed-tabs-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1rem;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.tabs-group {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.tab-btn {
		background: transparent;
		border: 1px solid var(--border-subtle);
		color: var(--text-secondary);
		padding: 0.4rem 0.85rem;
		border-radius: 8px;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.15s;
	}

	.tab-btn:hover {
		color: var(--text-primary);
		background: rgba(255, 255, 255, 0.04);
	}

	.tab-btn.active {
		background: rgba(99, 102, 241, 0.15);
		border-color: rgba(99, 102, 241, 0.4);
		color: #c7d2fe;
	}

	.search-wrap {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.search-input {
		background: rgba(0, 0, 0, 0.3);
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		color: var(--text-primary);
		padding: 0.4rem 0.8rem;
		font-size: 0.8rem;
		outline: none;
		transition: border-color 0.15s;
		min-width: 220px;
	}

	.search-input:focus {
		border-color: var(--accent-primary);
	}

	/* History List */
	.history-list, .sessions-list, .entities-list {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
		max-height: 700px;
		overflow-y: auto;
		padding-right: 0.25rem;
	}

	.empty-state {
		text-align: center;
		padding: 2.5rem;
		color: var(--text-muted);
		font-size: 0.9rem;
	}

	.feed-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.8rem 1rem;
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid rgba(255, 255, 255, 0.04);
		border-radius: 10px;
		transition: all 0.15s;
	}

	.feed-item:hover {
		background: rgba(255, 255, 255, 0.04);
		border-color: rgba(255, 255, 255, 0.08);
	}

	.feed-item-left {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		min-width: 0;
	}

	.feed-icon {
		width: 40px;
		height: 40px;
		border-radius: 10px;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 1.25rem;
		border: 1px solid;
		flex-shrink: 0;
	}

	.feed-content {
		min-width: 0;
	}

	.feed-title-line {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.2rem;
		flex-wrap: wrap;
	}

	.feed-app {
		font-weight: 700;
		font-size: 0.85rem;
	}

	.feed-project-tag {
		font-size: 0.7rem;
		background: rgba(56, 189, 248, 0.12);
		border: 1px solid rgba(56, 189, 248, 0.25);
		color: #38bdf8;
		padding: 0.05rem 0.4rem;
		border-radius: 4px;
		font-weight: 600;
	}

	.feed-time-span {
		font-size: 0.75rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	.feed-ws {
		font-size: 0.7rem;
		background: rgba(255, 255, 255, 0.05);
		padding: 0.05rem 0.35rem;
		border-radius: 4px;
		color: var(--text-secondary);
	}

	.feed-entity-line {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		margin-bottom: 0.2rem;
	}

	.entity-bullet {
		font-size: 0.85rem;
	}

	.entity-name {
		font-size: 0.85rem;
		font-weight: 700;
		color: #f1f5f9;
		font-family: var(--font-mono);
	}

	.feed-win-title {
		font-size: 0.8rem;
		color: var(--text-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 550px;
	}

	.feed-mouse-badge {
		font-size: 0.72rem;
		background: rgba(56, 189, 248, 0.1);
		color: #38bdf8;
		border: 1px solid rgba(56, 189, 248, 0.25);
		padding: 0.15rem 0.45rem;
		border-radius: 9999px;
		font-family: var(--font-mono);
		font-weight: 500;
		white-space: nowrap;
	}

	.feed-duration-badge {
		font-size: 0.85rem;
		font-weight: 700;
		font-family: var(--font-mono);
		color: #38bdf8;
		background: rgba(56, 189, 248, 0.1);
		border: 1px solid rgba(56, 189, 248, 0.2);
		padding: 0.25rem 0.65rem;
		border-radius: 6px;
		flex-shrink: 0;
	}

	/* Entities (Files / Sites / Dirs) List */
	.entity-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.85rem 1rem;
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid rgba(255, 255, 255, 0.05);
		border-radius: 10px;
		transition: all 0.15s;
	}

	.entity-item:hover {
		background: rgba(255, 255, 255, 0.05);
		border-color: rgba(255, 255, 255, 0.1);
	}

	.entity-item-left {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		min-width: 0;
	}

	.entity-type-icon {
		font-size: 1.4rem;
		width: 36px;
		height: 36px;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgba(255, 255, 255, 0.04);
		border-radius: 8px;
		flex-shrink: 0;
	}

	.entity-details {
		min-width: 0;
	}

	.entity-heading {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.2rem;
	}

	.entity-main-title {
		font-weight: 700;
		font-size: 0.9rem;
		color: var(--text-primary);
		font-family: var(--font-mono);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 500px;
	}

	.entity-project-badge {
		font-size: 0.7rem;
		background: rgba(56, 189, 248, 0.12);
		border: 1px solid rgba(56, 189, 248, 0.25);
		color: #38bdf8;
		padding: 0.05rem 0.4rem;
		border-radius: 4px;
	}

	.entity-sub {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.bullet {
		opacity: 0.5;
	}

	.entity-item-right {
		flex-shrink: 0;
	}

	.entity-duration {
		font-size: 0.85rem;
		font-weight: 700;
		font-family: var(--font-mono);
		color: #818cf8;
	}

	/* Sessions / Deep Work Cards */
	.session-card {
		padding: 1rem 1.25rem;
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid rgba(255, 255, 255, 0.05);
		border-radius: 12px;
	}

	.session-card.deep-work {
		background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(16, 18, 26, 0.7) 100%);
		border-color: rgba(99, 102, 241, 0.3);
	}

	.session-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.session-title-group {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.session-icon {
		font-size: 1.5rem;
	}

	.session-label {
		font-weight: 700;
		font-size: 0.95rem;
		color: var(--text-primary);
	}

	.session-time-sub {
		font-size: 0.75rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
		margin-top: 0.15rem;
	}

	.session-stats {
		display: flex;
		align-items: center;
		gap: 0.65rem;
	}

	.session-app-badge {
		font-size: 0.8rem;
		font-weight: 600;
	}

	.session-switches-badge {
		font-size: 0.75rem;
		padding: 0.2rem 0.5rem;
		background: rgba(255, 255, 255, 0.06);
		border-radius: 6px;
		color: var(--text-secondary);
	}
</style>
