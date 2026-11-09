import { getActiveWindow, BACKEND, DB_PATH } from '$lib/backend.js';

export async function GET() {
	if (BACKEND === 'clickhouse') {
		try {
			const active = await getActiveWindow();
			return new Response(JSON.stringify(active || null), {
				headers: { 'Content-Type': 'application/json' }
			});
		} catch (e) {
			console.error('ClickHouse active error:', e);
			return new Response(JSON.stringify({ error: e.message }), { status: 500 });
		}
	}

	// SQLite fallback dla lokalnego trybu dev
	const { default: sqlite3 } = await import('sqlite3');
	return new Promise((resolve) => {
		const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
			if (err) {
				resolve(new Response(JSON.stringify(null), { headers: { 'Content-Type': 'application/json' } }));
				return;
			}
		});

		db.get(
			`SELECT
				id,
				ts_start,
				ts_end,
				duration_ms,
				host,
				app_class,
				window_title,
				workspace,
				monitor,
				category,
				project,
				entity
			 FROM window_activity
			 ORDER BY id DESC
			 LIMIT 1`,
			[],
			(err, row) => {
				db.close();
				if (err || !row) {
					resolve(new Response(JSON.stringify(null), { headers: { 'Content-Type': 'application/json' } }));
				} else {
					resolve(new Response(JSON.stringify({
						...row,
						monitor: Number(row.monitor),
						duration_ms: Number(row.duration_ms)
					}), {
						headers: { 'Content-Type': 'application/json' }
					}));
				}
			}
		);
	});
}
