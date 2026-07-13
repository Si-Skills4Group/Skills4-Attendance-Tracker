import session from "express-session";
import connectPgSimple from "connect-pg-simple";
import { pool } from "@workspace/db";

const PgSession = connectPgSimple(session);

const sessionSecret = process.env.SESSION_SECRET;

if (!sessionSecret) {
  throw new Error(
    "SESSION_SECRET must be set. Did you forget to provision it?",
  );
}

const isProduction = process.env.NODE_ENV === "production";

export const sessionMiddleware = session({
  store: new PgSession({
    pool,
    tableName: "user_sessions",
    // Table is created via a one-off SQL migration, not at runtime: when
    // bundled by esbuild, connect-pg-simple can't resolve its bundled
    // `table.sql` asset path to auto-create the table on first use.
    createTableIfMissing: false,
  }),
  secret: sessionSecret,
  resave: false,
  saveUninitialized: false,
  name: "s4a.sid",
  cookie: {
    httpOnly: true,
    secure: isProduction,
    sameSite: "lax",
    maxAge: 1000 * 60 * 60 * 12, // 12 hours
  },
});
