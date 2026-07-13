import { Router, type IRouter } from "express";
import { and, eq, ilike, or, sql } from "drizzle-orm";
import { db, learnersTable, learnerAllocationHistoryTable } from "@workspace/db";
import { learnersWithNames } from "../lib/learners-query";
import {
  ListLearnersResponse,
  CreateLearnerBody,
  CreateLearnerResponse,
  GetLearnerCsvTemplateResponse,
  PreviewLearnerCsvBody,
  PreviewLearnerCsvResponse,
  ImportLearnerCsvBody,
  ImportLearnerCsvResponse,
  GetLearnerParams,
  GetLearnerResponse,
  UpdateLearnerParams,
  UpdateLearnerBody,
  UpdateLearnerResponse,
  GetLearnerAllocationHistoryParams,
  GetLearnerAllocationHistoryResponse,
} from "@workspace/api-zod";
import { requireAuth, requireAdmin } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";
import { LEARNER_CSV_COLUMNS, stringifyRowsToCsv } from "../lib/csv";
import { toDateOnly } from "../lib/dates";

const router: IRouter = Router();

router.get("/learners", requireAuth, async (req, res): Promise<void> => {
  const { search, status, programme, tutorId, cohortId, page, pageSize } =
    req.query as Record<string, string | undefined>;

  const pageNum = page ? parseInt(page, 10) : 1;
  const pageSizeNum = pageSize ? parseInt(pageSize, 10) : 25;

  const filters = [];
  if (req.session.role === "tutor" && req.session.tutorId) {
    filters.push(eq(learnersTable.tutorId, req.session.tutorId));
  }
  if (search) {
    filters.push(
      or(
        ilike(learnersTable.firstName, `%${search}%`),
        ilike(learnersTable.lastName, `%${search}%`),
        ilike(learnersTable.learnerRef, `%${search}%`),
      ),
    );
  }
  if (status) filters.push(eq(learnersTable.status, status as never));
  if (programme) filters.push(eq(learnersTable.programme, programme));
  if (tutorId) filters.push(eq(learnersTable.tutorId, parseInt(tutorId, 10)));
  if (cohortId)
    filters.push(eq(learnersTable.cohortId, parseInt(cohortId, 10)));

  const whereClause = filters.length > 0 ? and(...filters) : undefined;

  const [items, [{ count }]] = await Promise.all([
    learnersWithNames
      .where(whereClause)
      .limit(pageSizeNum)
      .offset((pageNum - 1) * pageSizeNum),
    db
      .select({ count: sql<number>`count(*)::int` })
      .from(learnersTable)
      .where(whereClause),
  ]);

  res.json(
    ListLearnersResponse.parse({
      items,
      total: count,
      page: pageNum,
      pageSize: pageSizeNum,
    }),
  );
});

router.post("/learners", requireAdmin, async (req, res): Promise<void> => {
  const parsed = CreateLearnerBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const existing = await db
    .select()
    .from(learnersTable)
    .where(eq(learnersTable.learnerRef, parsed.data.learnerRef));
  if (existing.length > 0) {
    res
      .status(400)
      .json({ error: "A learner with this reference already exists" });
    return;
  }

  const [learner] = await db
    .insert(learnersTable)
    .values({
      learnerRef: parsed.data.learnerRef,
      uln: parsed.data.uln ?? null,
      firstName: parsed.data.firstName,
      lastName: parsed.data.lastName,
      email: parsed.data.email ?? null,
      employer: parsed.data.employer ?? null,
      programme: parsed.data.programme,
      level: parsed.data.level,
      startDate: toDateOnly(parsed.data.startDate),
      plannedEndDate: parsed.data.plannedEndDate
        ? toDateOnly(parsed.data.plannedEndDate)
        : null,
      status: parsed.data.status ?? "active",
      tutorId: parsed.data.tutorId ?? null,
      cohortId: parsed.data.cohortId ?? null,
      externalSystemId: parsed.data.externalSystemId ?? null,
    })
    .returning();

  if (!learner) {
    res.status(500).json({ error: "Failed to create learner" });
    return;
  }

  await writeAuditLog(req, {
    action: "create",
    entityType: "learner",
    entityId: learner.id,
    newValue: learner,
  });

  const [full] = await learnersWithNames.where(eq(learnersTable.id, learner.id));
  res.status(201).json(CreateLearnerResponse.parse(full));
});

