import { Router, type IRouter } from "express";
import { and, eq, gte, lte, sql } from "drizzle-orm";
import {
  db,
  attendanceSessionsTable,
  attendanceRecordsTable,
  cohortsTable,
  tutorsTable,
  learnersTable,
  usersTable,
} from "@workspace/db";
import {
  ListAttendanceSessionsResponse,
  CreateAttendanceSessionBody,
  CreateAttendanceSessionResponse,
  GetAttendanceSessionParams,
  GetAttendanceSessionResponse,
  UpdateAttendanceSessionParams,
  UpdateAttendanceSessionBody,
  UpdateAttendanceSessionResponse,
  SaveAttendanceRegisterParams,
  SaveAttendanceRegisterBody,
  SaveAttendanceRegisterResponse,
  MarkAllPresentParams,
  MarkAllPresentResponse,
} from "@workspace/api-zod";
import { requireAuth } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";
import { toDateOnly } from "../lib/dates";

const router: IRouter = Router();

const sessionWithNames = db
  .select({
    id: attendanceSessionsTable.id,
    cohortId: attendanceSessionsTable.cohortId,
    cohortName: cohortsTable.name,
    tutorId: cohortsTable.tutorId,
    tutorName: sql<string | null>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
    sessionDate: attendanceSessionsTable.sessionDate,
    plannedStartTime: attendanceSessionsTable.plannedStartTime,
    plannedEndTime: attendanceSessionsTable.plannedEndTime,
    plannedDurationHours: attendanceSessionsTable.plannedDurationHours,
    title: attendanceSessionsTable.title,
    notes: attendanceSessionsTable.notes,
    createdBy: attendanceSessionsTable.createdBy,
    createdAt: attendanceSessionsTable.createdAt,
    updatedAt: attendanceSessionsTable.updatedAt,
  })
  .from(attendanceSessionsTable)
  .innerJoin(cohortsTable, eq(attendanceSessionsTable.cohortId, cohortsTable.id))
  .leftJoin(tutorsTable, eq(cohortsTable.tutorId, tutorsTable.id));

const withCounts = async (sessionId: number) => {
  const [cohortInfo] = await db
    .select({ cohortId: attendanceSessionsTable.cohortId })
    .from(attendanceSessionsTable)
    .where(eq(attendanceSessionsTable.id, sessionId));
  if (!cohortInfo) return { recordedCount: 0, expectedCount: 0 };

  const [{ expectedCount }] = await db
    .select({ expectedCount: sql<number>`count(*)::int` })
    .from(learnersTable)
    .where(eq(learnersTable.cohortId, cohortInfo.cohortId));

  const [{ recordedCount }] = await db
    .select({ recordedCount: sql<number>`count(*)::int` })
    .from(attendanceRecordsTable)
    .where(eq(attendanceRecordsTable.sessionId, sessionId));

  return { recordedCount, expectedCount };
};

router.get("/attendance/sessions", requireAuth, async (req, res): Promise<void> => {
  const { cohortId, tutorId, dateFrom, dateTo } = req.query as Record<
    string,
    string | undefined
  >;

  const filters = [];
  if (req.session.role === "tutor" && req.session.tutorId) {
    filters.push(eq(cohortsTable.tutorId, req.session.tutorId));
  } else if (tutorId) {
    filters.push(eq(cohortsTable.tutorId, parseInt(tutorId, 10)));
  }
  if (cohortId)
    filters.push(eq(attendanceSessionsTable.cohortId, parseInt(cohortId, 10)));
  if (dateFrom)
    filters.push(gte(attendanceSessionsTable.sessionDate, toDateOnly(dateFrom)));
  if (dateTo)
    filters.push(lte(attendanceSessionsTable.sessionDate, toDateOnly(dateTo)));

  const rows = await sessionWithNames.where(
    filters.length > 0 ? and(...filters) : undefined,
  );

  const withCountsRows = await Promise.all(
    rows.map(async (row) => ({ ...row, ...(await withCounts(row.id)) })),
  );

  res.json(ListAttendanceSessionsResponse.parse(withCountsRows));
});

