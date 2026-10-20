# Virasat Guide Backend

Node.js backend configured for the MongoDB Atlas `virasat-guide` cluster.

## Run locally

1. Install Node.js 22 or later.
2. Run `npm install`.
3. Copy `.env.example` to `.env`.
4. Replace `<db_password>` in `.env` with your Atlas database user's password. URL-encode special characters in the password.
5. Allow your current machine's IP in Atlas → Database & Network Access.
6. Run `npm start`.
7. Open http://127.0.0.1:3000/health. A successful database ping returns `{"status":"ok","database":"connected"}`.

The server connects to Atlas before it starts listening. Credentials belong only in your private `.env` or your hosting provider's secret environment variables. Never commit them.

GitHub stores the source; running this backend creates the database connection. Deployment requires a hosting provider and its outbound IP in Atlas. No hosting or deployment has been configured.
