import {
  pgTable,
  serial,
  text,
  integer,
  numeric,
  date,
  timestamp,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const attendanceSessionsTable = pgTable("attendance_sessions", {
  id: serial("id").primaryKey(),
  cohortId: integer("cohort_id").notNull(),
  sessionDate: date("session_date", { mode: "string" }).notNull(),
  plannedStartTime: text("planned_start_time").notNull(),
  plannedEndTime: text("planned_end_time").notNull(),
  plannedDurationHours: numeric("planned_duration_hours", {
    mode: "number",
  }).notNull(),
  title: text("title"),
  notes: text("notes"),
  createdBy: integer("created_by").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const insertAttendanceSessionSchema = createInsertSchema(
  attendanceSessionsTable,
).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertAttendanceSession = z.infer<
  typeof insertAttendanceSessionSchema
>;
export type AttendanceSession = typeof attendanceSessionsTable.$inferSelect;
