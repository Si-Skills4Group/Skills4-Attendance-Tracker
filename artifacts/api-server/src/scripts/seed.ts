// Dev-only seed script: creates an initial admin user, a couple of tutors
// (with linked user accounts), cohorts, and learners so the app is usable
// on first load. Run with:
//   node --experimental-strip-types src/scripts/seed.ts
// from the artifacts/api-server directory.
import {
  db,
  usersTable,
  tutorsTable,
  cohortsTable,
  learnersTable,
  appSettingsTable,
} from "@workspace/db";
import { eq } from "drizzle-orm";
import { hashPassword } from "../lib/auth";

async function main() {
  console.log("Seeding Skills4Attendance database...");

  const [existingAdmin] = await db
    .select()
    .from(usersTable)
    .where(eq(usersTable.email, "admin@skills4group.org"));

  if (existingAdmin) {
    console.log("Seed data already present, skipping.");
    return;
  }

  const adminPasswordHash = await hashPassword("Admin123!");
  const [admin] = await db
    .insert(usersTable)
    .values({
      email: "admin@skills4group.org",
      passwordHash: adminPasswordHash,
      firstName: "Alex",
      lastName: "Morgan",
      role: "admin",
    })
    .returning();
  if (!admin) throw new Error("Failed to seed admin user");

  const tutorPasswordHash = await hashPassword("Tutor123!");

  const tutorSeeds = [
    {
      firstName: "Priya",
      lastName: "Shah",
      email: "priya.shah@skills4group.org",
      employeeRef: "T-001",
    },
    {
      firstName: "Daniel",
      lastName: "Okafor",
      email: "daniel.okafor@skills4group.org",
      employeeRef: "T-002",
    },
  ];

  const tutors = [];
  for (const t of tutorSeeds) {
    const [user] = await db
      .insert(usersTable)
      .values({
        email: t.email,
        passwordHash: tutorPasswordHash,
        firstName: t.firstName,
        lastName: t.lastName,
        role: "tutor",
      })
      .returning();
    if (!user) throw new Error("Failed to seed tutor user");

    const [tutor] = await db
      .insert(tutorsTable)
      .values({
        userId: user.id,
        firstName: t.firstName,
        lastName: t.lastName,
        email: t.email,
        employeeRef: t.employeeRef,
        active: true,
      })
      .returning();
    if (!tutor) throw new Error("Failed to seed tutor");

    await db
      .update(usersTable)
      .set({ tutorId: tutor.id })
      .where(eq(usersTable.id, user.id));

    tutors.push(tutor);
  }

  const today = new Date();
  const startDate = new Date(today.getTime() - 60 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);

  const cohortSeeds = [
    {
      name: "Digital Marketing L3 - Cohort A",
      programme: "Digital Marketing",
      level: "Level 3",
      deliveryDay: "monday" as const,
      sessionStartTime: "09:00",
      sessionEndTime: "12:00",
      tutorId: tutors[0]!.id,
    },
    {
      name: "Business Admin L2 - Cohort B",
      programme: "Business Administration",
      level: "Level 2",
      deliveryDay: "wednesday" as const,
      sessionStartTime: "13:00",
      sessionEndTime: "16:00",
      tutorId: tutors[1]!.id,
    },
  ];

  const cohorts = [];
  for (const c of cohortSeeds) {
    const [cohort] = await db
      .insert(cohortsTable)
      .values({ ...c, startDate, active: true })
      .returning();
    if (!cohort) throw new Error("Failed to seed cohort");
    cohorts.push(cohort);
  }

  const learnerSeeds = [
    { firstName: "Jamie", lastName: "Clarke", ref: "L-1001", cohort: cohorts[0]! },
    { firstName: "Sam", lastName: "Iqbal", ref: "L-1002", cohort: cohorts[0]! },
    { firstName: "Robyn", lastName: "Fletcher", ref: "L-1003", cohort: cohorts[1]! },
    { firstName: "Chidi", lastName: "Eze", ref: "L-1004", cohort: cohorts[1]! },
  ];

  for (const l of learnerSeeds) {
    await db.insert(learnersTable).values({
      learnerRef: l.ref,
      firstName: l.firstName,
      lastName: l.lastName,
      programme: l.cohort.programme,
      level: l.cohort.level,
      startDate,
      status: "active",
      tutorId: l.cohort.tutorId,
      cohortId: l.cohort.id,
    });
  }

  await db.insert(appSettingsTable).values({});

  console.log("Seed complete.");
  console.log("Admin login: admin@skills4group.org / Admin123!");
  console.log("Tutor login: priya.shah@skills4group.org / Tutor123!");
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
