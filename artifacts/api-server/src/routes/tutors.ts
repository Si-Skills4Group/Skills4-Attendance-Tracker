import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, usersTable, tutorsTable } from "@workspace/db";
import {
  ListTutorsResponse,
  CreateTutorBody,
  CreateTutorResponse,
  GetTutorParams,
  GetTutorResponse,
  UpdateTutorParams,
  UpdateTutorBody,
  UpdateTutorResponse,
} from "@workspace/api-zod";
import { requireAdmin, hashPassword } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";

const router: IRouter = Router();

router.get("/tutors", requireAdmin, async (req, res): Promise<void> => {
  const activeParam = req.query.active;
  const rows = await db.select().from(tutorsTable);
  const filtered =
    activeParam === undefined
      ? rows
      : rows.filter((t) => t.active === (activeParam === "true"));
  res.json(ListTutorsResponse.parse(filtered));
});

router.post("/tutors", requireAdmin, async (req, res): Promise<void> => {
  const parsed = CreateTutorBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const existing = await db
    .select()
    .from(usersTable)
    .where(eq(usersTable.email, parsed.data.email.toLowerCase()));
  if (existing.length > 0) {
    res.status(400).json({ error: "A user with this email already exists" });
    return;
  }

  const passwordHash = await hashPassword(parsed.data.password);
  const [user] = await db
    .insert(usersTable)
    .values({
      firstName: parsed.data.firstName,
      lastName: parsed.data.lastName,
      email: parsed.data.email.toLowerCase(),
      passwordHash,
      role: "tutor",
    })
    .returning();

  if (!user) {
    res.status(500).json({ error: "Failed to create tutor account" });
    return;
  }

  const [tutor] = await db
    .insert(tutorsTable)
    .values({
      userId: user.id,
      firstName: parsed.data.firstName,
      lastName: parsed.data.lastName,
      email: parsed.data.email.toLowerCase(),
      employeeRef: parsed.data.employeeRef,
      active: parsed.data.active ?? true,
      externalSystemId: parsed.data.externalSystemId ?? null,
    })
    .returning();

  if (!tutor) {
    res.status(500).json({ error: "Failed to create tutor" });
    return;
  }

  await db
    .update(usersTable)
    .set({ tutorId: tutor.id })
    .where(eq(usersTable.id, user.id));

  await writeAuditLog(req, {
    action: "create",
    entityType: "tutor",
    entityId: tutor.id,
    newValue: tutor,
  });

  res.status(201).json(CreateTutorResponse.parse(tutor));
});

router.get("/tutors/:id", requireAdmin, async (req, res): Promise<void> => {
  const params = GetTutorParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }

  const [tutor] = await db
    .select()
    .from(tutorsTable)
    .where(eq(tutorsTable.id, params.data.id));

  if (!tutor) {
    res.status(404).json({ error: "Tutor not found" });
    return;
  }

  res.json(GetTutorResponse.parse(tutor));
});

router.patch("/tutors/:id", requireAdmin, async (req, res): Promise<void> => {
  const params = UpdateTutorParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }

  const parsed = UpdateTutorBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const [existingTutor] = await db
    .select()
    .from(tutorsTable)
    .where(eq(tutorsTable.id, params.data.id));

  if (!existingTutor) {
    res.status(404).json({ error: "Tutor not found" });
    return;
  }

  const { password, ...rest } = parsed.data;

  const [tutor] = await db
    .update(tutorsTable)
    .set(rest)
    .where(eq(tutorsTable.id, params.data.id))
    .returning();

  if (!tutor) {
    res.status(404).json({ error: "Tutor not found" });
    return;
  }

  const userUpdate: Record<string, unknown> = {};
  if (rest.firstName) userUpdate.firstName = rest.firstName;
  if (rest.lastName) userUpdate.lastName = rest.lastName;
  if (rest.email) userUpdate.email = rest.email.toLowerCase();
  if (password) userUpdate.passwordHash = await hashPassword(password);

  if (Object.keys(userUpdate).length > 0) {
    await db
      .update(usersTable)
      .set(userUpdate)
      .where(eq(usersTable.id, existingTutor.userId));
  }

  await writeAuditLog(req, {
    action: "update",
    entityType: "tutor",
    entityId: tutor.id,
    previousValue: existingTutor,
    newValue: tutor,
  });

  res.json(UpdateTutorResponse.parse(tutor));
});

export default router;
