import { inArray } from "drizzle-orm";
import {
  db,
  tutorsTable,
  cohortsTable,
  usersTable,
  learnersTable,
} from "@workspace/db";
import type { LearnerAllocationHistory } from "@workspace/db";

export const enrichAllocationHistory = async (
  rows: LearnerAllocationHistory[],
): Promise<
  (LearnerAllocationHistory & {
    learnerName: string;
    previousTutorName: string | null;
    newTutorName: string | null;
    previousCohortName: string | null;
    newCohortName: string | null;
    changedByName: string;
  })[]
> => {
  if (rows.length === 0) return [];

  const tutorIds = [
    ...new Set(
      rows.flatMap((r) => [r.previousTutorId, r.newTutorId]).filter((v): v is number => v != null),
    ),
  ];
  const cohortIds = [
    ...new Set(
      rows.flatMap((r) => [r.previousCohortId, r.newCohortId]).filter((v): v is number => v != null),
    ),
  ];
  const learnerIds = [...new Set(rows.map((r) => r.learnerId))];
  const userIds = [...new Set(rows.map((r) => r.changedBy))];

  const [tutors, cohorts, learners, users] = await Promise.all([
    tutorIds.length
      ? db.select().from(tutorsTable).where(inArray(tutorsTable.id, tutorIds))
      : Promise.resolve([] as (typeof tutorsTable.$inferSelect)[]),
    cohortIds.length
      ? db.select().from(cohortsTable).where(inArray(cohortsTable.id, cohortIds))
      : Promise.resolve([] as (typeof cohortsTable.$inferSelect)[]),
    learnerIds.length
      ? db.select().from(learnersTable).where(inArray(learnersTable.id, learnerIds))
      : Promise.resolve([] as (typeof learnersTable.$inferSelect)[]),
    userIds.length
      ? db.select().from(usersTable).where(inArray(usersTable.id, userIds))
      : Promise.resolve([] as (typeof usersTable.$inferSelect)[]),
  ]);

  const tutorName = (id: number | null) =>
    id == null ? null : (() => {
      const t = tutors.find((x) => x.id === id);
      return t ? `${t.firstName} ${t.lastName}` : null;
    })();
  const cohortName = (id: number | null) =>
    id == null ? null : (cohorts.find((x) => x.id === id)?.name ?? null);
  const learnerName = (id: number) => {
    const l = learners.find((x) => x.id === id);
    return l ? `${l.firstName} ${l.lastName}` : "Unknown learner";
  };
  const userName = (id: number) => {
    const u = users.find((x) => x.id === id);
    return u ? `${u.firstName} ${u.lastName}` : "Unknown user";
  };

  return rows.map((r) => ({
    ...r,
    learnerName: learnerName(r.learnerId),
    previousTutorName: tutorName(r.previousTutorId),
    newTutorName: tutorName(r.newTutorId),
    previousCohortName: cohortName(r.previousCohortId),
    newCohortName: cohortName(r.newCohortId),
    changedByName: userName(r.changedBy),
  }));
};