router.post("/attendance/sessions", requireAuth, async (req, res): Promise<void> => {
  const parsed = CreateAttendanceSessionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  if (!req.session.userId) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  const [cohort] = await db
    .select()
    .from(cohortsTable)
    .where(eq(cohortsTable.id, parsed.data.cohortId));
  if (!cohort) {
    res.status(404).json({ error: "Cohort not found" });
    return;
  }
  if (req.session.role === "tutor" && cohort.tutorId !== req.session.tutorId) {
    res.status(403).json({ error: "Not allowed to create sessions for this cohort" });
    return;
  }

  const sessionDateStr = toDateOnly(parsed.data.sessionDate);

  if (!parsed.data.force) {
    const [existing] = await db
      .select()
      .from(attendanceSessionsTable)
      .where(
        and(
          eq(attendanceSessionsTable.cohortId, parsed.data.cohortId),
          eq(attendanceSessionsTable.sessionDate, sessionDateStr),
        ),
      );
    if (existing) {
      res.status(409).json({
        error: "A session already exists for this cohort on this date",
      });
      return;
    }
  }

  const [session] = await db
    .insert(attendanceSessionsTable)
    .values({
      cohortId: parsed.data.cohortId,
      sessionDate: sessionDateStr,
      plannedStartTime: parsed.data.plannedStartTime,
      plannedEndTime: parsed.data.plannedEndTime,
      plannedDurationHours: parsed.data.plannedDurationHours,
      title: parsed.data.title ?? null,
      notes: parsed.data.notes ?? null,
      createdBy: req.session.userId,
    })
    .returning();

  if (!session) {
    res.status(500).json({ error: "Failed to create session" });
    return;
  }

  await writeAuditLog(req, {
    action: "create",
    entityType: "attendance_session",
    entityId: session.id,
    newValue: session,
  });

  const [full] = await sessionWithNames.where(
    eq(attendanceSessionsTable.id, session.id),
  );
  res.status(201).json(
    CreateAttendanceSessionResponse.parse({
      ...full,
      ...(await withCounts(session.id)),
    }),
  );
});

const buildRegister = async (sessionId: number) => {
  const [session] = await sessionWithNames.where(
    eq(attendanceSessionsTable.id, sessionId),
  );
  if (!session) return null;

  const counts = await withCounts(sessionId);

  const learners = await db
    .select()
    .from(learnersTable)
    .where(eq(learnersTable.cohortId, session.cohortId));

  const records = await db
    .select()
    .from(attendanceRecordsTable)
    .where(eq(attendanceRecordsTable.sessionId, sessionId));

  const editors = await db.select().from(usersTable);

  const entries = learners.map((learner) => {
    const record = records.find((r) => r.learnerId === learner.id);
    const editor = record?.lastEditedBy
      ? editors.find((u) => u.id === record.lastEditedBy)
      : undefined;
    return {
      recordId: record?.id ?? null,
      learnerId: learner.id,
      learnerName: `${learner.firstName} ${learner.lastName}`,
      learnerRef: learner.learnerRef,
      status: record?.status ?? "absent_unauthorised",
      hoursAttended: record?.hoursAttended ?? 0,
      minutesLate: record?.minutesLate ?? 0,
      notes: record?.notes ?? null,
      overrideReason: record?.overrideReason ?? null,
      lastEditedBy: record?.lastEditedBy ?? null,
      lastEditedByName: editor ? `${editor.firstName} ${editor.lastName}` : null,
    };
  });

  return { session: { ...session, ...counts }, entries };
};

router.get(
  "/attendance/sessions/:id",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = GetAttendanceSessionParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const register = await buildRegister(params.data.id);
    if (!register) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    if (
      req.session.role === "tutor" &&
      register.session.tutorId !== req.session.tutorId
    ) {
      res.status(403).json({ error: "Not allowed to view this session" });
      return;
    }

    res.json(GetAttendanceSessionResponse.parse(register));
  },
);

router.patch(
  "/attendance/sessions/:id",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = UpdateAttendanceSessionParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }
    const parsed = UpdateAttendanceSessionBody.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const [existing] = await sessionWithNames.where(
      eq(attendanceSessionsTable.id, params.data.id),
    );
    if (!existing) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    if (
      req.session.role === "tutor" &&
      existing.tutorId !== req.session.tutorId
    ) {
      res.status(403).json({ error: "Not allowed to edit this session" });
      return;
    }

    const { sessionDate, ...restUpdate } = parsed.data;

    await db
      .update(attendanceSessionsTable)
      .set({
        ...restUpdate,
        ...(sessionDate ? { sessionDate: toDateOnly(sessionDate) } : {}),
      })
      .where(eq(attendanceSessionsTable.id, params.data.id));

    await writeAuditLog(req, {
      action: "update",
      entityType: "attendance_session",
      entityId: params.data.id,
      previousValue: existing,
      newValue: parsed.data,
    });

    const [full] = await sessionWithNames.where(
      eq(attendanceSessionsTable.id, params.data.id),
    );
    res.json(
      UpdateAttendanceSessionResponse.parse({
        ...full,
        ...(await withCounts(params.data.id)),
      }),
    );
  },
);

