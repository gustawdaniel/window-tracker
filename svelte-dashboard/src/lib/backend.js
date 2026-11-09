/**
 * backend.js — Adapter źródła danych dla Window Tracker (SQLite local vs ClickHouse remote).
 *
 * WINDOW_TRACKER_BACKEND=sqlite     (domyślnie lokalnie) -> odpytuje SQLite (~/.local/share/window-tracker/windows.db)
 * WINDOW_TRACKER_BACKEND=clickhouse (produkcja) -> odpytuje ClickHouse HTTP API (brain_events.window_intervals)
 */

import { env } from '$env/dynamic/private';
import os from 'os';
import path from 'path';

export const BACKEND = env.WINDOW_TRACKER_BACKEND ?? 'sqlite';
const CH_URL = env.CLICKHOUSE_URL ?? 'http://localhost:8123';
const CH_USER = env.CLICKHOUSE_USER ?? '';
const CH_PASS = env.CLICKHOUSE_PASSWORD ?? '';
const CH_DB = env.CLICKHOUSE_DB ?? 'brain_events';

export const DB_PATH = env.WINDOW_TRACKER_DB_PATH ??
	path.join(os.homedir(), '.local', 'share', 'window-tracker', 'windows.db');

/**
 * Zapytanie do ClickHouse HTTP API (format JSONEachRow).
 * @param {string} sql
 * @returns {Promise<Array<Record<string, unknown>>>}
 */
export async function clickhouseQuery(sql) {
	const url = `${CH_URL}/?query=${encodeURIComponent(sql)}&default_format=JSONEachRow&database=${CH_DB}`;
	const headers = { 'X-ClickHouse-Format': 'JSONEachRow' };
	if (CH_USER) headers['X-ClickHouse-User'] = CH_USER;
	if (CH_PASS) headers['X-ClickHouse-Key'] = CH_PASS;
	const res = await fetch(url, { headers });
	if (!res.ok) {
		const text = await res.text();
		throw new Error(`ClickHouse error ${res.status}: ${text}`);
	}
	const text = await res.text();
	if (!text.trim()) return [];
	return text.trim().split('\n').map(line => JSON.parse(line));
}

const statsCache = new Map();
const STATS_CACHE_TTL_MS = 30_000; // 30 sekund cache'owania ciężkich agregacji

/**
 * Pobierz zagregowane statystyki aktywności okien w wybranym przedziale czasu.
 * @param {'today' | '7d' | '30d' | 'all'} range
 */
