import { getRecentHistory, BACKEND, DB_PATH } from '$lib/backend.js';

export async function GET({ url }) {
	const limit = parseInt(url.searchParams.get('limit') || '300', 10);

	let rows = [];
	if (BACKEND === 'clickhouse') {
		try {
			rows = await getRecentHistory(limit);
		} catch (e) {
			return new Response(JSON.stringify({ error: e.message }), { status: 500 });
		}
	} else {
		const { default: sqlite3 } = await import('sqlite3');
		rows = await new Promise((resolve) => {
			const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
				if (err) {
					resolve([]);
					return;
				}
			});
			db.all(
				`SELECT id, ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor
				 FROM window_activity
				 WHERE duration_ms > 0
				 ORDER BY ts_start DESC
				 LIMIT ?`,
				[limit],
				(err, data) => {
					db.close();
					resolve(data || []);
				}
			);
		});
	}

	// Sort chronologically ascending
	rows.sort((a, b) => a.ts_start - b.ts_start);

	// Group into sessions (gap > 15 min creates a new session)
	const sessions = [];
	let currentSession = null;

	for (const r of rows) {
		if (!currentSession || (r.ts_start - currentSession.ts_end) > 15 * 60 * 1000) {
			if (currentSession) {
				sessions.push(currentSession);
			}
			currentSession = {
				id: sessions.length + 1,
				ts_start: r.ts_start,
				ts_end: r.ts_end,
				duration_ms: r.duration_ms,
				switches: 1,
				appCounts: { [r.app_class]: r.duration_ms },
				chunks: [r]
			};
		} else {
			currentSession.ts_end = Math.max(currentSession.ts_end, r.ts_end);
			currentSession.duration_ms += r.duration_ms;
			currentSession.switches += 1;
			currentSession.appCounts[r.app_class] = (currentSession.appCounts[r.app_class] || 0) + r.duration_ms;
			currentSession.chunks.push(r);
		}
	}
	if (currentSession) {
		sessions.push(currentSession);
	}

	// Calculate dominant app and labels
	const formattedSessions = sessions.reverse().map(s => {
		let dominantApp = 'Unknown';
		let maxDuration = 0;
		for (const [app, dur] of Object.entries(s.appCounts)) {
			if (dur > maxDuration) {
				maxDuration = dur;
				dominantApp = app;
			}
		}

		const startDate = new Date(s.ts_start);
		const endDate = new Date(s.ts_end);

		return {
			id: s.id,
			startTime: startDate.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' }),
			endTime: endDate.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' }),
			date: startDate.toLocaleDateString('pl-PL'),
			durationMin: Math.round(s.duration_ms / 60000),
			switches: s.switches,
			dominantApp,
			isDeepWork: s.duration_ms >= 20 * 60 * 1000,
			chunksCount: s.chunks.length
		};
	});

	return new Response(JSON.stringify(formattedSessions), {
		headers: { 'Content-Type': 'application/json' }
	});
}
