import {
  pgTable,
  serial,
  text,
  integer,
  boolean,
  date,
  timestamp,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { deliveryDayEnum } from "./enums";

export const cohortsTable = pgTable("cohorts", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  programme: text("programme").notNull(),
  level: text("level").notNull(),
  tutorId: integer("tutor_id"),
  deliveryDay: deliveryDayEnum("delivery_day").notNull(),
  sessionStartTime: text("session_start_time").notNull(),
  sessionEndTime: text("session_end_time").notNull(),
  startDate: date("start_date", { mode: "string" }).notNull(),
  endDate: date("end_date", { mode: "string" }),
  active: boolean("active").notNull().default(true),
  externalSystemId: text("external_system_id"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const insertCohortSchema = createInsertSchema(cohortsTable).omit({
  id: true,
  createdAt: true,
  updatedAt: true,
});
export type InsertCohort = z.infer<typeof insertCohortSchema>;
export type Cohort = typeof cohortsTable.$inferSelect;
