import {
  pgTable,
  serial,
  text,
  integer,
  date,
  timestamp,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const learnerAllocationHistoryTable = pgTable(
  "learner_allocation_history",
  {
    id: serial("id").primaryKey(),
    learnerId: integer("learner_id").notNull(),
    previousTutorId: integer("previous_tutor_id"),
    newTutorId: integer("new_tutor_id"),
    previousCohortId: integer("previous_cohort_id"),
    newCohortId: integer("new_cohort_id"),
    effectiveDate: date("effective_date", { mode: "string" }).notNull(),
    transferReason: text("transfer_reason"),
    changedBy: integer("changed_by").notNull(),
    changedDate: timestamp("changed_date", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
);

export const insertLearnerAllocationHistorySchema = createInsertSchema(
  learnerAllocationHistoryTable,
).omit({ id: true, changedDate: true });
export type InsertLearnerAllocationHistory = z.infer<
  typeof insertLearnerAllocationHistorySchema
>;
export type LearnerAllocationHistory =
  typeof learnerAllocationHistoryTable.$inferSelect;
