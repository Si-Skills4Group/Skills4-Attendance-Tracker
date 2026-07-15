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
import { learnerStatusEnum } from "./enums";

export const learnersTable = pgTable("learners", {
  id: serial("id").primaryKey(),
  learnerRef: text("learner_ref").notNull().unique(),
  uln: text("uln"),
  firstName: text("first_name").notNull(),
  lastName: text("last_name").notNull(),
  email: text("email"),
  employer: text("employer"),
  programme: text("programme").notNull(),
  level: text("level").notNull(),
  startDate: date("start_date", { mode: "string" }).notNull(),
  plannedEndDate: date("planned_end_date", { mode: "string" }),
  actualEndDate: date("actual_end_date", { mode: "string" }),
  withdrawalDate: date("withdrawal_date", { mode: "string" }),
  status: learnerStatusEnum("status").notNull().default("active"),
  tutorId: integer("tutor_id"),
  cohortId: integer("cohort_id"),
  externalSystemId: text("external_system_id"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const insertLearnerSchema = createInsertSchema(learnersTable).omit({
  id: true,
  createdAt: true,
  updatedAt: true,
});
export type InsertLearner = z.infer<typeof insertLearnerSchema>;
export type Learner = typeof learnersTable.$inferSelect;
