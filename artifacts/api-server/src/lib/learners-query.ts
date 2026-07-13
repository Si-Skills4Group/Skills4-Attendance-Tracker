import { eq, sql } from "drizzle-orm";
import { db, learnersTable, tutorsTable, cohortsTable } from "@workspace/db";

export const learnersWithNames = db
  .select({
    id: learnersTable.id,
    learnerRef: learnersTable.learnerRef,
    uln: learnersTable.uln,
    firstName: learnersTable.firstName,
    lastName: learnersTable.lastName,
    email: learnersTable.email,
    employer: learnersTable.employer,
    programme: learnersTable.programme,
    level: learnersTable.level,
    startDate: learnersTable.startDate,
    plannedEndDate: learnersTable.plannedEndDate,
    status: learnersTable.status,
    tutorId: learnersTable.tutorId,
    cohortId: learnersTable.cohortId,
    externalSystemId: learnersTable.externalSystemId,
    createdAt: learnersTable.createdAt,
    updatedAt: learnersTable.updatedAt,
    tutorName: sql<string | null>`concat(${tutorsTable.firstName}, ' ', ${tutorsTable.lastName})`,
    cohortName: cohortsTable.name,
  })
  .from(learnersTable)
  .leftJoin(tutorsTable, eq(learnersTable.tutorId, tutorsTable.id))
  .leftJoin(cohortsTable, eq(learnersTable.cohortId, cohortsTable.id));
