import { Router, type IRouter } from "express";
import rateLimit from "express-rate-limit";
import { eq } from "drizzle-orm";
import { db, usersTable } from "@workspace/db";
import { LoginBody, LoginResponse, GetCurrentUserResponse } from "@workspace/api-zod";
import { verifyPassword } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";

const router: IRouter = Router();

const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many login attempts. Try again later." },
});

router.post("/auth/login", loginLimiter, async (req, res): Promise<void> => {
  const parsed = LoginBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(401).json({ error: "Invalid credentials" });
    return;
  }

  const [user] = await db
    .select()
    .from(usersTable)
    .where(eq(usersTable.email, parsed.data.email.toLowerCase()));

  if (!user) {
    res.status(401).json({ error: "Invalid credentials" });
    return;
  }

  const valid = await verifyPassword(parsed.data.password, user.passwordHash);
  if (!valid) {
    req.log.warn({ userId: user.id }, "Failed login attempt");
    res.status(401).json({ error: "Invalid credentials" });
    return;
  }

  req.session.userId = user.id;
  req.session.role = user.role;
  req.session.tutorId = user.tutorId;

  await writeAuditLog(req, {
    action: "login",
    entityType: "user",
    entityId: user.id,
  });

  res.json(
    LoginResponse.parse({
      id: user.id,
      firstName: user.firstName,
      lastName: user.lastName,
      email: user.email,
      role: user.role,
      tutorId: user.tutorId,
    }),
  );
});

router.post("/auth/logout", async (req, res): Promise<void> => {
  req.session.destroy((err) => {
    if (err) {
      req.log.error({ err }, "Failed to destroy session");
    }
    res.clearCookie("s4a.sid");
    res.sendStatus(204);
  });
});

router.get("/auth/me", async (req, res): Promise<void> => {
  if (!req.session.userId) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  const [user] = await db
    .select()
    .from(usersTable)
    .where(eq(usersTable.id, req.session.userId));

  if (!user) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  res.json(
    GetCurrentUserResponse.parse({
      id: user.id,
      firstName: user.firstName,
      lastName: user.lastName,
      email: user.email,
      role: user.role,
      tutorId: user.tutorId,
    }),
  );
});

export default router;