export async function getAggregatedStats(range = 'today') {
	if (BACKEND === 'clickhouse') {
		const cached = statsCache.get(range);
		if (cached && (Date.now() - cached.timestamp < STATS_CACHE_TTL_MS)) {
			return cached.data;
		}

		let timeFilter = '';
		if (range === 'today') {
			timeFilter = "AND toDate(ts, 'Europe/Warsaw') = today()";
		} else if (range === '7d') {
			timeFilter = "AND ts > now() - INTERVAL 7 DAY";
		} else if (range === '30d') {
			timeFilter = "AND ts > now() - INTERVAL 30 DAY";
		}

		const baseWhere = `
			WHERE duration_ms > 0
			  AND lower(app_class) NOT IN ('afk', 'loginwindow', 'hyprlock', 'swaylock', 'gtklock', 'i3lock', 'screensaverengine', 'lockscreen')
			${timeFilter}
		`;

		const activeCond = `(mouse_moves > 0 or mouse_clicks > 0 or mouse_scrolls > 0 or keys > 0) and app_class != 'AFK'`;

		const resolvedProjectExpr = `
			multiIf(
				lower(project) IN ('tactile', 'fancy-cat', 'libvaxis-tactile', 'ghostty'), 'tactile',
				lower(project) IN ('invoice', 'brain', 'dotfiles', 'hajduk', 'well-done', 'net-pocket', 'google-api-key', 'open-cash-manager', 'master-thesis', 'studies'), lower(project),
				lower(project) = 'open_cash_manager', 'open-cash-manager',
				lower(project) = 'landing_hajduk', 'hajduk',
				lower(project) = 'my-arch-i3-config', 'dotfiles',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'invoice') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'ksef') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'podatki') > 0, 'invoice',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'open_cash_manager') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'open-cash-manager') > 0, 'open-cash-manager',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'tactile') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'fancy-cat') > 0, 'tactile',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'hajduk') > 0, 'hajduk',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'aqmmbs') > 0, 'studies/aqmmbs',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'spns') > 0, 'studies/spns',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'fpga') > 0, 'studies/fpga',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'materiałów kursu') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'usos') > 0, 'studies',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'tpc-reco') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'tpcreco') > 0, 'master-thesis',
				positionCaseInsensitive(concat(entity, ' ', window_title), 'brain') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'noctalia') > 0 OR positionCaseInsensitive(concat(entity, ' ', window_title), 'signoz') > 0, 'brain',
				lower(project) NOT IN ('', 'home', 'terminal', 'system', 'other', 'unknown', 'desktop', 'pulpit', 'finder', 'zsh', 'fish', 'bash', 'tmux', 'media', 'downloads'), lower(project),
				''
			)
		`;

		// 1. Podsumowanie (aktywny czas pracy vs czas włączonego komputera)
		const summaryRows = await clickhouseQuery(`
			SELECT
				count() AS switch_count,
				sum(duration_ms) AS wall_duration_ms,
				sumIf(duration_ms, ${activeCond}) AS active_duration_ms,
				uniq(app_class) AS unique_apps,
				uniq(window_title) AS unique_windows
			FROM ${CH_DB}.window_intervals
			${baseWhere}
		`);

		// 2. Top aplikacje wg aktywnego czasu skupienia
		const topAppsRows = await clickhouseQuery(`
			SELECT
				app_class,
				category,
				count() AS switches,
				sum(duration_ms) AS wall_ms,
				sumIf(duration_ms, ${activeCond}) AS active_ms
			FROM ${CH_DB}.window_intervals
			${baseWhere}
			GROUP BY app_class, category
			ORDER BY active_ms DESC
			LIMIT 15
		`);

		// 3. Rozkład godzinowy z podziałem na aplikacje (aktywny czas + czas włączony)
		const hourlyAppRows = await clickhouseQuery(`
			SELECT
				toHour(toTimeZone(ts, 'Europe/Warsaw')) AS hour,
				app_class,
				sum(duration_ms) AS wall_ms,
				sumIf(duration_ms, ${activeCond}) AS active_ms,
				count() AS switches
			FROM ${CH_DB}.window_intervals
			${baseWhere}
			GROUP BY hour, app_class
			ORDER BY hour ASC, active_ms DESC
		`);

		// 4. Top pliki / strony / katalogi (entities)
		const topEntitiesRows = await clickhouseQuery(`
			SELECT
				entity,
				app_class,
				category,
				${resolvedProjectExpr} AS project,
				sum(duration_ms) AS wall_ms,
				sumIf(duration_ms, ${activeCond}) AS active_ms,
				count() AS switches
			FROM ${CH_DB}.window_intervals
			${baseWhere} AND entity != ''
			GROUP BY entity, app_class, category, project
			ORDER BY active_ms DESC
			LIMIT 25
		`);

		// 5. Projekty
		const projectsRows = await clickhouseQuery(`
			SELECT
				${resolvedProjectExpr} AS project,
				sum(duration_ms) AS wall_ms,
				sumIf(duration_ms, ${activeCond}) AS active_ms
			FROM ${CH_DB}.window_intervals
			${baseWhere}
			GROUP BY project
			HAVING project != ''
			ORDER BY wall_ms DESC
			LIMIT 12
		`);

		const summary = summaryRows[0] || {};
		const wallMs = Number(summary.wall_duration_ms || 0);
		const actMs = Number(summary.active_duration_ms || 0);

		// Zbuduj strukturę 24h z rozbiciem na aplikacje
		const hourlyMap = new Map();
		for (let h = 0; h < 24; h++) {
			hourlyMap.set(h, { 
				hour: h, 
				durationMs: 0, 
				activeDurationMs: 0, 
				wallDurationMs: 0, 
				switches: 0, 
				apps: {}, 
				wallApps: {} 
			});
		}
		for (const r of hourlyAppRows) {
			const h = Number(r.hour);
			const rowWallMs = Number(r.wall_ms || 0);
			const rowActMs = Number(r.active_ms || 0);
			const sw = Number(r.switches || 0);
			if (hourlyMap.has(h)) {
				const entry = hourlyMap.get(h);
				entry.durationMs += rowActMs;
				entry.activeDurationMs += rowActMs;
				entry.wallDurationMs += rowWallMs;
				entry.switches += sw;
				entry.apps[r.app_class] = (entry.apps[r.app_class] || 0) + rowActMs;
				entry.wallApps[r.app_class] = (entry.wallApps[r.app_class] || 0) + rowWallMs;
			}
		}

		const result = {
			range,
			backend: 'clickhouse',
			totalDurationMs: actMs,
			wallDurationMs: wallMs,
			activeDurationMs: actMs,
			switchCount: Number(summary.switch_count || 0),
			uniqueApps: Number(summary.unique_apps || 0),
			uniqueWindows: Number(summary.unique_windows || 0),
			topApps: topAppsRows.map(r => {
				const aMs = Number(r.active_ms || 0);
				const wMs = Number(r.wall_ms || 0);
				return {
					appClass: r.app_class || 'Unknown',
					category: r.category || 'system',
					switches: Number(r.switches || 0),
					durationMs: aMs,
					wallMs: wMs,
					pct: actMs > 0 ? Number(((aMs / actMs) * 100).toFixed(1)) : 0
				};
			}),
			hourlyActivity: Array.from(hourlyMap.values()),
			topEntities: topEntitiesRows.map(r => ({
				entity: r.entity,
				appClass: r.app_class,
				category: r.category || 'system',
				project: r.project || '',
				durationMs: Number(r.active_ms || 0),
				wallMs: Number(r.wall_ms || 0),
				switches: Number(r.switches || 0)
			})),
			topProjects: projectsRows.map(r => ({
				project: r.project,
				durationMs: Number(r.active_ms || 0),
				wallMs: Number(r.wall_ms || 0)
			}))
		};

		statsCache.set(range, { timestamp: Date.now(), data: result });
		return result;
	}

	return null;
}

