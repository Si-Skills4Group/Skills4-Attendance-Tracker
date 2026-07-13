import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, appSettingsTable } from "@workspace/db";
import { GetSettingsResponse, UpdateSettingsBody, UpdateSettingsResponse } from "@workspace/api-zod";
import { requireAuth, requireAdmin } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";

const router: IRouter = Router();

const getOrCreateSettings = async () => {
  const [existing] = await db.select().from(appSettingsTable);
  if (existing) return existing;
  const [created] = await db.insert(appSettingsTable).values({}).returning();
  if (!created) throw new Error("Failed to initialise application settings");
  return created;
};

router.get("/settings", requireAuth, async (_req, res): Promise<void> => {
  const settings = await getOrCreateSettings();
  res.json(GetSettingsResponse.parse(settings));
});

router.patch("/settings", requireAdmin, async (req, res): Promise<void> => {
  const parsed = UpdateSettingsBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const existing = await getOrCreateSettings();

  const [updated] = await db
    .update(appSettingsTable)
    .set(parsed.data)
    .where(eq(appSettingsTable.id, existing.id))
    .returning();

  if (!updated) {
    res.status(500).json({ error: "Failed to update settings" });
    return;
  }

  await writeAuditLog(req, {
    action: "update",
    entityType: "settings",
    entityId: updated.id,
    previousValue: existing,
    newValue: updated,
  });

  res.json(UpdateSettingsResponse.parse(updated));
});

export default router;
