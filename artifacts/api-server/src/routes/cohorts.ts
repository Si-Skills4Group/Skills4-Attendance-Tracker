import { Router, type IRouter } from "express";
import { and, eq, sql } from "drizzle-orm";
import { db, cohortsTable, tutorsTable, learnersTable } from "@workspace/db";
import {
  ListCohortsResponse,
  CreateCohortBody,
  CreateCohortResponse,
  GetCohortParams,
  GetCohortResponse,
  UpdateCohortParams,
  UpdateCohortBody,
  UpdateCohortResponse,
  GetCohortLearnersParams,
  GetCohortLearnersResponse,
} from "@workspace/api-zod";
import { requireAuth, requireAdmin } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";
import { toDateOnly } from "../lib/dates";

const router: IRouter = Router();

const withTutorName = db
  .select({
    id: cohortsTable.id,
    name: cohortsTable.name,
    programme: cohortsTable.programme,
    level: cohortsTable.level,
    tutorId: cohortsTable.tutorId,
    deliveryDay: cohortsTable.deliveryDay,
    sessionStartTime: cohortsTable.sessionStartTime,
    sessionEndTime: cohortsTable.sessionEndTime,
    startDate: cohortsTable.startDate,
    endDate: cohortsTable.endDate,
    active: cohortsTable.active,
    externalSystemId: cohortsTable.externalSystemId,
    createdAt: cohortsTable.createdAt,
    updatedAt: cohortsTable.updatedAt,
    tutorName: sql<string | null>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
  })
  .from(cohortsTable)
  .leftJoin(tutorsTable, eq(cohortsTable.tutorId, tutorsTable.id));

router.get("/cohorts", requireAuth, async (req, res): Promise<void> => {
  const { tutorId, active, programme } = req.query as Record<
    string,
    string | undefined
  >;

  const filters = [];
  if (req.session.role === "tutor" && req.session.tutorId) {
    filters.push(eq(cohortsTable.tutorId, req.session.tutorId));
  } else if (tutorId) {
    filters.push(eq(cohortsTable.tutorId, parseInt(tutorId, 10)));
  }
  if (active !== undefined) filters.push(eq(cohortsTable.active, active === "true"));
  if (programme) filters.push(eq(cohortsTable.programme, programme));

  const rows = await withTutorName.where(
    filters.length > 0 ? and(...filters) : undefined,
  );
  res.json(ListCohortsResponse.parse(rows));
});

router.post("/cohorts", requireAdmin, async (req, res): Promise<void> => {
  const parsed = CreateCohortBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const [cohort] = await db
    .insert(cohortsTable)
    .values({
      name: parsed.data.name,
      programme: parsed.data.programme,
      level: parsed.data.level,
      tutorId: parsed.data.tutorId ?? null,
      deliveryDay: parsed.data.deliveryDay,
      sessionStartTime: parsed.data.sessionStartTime,
      sessionEndTime: parsed.data.sessionEndTime,
      startDate: toDateOnly(parsed.data.startDate),
      endDate: parsed.data.endDate ? toDateOnly(parsed.data.endDate) : null,
      active: parsed.data.active ?? true,
      externalSystemId: parsed.data.externalSystemId ?? null,
    })
    .returning();

  if (!cohort) {
    res.status(500).json({ error: "Failed to create cohort" });
    return;
  }

  await writeAuditLog(req, {
    action: "create",
    entityType: "cohort",
    entityId: cohort.id,
    newValue: cohort,
  });

  const [full] = await withTutorName.where(eq(cohortsTable.id, cohort.id));
  res.status(201).json(CreateCohortResponse.parse(full));
});

router.get("/cohorts/:id", requireAuth, async (req, res): Promise<void> => {
  const params = GetCohortParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }

  const [cohort] = await withTutorName.where(
    eq(cohortsTable.id, params.data.id),
  );

  if (!cohort) {
    res.status(404).json({ error: "Cohort not found" });
    return;
  }

  if (req.session.role === "tutor" && cohort.tutorId !== req.session.tutorId) {
    res.status(403).json({ error: "Not allowed to view this cohort" });
    return;
  }

  const [{ count }] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(learnersTable)
    .where(eq(learnersTable.cohortId, cohort.id));

  res.json(GetCohortResponse.parse({ ...cohort, learnerCount: count }));
});

router.patch("/cohorts/:id", requireAdmin, async (req, res): Promise<void> => {
  const params = UpdateCohortParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }

  const parsed = UpdateCohortBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const [existing] = await db
    .select()
    .from(cohortsTable)
    .where(eq(cohortsTable.id, params.data.id));

  if (!existing) {
    res.status(404).json({ error: "Cohort not found" });
    return;
  }

  const { startDate, endDate, ...restCohortUpdate } = parsed.data;

  const [updated] = await db
    .update(cohortsTable)
    .set({
      ...restCohortUpdate,
      ...(startDate ? { startDate: toDateOnly(startDate) } : {}),
      ...(endDate !== undefined
        ? { endDate: endDate ? toDateOnly(endDate) : null }
        : {}),
    })
    .where(eq(cohortsTable.id, params.data.id))
    .returning();

  if (!updated) {
    res.status(404).json({ error: "Cohort not found" });
    return;
  }

  await writeAuditLog(req, {
    action: "update",
    entityType: "cohort",
    entityId: updated.id,
    previousValue: existing,
    newValue: updated,
  });

  const [full] = await withTutorName.where(eq(cohortsTable.id, updated.id));
  res.json(UpdateCohortResponse.parse(full));
});

router.get(
  "/cohorts/:id/learners",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetCohortLearnersParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const [cohort] = await db
      .select()
      .from(cohortsTable)
      .where(eq(cohortsTable.id, params.data.id));
    if (!cohort) {
      res.status(404).json({ error: "Cohort not found" });
      return;
    }
    if (
      req.session.role === "tutor" &&
      cohort.tutorId !== req.session.tutorId
    ) {
      res.status(403).json({ error: "Not allowed to view this cohort" });
      return;
    }

    const { learnersWithNames } = await import("../lib/learners-query");
    const rows = await learnersWithNames.where(
      eq(learnersTable.cohortId, params.data.id),
    );

    res.json(GetCohortLearnersResponse.parse(rows));
  },
);

export default router;