router.get(
  "/learners/csv-template",
  requireAdmin,
  async (_req, res): Promise<void> => {
    const csv = stringifyRowsToCsv([], LEARNER_CSV_COLUMNS);
    res.json(
      GetLearnerCsvTemplateResponse.parse({
        csv,
        filename: "learner-import-template.csv",
      }),
    );
  },
);

router.post(
  "/learners/csv-preview",
  requireAdmin,
  async (req, res): Promise<void> => {
    const parsed = PreviewLearnerCsvBody.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const { parseCsvToRows } = await import("../lib/csv");
    let parsedRows: Record<string, string>[];
    try {
      parsedRows = parseCsvToRows(parsed.data.csv);
    } catch {
      res.status(400).json({ error: "Could not parse CSV content" });
      return;
    }

    const existingLearners = await db
      .select({
        learnerRef: learnersTable.learnerRef,
        uln: learnersTable.uln,
        email: learnersTable.email,
      })
      .from(learnersTable);
    const refSet = new Set(existingLearners.map((l) => l.learnerRef));
    const ulnSet = new Set(
      existingLearners.map((l) => l.uln).filter((v): v is string => !!v),
    );
    const emailSet = new Set(
      existingLearners.map((l) => l.email).filter((v): v is string => !!v),
    );

    const seenRefs = new Set<string>();
    const rows = parsedRows.map((data, index) => {
      const rowNumber = index + 1;
      const errors: string[] = [];
      if (!data.learnerRef) errors.push("learnerRef is required");
      if (!data.firstName) errors.push("firstName is required");
      if (!data.lastName) errors.push("lastName is required");
      if (!data.programme) errors.push("programme is required");
      if (!data.level) errors.push("level is required");
      if (!data.startDate) errors.push("startDate is required");

      let isDuplicate = false;
      let duplicateReason: string | null = null;
      if (data.learnerRef && refSet.has(data.learnerRef)) {
        isDuplicate = true;
        duplicateReason = "learnerRef already exists";
      } else if (data.learnerRef && seenRefs.has(data.learnerRef)) {
        isDuplicate = true;
        duplicateReason = "duplicate learnerRef within this file";
      } else if (data.uln && ulnSet.has(data.uln)) {
        isDuplicate = true;
        duplicateReason = "ULN already exists";
      } else if (data.email && emailSet.has(data.email)) {
        isDuplicate = true;
        duplicateReason = "email already exists";
      }
      if (data.learnerRef) seenRefs.add(data.learnerRef);

      return { rowNumber, data, isDuplicate, duplicateReason, errors };
    });

    const totalRows = rows.length;
    const invalidRows = rows.filter((r) => r.errors.length > 0).length;
    const duplicateRows = rows.filter((r) => r.isDuplicate).length;
    const validRows = rows.filter(
      (r) => r.errors.length === 0 && !r.isDuplicate,
    ).length;

    res.json(
      PreviewLearnerCsvResponse.parse({
        totalRows,
        validRows,
        invalidRows,
        duplicateRows,
        rows,
      }),
    );
  },
);