router.put(
  "/attendance/sessions/:id/register",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = SaveAttendanceRegisterParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }
    const parsed = SaveAttendanceRegisterBody.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.message });
      return;
    }

    const [session] = await sessionWithNames.where(
      eq(attendanceSessionsTable.id, params.data.id),
    );
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    if (req.session.role === "tutor" && session.tutorId !== req.session.tutorId) {
      res.status(403).json({ error: "Not allowed to edit this session" });
      return;
    }

    for (const entry of parsed.data.entries) {
      const outsidePlannedDuration =
        entry.hoursAttended > session.plannedDurationHours;
      if (outsidePlannedDuration && !entry.overrideReason) {
        res.status(400).json({
          error: `An override reason is required for learner ${entry.learnerId}: hours attended exceed the session's planned duration`,
        });
        return;
      }

      const [existingRecord] = await db
        .select()
        .from(attendanceRecordsTable)
        .where(
          and(
            eq(attendanceRecordsTable.sessionId, params.data.id),
            eq(attendanceRecordsTable.learnerId, entry.learnerId),
          ),
        );

      if (existingRecord) {
        await db
          .update(attendanceRecordsTable)
          .set({
            status: entry.status,
            hoursAttended: entry.hoursAttended,
            minutesLate: entry.minutesLate,
            notes: entry.notes ?? null,
            overrideReason: entry.overrideReason ?? null,
            lastEditedBy: req.session.userId ?? null,
          })
          .where(eq(attendanceRecordsTable.id, existingRecord.id));
      } else {
        await db.insert(attendanceRecordsTable).values({
          sessionId: params.data.id,
          learnerId: entry.learnerId,
          status: entry.status,
          hoursAttended: entry.hoursAttended,
          minutesLate: entry.minutesLate,
          notes: entry.notes ?? null,
          overrideReason: entry.overrideReason ?? null,
          lastEditedBy: req.session.userId ?? null,
        });
      }
    }

    await writeAuditLog(req, {
      action: "save_register",
      entityType: "attendance_session",
      entityId: params.data.id,
      newValue: parsed.data,
    });

    const register = await buildRegister(params.data.id);
    res.json(SaveAttendanceRegisterResponse.parse(register));
  },
);

router.post(
  "/attendance/sessions/:id/mark-all-present",
  requireAuth,
  async (req, res): Promise<void> => {
    const params = MarkAllPresentParams.safeParse(req.params);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    const [session] = await sessionWithNames.where(
      eq(attendanceSessionsTable.id, params.data.id),
    );
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    if (req.session.role === "tutor" && session.tutorId !== req.session.tutorId) {
      res.status(403).json({ error: "Not allowed to edit this session" });
      return;
    }

    const learners = await db
      .select()
      .from(learnersTable)
      .where(eq(learnersTable.cohortId, session.cohortId));

    for (const learner of learners) {
      const [existingRecord] = await db
        .select()
        .from(attendanceRecordsTable)
        .where(
          and(
            eq(attendanceRecordsTable.sessionId, params.data.id),
            eq(attendanceRecordsTable.learnerId, learner.id),
          ),
        );

      const values = {
        status: "present" as const,
        hoursAttended: session.plannedDurationHours,
        minutesLate: 0,
        overrideReason: null,
        lastEditedBy: req.session.userId ?? null,
      };

      if (existingRecord) {
        await db
          .update(attendanceRecordsTable)
          .set(values)
          .where(eq(attendanceRecordsTable.id, existingRecord.id));
      } else {
        await db.insert(attendanceRecordsTable).values({
          sessionId: params.data.id,
          learnerId: learner.id,
          ...values,
        });
      }
    }

    await writeAuditLog(req, {
      action: "mark_all_present",
      entityType: "attendance_session",
      entityId: params.data.id,
    });

    const register = await buildRegister(params.data.id);
    res.json(MarkAllPresentResponse.parse(register));
  },
);

export default router;
