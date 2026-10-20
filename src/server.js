import http from 'node:http';
import { MongoClient } from 'mongodb';

if (!process.env.MONGODB_URI || process.env.MONGODB_URI.includes('<db_password>')) {
  console.error('Set MONGODB_URI in your private .env file before starting.');
  process.exit(1);
}
const client = new MongoClient(process.env.MONGODB_URI, { serverSelectionTimeoutMS: 10000 });
try {
  await client.connect();
  await client.db(process.env.MONGODB_DB || 'virasatguide').command({ ping: 1 });
} catch {
  console.error('Atlas connection failed. Check your URI, database password, and Atlas IP access list.');
  await client.close();
  process.exit(1);
}
const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'GET' && req.url === '/health') {
    try {
      await client.db(process.env.MONGODB_DB || 'virasatguide').command({ ping: 1 });
      res.writeHead(200);
      res.end(JSON.stringify({ status: 'ok', database: 'connected' }));
    } catch {
      res.writeHead(503);
      res.end(JSON.stringify({ status: 'unavailable', database: 'disconnected' }));
    }
    return;
  }
  res.writeHead(404);
  res.end(JSON.stringify({ error: 'Not found' }));
});
server.listen(Number(process.env.PORT || 3000), '127.0.0.1', () => {
  console.log('Backend started; Atlas connection verified.');
});
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  server.close(async () => { await client.close(); process.exit(0); });
  setTimeout(() => process.exit(1), 10000).unref();
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