router.post(
  "/learners/csv-import",
  requireAdmin,
  async (req, res): Promise<void> => {
    const parsed = ImportLearnerCsvBody.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const existingRefs = new Set(
      (
        await db.select({ ref: learnersTable.learnerRef }).from(learnersTable)
      ).map((r) => r.ref),
    );

    let imported = 0;
    let skipped = 0;
    const errors: { rowNumber: number; field: string | null; message: string }[] =
      [];

    for (let i = 0; i < parsed.data.rows.length; i++) {
      const row = parsed.data.rows[i];
      const rowNumber = i + 1;
      if (!row) continue;

      if (
        !row.learnerRef ||
        !row.firstName ||
        !row.lastName ||
        !row.programme ||
        !row.level ||
        !row.startDate
      ) {
        errors.push({
          rowNumber,
          field: null,
          message: "Missing required field",
        });
        skipped += 1;
        continue;
      }

      if (existingRefs.has(row.learnerRef)) {
        errors.push({
          rowNumber,
          field: "learnerRef",
          message: "Learner reference already exists -- row skipped",
        });
        skipped += 1;
        continue;
      }

      await db.insert(learnersTable).values({
        learnerRef: row.learnerRef,
        uln: row.uln || null,
        firstName: row.firstName,
        lastName: row.lastName,
        email: row.email || null,
        employer: row.employer || null,
        programme: row.programme,
        level: row.level,
        startDate: row.startDate,
        plannedEndDate: row.plannedEndDate || null,
        status: "active",
      });
      existingRefs.add(row.learnerRef);
      imported += 1;
    }

    await writeAuditLog(req, {
      action: "csv_import",
      entityType: "learner",
      newValue: { imported, skipped },
    });

    res.json(ImportLearnerCsvResponse.parse({ imported, skipped, errors }));
  },
);

router.get("/learners/:id", requireAuth, async (req, res): Promise<void> => {
  const params = GetLearnerParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }

  const [learner] = await learnersWithNames.where(
    eq(learnersTable.id, params.data.id),
  );

  if (!learner) {
    res.status(404).json({ error: "Learner not found" });
    return;
  }

  if (
    req.session.role === "tutor" &&
    learner.tutorId !== req.session.tutorId
  ) {
    res.status(403).json({ error: "Not allowed to view this learner" });
    return;
  }

  res.json(GetLearnerResponse.parse(learner));
});

router.patch(
  "/learners/:id",
  requireAdmin,
  async (req, res): Promise<void> => {
    const params = UpdateLearnerParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const parsed = UpdateLearnerBody.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const [existing] = await db
      .select()
      .from(learnersTable)
      .where(eq(learnersTable.id, params.data.id));

    if (!existing) {
      res.status(404).json({ error: "Learner not found" });
      return;
    }

    const { startDate, plannedEndDate, ...restUpdate } = parsed.data;

    const [updated] = await db
      .update(learnersTable)
      .set({
        ...restUpdate,
        ...(startDate ? { startDate: toDateOnly(startDate) } : {}),
        ...(plannedEndDate !== undefined
          ? { plannedEndDate: plannedEndDate ? toDateOnly(plannedEndDate) : null }
          : {}),
      })
      .where(eq(learnersTable.id, params.data.id))
      .returning();

    if (!updated) {
      res.status(404).json({ error: "Learner not found" });
      return;
    }

    await writeAuditLog(req, {
      action: "update",
      entityType: "learner",
      entityId: updated.id,
      previousValue: existing,
      newValue: updated,
    });

    const [full] = await learnersWithNames.where(eq(learnersTable.id, updated.id));
    res.json(UpdateLearnerResponse.parse(full));
  },
);

router.get(
  "/learners/:id/allocation-history",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetLearnerAllocationHistoryParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const rows = await db
      .select({
        id: learnerAllocationHistoryTable.id,
        learnerId: learnerAllocationHistoryTable.learnerId,
        previousTutorId: learnerAllocationHistoryTable.previousTutorId,
        newTutorId: learnerAllocationHistoryTable.newTutorId,
        previousCohortId: learnerAllocationHistoryTable.previousCohortId,
        newCohortId: learnerAllocationHistoryTable.newCohortId,
        effectiveDate: learnerAllocationHistoryTable.effectiveDate,
        transferReason: learnerAllocationHistoryTable.transferReason,
        changedBy: learnerAllocationHistoryTable.changedBy,
        changedDate: learnerAllocationHistoryTable.changedDate,
      })
      .from(learnerAllocationHistoryTable)
      .where(eq(learnerAllocationHistoryTable.learnerId, params.data.id));

    const { enrichAllocationHistory } = await import("../lib/allocation");
    const enriched = await enrichAllocationHistory(rows);

    res.json(GetLearnerAllocationHistoryResponse.parse(enriched));
  },
);

export default router;