let historyCache = { timestamp: 0, limit: 0, data: null };
const HISTORY_CACHE_TTL_MS = 3_000; // 3s cache chroni bazę przed równoległym pollingiem wielu kart

let activeCache = { timestamp: 0, data: null };
const ACTIVE_CACHE_TTL_MS = 2_000; // 2s cache

/**
 * Pobierz ostatnie wpisy historii okien wraz z metadanymi pliku/strony.
 * @param {number} limit
 */
export async function getRecentHistory(limit = 100) {
	if (BACKEND === 'clickhouse') {
		if (historyCache.data && historyCache.limit === limit && (Date.now() - historyCache.timestamp < HISTORY_CACHE_TTL_MS)) {
			return historyCache.data;
		}

		const rows = await clickhouseQuery(`
			SELECT
				toInt64(toUnixTimestamp64Milli(ts)) AS ts_end,
				duration_ms,
				app_class,
				window_title,
				workspace,
				monitor,
				category,
				project,
				entity,
				mouse_moves,
				mouse_clicks,
				mouse_scrolls,
				keys AS keys_count,
				user_active,
				is_idle,
				host
			FROM ${CH_DB}.window_intervals
			WHERE duration_ms > 0
			  AND ts > now() - INTERVAL 24 HOUR
			ORDER BY ts DESC
			LIMIT ${limit}
		`);

		const data = rows.map((r, idx) => ({
			id: `${r.host || 'rog'}_${r.ts_end}_${r.duration_ms}_${r.app_class}_${idx}`,
			ts_start: Number(r.ts_end) - Number(r.duration_ms),
			ts_end: Number(r.ts_end),
			duration_ms: Number(r.duration_ms),
			app_class: r.app_class,
			window_title: r.window_title,
			workspace: r.workspace,
			monitor: Number(r.monitor),
			category: r.category || 'system',
			project: r.project || '',
			entity: r.entity || '',
			mouse_moves: Number(r.mouse_moves || 0),
			mouse_clicks: Number(r.mouse_clicks || 0),
			mouse_scrolls: Number(r.mouse_scrolls || 0),
			keys_count: Number(r.keys_count || 0),
			user_active: Boolean(r.user_active),
			is_idle: Boolean(r.is_idle),
			host: r.host
		}));

		historyCache = { timestamp: Date.now(), limit, data };
		return data;
	}

	return null;
}

