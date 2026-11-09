import { getAggregatedStats, BACKEND, DB_PATH } from '$lib/backend.js';

export async function GET({ url }) {
	const range = url.searchParams.get('range') || 'today';

	if (BACKEND === 'clickhouse') {
		try {
			const stats = await getAggregatedStats(range);
			return new Response(JSON.stringify(stats), {
				headers: { 'Content-Type': 'application/json' }
			});
		} catch (e) {
			console.error('ClickHouse stats error:', e);
			return new Response(JSON.stringify({ error: e.message }), { status: 500 });
		}
	}

	// SQLite fallback dla lokalnego trybu dev
	const { default: sqlite3 } = await import('sqlite3');
	return new Promise((resolve) => {
		const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
			if (err) {
				resolve(new Response(JSON.stringify({
					range,
					backend: 'sqlite',
					totalDurationMs: 0,
					switchCount: 0,
					uniqueApps: 0,
					uniqueWindows: 0,
					topApps: [],
					hourlyActivity: [],
					topEntities: [],
					topProjects: []
				}), { headers: { 'Content-Type': 'application/json' } }));
				return;
			}
		});

		let timeWhere = '';
		if (range === 'today') {
			timeWhere = "AND date(ts_start/1000, 'unixepoch', 'localtime') = date('now', 'localtime')";
		} else if (range === '7d') {
			timeWhere = "AND ts_start > (strftime('%s', 'now') - 7*86400) * 1000";
		} else if (range === '30d') {
			timeWhere = "AND ts_start > (strftime('%s', 'now') - 30*86400) * 1000";
		}

		const activeCond = "((coalesce(mouse_moves, 0) + coalesce(mouse_clicks, 0) + coalesce(mouse_scrolls, 0) + coalesce(keys, 0) > 0) AND app_class != 'AFK' AND coalesce(is_idle, 0) = 0)";

		const summaryQuery = `
			SELECT
				count(*) AS switch_count,
				coalesce(sum(duration_ms), 0) AS wall_duration_ms,
				coalesce(sum(CASE WHEN ${activeCond} THEN duration_ms ELSE 0 END), 0) AS active_duration_ms,
				count(DISTINCT app_class) AS unique_apps,
				count(DISTINCT window_title) AS unique_windows
			FROM window_activity
			WHERE 1=1 ${timeWhere}
		`;

		const topAppsQuery = `
			SELECT
				app_class,
				coalesce(category, 'system') as category,
				count(*) AS switches,
				sum(duration_ms) AS wall_ms,
				sum(CASE WHEN ${activeCond} THEN duration_ms ELSE 0 END) AS active_ms
			FROM window_activity
			WHERE 1=1 ${timeWhere}
			GROUP BY app_class, category
			ORDER BY active_ms DESC
			LIMIT 15
		`;

		const hourlyAppQuery = `
			SELECT
				cast(strftime('%H', ts_start/1000, 'unixepoch', 'localtime') as integer) as hour,
				app_class,
				sum(duration_ms) as wall_ms,
				sum(CASE WHEN ${activeCond} THEN duration_ms ELSE 0 END) as active_ms,
				count(*) as switches
			FROM window_activity
			WHERE 1=1 ${timeWhere}
			GROUP BY hour, app_class
			ORDER BY hour ASC, active_ms DESC
		`;

		const topEntitiesQuery = `
			SELECT
				coalesce(nullif(entity, ''), window_title) as entity,
				app_class,
				coalesce(category, 'system') as category,
				coalesce(project, '') as project,
				sum(duration_ms) as wall_ms,
				sum(CASE WHEN ${activeCond} THEN duration_ms ELSE 0 END) as active_ms,
				count(*) as switches
			FROM window_activity
			WHERE 1=1 ${timeWhere}
			GROUP BY entity, app_class
			ORDER BY active_ms DESC
			LIMIT 25
		`;

		const topProjectsQuery = `
			SELECT
				project,
				sum(duration_ms) as wall_ms,
				sum(CASE WHEN ${activeCond} THEN duration_ms ELSE 0 END) as active_ms
			FROM window_activity
			WHERE 1=1 ${timeWhere} AND project != ''
			GROUP BY project
			ORDER BY active_ms DESC
			LIMIT 10
		`;

		db.get(summaryQuery, [], (err1, summary) => {
			if (err1) {
				db.close();
				resolve(new Response(JSON.stringify({ error: err1.message }), { status: 500 }));
				return;
			}

			db.all(topAppsQuery, [], (err2, topAppsRows) => {
				db.all(hourlyAppQuery, [], (err3, hourlyAppRows) => {
					db.all(topEntitiesQuery, [], (err4, topEntitiesRows) => {
						db.all(topProjectsQuery, [], (err5, topProjectsRows) => {
							db.close();

							const wallMs = summary?.wall_duration_ms || 0;
							const actMs = summary?.active_duration_ms || 0;

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
							for (const r of hourlyAppRows || []) {
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

							resolve(new Response(JSON.stringify({
								range,
								backend: 'sqlite',
								totalDurationMs: actMs,
								wallDurationMs: wallMs,
								activeDurationMs: actMs,
								switchCount: summary?.switch_count || 0,
								uniqueApps: summary?.unique_apps || 0,
								uniqueWindows: summary?.unique_windows || 0,
								topApps: (topAppsRows || []).map(r => ({
									appClass: r.app_class,
									category: r.category,
									switches: r.switches,
									durationMs: Number(r.active_ms || 0),
									wallMs: Number(r.wall_ms || 0),
									pct: actMs > 0 ? Number(((Number(r.active_ms || 0) / actMs) * 100).toFixed(1)) : 0
								})),
								hourlyActivity: Array.from(hourlyMap.values()),
								topEntities: (topEntitiesRows || []).map(r => ({
									entity: r.entity,
									appClass: r.app_class,
									category: r.category,
									project: r.project,
									durationMs: Number(r.active_ms || 0),
									wallMs: Number(r.wall_ms || 0),
									switches: r.switches
								})),
								topProjects: (topProjectsRows || []).map(r => ({
									project: r.project,
									durationMs: Number(r.active_ms || 0),
									wallMs: Number(r.wall_ms || 0)
								}))
							}), { headers: { 'Content-Type': 'application/json' } }));
						});
					});
				});
			});
		});
	});
}
