import { Router, type IRouter } from "express";
import { and, eq, inArray, isNull } from "drizzle-orm";
import {
  db,
  learnersTable,
  tutorsTable,
  cohortsTable,
  learnerAllocationHistoryTable,
} from "@workspace/db";
import {
  ListUnallocatedLearnersResponse,
  GetAllocationByTutorResponse,
  AllocateLearnersBody,
  AllocateLearnersResponse,
  ListAllocationHistoryResponse,
} from "@workspace/api-zod";
import { requireAdmin } from "../lib/auth";
import { writeAuditLog } from "../lib/audit";
import { learnersWithNames } from "../lib/learners-query";
import { enrichAllocationHistory } from "../lib/allocation";
import { toDateOnly } from "../lib/dates";

const router: IRouter = Router();

router.get(
  "/allocation/unallocated-learners",
  requireAdmin,
  async (_req, res): Promise<void> => {
    const rows = await learnersWithNames.where(
      isNull(learnersTable.tutorId),
    );
    res.json(ListUnallocatedLearnersResponse.parse(rows));
  },
);

router.get(
  "/allocation/by-tutor",
  requireAdmin,
  async (_req, res): Promise<void> => {
    const tutors = await db.select().from(tutorsTable);
    const cohorts = await db.select().from(cohortsTable);
    const learners = await learnersWithNames;

    const result = tutors.map((tutor) => {
      const tutorCohorts = cohorts.filter((c) => c.tutorId === tutor.id);
      const cohortGroups = tutorCohorts.map((cohort) => ({
        cohortId: cohort.id,
        cohortName: cohort.name,
        learners: learners.filter((l) => l.cohortId === cohort.id),
      }));
      const directLearners = learners.filter(
        (l) => l.tutorId === tutor.id && l.cohortId == null,
      );
      return {
        tutorId: tutor.id,
        tutorName: `${tutor.firstName} ${tutor.lastName}`,
        cohorts: cohortGroups,
        unassignedCohortLearners: directLearners,
      };
    });

    res.json(GetAllocationByTutorResponse.parse(result));
  },
);

router.post("/allocation/allocate", requireAdmin, async (req, res): Promise<void> => {
  const parsed = AllocateLearnersBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const { learnerIds, tutorId, cohortId, effectiveDate, transferReason } =
    parsed.data;

  if (!req.session.userId) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  const learners = await db
    .select()
    .from(learnersTable)
    .where(inArray(learnersTable.id, learnerIds));

  const updatedIds: number[] = [];
  const effectiveDateStr = toDateOnly(effectiveDate);

  for (const learner of learners) {
    await db
      .update(learnersTable)
      .set({ tutorId: tutorId ?? null, cohortId: cohortId ?? null })
      .where(eq(learnersTable.id, learner.id));

    await db.insert(learnerAllocationHistoryTable).values({
      learnerId: learner.id,
      previousTutorId: learner.tutorId,
      newTutorId: tutorId ?? null,
      previousCohortId: learner.cohortId,
      newCohortId: cohortId ?? null,
      effectiveDate: effectiveDateStr,
      transferReason: transferReason ?? null,
      changedBy: req.session.userId,
    });

    updatedIds.push(learner.id);
  }

  await writeAuditLog(req, {
    action: "allocate",
    entityType: "learner",
    newValue: { learnerIds: updatedIds, tutorId, cohortId },
  });

  res.json(AllocateLearnersResponse.parse({ updated: updatedIds.length }));
});

router.get(
  "/allocation/history",
  requireAdmin,
  async (req, res): Promise<void> => {
    const { learnerId, tutorId } = req.query as Record<
      string,
      string | undefined
    >;

    const filters = [];
    if (learnerId)
      filters.push(
        eq(learnerAllocationHistoryTable.learnerId, parseInt(learnerId, 10)),
      );
    if (tutorId)
      filters.push(
        eq(learnerAllocationHistoryTable.newTutorId, parseInt(tutorId, 10)),
      );

    const rows = await db
      .select()
      .from(learnerAllocationHistoryTable)
      .where(filters.length > 0 ? and(...filters) : undefined);

    const enriched = await enrichAllocationHistory(rows);
    res.json(ListAllocationHistoryResponse.parse(enriched));
  },
);

export default router;