/**
 * Pobierz nowe zdarzenia po określonym timestampie lub ID (do streamingu SSE).
 * @param {number} afterTsMs
 */
export async function getNewEvents(afterTsMs = 0, limit = 50) {
	if (BACKEND === 'clickhouse') {
		const safeTs = afterTsMs > 0 ? afterTsMs : Date.now() - 5000;
		const rows = await clickhouseQuery(`
			SELECT
				toInt64(toUnixTimestamp64Milli(ts)) AS ts_end,
				duration_ms,
				app_class,
				window_title,
				workspace,
				monitor,
				category,
				project,
				entity,
				mouse_moves,
				mouse_clicks,
				mouse_scrolls,
				keys AS keys_count,
				user_active,
				is_idle,
				host
			FROM ${CH_DB}.window_intervals
			WHERE ts > fromUnixTimestamp64Milli(${safeTs})
			ORDER BY ts ASC
			LIMIT ${limit}
		`);

		return rows.map((r, idx) => ({
			id: `${r.host || 'rog'}_${r.ts_end}_${r.duration_ms}_${r.app_class}_${idx}`,
			ts_start: Number(r.ts_end) - Number(r.duration_ms),
			ts_end: Number(r.ts_end),
			duration_ms: Number(r.duration_ms),
			app_class: r.app_class,
			window_title: r.window_title,
			workspace: r.workspace,
			monitor: Number(r.monitor),
			category: r.category || 'system',
			project: r.project || '',
			entity: r.entity || '',
			mouse_moves: Number(r.mouse_moves || 0),
			mouse_clicks: Number(r.mouse_clicks || 0),
			mouse_scrolls: Number(r.mouse_scrolls || 0),
			keys_count: Number(r.keys_count || 0),
			user_active: Boolean(r.user_active),
			is_idle: Boolean(r.is_idle),
			host: r.host
		}));
	}

	return null;
}

/**
 * Pobierz aktualne, aktywne okno (najnowszy rekord focus_start lub interval).
 */
export async function getActiveWindow() {
	if (BACKEND === 'clickhouse') {
		if (activeCache.data && (Date.now() - activeCache.timestamp < ACTIVE_CACHE_TTL_MS)) {
			return activeCache.data;
		}

		const rows = await clickhouseQuery(`
			SELECT
				toInt64(toUnixTimestamp64Milli(ts)) AS ts_end,
				duration_ms,
				app_class,
				window_title,
				workspace,
				monitor,
				category,
				project,
				entity,
				event_type,
				host
			FROM ${CH_DB}.window_intervals
			WHERE ts > now() - INTERVAL 24 HOUR
			ORDER BY ts DESC
			LIMIT 1
		`);

		if (rows && rows.length > 0) {
			const r = rows[0];
			const result = {
				id: 1,
				ts_start: Number(r.ts_end) - Number(r.duration_ms),
				ts_end: Number(r.ts_end),
				duration_ms: Number(r.duration_ms),
				app_class: r.app_class,
				window_title: r.window_title,
				workspace: r.workspace,
				monitor: Number(r.monitor),
				category: r.category || 'system',
				project: r.project || '',
				entity: r.entity || '',
				event_type: r.event_type || 'focus_start',
				host: r.host
			};
			activeCache = { timestamp: Date.now(), data: result };
			return result;
		}
	}
	return null;
}
